"""Kodi remote control and live playback state for the web and iOS clients.

The server owns Kodi credentials; clients never connect to Kodi directly.
"""
import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from collections import OrderedDict
from urllib.parse import quote_plus, quote, urlsplit

import aiohttp
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from .auth import COOKIE, authenticated, secure_equal, writable
from .connectors.base import ConnectorError
from .players import connection, endpoint

LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/players", tags=["Players"])
# Opaque, deterministic-per-process artwork handles keep even embedded SMB or
# HTTP image credentials out of web/iOS URLs and JSON responses.
_ARTWORK_SECRET = secrets.token_bytes(32)
_ARTWORK_REFS = OrderedDict()
_ARTWORK_MAX = 512
_ARTWORK_TTL = 3600


def _remember_artwork(player_id: str, kodi_image: str) -> str:
    key = (player_id, hmac.new(
        _ARTWORK_SECRET, (player_id + "\0" + kodi_image).encode("utf-8"),
        hashlib.sha256).hexdigest())
    _ARTWORK_REFS[key] = (kodi_image, time.monotonic() + _ARTWORK_TTL)
    _ARTWORK_REFS.move_to_end(key)
    while len(_ARTWORK_REFS) > _ARTWORK_MAX:
        _ARTWORK_REFS.popitem(last=False)
    return f"/api/v1/players/{player_id}/artwork?ref={key[1]}"


def _resolve_artwork(player_id: str, ref: str) -> str:
    if len(ref) != 64 or any(c not in "0123456789abcdef" for c in ref):
        raise HTTPException(404, "Unknown Kodi artwork.")
    item = _ARTWORK_REFS.get((player_id, ref))
    if not item or time.monotonic() >= item[1]:
        raise HTTPException(404, "Kodi artwork reference expired.")
    return item[0]


def _kodi_artwork_path(image: str) -> str:
    # Identical to integration-kodi's thumbnail_url() for image:// references.
    # Raw file paths need Kodi's double-encoded image:// wrapper.
    if image.startswith("image://"):
        return quote_plus(image)
    return quote(f"image://{quote(image, safe='')}/", safe="")



class RemoteAction(BaseModel):
    action: str = Field(pattern=r"^(up|down|left|right|select|back|home|context|info|osd|fullscreen|playpause|stop|next|previous|seek|subtitle_next|subtitle_off|subtitle_on|subtitle_select|audio_next|audio_select)$")
    percentage: float | None = Field(default=None, ge=0, le=100)
    seconds: float | None = Field(default=None, ge=0, le=172800)
    index: int | None = Field(default=None, ge=0, le=999)


def kodi_seek_time(position: float) -> dict[str, int]:
    """JSON-RPC Player.Seek's absolute time, as in integration-kodi."""
    millis = max(0, round(position * 1000))
    seconds_total, milliseconds = divmod(millis, 1000)
    hours, remainder = divmod(seconds_total, 3600)
    minutes, secs = divmod(remainder, 60)
    return {"hours": hours, "minutes": minutes, "seconds": secs,
            "milliseconds": milliseconds}


def seconds(value):
    if not isinstance(value, dict):
        return 0.0
    return float(value.get("hours", 0) * 3600 + value.get("minutes", 0) * 60 +
                 value.get("seconds", 0) + value.get("milliseconds", 0) / 1000)


async def current_state(player):
    """Fetch an authoritative snapshot after WebSocket notifications and on a slow tick."""
    async with connection(player["config"], timeout=5) as rpc:
        active = await rpc("Player.GetActivePlayers", {})
        active = next((p for p in active if p.get("type") in ("video", "audio")), None)
        if active is None:
            return {"active": False}
        pid = active["playerid"]
        props, item = await asyncio.gather(
            rpc("Player.GetProperties", {"playerid": pid, "properties": [
                "time", "totaltime", "speed", "percentage", "audiostreams",
                "currentaudiostream", "subtitles", "currentsubtitle", "subtitleenabled"]}),
            rpc("Player.GetItem", {"playerid": pid, "properties": [
                "title", "showtitle", "season", "episode", "thumbnail", "art", "file"]}),
        )
        media = item.get("item", {})
        art = media.get("art") or {}
        image = media.get("thumbnail") or art.get("thumb") or art.get("poster") or art.get("fanart")
        return {
            "active": True, "player_id": pid, "type": active.get("type"),
            "title": media.get("title") or media.get("label") or "",
            "show_title": media.get("showtitle") or "",
            "season": media.get("season"), "episode": media.get("episode"),
            "artwork": _remember_artwork(player['id'], image) if image else None,
            "position": seconds(props.get("time")), "duration": seconds(props.get("totaltime")),
            "percentage": props.get("percentage", 0), "paused": props.get("speed", 0) == 0,
            "speed": props.get("speed", 0),
            "audio_streams": props.get("audiostreams") or [],
            "current_audio": props.get("currentaudiostream"),
            "subtitles": props.get("subtitles") or [],
            "current_subtitle": props.get("currentsubtitle"),
            "subtitles_enabled": bool(props.get("subtitleenabled")),
        }


def lookup(request, player_id):
    player = request.app.state.store.player(player_id)
    if not player:
        raise HTTPException(404, "Unknown Kodi player.")
    return player


@router.get("/{player_id}/remote")
async def remote_status(player_id: str, request: Request, _=Depends(authenticated)):
    player = lookup(request, player_id)
    try:
        return await current_state(player)
    except (ConnectorError, httpx.HTTPError) as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/{player_id}/remote")
async def remote_command(player_id: str, action: RemoteAction, request: Request, _=Depends(writable)):
    player = lookup(request, player_id)
    directions = {"up": "Up", "down": "Down", "left": "Left", "right": "Right",
                  "select": "Select", "back": "Back", "home": "Home",
                  "context": "ContextMenu", "info": "Info", "osd": "ShowOSD"}
    try:
        async with connection(player["config"], timeout=5) as rpc:
            if action.action in directions:
                await rpc("Input." + directions[action.action], {})
            elif action.action == "fullscreen":
                prop = await rpc("GUI.GetProperties", {"properties": ["fullscreen"]})
                await rpc("GUI.SetFullscreen", {"fullscreen": not prop.get("fullscreen", False)})
            else:
                active = await rpc("Player.GetActivePlayers", {})
                current = next((p for p in active if p.get("type") in ("video", "audio")), None)
                if current is None:
                    raise HTTPException(409, "No active Kodi playback.")
                pid = current["playerid"]
                if action.action == "playpause":
                    await rpc("Player.PlayPause", {"playerid": pid})
                elif action.action == "stop":
                    await rpc("Player.Stop", {"playerid": pid})
                elif action.action in ("next", "previous"):
                    await rpc("Player.GoTo", {"playerid": pid, "to": action.action})
                elif action.action == "seek":
                    if action.seconds is not None:
                        # Time-based seek mirrors integration-kodi and avoids
                        # percentage/duration rounding or incorrectly naked
                        # numbers in the JSON-RPC value parameter.
                        seek_value = {"time": kodi_seek_time(action.seconds)}
                    elif action.percentage is not None:
                        seek_value = {"percentage": action.percentage}
                    else:
                        raise HTTPException(422, "A position or percentage is required to seek.")
                    await rpc("Player.Seek", {"playerid": pid, "value": seek_value})
                elif action.action in ("audio_next", "audio_select"):
                    if action.action == "audio_select" and action.index is None:
                        raise HTTPException(422, "An audio stream index is required.")
                    await rpc("Player.SetAudioStream", {
                        "playerid": pid, "stream": action.index if action.action == "audio_select" else "next"
                    })
                elif action.action.startswith("subtitle_"):
                    if action.action == "subtitle_select" and action.index is None:
                        raise HTTPException(422, "A subtitle index is required.")
                    value = action.index if action.action == "subtitle_select" else action.action.removeprefix("subtitle_")
                    await rpc("Player.SetSubtitle", {"playerid": pid, "subtitle": value,
                                                      "enable": action.action in ("subtitle_next", "subtitle_on", "subtitle_select")})
        return {"ok": True}
    except ConnectorError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/{player_id}/artwork")
async def remote_artwork(player_id: str, ref: str, request: Request, _=Depends(authenticated)):
    player = lookup(request, player_id)
    image = _resolve_artwork(player_id, ref)
    if len(image) > 2048 or any(ord(c) < 32 for c in image):
        raise HTTPException(422, "Invalid Kodi image reference.")
    base = endpoint(player["config"]).removesuffix("/jsonrpc")
    config = player["config"]
    credentials = (config["username"], config.get("password") or "") if config.get("username") else None
    try:
        async with httpx.AsyncClient(timeout=8, auth=credentials, trust_env=False,
                                     follow_redirects=False) as client:
            result = await client.get(f"{base}/image/{_kodi_artwork_path(image)}")
            result.raise_for_status()
            mime = result.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif") or len(result.content) > 8_000_000:
                raise HTTPException(502, "Invalid or oversized Kodi artwork.")
            return Response(content=result.content, media_type=mime, headers={"Cache-Control": "private, max-age=30"})
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Cannot retrieve artwork from Kodi.") from exc


def websocket_authorized(socket: WebSocket):
    """Authenticate browser sessions or native bearer tokens without URL credentials."""
    origin = socket.headers.get("origin")
    if origin:
        value = urlsplit(origin)
        if value.scheme not in ("http", "https") or value.netloc.lower() != socket.headers.get("host", "").lower():
            return False
    auth = socket.app.state.auth
    header = socket.headers.get("authorization", "")
    if header:
        parts = header.split(" ", 1)
        return len(parts) == 2 and parts[0].lower() == "bearer" and bool(auth.token_role(parts[1]))
    cookie = socket.cookies.get(COOKIE)
    if cookie:
        record = auth.sessions.get(cookie)
        return bool(record and record[0] > time.monotonic())
    ingress = socket.headers.get("x-kodi-library-ingress-token", "")
    if auth.settings.ingress_token and secure_equal(ingress, auth.settings.ingress_token):
        return True
    # Use exactly the same trusted-LAN policy as authenticated HTTP requests.
    scope = dict(socket.scope)
    scope["type"] = "http"
    return auth.is_local(Request(scope))


@router.websocket("/{player_id}/events")
async def remote_events(socket: WebSocket, player_id: str):
    if not websocket_authorized(socket):
        await socket.close(code=1008)
        return
    player = await asyncio.to_thread(socket.app.state.store.player, player_id)
    if not player:
        await socket.close(code=1008)
        return
    await socket.accept()
    config = player["config"]
    host = config["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    protocol = "wss" if config.get("tls") else "ws"
    ws_url = f"{protocol}://{host}:{config.get('websocket_port', 9090)}/jsonrpc"
    credentials = aiohttp.BasicAuth(config["username"], config.get("password") or "") if config.get("username") else None
    # The HTTP JSON-RPC connection and Kodi's TCP WebSocket service are
    # independent. A disabled/blocked port 9090 must NEVER make a working
    # HTTP-controlled Kodi look offline. Continue polling playback via HTTP
    # while retrying the notification subscription.
    upstream = None
    notification_task = None
    next_retry = 0.0
    retry_delay = 2.0
    last_state = None
    last_heartbeat = time.monotonic()
    try:
        async with aiohttp.ClientSession(
            trust_env=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)
        ) as session:
            while True:
                if upstream is None and time.monotonic() >= next_retry:
                    try:
                        upstream = await session.ws_connect(
                            ws_url,
                            auth=credentials,
                            heartbeat=20,
                            timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=3),
                        )
                        notification_task = asyncio.create_task(upstream.receive())
                        retry_delay = 2.0
                        LOG.info("Kodi notifications connected for player %s", player_id)
                    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
                        LOG.warning("Kodi notifications unavailable for player %s (%s); "
                                    "using HTTP state polling", player_id, type(exc).__name__)
                        next_retry = time.monotonic() + retry_delay
                        retry_delay = min(30.0, retry_delay * 1.7)

                # Independent playback snapshot, including initial connection
                # and time changes when Kodi emits no position notifications.
                try:
                    state = await asyncio.wait_for(current_state(player), timeout=8)
                    # A websocket transport error is NOT an offline player.
                    state["websocket_connected"] = upstream is not None
                    if state != last_state:
                        await socket.send_json({"type": "state", "state": state})
                        last_state = state
                except (ConnectorError, httpx.HTTPError, TimeoutError, OSError) as exc:
                    LOG.warning("Kodi HTTP state unavailable for player %s: %s",
                                player_id, type(exc).__name__)
                    offline = {"type": "offline", "message": "Kodi HTTP JSON-RPC unavailable."}
                    if last_state != offline:
                        await socket.send_json(offline)
                        last_state = offline

                if time.monotonic() - last_heartbeat >= 15:
                    await socket.send_json({"type": "heartbeat"})
                    last_heartbeat = time.monotonic()

                wait = 2.0 if last_state and last_state.get("active") else 5.0
                if notification_task is not None:
                    done, _ = await asyncio.wait({notification_task}, timeout=wait)
                    if done:
                        try:
                            message = notification_task.result()
                        except (aiohttp.ClientError, RuntimeError, OSError):
                            message = None
                        if message is None or message.type in (
                            aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR
                        ):
                            notification_task = None
                            await upstream.close()
                            upstream = None
                            next_retry = time.monotonic() + retry_delay
                            retry_delay = min(30.0, retry_delay * 1.7)
                        else:
                            # Subscribed automatically to Player.OnPlay/OnPause,
                            # OnStop/OnSeek etc. by the Kodi JSON-RPC WS service.
                            notification_task = asyncio.create_task(upstream.receive())
                else:
                    await asyncio.sleep(wait)
    except (WebSocketDisconnect, RuntimeError, OSError):
        return
    finally:
        if notification_task is not None:
            notification_task.cancel()
            await asyncio.gather(notification_task, return_exceptions=True)
        if upstream is not None:
            await upstream.close()
