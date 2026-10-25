"""Kodi-style thumbnail extraction over a temporary *local-only* HTTP Range bridge.

FFmpeg needs a seekable byte source to extract the frame at 1/3 of a movie,
even for a multi-gigabyte SMB/NFS/FTP/SFTP file. The bridge is bound to
127.0.0.1, serves only one random URL, and streams bounded byte ranges from
the existing read-only network connector. It never stages the whole movie.
"""

from __future__ import annotations

import http.server
import logging
import math
import re
import secrets
import subprocess
import threading
from urllib.parse import quote

from .playback_browser import _sync_stream

LOG = logging.getLogger(__name__)


def extract_video_frame(location: dict, path: str, password: str, mounts: dict) -> bytes | None:
    # Stat the file without reading its body. Opening a new range must not
    # reuse a previous reader's offset (FFmpeg seeks during demux probing).
    length, _ = _sync_stream(location, path, password, mounts, 0, 0)
    if length <= 0:
        return None

    token = secrets.token_urlsafe(24)
    route = "/" + token + "/" + quote(path.rsplit("/", 1)[-1], safe="")
    source = (location, path, password, mounts)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.reply(send_body=False)

        def do_GET(self):
            self.reply(send_body=True)

        def reply(self, *, send_body: bool):
            if self.path != route:
                self.send_error(404)
                return
            start, end = 0, length - 1
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
                if not match or not (match.group(1) or match.group(2)):
                    self.send_error(416)
                    return
                if match.group(1):
                    start = int(match.group(1))
                    if match.group(2):
                        end = min(length - 1, int(match.group(2)))
                else:
                    suffix = int(match.group(2))
                    if suffix == 0:
                        self.send_error(416)
                        return
                    start = max(0, length - suffix)
                if start >= length or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{length}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            self.send_response(206 if requested else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if requested:
                self.send_header("Content-Range", f"bytes {start}-{end}/{length}")
            self.send_header("Connection", "close")
            self.end_headers()
            if not send_body:
                return
            try:
                _, supplier = _sync_stream(*source, start, end)
                for chunk in supplier():
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # FFmpeg usually closes a previous range socket when seeking.
                pass
            except Exception as exc:
                LOG.debug("Thumbnail range aborted (%s)", type(exc).__name__)
            finally:
                self.close_connection = True

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.timeout = 0.5
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}{route}"
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-rw_timeout", "6000000", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", url,
            ],
            capture_output=True, timeout=12, check=False,
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
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-rw_timeout", "6000000", "-ss", str(second), "-i", url,
                    "-map", "0:v:0", "-frames:v", "1",
                    "-vf", "scale=360:256:force_original_aspect_ratio=decrease",
                    "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
                ],
                capture_output=True, timeout=16, check=False,
            )
            if run.returncode == 0 and 0 < len(run.stdout) <= 512 * 1024:
                return run.stdout
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        LOG.debug("FFmpeg preview unavailable (%s)", type(exc).__name__)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return None
