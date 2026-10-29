"""Short-lived streaming of a Kodi VFS file to a native iOS video player.

Kodi credentials and network-share credentials never leave the NAS. Streams
are byte ranges: a 24 GB movie is never downloaded into server memory.
"""

import asyncio
import logging
import mimetypes
import re
import secrets
import time
from collections import OrderedDict
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from .auth import authenticated
from .connectors.base import ConnectorError
from .models import PlayInput
from .playback_browser import safe_path, stream_location
from .playback_media import kodi_source, media_stream, network_source, parse_video_copy, probe, subtitle_vtt
from .players import connection, endpoint
from .utils import identifiers

router = APIRouter(prefix="/api/v1/players", tags=["Players"])
LOG = logging.getLogger(__name__)
# Ephemeral tickets: only the iOS client with the authenticated API token may
# request one. AVPlayer doesn't support our API's bearer header, so the GET
# uses a non-reusable-to-other-media, high-entropy capability ticket.
_TICKETS = OrderedDict()
_TICKET_TTL = 4 * 3600
_TICKET_LIMIT = 128
_RANGE = re.compile(r"^bytes=(?:\d+-\d*|-\d+)$")


def _lookup(request, player_id):
    player = request.app.state.store.player(player_id)
    if not player:
        raise HTTPException(404, "Unknown Kodi instance.")
    return player


def _registered_media_location(request: Request, kodi_path: str):
    """Match a server-owned SMB/NFS share using its IP or optional Kodi hostname alias.

    Only canonical paths imported from Kodi are eligible, and all resulting
    paths remain confined to the administrator-configured share root.
    """
    url = urlsplit(kodi_path)
    kind = url.scheme.lower()
    if kind not in {"smb", "nfs"} or not url.hostname or url.username or url.password:
        return None
    requested_host = url.hostname.lower().rstrip(".")
    try:
        media_path = safe_path(unquote(url.path).lstrip("/"))
    except HTTPException:
        return None
    if not media_path:
        return None
    for location in request.app.state.store.playback_locations():
        if location["kind"] != kind:
            continue
        known_hosts = {
            str(location.get("host") or "").lower().rstrip("."),
            str(location.get("hostname") or "").lower().rstrip("."),
        }
        if requested_host not in known_hosts:
            continue
        root = str(location.get("share") or "").strip("/")
        if not root or not media_path.startswith(root + "/"):
            continue
        relative = safe_path(media_path[len(root) + 1 :])
        if relative:
            return location, relative
    return None


async def _resolve_video(request, player, data):
    detail = await asyncio.to_thread(request.app.state.store.detail, data.media_id)
    if not detail or detail["kind"] not in ("movie", "episode"):
        raise HTTPException(404, "No playable Kodi movie or episode.")
    editions = [
        item
        for item in detail["editions"]
        if item["source_id"] == player["source_id"] and item["source_type"] == "kodi"
    ]
    if data.edition_id:
        editions = [item for item in editions if item["id"] == data.edition_id]
    if not editions:
        raise HTTPException(422, "The selected file is not in this Kodi source.")
    editions.sort(key=lambda item: not item["metadata"].get("is_default", True))
    edition = editions[0]
    kodi_id = edition["metadata"].get("kodi_id")
    if type(kodi_id) is not int or kodi_id < 0:
        raise HTTPException(422, "Invalid Kodi ID. Synchronize this library.")
    # Fast path: the catalogue already contains the canonical Kodi edition
    # path from its last import/scan. Only server-side Kodi editions linked to
    # this player are eligible; no client-supplied path is ever accepted.
    # Kodi VFS independently enforces its configured shared source directories.
    # Skipping another JSON-RPC lookup avoids an extra Kodi connection before
    # every remote SMB/NFS media open, which is noticeable for large files.
    known_path = edition["metadata"].get("file")
    if (
        isinstance(known_path, str)
        and known_path
        and len(known_path) <= 8192
        and all(ord(c) >= 32 for c in known_path)
    ):
        return known_path, edition

    # Backward compatibility for older imports without a persisted file path.
    key = "movieid" if detail["kind"] == "movie" else "episodeid"
    method = (
        "VideoLibrary.GetMovieDetails" if key == "movieid" else "VideoLibrary.GetEpisodeDetails"
    )
    result_key = "moviedetails" if key == "movieid" else "episodedetails"
    try:
        async with connection(player["config"]) as rpc:
            result = await rpc(method, {key: kodi_id, "properties": ["file", "uniqueid"]})
    except ConnectorError as exc:
        raise HTTPException(502, "Kodi is unavailable or rejected JSON-RPC.") from exc
    item = result.get(result_key, {})
    if not isinstance(item, dict) or not item:
        raise HTTPException(409, "Kodi no longer has this media ID. Synchronize the library.")
    remote_ids = identifiers(item.get("uniqueid") or {})
    if any(
        k in remote_ids and remote_ids[k] != value for k, value in edition["identifiers"].items()
    ):
        raise HTTPException(409, "Kodi library identity differs from the catalogue.")
    # Honor the explicit movie-version file; otherwise resolve live through Kodi.
    path = (
        edition["metadata"].get("file")
        if data.edition_id or (key == "movieid" and not edition["metadata"].get("is_default", True))
        else item.get("file") or edition["metadata"].get("file")
    )
    if not isinstance(path, str) or not path or len(path) > 8192 or any(ord(c) < 32 for c in path):
        raise HTTPException(422, "Kodi did not return a playable file path.")
    return path, edition


@router.get("/{player_id}/local-size")
async def kodi_local_file_size(
    player_id: str,
    media_id: str,
    edition_id: str,
    request: Request,
    _=Depends(authenticated),
):
    """Read the *entire* Kodi file byte length without opening a player.

    Unlike a HEAD against a temporary streaming ticket, this endpoint is
    authenticated, edition-scoped, and validates the VFS Content-Range for
    a one-byte GET. Do not confuse a 206's one-byte Content-Length with
    the actual file length.
    """
    player = _lookup(request, player_id)
    path, edition = await _resolve_video(
        request, player, PlayInput(media_id=media_id, edition_id=edition_id)
    )
    base = endpoint(player["config"]).removesuffix("/jsonrpc")
    vfs_url = f"{base}/vfs/{quote(path, safe='')}"
    config = player["config"]
    auth = (config["username"], config.get("password") or "") if config.get("username") else None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8, read=12, write=8, pool=8),
            auth=auth,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                vfs_url,
                headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"},
            ) as upstream:
                if upstream.status_code == 206:
                    content_range = upstream.headers.get("content-range", "")
                    match = re.fullmatch(r"bytes\s+0-0/(\d+)", content_range)
                    byte_count = int(match.group(1)) if match else None
                elif upstream.status_code == 200:
                    # Kodi ignored the range and returned the whole file.
                    # Headers are enough; do not read or buffer the body.
                    length = upstream.headers.get("content-length", "")
                    byte_count = int(length) if length.isdigit() else None
                else:
                    raise HTTPException(502, f"Kodi VFS returned HTTP {upstream.status_code}.")
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cannot read Kodi VFS file size.") from exc
    if byte_count is None or byte_count <= 0:
        raise HTTPException(404, "Kodi VFS did not expose the total file size.")
    return {"edition_id": edition["id"], "bytes": byte_count}


@router.post("/{player_id}/local-play")
async def create_local_stream(
    player_id: str, data: PlayInput, request: Request, _=Depends(authenticated)
):
    """Validate the precise Kodi edition, return only a relative time-limited URL."""
    player = _lookup(request, player_id)
    started = time.monotonic()
    file_path, edition = await _resolve_video(request, player, data)
    resolved_ms = round((time.monotonic() - started) * 1000, 1)
    LOG.info("Kodi stream ticket resolved player=%s resolve_ms=%.1f", player_id, resolved_ms)
    current = time.monotonic()
    for old, value in list(_TICKETS.items()):
        if value["expires"] <= current:
            _TICKETS.pop(old, None)
    # Include a file extension in the URL so AVFoundation can recognize
    # the container even though the opaque capability token hides the filename.
    suffix = urlsplit(file_path).path.rsplit("/", 1)[-1].rsplit(".", 1)
    extension = suffix[-1].lower() if len(suffix) == 2 else "mp4"
    if extension not in {"mp4", "m4v", "mov", "mkv", "avi", "ts", "m2ts"}:
        extension = "mp4"
    filename = f"video.{extension}"
    token = secrets.token_urlsafe(40)
    _TICKETS[token] = {
        "player_id": player_id,
        "file": file_path,
        "filename": filename,
        "expires": current + _TICKET_TTL,
    }
    while len(_TICKETS) > _TICKET_LIMIT:
        _TICKETS.popitem(last=False)
    return {
        "path": f"/api/v1/players/{player_id}/local-stream/{token}/{filename}",
        "edition_id": edition["id"],
        "resume_seconds": edition.get("resume_seconds") or 0,
        "expires_in": _TICKET_TTL,
        "resolution_ms": resolved_ms,
    }


@router.api_route("/{player_id}/local-stream/{ticket}/{filename}", methods=["GET", "HEAD"])
async def stream_local_video(player_id: str, ticket: str, filename: str, request: Request):
    """Proxy byte-ranged Kodi /vfs HTTP without holding the entire movie in RAM."""
    item = _TICKETS.get(ticket)
    if (
        not item
        or item["player_id"] != player_id
        or item["filename"] != filename
        or item["expires"] <= time.monotonic()
    ):
        raise HTTPException(403, "Stream ticket is invalid or expired.")
    player = _lookup(request, player_id)
    start = time.monotonic()
    range_header = request.headers.get("range")
    if range_header and not _RANGE.fullmatch(range_header):
        raise HTTPException(416, "Only single byte ranges are supported.")
    matched = await asyncio.to_thread(_registered_media_location, request, item["file"])
    if matched:
        location, relative = matched
        try:
            response = await stream_location(
                location, relative, range_header, request.app.state.store,
                request.app.state.playback_oauth,
                request.app.state.playback_nfs_mounts, method=request.method,
            )
            if request.method == "HEAD":
                return Response(
                    status_code=response.status_code,
                    headers=dict(response.headers), media_type=response.media_type,
                )
            return response
        except HTTPException as exc:
            if exc.status_code not in (502, 503, 504):
                raise
            LOG.info("Configured %s share not reachable; trying Kodi VFS", location["kind"])
    base = endpoint(player["config"]).removesuffix("/jsonrpc")
    url = f"{base}/vfs/{quote(item['file'], safe='')}"
    config = player["config"]
    auth = (config["username"], config.get("password") or "") if config.get("username") else None
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8, read=40, write=8, pool=8),
        auth=auth,
        follow_redirects=False,
        trust_env=False,
    )
    headers = {"Accept-Encoding": "identity"}
    if range_header:
        headers["Range"] = range_header
    # Kodi's VFS does not consistently answer HEAD. A tiny range GET is
    # sufficient to advertise size to AVPlayer without reading the media.
    if request.method == "HEAD":
        headers["Range"] = "bytes=0-0"
    try:
        upstream_request = client.build_request("GET", url, headers=headers)
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, "Cannot reach Kodi's VFS HTTP endpoint.") from exc
    if upstream.status_code not in (200, 206):
        code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, f"Kodi VFS returned HTTP {code}.")
    # Never silently present a full-file response as the requested nonzero
    # range. AVPlayer would interpret these bytes at the wrong offset.
    if range_header and upstream.status_code == 200:
        segment = range_header.removeprefix("bytes=")
        requested_start = int(segment.split("-", 1)[0]) if not segment.startswith("-") else 1
        if requested_start > 0:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(502, "Kodi VFS does not support seeking (HTTP byte ranges).")
    exposed = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower()
        in {"content-length", "content-range", "accept-ranges", "etag", "last-modified"}
    }
    kodi_header_ms = round((time.monotonic() - start) * 1000, 1)
    LOG.info(
        "Kodi VFS opened player=%s upstream_status=%d response_headers_ms=%.1f range=%s",
        player_id,
        upstream.status_code,
        kodi_header_ms,
        "present" if range_header else "absent",
    )
    exposed["Server-Timing"] = f"kodi_vfs;dur={kodi_header_ms}"
    exposed["Accept-Ranges"] = "bytes" if upstream.status_code == 206 else "none"
    exposed["X-Kodi-VFS-Ranges"] = "supported" if upstream.status_code == 206 else "unconfirmed"
    exposed["Cache-Control"] = "private, no-store"
    exposed["Referrer-Policy"] = "no-referrer"
    media_type = mimetypes.guess_type(item["file"])[0] or upstream.headers.get(
        "content-type", "application/octet-stream"
    )
    if request.method == "HEAD":
        # A range GET is only a metadata probe; advertise the total file size.
        total = upstream.headers.get("content-range", "").rsplit("/", 1)
        if len(total) == 2 and total[1].isdigit():
            exposed["Content-Length"] = total[1]
        await upstream.aclose()
        await client.aclose()
        return Response(status_code=200, headers=exposed, media_type=media_type)

    async def chunks():
        first_chunk = True
        sent = 0
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=128 * 1024):
                if first_chunk:
                    first_chunk = False
                    LOG.info(
                        "Kodi VFS first chunk player=%s first_byte_ms=%.1f",
                        player_id,
                        (time.monotonic() - start) * 1000,
                    )
                sent += len(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            LOG.debug("Kodi VFS stream finished player=%s bytes=%d", player_id, sent)

    return StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        headers=exposed,
        media_type=media_type,
    )


# Browser player: track list, chapters, WebVTT subtitles and a seekable,
# browser-compatible stream for the same short-lived ticket. Unlike the raw
# byte-range route above (used by AVPlayer), these also require the session.
async def _ticket_source(request: Request, player_id: str, ticket: str):
    item = _TICKETS.get(ticket)
    if not item or item["player_id"] != player_id or item["expires"] <= time.monotonic():
        raise HTTPException(403, "Stream ticket is invalid or expired.")
    player = _lookup(request, player_id)
    matched = await asyncio.to_thread(_registered_media_location, request, item["file"])
    if matched:
        location, relative = matched
        password = await asyncio.to_thread(
            __import__("home_media_catalog.playback_browser", fromlist=["_credentials"])._credentials,
            request.app.state.store, request.app.state.playback_oauth, location["id"],
        )
        try:
            source = await asyncio.to_thread(
                network_source, location, relative, password,
                request.app.state.playback_nfs_mounts,
            )
            return source, await probe(source)
        except (OSError, HTTPException):
            LOG.info("Configured network source unavailable; trying Kodi VFS")
    config = player["config"]
    base = endpoint(config).removesuffix("/jsonrpc")
    url = f"{base}/vfs/{quote(item['file'], safe='')}"
    auth = (config["username"], config.get("password") or "") if config.get("username") else None
    source = await asyncio.to_thread(kodi_source, url, auth, f"kodi:{player_id}:{item['file']}")
    return source, await probe(source)


@router.get("/{player_id}/local-media/{ticket}/probe")
async def local_media_probe(
    player_id: str, ticket: str, request: Request, _=Depends(authenticated)
):
    _, info = await _ticket_source(request, player_id, ticket)
    return info


@router.get("/{player_id}/local-media/{ticket}/subtitles/{stream_index}.vtt")
async def local_media_subtitle(
    player_id: str, ticket: str, stream_index: int, request: Request, _=Depends(authenticated)
):
    source, info = await _ticket_source(request, player_id, ticket)
    path = await subtitle_vtt(source, info, stream_index, request.app.state.data_dir)
    return FileResponse(
        path, media_type="text/vtt", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/{player_id}/local-media/{ticket}/stream.mp4")
async def local_media_stream(
    player_id: str,
    ticket: str,
    request: Request,
    start: float = Query(0, ge=0),
    audio: int | None = Query(None, ge=0),
    subtitle: int | None = Query(None, ge=0),
    copy: str = Query("", max_length=40),
    height: int = Query(1080, ge=240, le=2160),
    _=Depends(authenticated),
):
    source, info = await _ticket_source(request, player_id, ticket)
    return await media_stream(
        source,
        info,
        start=start,
        audio=audio,
        subtitle=subtitle,
        video_copy=parse_video_copy(copy),
        max_height=height,
    )
