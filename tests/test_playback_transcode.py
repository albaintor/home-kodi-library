"""Browser MKV decoding is progressive and produces valid fragmented MP4."""

import shutil
import subprocess

import pytest

from kodi_library.playback_transcode import _mp4_fragments


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_transcode_mkv_bytes_to_progressive_mp4():
    mkv = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=12:duration=1",
            "-c:v",
            "mpeg4",
            "-f",
            "matroska",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=12,
    ).stdout

    source = iter([mkv[i : i + 2048] for i in range(0, len(mkv), 2048)])
    data = b"".join(_mp4_fragments(source))
    assert b"ftyp" in data[:32]
    assert b"moof" in data
    assert b"mdat" in data
    assert len(data) > 1000
