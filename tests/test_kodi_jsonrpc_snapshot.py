"""Kodi local SQLite libraries can be imported without MariaDB."""

import asyncio
from unittest.mock import patch

import pytest

from kodi_library.connectors.base import ConnectorError
from kodi_library.connectors.kodi_jsonrpc import fetch_kodi_snapshot, normalize_jsonrpc


def test_record_normalizer_keeps_identity_and_parent_links():
    row = {
        "episodeid": 44, "tvshowid": 10, "title": "Pilot",
        "season": 1, "episode": 1, "playcount": 1,
        "uniqueid": {"tvdb": "9876"}, "runtime": 600,
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
        label = {"GetMovies": ("movies", "movieid"), "GetTVShows": ("tvshows", "tvshowid"),
                 "GetEpisodes": ("episodes", "episodeid")}
        key, id_field = label[method.split(".")[-1]]
        if key == "movies":
            if params["limits"]["start"] == 0:
                return {"limits": {"total": 201}, key: [
                    {id_field: i, "title": str(i)} for i in range(200)
                ]}
            return {"limits": {"total": 201}, key: [{id_field: 200, "title": "200"}]}
        if key == "episodes":
            return {"limits": {"total": 0}, key: []}
        return {"limits": {"total": 1}, key: [{id_field: 11, "title": "Show"}]}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy(_config, **_kwargs):
        yield rpc

    with patch("kodi_library.connectors.kodi_jsonrpc.connection", dummy):
        records = asyncio.run(fetch_kodi_snapshot({"host": "localhost"}))
    assert len(records) == 202
    assert calls[:2] == [
        ("VideoLibrary.GetMovies", 0), ("VideoLibrary.GetMovies", 200)
    ]


def test_incomplete_page_never_returns_a_partial_snapshot():
    async def rpc(_method, _params):
        return {"limits": {"total": 1}, "movies": []}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy(_config, **_kwargs):
        yield rpc

    with patch("kodi_library.connectors.kodi_jsonrpc.connection", dummy):
        with pytest.raises(ConnectorError, match="incomplete"):
            asyncio.run(fetch_kodi_snapshot({"host": "localhost"}))
