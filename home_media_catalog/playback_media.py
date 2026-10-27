"""Track-aware browser playback: probing, subtitle extraction and seekable streams.

HTML5 video can only play one audio track, cannot render embedded Matroska
subtitles and does not expose chapters. The web player therefore asks the
server for the media structure (FFprobe), extracts text subtitles once to
WebVTT, and restarts a fragmented MP4 stream at any position with the chosen
audio track (and burned-in bitmap subtitles when requested).

FFmpeg always reads the original file through a loopback HTTP Range bridge:
the movie is never staged, and Kodi or network-share credentials never
appear on a command line nor reach the browser.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import logging
import math
import re
import secrets
import shutil
import subprocess
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

LOG = logging.getLogger(__name__)

TEXT_SUBTITLES = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "microdvd"}
BITMAP_SUBTITLES = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
_COPYABLE_VIDEO = {"h264": {"yuv420p", "yuvj420p"}, "hevc": {"yuv420p", "yuv420p10le"}}
_STREAMS = asyncio.Semaphore(3)
_EXTRACTIONS = asyncio.Semaphore(2)
_EXTRACTION_LOCKS: dict[str, asyncio.Lock] = {}
_PROBES: OrderedDict[str, dict] = OrderedDict()
_PROBE_LIMIT = 64
_FILTERS: set[str] | None = None


@dataclass(frozen=True)
class RangeSource:
    """A remote file the bridge can read in bounded byte ranges."""

    key: str
    length: int
    open: Callable[[int, int], Iterator[bytes]]


class LoopbackBridge:
    """Serve one random, seekable URL on 127.0.0.1 for FFmpeg/FFprobe."""

    def __init__(self, source: RangeSource, name: str = "media"):
        self.source = source
        self.route = "/" + secrets.token_urlsafe(24) + "/" + re.sub(r"[^\w.-]", "_", name)
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}{self.route}"

    def __enter__(self) -> LoopbackBridge:
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                bridge._reply(self, send_body=False)

            def do_GET(self):
                bridge._reply(self, send_body=True)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.timeout = 0.5
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None

    def _reply(self, handler: http.server.BaseHTTPRequestHandler, *, send_body: bool):
        length = self.source.length
        if handler.path != self.route:
            handler.send_error(404)
            return
        start, end = 0, length - 1
        requested = handler.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            if not match or not (match.group(1) or match.group(2)):
                handler.send_error(416)
                return
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(length - 1, int(match.group(2)))
            else:
                suffix = int(match.group(2))
                if suffix == 0:
                    handler.send_error(416)
                    return
                start = max(0, length - suffix)
            if start >= length or start > end:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{length}")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
        handler.send_response(206 if requested else 200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(end - start + 1))
        if requested:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{length}")
        handler.send_header("Connection", "close")
        handler.end_headers()
        if not send_body:
            return
        left = end - start + 1
        try:
            for chunk in self.source.open(start, end):
                # Never send more than advertised, even if the upstream
                # ignored the requested range end.
                piece = chunk[:left]
                handler.wfile.write(piece)
                left -= len(piece)
                if left <= 0:
                    break
        except (BrokenPipeError, ConnectionResetError):
            # FFmpeg closes the previous range socket whenever it seeks.
            pass
        except Exception as exc:
            LOG.debug("Bridge range aborted (%s)", type(exc).__name__)
        finally:
            handler.close_connection = True


def network_source(location: dict, relative: str, password: str, mounts: dict) -> RangeSource:
    from .playback_browser import _sync_stream

    length, _ = _sync_stream(location, relative, password, mounts, 0, 0)
    if length <= 0:
        raise HTTPException(404, "Empty media file.")

    def opener(start: int, end: int) -> Iterator[bytes]:
        _, supplier = _sync_stream(location, relative, password, mounts, start, end)
        return supplier()

    return RangeSource(f"location:{location.get('id')}:{relative}", length, opener)


def kodi_source(vfs_url: str, auth: tuple[str, str] | None, key: str) -> RangeSource:
    """Range reader for Kodi's /vfs HTTP endpoint (credentials stay server-side)."""
    timeout = httpx.Timeout(connect=8, read=40, write=8, pool=8)
    headers = {"Range": "bytes=0-0", "Accept-Encoding": "identity"}
    try:
        with httpx.Client(timeout=timeout, auth=auth, trust_env=False) as client:
            with client.stream("GET", vfs_url, headers=headers) as upstream:
                if upstream.status_code != 206:
                    raise HTTPException(
                        502, "Kodi VFS does not support seeking (HTTP byte ranges)."
                    )
                total = upstream.headers.get("content-range", "").rsplit("/", 1)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cannot reach Kodi's VFS HTTP endpoint.") from exc
    if len(total) != 2 or not total[1].isdigit() or int(total[1]) <= 0:
        raise HTTPException(502, "Kodi VFS did not expose the total file size.")

    def opener(start: int, end: int) -> Iterator[bytes]:
        with httpx.Client(timeout=timeout, auth=auth, trust_env=False) as client:
            with client.stream(
                "GET",
                vfs_url,
                headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
            ) as upstream:
                if upstream.status_code not in (200, 206) or (
                    upstream.status_code == 200 and start > 0
                ):
                    raise OSError(f"Kodi VFS returned HTTP {upstream.status_code}")
                yield from upstream.iter_bytes(512 * 1024)

    return RangeSource(key, int(total[1]), opener)


def _language(tags: dict) -> str | None:
    value = (tags.get("language") or "").strip().lower()
    return value if value and value != "und" else None


def _hdr(stream: dict) -> str | None:
    for side in stream.get("side_data_list") or []:
        if "DOVI" in (side.get("side_data_type") or ""):
            return "Dolby Vision"
    transfer = stream.get("color_transfer")
    if transfer == "smpte2084":
        return "HDR10"
    if transfer == "arib-std-b67":
        return "HLG"
    return None


def _frame_rate(value: str | None) -> float | None:
    try:
        numerator, denominator = (value or "").split("/")
        rate = int(numerator) / int(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if 0 < rate < 1000 else None


def normalize_probe(data: dict) -> dict:
    """Reduce FFprobe JSON to what the player UI and stream builder need."""
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    video, audio, subtitles = None, [], []
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        base = {
            "index": stream.get("index"),
            "codec": stream.get("codec_name"),
            "language": _language(tags),
            "title": (tags.get("title") or "").strip() or None,
            "default": bool(disposition.get("default")),
        }
        if kind == "video" and video is None and not disposition.get("attached_pic"):
            video = {
                "index": stream.get("index"),
                "codec": stream.get("codec_name"),
                "profile": stream.get("profile"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "pix_fmt": stream.get("pix_fmt"),
                "frame_rate": _frame_rate(stream.get("avg_frame_rate")),
                "hdr": _hdr(stream),
            }
        elif kind == "audio":
            audio.append(
                {
                    **base,
                    "order": len(audio),
                    "channels": stream.get("channels"),
                    "layout": stream.get("channel_layout"),
                    "profile": stream.get("profile"),
                }
            )
        elif kind == "subtitle":
            codec = stream.get("codec_name") or ""
            subtitles.append(
                {
                    **base,
                    "order": len(subtitles),
                    "forced": bool(disposition.get("forced")),
                    "hearing_impaired": bool(disposition.get("hearing_impaired")),
                    "kind": "text"
                    if codec in TEXT_SUBTITLES
                    else "bitmap"
                    if codec in BITMAP_SUBTITLES
                    else "unsupported",
                }
            )
    chapters = []
    for chapter in data.get("chapters") or []:
        try:
            start = float(chapter.get("start_time"))
            end = float(chapter.get("end_time"))
        except (TypeError, ValueError):
            continue
        title = ((chapter.get("tags") or {}).get("title") or "").strip()
        chapters.append({"start": round(start, 3), "end": round(end, 3), "title": title or None})
    if duration <= 0 and chapters:
        duration = chapters[-1]["end"]
    return {
        "duration": round(duration, 3) if math.isfinite(duration) else 0,
        "container": fmt.get("format_name"),
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate") or "").isdigit() else None,
        "size": int(fmt["size"]) if str(fmt.get("size") or "").isdigit() else None,
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
        "chapters": chapters,
    }


def _run_probe(source: RangeSource) -> dict:
    with LoopbackBridge(source) as bridge:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-rw_timeout",
                    "15000000",
                    "-probesize",
                    "20000000",
                    "-analyzeduration",
                    "10000000",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    "-show_chapters",
                    bridge.url,
                ],
                capture_output=True,
                timeout=45,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HTTPException(501, "FFprobe is not installed on the server.") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Timed out analysing the media file.") from exc
    if result.returncode != 0:
        raise HTTPException(422, "FFprobe could not read this media file.")
    try:
        return normalize_probe(json.loads(result.stdout))
    except ValueError as exc:
        raise HTTPException(502, "FFprobe returned invalid JSON.") from exc


async def probe(source: RangeSource) -> dict:
    cache_key = f"{source.key}:{source.length}"
    if cache_key in _PROBES:
        _PROBES.move_to_end(cache_key)
        return _PROBES[cache_key]
    info = await asyncio.to_thread(_run_probe, source)
    _PROBES[cache_key] = info
    while len(_PROBES) > _PROBE_LIMIT:
        _PROBES.popitem(last=False)
    return info


def _subtitle_dir(data_dir: Path, source: RangeSource) -> Path:
    digest = hashlib.sha256(f"{source.key}:{source.length}".encode()).hexdigest()[:32]
    return Path(data_dir) / "subtitles" / digest


def _extract_text_subtitles(source: RangeSource, indexes: list[int], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index in indexes:
        outputs += ["-map", f"0:{index}", "-c:s", "webvtt", "-f", "webvtt"]
        outputs.append(str(folder / f"{index}.vtt.part"))
    with LoopbackBridge(source) as bridge:
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-rw_timeout",
                    "30000000",
                    "-i",
                    bridge.url,
                    *outputs,
                ],
                capture_output=True,
                timeout=30 * 60,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HTTPException(501, "FFmpeg is not installed on the server.") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Timed out extracting subtitles.") from exc
    if result.returncode != 0:
        LOG.warning("Subtitle extraction failed: %s", result.stderr[-300:].decode(errors="replace"))
        raise HTTPException(422, "FFmpeg could not extract these subtitles.")
    for index in indexes:
        (folder / f"{index}.vtt.part").replace(folder / f"{index}.vtt")


async def subtitle_vtt(source: RangeSource, info: dict, stream_index: int, data_dir: Path) -> Path:
    """Extract every text subtitle in one sequential read, then serve from cache."""
    texts = [s["index"] for s in info["subtitles"] if s["kind"] == "text"]
    if stream_index not in texts:
        raise HTTPException(404, "No text subtitle stream with this index.")
    folder = _subtitle_dir(data_dir, source)
    target = folder / f"{stream_index}.vtt"
    if target.is_file():
        return target
    lock = _EXTRACTION_LOCKS.setdefault(str(folder), asyncio.Lock())
    async with lock:
        if not target.is_file():
            async with _EXTRACTIONS:
                missing = [i for i in texts if not (folder / f"{i}.vtt").is_file()]
                await asyncio.to_thread(_extract_text_subtitles, source, missing, folder)
    return target


def _available_filters() -> set[str]:
    global _FILTERS
    if _FILTERS is None:
        try:
            output = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, timeout=10, check=False
            ).stdout.decode(errors="replace")
            _FILTERS = {line.split()[1] for line in output.splitlines() if len(line.split()) > 2}
        except (OSError, subprocess.SubprocessError):
            _FILTERS = set()
    return _FILTERS


def stream_command(
    url: str,
    info: dict,
    *,
    start: float = 0,
    audio: int | None = None,
    subtitle: int | None = None,
    video_copy: set[str] | frozenset[str] = frozenset(),
    max_height: int = 1080,
) -> tuple[list[str], str]:
    """Build the FFmpeg command and return it with the video mode used.

    ``audio`` and ``subtitle`` are absolute stream indexes from the probe.
    Only bitmap subtitles are burned in: text subtitles are delivered as
    WebVTT so they remain selectable, resizable and cheap.
    """
    video = info.get("video")
    if not video:
        raise HTTPException(422, "This file has no video stream.")
    audio_ids = {a["index"] for a in info["audio"]}
    if audio is not None and audio not in audio_ids:
        raise HTTPException(422, "Unknown audio track.")
    if audio is None and info["audio"]:
        audio = next((a["index"] for a in info["audio"] if a["default"]), info["audio"][0]["index"])
    burn = None
    if subtitle is not None:
        burn = next((s for s in info["subtitles"] if s["index"] == subtitle), None)
        if not burn or burn["kind"] != "bitmap":
            raise HTTPException(422, "Only bitmap subtitles can be burned into the video.")

    codec, pix_fmt = video.get("codec"), video.get("pix_fmt")
    copy = (
        burn is None
        and codec in video_copy
        and pix_fmt in _COPYABLE_VIDEO.get(codec, set())
        and video.get("hdr") != "Dolby Vision"
    )
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    command += ["-rw_timeout", "30000000", "-fflags", "+genpts"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", url]
    if copy:
        command += ["-map", f"0:{video['index']}", "-c:v", "copy"]
        if codec == "hevc":
            command += ["-tag:v", "hvc1"]
        mode = "copy"
    else:
        height = max(240, min(int(max_height), 2160))
        chain = []
        filters = _available_filters()
        if video.get("hdr") and {"zscale", "tonemap"} <= filters:
            chain.append(
                "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv"
            )
        chain += [f"scale=w=-2:h='min({height},ih)'", "format=yuv420p"]
        if burn:
            graph = (
                f"[0:{burn['index']}][0:{video['index']}]scale2ref[sub][base];"
                f"[base][sub]overlay=eof_action=pass,{','.join(chain)}[out]"
            )
            command += ["-filter_complex", graph, "-map", "[out]"]
        else:
            command += ["-map", f"0:{video['index']}", "-vf", ",".join(chain)]
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "0"]
        command += ["-g", "48", "-profile:v", "high"]
        mode = "transcode"
    if audio is not None:
        command += ["-map", f"0:{audio}", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    command += [
        "-sn",
        "-dn",
        "-map_chapters",
        "-1",
        "-map_metadata",
        "-1",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-frag_duration",
        "1000000",
        "-f",
        "mp4",
        "pipe:1",
    ]
    return command, mode


async def _pipe(
    source: RangeSource, command_for: Callable[[str], list[str]]
) -> AsyncIterator[bytes]:
    try:
        # A seek aborts the previous request; give its FFmpeg a moment to stop.
        await asyncio.wait_for(_STREAMS.acquire(), timeout=15)
    except TimeoutError:
        LOG.warning("Browser stream refused: too many concurrent conversions")
        return
    bridge = LoopbackBridge(source)
    process = None
    try:
        await asyncio.to_thread(bridge.__enter__)
        process = subprocess.Popen(
            command_for(bridge.url),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        assert process.stdout is not None
        while True:
            piece = await asyncio.to_thread(process.stdout.read, 256 * 1024)
            if not piece:
                break
            yield piece
    finally:
        # Runs on normal completion and when the browser seeks (the previous
        # request is aborted), so FFmpeg never keeps reading the NAS.
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, 3)
            except subprocess.TimeoutExpired:
                process.kill()
            if process.stdout:
                process.stdout.close()
        await asyncio.to_thread(bridge.close)
        _STREAMS.release()


async def media_stream(
    source: RangeSource,
    info: dict,
    *,
    start: float = 0,
    audio: int | None = None,
    subtitle: int | None = None,
    video_copy: set[str] | frozenset[str] = frozenset(),
    max_height: int = 1080,
) -> StreamingResponse:
    if not shutil.which("ffmpeg"):
        raise HTTPException(501, "FFmpeg is not installed on the server.")
    duration = info.get("duration") or 0
    start = max(0.0, min(float(start), max(0.0, duration - 1) if duration else float(start)))
    _, mode = stream_command(
        "",
        info,
        start=start,
        audio=audio,
        subtitle=subtitle,
        video_copy=video_copy,
        max_height=max_height,
    )

    def command_for(url: str) -> list[str]:
        return stream_command(
            url,
            info,
            start=start,
            audio=audio,
            subtitle=subtitle,
            video_copy=video_copy,
            max_height=max_height,
        )[0]

    return StreamingResponse(
        _pipe(source, command_for),
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Playback-Mode": mode,
            "X-Playback-Start": f"{start:.3f}",
        },
    )


def parse_video_copy(value: str) -> frozenset[str]:
    return frozenset(v for v in value.split(",") if v in _COPYABLE_VIDEO)
