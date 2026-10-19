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
        self.stale_sync_lock = asyncio.Lock()

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

    async def sync_stale_on_open(self) -> list[dict[str, Any]]:
        """Schedule a SQL-only refresh on open if the last successful one exceeds 24h.

        Serialize the due check and job creation, so concurrent web/iOS opens
        do not submit the same source twice within the running server process.
        """
        cutoff = datetime.now(UTC).timestamp() - 24 * 60 * 60
        created = []
        async with self.stale_sync_lock:
            for source in await asyncio.to_thread(self.store.sources):
                if source["type"] != "kodi" or not source["enabled"]:
                    continue
                if source["id"] in self.active:
                    continue
                last = source.get("last_sync")
                if last:
                    try:
                        date = datetime.fromisoformat(last.replace("Z", "+00:00"))
                        if date.tzinfo is None:
                            date = date.replace(tzinfo=UTC)
                        if date.timestamp() >= cutoff:
                            continue
                    except (TypeError, ValueError, OverflowError):
                        pass
                try:
                    created.append(await self.submit(source["id"]))
                except (BusyError, ValueError):
                    continue
        return created

    async def bootstrap_empty_catalogue(self) -> list[dict[str, Any]]:
        """Do the first import when the database has no records.

        Existing populated catalogues must *not* synchronize on server startup.
        Once the first import is complete, only explicit web/iOS opening checks
        (or manual actions) can trigger subsequent imports.
        """
        async with self.stale_sync_lock:
            status = await asyncio.to_thread(self.store.status)
            if status["editions"] > 0:
                return []
            created = []
            for source in await asyncio.to_thread(self.store.sources):
                if source["type"] != "kodi" or not source["enabled"]:
                    continue
                if source["id"] in self.active:
                    continue
                # A successfully checked but genuinely empty Kodi library is
                # not imported again on every server/container restart.
                last = source.get("last_sync")
                if last:
                    try:
                        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if (datetime.now(UTC) - dt).total_seconds() < 24 * 60 * 60:
                            continue
                    except (ValueError, TypeError, OverflowError):
                        pass
                try:
                    created.append(await self.submit(source["id"]))
                except (BusyError, ValueError):
                    continue
            return created

    async def close(self):
        # Scan listeners must not delay shutdown indefinitely.
        for task in self.scan_tasks:
            task.cancel()
        # Let regular imports finish before the database/container exits.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
