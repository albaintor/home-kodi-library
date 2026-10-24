"""Short-lived streaming of a Kodi VFS file to a native iOS video player.

Kodi credentials and network-share credentials never leave the NAS. Streams
are byte ranges: a 24 GB movie is never downloaded into server memory.
"""

import asyncio
import mimetypes
import re
import secrets
import time
from collections import OrderedDict
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .auth import authenticated
from .connectors.base import ConnectorError
from .models import PlayInput
from .players import connection, endpoint
from .utils import identifiers

router = APIRouter(prefix="/api/v1/players", tags=["Players"])
# Ephemeral tickets: only the iOS client with the authenticated API token may
# request one. AVPlayer doesn't support our API's bearer header, so the GET
# uses a non-reusable-to-other-media, high-entropy capability ticket.
_TICKETS = OrderedDict()
_TICKET_TTL = 4 * 3600
_TICKET_LIMIT = 128
_RANGE = re.compile(r"^bytes=\d+-\d*$")


def _lookup(request, player_id):
    player = request.app.state.store.player(player_id)
    if not player:
        raise HTTPException(404, "Unknown Kodi instance.")
    return player


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


@router.post("/{player_id}/local-play")
async def create_local_stream(
    player_id: str, data: PlayInput, request: Request, _=Depends(authenticated)
):
    """Validate the precise Kodi edition, return only a relative time-limited URL."""
    player = _lookup(request, player_id)
    file_path, edition = await _resolve_video(request, player, data)
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
    range_header = request.headers.get("range")
    if range_header and not _RANGE.fullmatch(range_header):
        raise HTTPException(416, "Only single byte ranges are supported.")
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
        requested_start = int(range_header[6:].split("-", 1)[0])
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
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=256 * 1024):
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        headers=exposed,
        media_type=media_type,
    )
