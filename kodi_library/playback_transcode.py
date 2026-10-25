"""Progressive browser-compatible playback for MKV and unsupported containers.

The remote SMB/NFS/FTP/SFTP file is read once sequentially; FFmpeg decodes
and encodes a fragmented MP4 directly into the HTTP response. No complete
movie is staged in RAM or on the NAS. Limit parallel transcodes for NAS CPUs.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from collections.abc import Iterator
from pathlib import PurePosixPath

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .playback_browser import _credentials, _sync_stream, safe_path

_LOG = logging.getLogger(__name__)
_LIMIT = threading.BoundedSemaphore(value=2)
_SUPPORTED = {".mkv", ".avi", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg"}


def _mp4_fragments(source: Iterator[bytes]) -> Iterator[bytes]:
    """Stream complete AV-compatible fragments without accumulating a movie."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-fflags", "+genpts",
        "-probesize", "6000000", "-analyzeduration", "3000000",
        "-i", "pipe:0",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn",
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
        "-crf", "28", "-pix_fmt", "yuv420p",
        "-vf", "scale=w='min(1280,iw)':h=-2",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-frag_duration", "1000000",
        "-f", "mp4", "pipe:1",
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0,
    )

    def write_source():
        try:
            assert process.stdin is not None
            for part in source:
                if not part or process.poll() is not None:
                    break
                process.stdin.write(part)
        except (BrokenPipeError, OSError):
            pass
        finally:
            closer = getattr(source, "close", None)
            if closer:
                closer()
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass

    feeder = threading.Thread(target=write_source, daemon=True)
    feeder.start()
    try:
        assert process.stdout is not None
        while True:
            piece = process.stdout.read(64 * 1024)
            if not piece:
                break
            yield piece
    finally:
        if process.poll() is None:
            process.terminate()
        if process.stdout:
            process.stdout.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


async def browser_transcode(location: dict, path: str, store, encryption, mounts: dict):
    relative = safe_path(path)
    extension = PurePosixPath(relative).suffix.lower()
    if location["kind"] not in {"smb", "nfs", "ftp", "sftp"}:
        raise HTTPException(501, "Browser transcoding is not available for this source.")
    if extension not in _SUPPORTED:
        raise HTTPException(415, "This container does not require browser transcoding.")

    # Keep any credential access on the server, never send network secrets to
    # the HTML5 player. _sync_stream opens a sequential file reader on demand.
    secret = _credentials(store, encryption, location["id"])
    try:
        _, factory = await asyncio.wait_for(
            asyncio.to_thread(_sync_stream, location, relative, secret, mounts, 0, None),
            timeout=18,
        )
    except TimeoutError as exc:
        raise HTTPException(504, "Timed out opening the remote media.") from exc
    except Exception as exc:
        _LOG.warning("Unable to open browser source %s: %s", location["kind"], exc)
        raise HTTPException(502, "Unable to open the remote media for playback.") from exc

    acquired = await asyncio.to_thread(_LIMIT.acquire, True, 2)
    if not acquired:
        raise HTTPException(503, "The server is already transcoding two remote videos.")

    def stream():
        try:
            yield from _mp4_fragments(iter(factory()))
        except (OSError, subprocess.SubprocessError) as exc:
            _LOG.warning("Browser transcoding failed: %s", type(exc).__name__)
        finally:
            _LIMIT.release()

    return StreamingResponse(
        stream(), media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
