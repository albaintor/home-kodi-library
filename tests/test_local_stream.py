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
