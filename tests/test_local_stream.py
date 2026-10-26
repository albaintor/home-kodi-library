"""Kodi local streaming API validation."""

import asyncio
import time

import pytest
from fastapi import HTTPException

from home_media_catalog import local_stream


class Request:
    headers = {}
    method = "GET"


def test_missing_ticket_rejected():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(local_stream.stream_local_video("player", "missing", "video.mp4", Request()))
    assert exc.value.status_code == 403


def test_expired_ticket_rejected():
    local_stream._TICKETS["expired-test"] = {
        "player_id": "player",
        "filename": "video.mp4",
        "file": "smb://localhost/video.mp4",
        "expires": time.monotonic() - 1,
    }
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                local_stream.stream_local_video("player", "expired-test", "video.mp4", Request())
            )
        assert exc.value.status_code == 403
    finally:
        local_stream._TICKETS.pop("expired-test", None)


def test_catalogue_path_is_used_without_extra_kodi_jsonrpc():
    from types import SimpleNamespace

    from home_media_catalog.models import PlayInput

    edition = {
        "id": "edition-1",
        "source_id": "source-1",
        "source_type": "kodi",
        "metadata": {
            "kodi_id": 123,
            "file": "smb://nas/Film 4K.mkv",
            "is_default": True,
        },
        "identifiers": {},
    }
    detail = {"kind": "movie", "editions": [edition]}
    fake_store = SimpleNamespace(detail=lambda media_id: detail)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=fake_store)))
    player = {"source_id": "source-1", "config": {}}
    result = asyncio.run(
        local_stream._resolve_video(
            request, player, PlayInput(media_id="movie-1", edition_id="edition-1")
        )
    )
    assert result[0] == "smb://nas/Film 4K.mkv"
    assert result[1]["id"] == "edition-1"


def test_stream_range_header_accepts_suffix_but_not_multi_ranges():

    assert local_stream._RANGE.fullmatch("bytes=0-1")
    assert local_stream._RANGE.fullmatch("bytes=100-")
    assert local_stream._RANGE.fullmatch("bytes=-8192")
    assert not local_stream._RANGE.fullmatch("bytes=0-1,5-6")
    assert not local_stream._RANGE.fullmatch("bytes=0-" + "x")


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (206, {"content-range": "bytes 0-0/25769803776", "content-length": "1"}, 25769803776),
        (200, {"content-length": "99887766"}, 99887766),
    ],
)
def test_kodi_file_size_uses_full_file_length(monkeypatch, status, headers, expected):
    from types import SimpleNamespace

    class Response:
        def __init__(self):
            self.status_code = status
            self.headers = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, method, url, headers):
            assert method == "GET"
            assert headers["Range"] == "bytes=0-0"
            return Response()

    async def resolve(*_):
        return "smb://nas/movie.mkv", {"id": "edition-1"}

    monkeypatch.setattr(local_stream, "_lookup", lambda *_: {"config": {}})
    monkeypatch.setattr(local_stream, "_resolve_video", resolve)
    monkeypatch.setattr(local_stream, "endpoint", lambda _: "http://kodi:8080/jsonrpc")
    monkeypatch.setattr(local_stream.httpx, "AsyncClient", Client)
    answer = asyncio.run(
        local_stream.kodi_local_file_size("player", "movie-1", "edition-1", SimpleNamespace(), None)
    )
    assert answer == {"edition_id": "edition-1", "bytes": expected}


def test_file_size_never_mistakes_single_range_byte_for_movie_size(monkeypatch):
    from types import SimpleNamespace

    class Response:
        status_code = 206
        headers = {"content-length": "1"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, *_a, **_kw):
            return Response()

    async def resolve(*_):
        return "smb://nas/movie.mkv", {"id": "edition-1"}

    monkeypatch.setattr(local_stream, "_lookup", lambda *_: {"config": {}})
    monkeypatch.setattr(local_stream, "_resolve_video", resolve)
    monkeypatch.setattr(local_stream, "endpoint", lambda _: "http://kodi:8080/jsonrpc")
    monkeypatch.setattr(local_stream.httpx, "AsyncClient", Client)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            local_stream.kodi_local_file_size(
                "player", "movie-1", "edition-1", SimpleNamespace(), None
            )
        )
    assert exc.value.status_code == 404
