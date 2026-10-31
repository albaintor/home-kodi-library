"""Durable per-edition resume points must survive sync and reject stale writes."""

import json
from pathlib import Path

from home_media_catalog.store import Store


def _catalogue(tmp_path: Path) -> Store:
    store = Store(tmp_path / "catalog.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO sources(id,name,type,config) VALUES(?,?,?,?)",
            ("source1", "Kodi", "kodi", "{}"),
        )
        conn.execute(
            "INSERT INTO works(id,kind,title) VALUES(?,?,?)",
            ("media1", "episode", "Episode one"),
        )
        payload = {
            "identifiers": {},
            "metadata": {"file": "smb://server/TV/Episode1.mkv", "kodi_id": 55},
            "formats": [], "collections": [],
            "kind": "episode", "title": "Episode one",
            "resume_seconds": 0, "total_seconds": 1000,
        }
        for edition in ("edition1", "edition2"):
            conn.execute(
                "INSERT INTO records(id,work_id,source_id,external_key,payload)"
                " VALUES(?,?,?,?,?)",
                (edition, "media1", "source1", edition, json.dumps(payload)),
            )
    return store


def test_resume_is_per_edition_and_newer_write_wins(tmp_path):
    store = _catalogue(tmp_path)
    first = store.save_playback_resume(
        "media1", "edition1", 120, 1000, "2026-10-31T09:00:00.000+00:00"
    )
    assert first["position_seconds"] == 120
    stale = store.save_playback_resume(
        "media1", "edition1", 10, 1000, "2026-10-31T08:00:00.000+00:00"
    )
    assert stale["position_seconds"] == 120
    second = store.save_playback_resume(
        "media1", "edition2", 400, 1000, "2026-10-31T09:01:00.000+00:00"
    )
    assert second["position_seconds"] == 400
    detail = store.detail("media1")
    editions = {e["id"]: e for e in detail["editions"]}
    assert editions["edition1"]["resume_seconds"] == 120
    assert editions["edition2"]["resume_seconds"] == 400
    assert detail["resume_seconds"] == 400
    assert not store.save_playback_resume(
        "media1", "does-not-exist", 1, 1000, "2026-10-31T09:02:00.000+00:00"
    )


def test_resume_survives_reinitialization(tmp_path):
    store = _catalogue(tmp_path)
    store.save_playback_resume(
        "media1", "edition1", 75.5, 1000, "2026-10-31T09:00:00.000+00:00"
    )
    reopened = Store(store.path)
    reopened.initialize()
    assert reopened.detail("media1")["editions"][0]["resume_seconds"] == 75.5


def test_catalogue_page_uses_newest_resume_and_keeps_it_after_restart(tmp_path):
    store = _catalogue(tmp_path)
    store.save_playback_resume(
        "media1", "edition1", 120, 1000, "2026-10-31T09:00:00.000+00:00"
    )
    store.save_playback_resume(
        "media1", "edition2", 400, 1000, "2026-10-31T09:01:00.000+00:00"
    )
    item = store.media(kind="episode")["items"][0]
    assert item["resume_seconds"] == 400
    assert item["total_seconds"] == 1000
    assert item["watch_status"] == "in_progress"
    assert item["playback_updated_at"] == "2026-10-31T09:01:00.000+00:00"
    store.save_playback_resume(
        "media1", "edition2", 1, 1000, "2026-10-31T08:00:00.000+00:00"
    )
    assert store.media(kind="episode")["items"][0]["resume_seconds"] == 400
    reopened = Store(store.path)
    reopened.initialize()
    assert reopened.media(kind="episode")["items"][0]["resume_seconds"] == 400


def test_series_poster_shows_partially_played_first_episode(tmp_path):
    store = _catalogue(tmp_path)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO works(id,kind,title) VALUES(?,?,?)",
            ("show1", "tvshow", "Show"),
        )
        conn.execute(
            "UPDATE works SET parent_id=? WHERE id=?",
            ("show1", "media1"),
        )
    store.save_playback_resume(
        "media1", "edition1", 250, 1000, "2026-10-31T09:00:00.000+00:00"
    )
    show = store.media(kind="tvshow")["items"][0]
    assert show["watch_status"] == "in_progress"
    assert show["series_resume_progress"] == 0.25
    assert store.detail("show1")["series_resume_progress"] == 0.25
