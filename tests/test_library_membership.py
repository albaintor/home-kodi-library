"""Kodi video-library membership is distinct from residual MyVideos rows."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from kodi_library.connectors.base import ConnectorError, Record
from kodi_library.connectors.kodi import KodiConnector
from kodi_library.players import library_movie_ids
from kodi_library.settings import Settings
from kodi_library.sync import SyncManager, only_movies_in_library


def test_jsonrpc_movie_library_pagination_reads_every_page():
    calls = []

    @asynccontextmanager
    async def fake_connection(config):
        async def rpc(method, params):
            assert method == "VideoLibrary.GetMovies"
            start = params["limits"]["start"]
            calls.append(start)
            ids = ([1, 2], [3, 4], [5])[start // 2]
            return {
                "limits": {"start": start, "end": start + len(ids), "total": 5},
                "movies": [{"movieid": number} for number in ids],
            }

        yield rpc

    with patch("kodi_library.players.connection", fake_connection):
        found = asyncio.run(library_movie_ids({"host": "kodi"}, page_size=2))
    assert found == {1, 2, 3, 4, 5}
    assert calls == [0, 2, 4]


@pytest.mark.parametrize(
    "pages",
    [
        [
            {"limits": {"total": 4}, "movies": [{"movieid": 1}, {"movieid": 2}]},
            {"limits": {"total": 4}, "movies": []},
        ],
        [
            {"limits": {"total": 3}, "movies": [{"movieid": 1}, {"movieid": 2}]},
            {"limits": {"total": 3}, "movies": [{"movieid": 2}]},
        ],
        [{"movies": [{"movieid": 1}]}],
    ],
)
def test_jsonrpc_failure_or_partial_response_cannot_delete_movies(pages):
    @asynccontextmanager
    async def fake_connection(config):
        async def rpc(method, params):
            return pages.pop(0)

        yield rpc

    with patch("kodi_library.players.connection", fake_connection):
        with pytest.raises(ConnectorError):
            asyncio.run(library_movie_ids({"host": "kodi"}, page_size=2))


def test_empty_kodi_library_is_valid_but_wont_wipe_nonempty_sql_snapshot():
    @asynccontextmanager
    async def fake_connection(config):
        async def rpc(method, params):
            return {"limits": {"start": 0, "end": 0, "total": 0}}

        yield rpc

    with patch("kodi_library.players.connection", fake_connection):
        assert asyncio.run(library_movie_ids({"host": "kodi"})) == set()
    with pytest.raises(ConnectorError, match="videothèque de films vide"):
        only_movies_in_library(
            [Record("movie:1:5", "movie", "Film", metadata={"kodi_id": 1})], set()
        )


def test_kodi_library_filter_preserves_versions_and_nonmovie_media():
    rows = [
        Record("movie:1:10", "movie", "Absent", metadata={"kodi_id": 1}),
        Record("movie:2:12", "movie", "Présent", metadata={"kodi_id": 2}),
        Record("movie:2:13", "movie", "Présent version longue", metadata={"kodi_id": 2}),
        Record("tvshow:3", "tvshow", "Série", metadata={"kodi_id": 3}),
    ]
    remaining, status = only_movies_in_library(rows, {2, 6})
    assert [r.external_key for r in remaining] == ["movie:2:12", "movie:2:13", "tvshow:3"]
    assert status["library_excluded_editions"] == 1
    assert status["library_extra_movie_ids"] == 1


def test_sync_removes_sql_movie_not_in_kodi_library(store):
    source_id = store.save_source(
        {
            "name": "MariaDB",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader"},
        }
    )["id"]
    store.save_player({"name": "Salon", "source_id": source_id, "host": "kodi", "port": 8080})
    movies = [
        Record("movie:7:8", "movie", "Retiré de la bibliothèque", metadata={"kodi_id": 7}),
        Record("movie:9:10", "movie", "Toujours présent", metadata={"kodi_id": 9}),
    ]
    store.apply_snapshot(source_id, movies)
    manager = SyncManager(store, Settings(scheduler_enabled=False))

    def fake_fetch(connector):
        connector.database = "MyVideos131"
        connector.snapshot_report = {}
        return movies

    async def fake_library(config):
        return {9}

    async def run():
        started = await manager.submit(source_id)
        await asyncio.gather(*manager.tasks)
        return store.job(started["id"])

    with (
        patch.object(KodiConnector, "fetch", fake_fetch),
        patch("kodi_library.sync.library_movie_ids", fake_library),
    ):
        job = asyncio.run(run())
    assert job["status"] == "completed"
    assert job["result"]["library_verified"] is True
    assert job["result"]["library_excluded_editions"] == 1
    assert job["result"]["removed_editions"] == 1
    assert store.media(kind="movie")["total"] == 1
    assert store.media(kind="movie")["items"][0]["title"] == "Toujours présent"


def test_failed_library_check_preserves_existing_catalogue(store):
    source_id = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    store.save_player(
        {
            "name": "Salon",
            "source_id": source_id,
            "host": "kodi",
            "port": 8080,
        }
    )
    movie = Record("movie:7:8", "movie", "Retained", metadata={"kodi_id": 7})
    store.apply_snapshot(source_id, [movie])
    manager = SyncManager(store, Settings(scheduler_enabled=False))

    def fake_fetch(connector):
        connector.database = "MyVideos131"
        connector.snapshot_report = {}
        return [movie]

    async def unavailable(config):
        raise ConnectorError("Kodi is offline")

    async def run():
        started = await manager.submit(source_id)
        await asyncio.gather(*manager.tasks)
        return store.job(started["id"])

    with (
        patch.object(KodiConnector, "fetch", fake_fetch),
        patch("kodi_library.sync.library_movie_ids", unavailable),
    ):
        job = asyncio.run(run())
    assert job["status"] == "failed"
    assert store.media(kind="movie")["total"] == 1


def test_without_configured_player_sql_sync_is_not_reported_as_verified(store):
    source_id = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    manager = SyncManager(store, Settings(scheduler_enabled=False))
    movie = Record("movie:7:8", "movie", "Movie", metadata={"kodi_id": 7})

    def fake_fetch(connector):
        connector.database = "MyVideos131"
        connector.snapshot_report = {}
        return [movie]

    async def run():
        started = await manager.submit(source_id)
        await asyncio.gather(*manager.tasks)
        return store.job(started["id"])

    with patch.object(KodiConnector, "fetch", fake_fetch):
        job = asyncio.run(run())
    assert job["status"] == "completed"
    assert job["result"]["library_verified"] is False
    assert "library_warning" in job["result"]
