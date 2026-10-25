"""Remote artwork extraction is bounded, cached, and protocol-independent."""

import io

import pytest
from fastapi import HTTPException
from PIL import Image

from kodi_library import playback_thumbnails as thumbs


def test_image_thumb_decodes_and_downscales():
    output = io.BytesIO()
    Image.new("RGB", (850, 620), (90, 140, 200)).save(output, format="PNG")
    jpeg = thumbs._jpeg_thumbnail(output.getvalue(), ".png")
    assert jpeg is not None
    with Image.open(io.BytesIO(jpeg)) as result:
        assert result.format == "JPEG"
        assert result.width <= 360
        assert result.height <= 256


def test_disallow_unsupported_formats(tmp_path):
    with pytest.raises(HTTPException) as error:
        thumbs._thumbnail_sync(
            {"id": "test", "kind": "nfs"}, "sample.txt", None, None, {}, tmp_path
        )
    assert error.value.status_code == 415


def test_thumb_reads_remote_bytes_once_and_reuses_disk_cache(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), (120, 40, 30)).save(buffer, format="PNG")
    image = buffer.getvalue()
    calls = []

    def fake_stream(location, path, password, mounts, start, end):
        calls.append((location["id"], path, start, end))
        return len(image), lambda: iter([image])

    monkeypatch.setattr(thumbs, "_sync_stream", fake_stream)
    monkeypatch.setattr(thumbs, "_credentials", lambda *_args: "")
    location = {"id": "shared-nfs", "kind": "nfs"}
    cache = tmp_path / "thumbnails"
    first = thumbs._thumbnail_sync(location, "images/family.png", None, None, {}, cache)
    second = thumbs._thumbnail_sync(location, "images/family.png", None, None, {}, cache)
    assert first == second
    assert len(calls) == 1
    assert cache.exists()


def test_large_stream_is_bounded():
    length = 32 * 1024
    def source():
        yield b"A" * length
        yield b"B" * length

    assert thumbs._read_limited(source, 1024) == b"A" * 1024
