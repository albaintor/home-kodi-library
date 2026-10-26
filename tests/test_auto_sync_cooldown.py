"""Regression coverage: app startup must not re-import stale sources repeatedly."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from home_media_catalog.sync import SyncManager


def _manager(last_attempt: str | None, last_sync: str | None = None):
    source = {
        "id": "source-kodi",
        "type": "kodi",
        "enabled": True,
        "last_sync": last_sync,
    }
    store = SimpleNamespace(
        sources=lambda: [source],
        status=lambda: {"editions": 0},
        last_kodi_sync_attempt=lambda source_id: last_attempt,
    )
    manager = SyncManager(store, settings=None)
    calls = []

    async def submit(source_id):
        calls.append(source_id)
        return {"id": "job-1", "source_id": source_id}

    manager.submit = submit
    return manager, calls


def test_stale_source_with_recent_failed_attempt_is_not_imported_on_app_open():
    now = datetime.now(UTC)
    manager, calls = _manager(
        last_attempt=(now - timedelta(minutes=20)).isoformat(),
        last_sync=(now - timedelta(days=3)).isoformat(),
    )
    assert asyncio.run(manager.sync_stale_on_open()) == []
    assert calls == []


def test_stale_source_with_old_attempt_is_scheduled_once():
    now = datetime.now(UTC)
    manager, calls = _manager(
        last_attempt=(now - timedelta(days=2)).isoformat(),
        last_sync=(now - timedelta(days=3)).isoformat(),
    )
    jobs = asyncio.run(manager.sync_stale_on_open())
    assert [job["id"] for job in jobs] == ["job-1"]
    assert calls == ["source-kodi"]


def test_empty_catalogue_bootstrap_respects_recent_attempt_across_server_restarts():
    now = datetime.now(UTC)
    manager, calls = _manager(
        last_attempt=(now - timedelta(hours=3)).isoformat(),
        last_sync=None,
    )
    assert asyncio.run(manager.bootstrap_empty_catalogue()) == []
    assert calls == []


def test_empty_catalogue_without_previous_attempt_can_bootstrap():
    manager, calls = _manager(last_attempt=None, last_sync=None)
    jobs = asyncio.run(manager.bootstrap_empty_catalogue())
    assert len(jobs) == 1
    assert calls == ["source-kodi"]
