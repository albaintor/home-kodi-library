"""Kodi local streaming API validation."""

import asyncio
import time

import pytest
from fastapi import HTTPException

from kodi_library import local_stream


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

    from kodi_library.models import PlayInput

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
    import re

    assert local_stream._RANGE.fullmatch("bytes=0-1")
    assert local_stream._RANGE.fullmatch("bytes=100-")
    assert local_stream._RANGE.fullmatch("bytes=-8192")
    assert not local_stream._RANGE.fullmatch("bytes=0-1,5-6")
    assert not local_stream._RANGE.fullmatch("bytes=0-" + "x")
