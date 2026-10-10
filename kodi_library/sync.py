"""Background imports with one job per source and atomic snapshot publication."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from .connectors.base import ConnectorError
from .connectors.clz import parse_clz
from .connectors.kodi import KodiConnector

LOGGER = logging.getLogger(__name__)


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
                records = await asyncio.to_thread(parse_clz, xml)
                database = None
            else:
                connector = KodiConnector(source["config"])
                records = await asyncio.to_thread(connector.fetch)
                database = connector.database
            result = await asyncio.to_thread(self.store.apply_snapshot, source["id"], records)
            if database:
                result["database"] = database
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
