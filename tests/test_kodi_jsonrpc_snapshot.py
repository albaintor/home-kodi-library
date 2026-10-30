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
        {
            "name": "Kodi - NAS",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader", "database": "MyVideos151"},
        }
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
        patch(
            "home_media_catalog.sync.connection", side_effect=AssertionError("Must skip JSON-RPC")
        ),
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


def test_sync_tries_each_kodi_player_and_prefers_last_success(store):
    from contextlib import asynccontextmanager

    from home_media_catalog.connectors.base import Record
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    store.save_player({"name": "A", "source_id": source_id, "host": "offline.local"})
    store.save_player({"name": "B", "source_id": source_id, "host": "online.local"})
    manager = SyncManager(store, settings=None)
    probes = []
    snapshots = []

    @asynccontextmanager
    async def fake_connection(config, *, timeout):
        probes.append((config["host"], timeout))
        async def rpc(method, params):
            assert method == "JSONRPC.Ping"
            if config["host"] == "offline.local":
                raise TimeoutError("offline")
            return "pong"
        yield rpc

    async def fake_snapshot(config, progress=None):
        snapshots.append(config["host"])
        return [Record("movie:5", "movie", "Available", identifiers={"tmdb": "5"})]

    with (
        patch("home_media_catalog.sync.connection", fake_connection),
        patch("home_media_catalog.sync.fetch_kodi_snapshot", fake_snapshot),
    ):
        for _ in range(2):
            job = store.new_job(source_id)
            asyncio.run(manager._run(job, store.source(source_id, private=True), None))
            completed = store.job(job["id"])
            assert completed["status"] == "completed", completed["error"]
            assert completed["result"]["import_method"] == "jsonrpc"

    assert probes == [
        ("offline.local", 3.0), ("online.local", 3.0),
        ("online.local", 3.0),
    ]
    assert snapshots == ["online.local", "online.local"]


def test_sync_uses_mariadb_when_all_jsonrpc_players_fail(store):
    from contextlib import asynccontextmanager

    from home_media_catalog.connectors.base import Record
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source({
        "name": "Kodi", "type": "kodi",
        "config": {"host": "nas", "username": "reader", "database": "MyVideos151"},
    })["id"]
    store.save_player({"name": "Kodi 1", "source_id": source_id, "host": "offline1"})
    store.save_player({"name": "Kodi 2", "source_id": source_id, "host": "offline2"})
    probes = []
    expected_records = [Record("movie:7", "movie", "SQL Film", identifiers={"tmdb": "7"})]

    @asynccontextmanager
    async def fake_connection(config, *, timeout):
        probes.append((config["host"], timeout))
        async def rpc(method, params):
            raise TimeoutError("Kodi unreachable")
        yield rpc

    class FakeMariaDB:
        def __init__(self, config, progress=None):
            self.database = "MyVideos151"
            self.snapshot_report = {}
        def fetch(self):
            return expected_records

    manager = SyncManager(store, settings=None)
    job = store.new_job(source_id)
    with (
        patch("home_media_catalog.sync.connection", fake_connection),
        patch("home_media_catalog.sync.KodiConnector", FakeMariaDB),
    ):
        asyncio.run(manager._run(job, store.source(source_id, private=True), None))

    completed = store.job(job["id"])
    assert completed["status"] == "completed", completed["error"]
    assert completed["result"]["import_method"] == "mariadb"
    assert probes == [("offline1", 3.0), ("offline2", 3.0)]



@pytest.mark.parametrize("mariadb_only", [False, True])
def test_mariadb_only_catalogue_refresh_without_kodi_instance(store, mariadb_only):
    """An iOS pull can update the SQL snapshot without any configured Kodi player."""
    from home_media_catalog.connectors.base import Record
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source(
        {
            "name": "NAS SQL library",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader", "database": "MyVideos151"},
        }
    )["id"]
    assert not [p for p in store.players() if p["source_id"] == source_id]

    class FakeMariaDB:
        def __init__(self, config, progress=None):
            assert config["host"] == "nas"
            self.database = "MyVideos151"
            self.snapshot_report = {}

        def fetch(self):
            return [Record("movie:12", "movie", "SQL movie", identifiers={"tmdb": "12"})]

    manager = SyncManager(store, settings=None)

    async def refresh():
        job = await manager.submit(source_id, mariadb_only=mariadb_only)
        await asyncio.gather(*manager.tasks)
        return store.job(job["id"])

    with (
        patch("home_media_catalog.sync.KodiConnector", FakeMariaDB),
        patch("home_media_catalog.sync.connection",
              side_effect=AssertionError("No Kodi connection should be attempted")),
        patch("home_media_catalog.sync.fetch_kodi_snapshot",
              side_effect=AssertionError("No JSON-RPC snapshot should be fetched")),
    ):
        result = asyncio.run(refresh())

    assert result["status"] == "completed", result["error"]
    assert result["result"]["import_method"] == "mariadb"
    assert store.source(source_id)["item_count"] == 1


def test_failed_sql_only_refresh_keeps_existing_catalogue(store):
    from home_media_catalog.connectors.base import ConnectorError, Record
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source({
        "name": "NAS SQL library",
        "type": "kodi",
        "config": {"host": "nas", "username": "reader"},
    })["id"]
    store.apply_snapshot(source_id, [
        Record("movie:12", "movie", "Existing film", identifiers={"tmdb": "12"})
    ])

    class FailingMariaDB:
        def __init__(self, config, progress=None):
            self.database = None

        def fetch(self):
            raise ConnectorError("MariaDB unavailable")

    manager = SyncManager(store, settings=None)

    async def refresh():
        job = await manager.submit(source_id, mariadb_only=True)
        await asyncio.gather(*manager.tasks)
        return store.job(job["id"])

    with patch("home_media_catalog.sync.KodiConnector", FailingMariaDB):
        result = asyncio.run(refresh())

    assert result["status"] == "failed"
    assert "MariaDB unavailable" in result["error"]
    assert store.source(source_id)["item_count"] == 1


def test_mariadb_only_refresh_requires_sql_connection(store):
    from home_media_catalog.sync import SyncManager

    source_id = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    manager = SyncManager(store, settings=None)

    async def refresh():
        await manager.submit(source_id, mariadb_only=True)

    with pytest.raises(ValueError, match="MariaDB host and username"):
        asyncio.run(refresh())
