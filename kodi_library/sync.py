"""Background imports with one job per source and atomic snapshot publication."""

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from .connectors.base import ConnectorError
from .connectors.clz import parse_clz
from .connectors.kodi import KodiConnector
from .players import library_movie_ids

LOGGER = logging.getLogger(__name__)


def only_movies_in_library(records, library_movie_ids: set[int]):
    """Keep non-movie media and Kodi versions of movies visible via JSON-RPC.

    A movie can have several files/versions; membership is by movie ID, not
    file availability, filenames, dates or local watch/list status.
    """
    sql_movie_ids = {
        record.metadata.get("kodi_id")
        for record in records
        if record.kind == "movie" and isinstance(record.metadata.get("kodi_id"), int)
    }
    # Reject a different Kodi instance/library before publishing mass deletions.
    if sql_movie_ids and library_movie_ids and not sql_movie_ids.intersection(library_movie_ids):
        raise ConnectorError(
            "L'instance Kodi et la source MariaDB n'ont aucun film en commun. "
            "Vérifiez que l'instance utilise la base MyVideos de cette source."
        )
    if sql_movie_ids and not library_movie_ids:
        raise ConnectorError(
            "Kodi renvoie une vidéothèque de films vide alors que MariaDB "
            "contient des films. Synchronisation annulée pour éviter une suppression massive."
        )
    omitted = sql_movie_ids - library_movie_ids
    kept = [
        record
        for record in records
        if record.kind != "movie" or record.metadata.get("kodi_id") in library_movie_ids
    ]
    return kept, {
        "library_movie_count": len(library_movie_ids),
        "library_excluded_movie_ids": len(omitted),
        "library_excluded_editions": len(records) - len(kept),
        "library_extra_movie_ids": len(library_movie_ids - sql_movie_ids),
    }


class BusyError(Exception):
    pass


class SyncManager:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.active: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.scheduler: asyncio.Task | None = None

    async def submit(self, source_id: str, xml: bytes | None = None) -> dict[str, Any]:
        if source_id in self.active:
            raise BusyError("An import or synchronization is already running for this source.")
        source = await asyncio.to_thread(self.store.source, source_id, private=True)
        if not source:
            raise ValueError("Unknown source.")
        if xml is None and source["type"] != "kodi":
            raise ValueError("CLZ sources are refreshed by uploading an XML export.")
        if xml is not None and source["type"] != "clz":
            raise ValueError("XML imports require a CLZ source.")
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

    async def _run(self, job, source, xml):
        try:
            await asyncio.to_thread(self.store.start_job, job["id"])
            if xml is not None:
                await asyncio.to_thread(self.store.update_job_progress, job["id"], "parsing_clz")
                records = await asyncio.to_thread(parse_clz, xml)
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
                library_report = {"library_verified": False}
                players = await asyncio.to_thread(self.store.players)
                linked = [player for player in players if player["source_id"] == source["id"]]
                if linked:
                    # Use a single deterministic reference player. It is
                    # explicitly reported, to avoid silently combining
                    # libraries from devices on different databases.
                    player = linked[0]
                    configured = await asyncio.to_thread(self.store.player, player["id"])
                    if configured is None:
                        raise ConnectorError("L'instance Kodi configurée est introuvable.")
                    await asyncio.to_thread(
                        self.store.update_job_progress, job["id"], "verifying_library"
                    )
                    ids = await library_movie_ids(configured["config"])
                    records, checked = only_movies_in_library(records, ids)
                    library_report.update(checked)
                    library_report["library_verified"] = True
                    library_report["library_player"] = player["name"]
                else:
                    library_report["library_warning"] = (
                        "Aucune instance Kodi associée à cette source : "
                        "impossible de vérifier les suppressions dans la vidéothèque. "
                        "Seul l'état MariaDB est synchronisé."
                    )
            await asyncio.to_thread(self.store.update_job_progress, job["id"], "publishing")
            publish_started = monotonic()
            result = await asyncio.to_thread(self.store.apply_snapshot, source["id"], records)
            result["sqlite_publish_ms"] = round((monotonic() - publish_started) * 1000)
            if database:
                result["database"] = database
                result["mariadb_read_ms"] = mariadb_read_ms
                result.update(connector.snapshot_report)
                result.update(library_report)
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
        # Let imports finish before the database/container exits.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
