"""Kodi local SQLite libraries can be imported without MariaDB."""

import asyncio
from unittest.mock import patch

import pytest

from home_media_catalog.connectors.base import ConnectorError
from home_media_catalog.connectors.kodi_jsonrpc import fetch_kodi_snapshot, normalize_jsonrpc


def test_record_normalizer_keeps_identity_and_parent_links():
    row = {
        "episodeid": 44,
        "tvshowid": 10,
        "title": "Pilot",
        "season": 1,
        "episode": 1,
        "playcount": 1,
        "uniqueid": {"tvdb": "9876"},
        "runtime": 600,
    }
    record = normalize_jsonrpc("episode", row)
    assert record.external_key == "episode:44"
    assert record.parent_key == "tvshow:10"
    assert record.watched is True
    assert record.runtime_minutes == 10


def test_invalid_parent_aborts_complete_snapshot():
    with pytest.raises(ConnectorError, match="TV show identifier"):
        normalize_jsonrpc("episode", {"episodeid": 22, "title": "orphan"})


def test_jsonrpc_fetches_all_pages():
    calls = []

    async def rpc(method, params):
        calls.append((method, params["limits"]["start"]))
        label = {
            "GetMovies": ("movies", "movieid"),
            "GetTVShows": ("tvshows", "tvshowid"),
            "GetEpisodes": ("episodes", "episodeid"),
        }
        key, id_field = label[method.split(".")[-1]]
        if key == "movies":
            if params["limits"]["start"] == 0:
                return {
                    "limits": {"total": 201},
                    key: [{id_field: i, "title": str(i)} for i in range(200)],
                }
            return {"limits": {"total": 201}, key: [{id_field: 200, "title": "200"}]}
        if key == "episodes":
            return {"limits": {"total": 0}, key: []}
        return {"limits": {"total": 1}, key: [{id_field: 11, "title": "Show"}]}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy(_config, **_kwargs):
        yield rpc

    with patch("home_media_catalog.connectors.kodi_jsonrpc.connection", dummy):
        records = asyncio.run(fetch_kodi_snapshot({"host": "localhost"}))
    assert len(records) == 202
    assert calls[:2] == [("VideoLibrary.GetMovies", 0), ("VideoLibrary.GetMovies", 200)]


def test_incomplete_page_never_returns_a_partial_snapshot():
    async def rpc(_method, _params):
        return {"limits": {"total": 1}, "movies": []}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy(_config, **_kwargs):
        yield rpc

    with patch("home_media_catalog.connectors.kodi_jsonrpc.connection", dummy):
        with pytest.raises(ConnectorError, match="incomplete"):
            asyncio.run(fetch_kodi_snapshot({"host": "localhost"}))


def test_rpc_cannot_erase_multiple_sql_movie_versions(store):
    from home_media_catalog.connectors.base import Record

    sid = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    store.apply_snapshot(
        sid,
        [
            Record("movie:21:100", "movie", "Movie", identifiers={"tmdb": "21"}),
            Record("movie:21:200", "movie", "Movie", identifiers={"tmdb": "21"}),
        ],
    )
    assert store.has_multiple_kodi_movie_versions(sid) is True
    original = store.media(kind="movie")["total"]
    assert original == 1



def test_renamed_source_with_jsonrpc_keys_has_no_sql_version_guard(store):
    from home_media_catalog.connectors.base import Record

    source = store.save_source({"name": "Kodi standalone", "type": "kodi"})
    source_id = source["id"]
    snapshot = [Record("movie:21", "movie", "Film", identifiers={"tmdb": "21"})]
    store.apply_snapshot(source_id, snapshot)
    store.save_source({"name": "Kodi - NAS", "type": "kodi"}, source_id=source_id)
    store.apply_snapshot(source_id, snapshot)
    assert store.has_multiple_kodi_movie_versions(source_id) is False
    assert store.source(source_id)["item_count"] == 1


def test_server_sync_preserves_merged_movie_versions_by_preferring_mariadb(store):
    from home_media_catalog.connectors.base import Record
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source(
        {"name": "Kodi - NAS", "type": "kodi",
         "config": {"host": "nas", "username": "reader", "database": "MyVideos151"}}
    )["id"]
    store.save_player({"name": "Salon", "source_id": source_id, "host": "kodi.local"})
    versions = [
        Record("movie:21:100", "movie", "Film", identifiers={"tmdb": "21"}),
        Record("movie:21:200", "movie", "Film", identifiers={"tmdb": "21"}),
    ]
    store.apply_snapshot(source_id, versions)

    class FakeMariaDB:
        def __init__(self, config, progress=None):
            assert config["host"] == "nas"
            self.database = "MyVideos151"
            self.snapshot_report = {}

        def fetch(self):
            return versions

    manager = SyncManager(store, settings=None)
    job = store.new_job(source_id)
    with (
        patch("home_media_catalog.sync.KodiConnector", FakeMariaDB),
        patch("home_media_catalog.sync.connection", side_effect=AssertionError("Must skip JSON-RPC")),
        patch(
            "home_media_catalog.sync.fetch_kodi_snapshot",
            side_effect=AssertionError("Must not flatten editions"),
        ),
    ):
        asyncio.run(manager._run(job, store.source(source_id, private=True), None))

    completed = store.job(job["id"])
    assert completed["status"] == "completed", completed["error"]
    assert completed["result"]["import_method"] == "mariadb"
    assert completed["result"]["edition_protection"] == "mariadb"
    assert store.has_multiple_kodi_movie_versions(source_id) is True
    assert store.source(source_id)["item_count"] == 2


def test_merged_versions_without_mariadb_are_not_overwritten(store):
    from home_media_catalog.connectors.base import Record
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source({"name": "Kodi - NAS", "type": "kodi"})["id"]
    store.apply_snapshot(
        source_id,
        [
            Record("movie:21:100", "movie", "Film", identifiers={"tmdb": "21"}),
            Record("movie:21:200", "movie", "Film", identifiers={"tmdb": "21"}),
        ],
    )
    manager = SyncManager(store, settings=None)
    job = store.new_job(source_id)
    asyncio.run(manager._run(job, store.source(source_id, private=True), None))
    failed = store.job(job["id"])
    assert failed["status"] == "failed"
    assert "Configurez le repli MariaDB" in failed["error"]
    assert store.has_multiple_kodi_movie_versions(source_id) is True
    assert store.source(source_id)["item_count"] == 2
