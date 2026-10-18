"""Background imports with one job per source and atomic snapshot publication."""

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from .connectors.base import ConnectorError
from .connectors.clz import parse_clz
from .connectors.collection_xml import COLLECTION_TYPES, parse_collection
from .connectors.kodi import KodiConnector
from .kodi_scan import KodiScanUnavailable, scan_until_finished

LOGGER = logging.getLogger(__name__)


class BusyError(Exception):
    pass


class SyncManager:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.active: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.scan_tasks: set[asyncio.Task] = set()
        self.scheduler: asyncio.Task | None = None

    async def submit(self, source_id: str, xml: bytes | None = None) -> dict[str, Any]:
        if source_id in self.active:
            raise BusyError("An import or synchronization is already running for this source.")
        source = await asyncio.to_thread(self.store.source, source_id, private=True)
        if not source:
            raise ValueError("Unknown source.")
        if xml is None and source["type"] != "kodi":
            raise ValueError("Collection sources are refreshed by uploading an XML export.")
        if xml is not None and source["type"] not in COLLECTION_TYPES:
            raise ValueError("XML imports require a collection source.")
        if not source["enabled"]:
            raise ValueError("Enable the source before synchronizing it.")
        # No await between checking again and claiming the source.
        if source_id in self.active:
            raise BusyError("A synchronization is already running for this source.")
        self.active.add(source_id)
        try:
            job = await asyncio.to_thread(self.store.new_job, source_id)
        except Exception:
            self.active.remove(source_id)
            raise
        task = asyncio.create_task(self._run(job, source, xml))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def submit_scan(self, source_id: str, player: dict) -> dict[str, Any]:
        return await self.submit_scans(source_id, [player])

    async def submit_scans(self, source_id: str, players: list[dict]) -> dict[str, Any]:
        """Reserve one job per source, scanning all configured instances first."""
        if source_id in self.active:
            raise BusyError("Une actualisation est déjà en cours pour cette source.")
        source = await asyncio.to_thread(self.store.source, source_id, private=True)
        if not source or source["type"] != "kodi" or not source["enabled"]:
            raise ValueError("Source Kodi inconnue ou désactivée.")
        if not players or any(player["source_id"] != source_id for player in players):
            raise ValueError("Aucune instance Kodi valide pour cette source.")
        self.active.add(source_id)
        try:
            job = await asyncio.to_thread(self.store.new_job, source_id)
        except Exception:
            self.active.discard(source_id)
            raise
        task = asyncio.create_task(self._run_scan(job, source, players))
        self.tasks.add(task)
        self.scan_tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self.scan_tasks.discard)
        return job

    async def _run_scan(self, job: dict, source: dict, players: list[dict]) -> None:
        try:
            await asyncio.to_thread(self.store.start_job, job["id"])
            scanned, warnings = [], []
            total = len(players)
            for index, player in enumerate(players):
                await asyncio.to_thread(
                    self.store.update_job_progress, job["id"], "kodi_scanning", index, total
                )
                try:
                    method = await scan_until_finished(player["config"])
                    scanned.append({"name": player["name"], "method": method})
                except KodiScanUnavailable as exc:
                    # The scan never started. Reading existing Kodi SQL data
                    # is safe, but must be labelled as an SQL-only refresh.
                    warnings.append(f"{player['name']} : {exc}")
                await asyncio.to_thread(
                    self.store.update_job_progress, job["id"], "kodi_scanning", index + 1, total
                )
            phase = "kodi_scan_finished" if scanned else "kodi_sql_fallback"
            await asyncio.to_thread(self.store.update_job_progress, job["id"], phase)
            await self._run(
                job,
                source,
                None,
                scan_triggered=bool(scanned),
                scan_info={"kodi_scanned_players": scanned, "kodi_scan_warnings": warnings},
            )
        except ConnectorError as exc:
            await asyncio.to_thread(self.store.finish_job, job["id"], error=str(exc))
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.store.finish_job,
                job["id"],
                error="Analyse Kodi interrompue avant confirmation de fin.",
            )
            raise
        except Exception:
            LOGGER.exception("Unexpected Kodi scan monitoring failure")
            await asyncio.to_thread(
                self.store.finish_job,
                job["id"],
                error="Erreur de suivi de l'analyse Kodi ; catalogue local non actualisé.",
            )
        finally:
            self.active.discard(source["id"])

    async def _run(
        self, job, source, xml, *, scan_triggered: bool = False, scan_info: dict | None = None
    ):
        try:
            await asyncio.to_thread(self.store.start_job, job["id"])
            if xml is not None:
                await asyncio.to_thread(
                    self.store.update_job_progress, job["id"], f"parsing_{source['type']}"
                )
                if source["type"] == "clz":
                    records = await asyncio.to_thread(parse_clz, xml)
                else:
                    records = await asyncio.to_thread(parse_collection, xml, source["type"])
                database = None
            else:
                connector = KodiConnector(
                    source["config"],
                    progress=lambda phase, current, total: self.store.update_job_progress(
                        job["id"], phase, current, total
                    ),
                )
                fetch_started = monotonic()
                records = await asyncio.to_thread(connector.fetch)
                mariadb_read_ms = round((monotonic() - fetch_started) * 1000)
                database = connector.database
            await asyncio.to_thread(self.store.update_job_progress, job["id"], "publishing")
            publish_started = monotonic()
            result = await asyncio.to_thread(self.store.apply_snapshot, source["id"], records)
            result["sqlite_publish_ms"] = round((monotonic() - publish_started) * 1000)
            if scan_info is not None:
                result["kodi_scan"] = "finished" if scan_triggered else "sql_only"
                result.update(scan_info)
            if database:
                result["database"] = database
                result["mariadb_read_ms"] = mariadb_read_ms
                result.update(connector.snapshot_report)
            await asyncio.to_thread(self.store.finish_job, job["id"], result=result)
        except (ConnectorError, ValueError) as exc:
            await asyncio.to_thread(self.store.finish_job, job["id"], error=str(exc))
        except Exception:
            LOGGER.exception("Unexpected catalogue job failure")
            await asyncio.to_thread(
                self.store.finish_job,
                job["id"],
                error="Unexpected import failure; see server logs. The previous catalogue was retained if publication failed.",
            )
        finally:
            self.active.discard(source["id"])

    def start(self):
        if self.settings.scheduler_enabled:
            self.scheduler = asyncio.create_task(self._schedule())

    async def _schedule(self):
        attempted: dict[str, float] = {}
        loop = asyncio.get_running_loop()
        while True:
            sources = await asyncio.to_thread(self.store.sources)
            for source in sources:
                if source["type"] != "kodi" or not source["enabled"] or source["id"] in self.active:
                    continue
                last = source.get("last_sync")
                due = (
                    not last
                    or (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
                    >= self.settings.sync_interval
                )
                if (
                    due
                    and loop.time() - attempted.get(source["id"], -self.settings.sync_interval)
                    >= self.settings.sync_interval
                ):
                    attempted[source["id"]] = loop.time()
                    try:
                        await self.submit(source["id"])
                    except (BusyError, ValueError):
                        pass
            await asyncio.sleep(30)

    async def close(self):
        if self.scheduler:
            self.scheduler.cancel()
            await asyncio.gather(self.scheduler, return_exceptions=True)
        # Scan listeners must not delay shutdown indefinitely.
        for task in self.scan_tasks:
            task.cancel()
        # Let regular imports finish before the database/container exits.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
