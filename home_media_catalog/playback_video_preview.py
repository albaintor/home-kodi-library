"""Kodi-style thumbnail extraction over a temporary *local-only* HTTP Range bridge.

FFmpeg needs a seekable byte source to extract the frame at 1/3 of a movie,
even for a multi-gigabyte SMB/NFS/FTP/SFTP file. The bridge is bound to
127.0.0.1, serves only one random URL, and streams bounded byte ranges from
the existing read-only network connector. It never stages the whole movie.
"""

from __future__ import annotations

import logging
import math
import subprocess

from .playback_browser import _sync_stream
from .playback_media import LoopbackBridge, RangeSource

LOG = logging.getLogger(__name__)


def extract_video_frame(location: dict, path: str, password: str, mounts: dict) -> bytes | None:
    # Stat the file without reading its body. Opening a new range must not
    # reuse a previous reader's offset (FFmpeg seeks during demux probing).
    length, _ = _sync_stream(location, path, password, mounts, 0, 0)
    if length <= 0:
        return None

    def opener(start: int, end: int):
        return _sync_stream(location, path, password, mounts, start, end)[1]()

    source = RangeSource(f"location:{location.get('id')}:{path}", length, opener)
    bridge = LoopbackBridge(source, path.rsplit("/", 1)[-1])
    bridge.__enter__()
    url = bridge.url
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-hide_banner",
                "-loglevel",
                "error",
                "-rw_timeout",
                "6000000",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                url,
            ],
            capture_output=True,
            timeout=12,
            check=False,
        )
        try:
            duration = float(probe.stdout.decode("ascii").strip())
        except (ValueError, UnicodeError):
            return None
        if not math.isfinite(duration) or duration <= 0:
            return None
        # Kodi DVDFileInfo::ExtractThumbToTexture seeks to length / 3.
        for second in (duration / 3, duration / 5):
            run = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-rw_timeout",
                    "6000000",
                    "-ss",
                    str(second),
                    "-i",
                    url,
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=360:256:force_original_aspect_ratio=decrease",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-q:v",
                    "5",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=16,
                check=False,
            )
            if run.returncode == 0 and 0 < len(run.stdout) <= 512 * 1024:
                return run.stdout
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        LOG.debug("FFmpeg preview unavailable (%s)", type(exc).__name__)
    finally:
        bridge.close()
    return None
