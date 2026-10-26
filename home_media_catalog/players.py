"""Optional playback targets. Browsing never depends on player availability."""

import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

from .connectors.base import ConnectorError
from .utils import identifiers


class KodiUnavailable(ConnectorError):
    """The HTTP JSON-RPC transport is unreachable; opt-in SQL fallback is possible."""


def endpoint(config: dict) -> str:
    host = config["host"]
    if any(c in host for c in ("/", "@", "?", "#")):
        raise ConnectorError("Configure a Kodi hostname or IP address, not a URL.")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"{'https' if config.get('tls') else 'http'}://{host}:{config['port']}/jsonrpc"
    try:
        valid = urlsplit(url).hostname is not None
    except ValueError:
        valid = False
    if not valid:
        raise ConnectorError("Invalid Kodi hostname.")
    return url


@asynccontextmanager
async def connection(config: dict, *, timeout: float = 15.0):
    """Shorter per-request timeout may be passed for latency-sensitive writes.

    Playback and configuration probes keep the regular 15-second tolerance.
    """
    url = endpoint(config)
    auth = (config["username"], config.get("password") or "") if config.get("username") else None
    try:
        async with httpx.AsyncClient(timeout=timeout, auth=auth, trust_env=False) as client:

            async def rpc(method, params):
                response = await client.post(
                    url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict) or value.get("jsonrpc") not in (None, "2.0"):
                    raise ConnectorError(
                        "The HTTP endpoint did not return a valid Kodi JSON-RPC response."
                    )
                if value.get("error"):
                    error = value["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    code = code if isinstance(code, int) else "unknown"
                    raise ConnectorError(
                        f"Kodi rejected JSON-RPC method {method} (code {code}). Check remote-control permissions."
                    )
                if "result" not in value:
                    raise ConnectorError("The HTTP endpoint did not return a Kodi JSON-RPC result.")
                return value["result"]

            yield rpc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        message = (
            "Kodi rejected HTTP authentication. Check username and password."
            if code in (401, 403)
            else "The Kodi HTTP endpoint returned an error. Check port and remote-control settings."
        )
        exception = ConnectorError if code in (401, 403) else KodiUnavailable
        raise exception(f"{message} (HTTP {code})") from exc
    except httpx.TimeoutException as exc:
        raise KodiUnavailable(
            "Kodi connection timed out. Check that Kodi is running, its host/port and the firewall."
        ) from exc
    except httpx.ConnectError as exc:
        current = exc
        dns = False
        while current:
            dns = dns or isinstance(current, socket.gaierror)
            current = current.__cause__
        message = (
            "Kodi hostname cannot be resolved. Check container DNS or use its IP address."
            if dns
            else "Cannot connect to Kodi. Start Kodi and check host, port, HTTPS settings and HTTP remote control."
        )
        raise KodiUnavailable(message) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise KodiUnavailable(
            "Invalid Kodi HTTP/JSON response. Check port, HTTP/HTTPS and remote-control settings."
        ) from exc


async def probe(config: dict) -> dict:
    async with connection(config) as rpc:
        if await rpc("JSONRPC.Ping", {}) != "pong":
            raise ConnectorError("The HTTP service is not a responding Kodi JSON-RPC endpoint.")
        permissions = await rpc("JSONRPC.Permission", {})
        if not isinstance(permissions, dict):
            raise ConnectorError(
                "Réponse JSONRPC.Permission inattendue : impossible de vérifier les droits de lecture Kodi."
            )
        # Kodi returns the permission names from PermissionToString in PascalCase,
        # although the published JSON-RPC schema uses lowercase property names.
        playback_allowed = permissions.get("ControlPlayback", permissions.get("controlplayback"))
        if playback_allowed is False:
            raise ConnectorError(
                "Kodi répond, mais refuse le contrôle de lecture (ControlPlayback=false). Vérifiez le contrôle distant HTTP sur l'instance Kodi contactée."
            )
        if playback_allowed is not True:
            raise ConnectorError(
                "Réponse JSONRPC.Permission inattendue : le droit ControlPlayback est absent ou invalide."
            )
        properties = await rpc("Application.GetProperties", {"properties": ["name", "version"]})
        version = properties.get("version", {}) if isinstance(properties, dict) else {}
        label = (
            f"{version['major']}.{version.get('minor', 0)}"
            if isinstance(version, dict) and "major" in version
            else None
        )
        return {"version": label, "playback_allowed": True}


async def play(player: dict, detail: dict, edition_id: str | None, resume: bool) -> dict:
    config = player["config"]
    candidates = [
        e
        for e in detail["editions"]
        if e["source_id"] == player["source_id"] and e["source_type"] == "kodi"
    ]
    if edition_id:
        candidates = [e for e in candidates if e["id"] == edition_id]
    if not candidates or detail["kind"] not in ("movie", "episode"):
        raise ConnectorError("No playable Kodi edition belongs to this player's configured source.")
    candidates.sort(key=lambda e: not e["metadata"].get("is_default", True))
    edition = candidates[0]
    kodi_id = edition["metadata"].get("kodi_id")
    if not isinstance(kodi_id, int) or kodi_id < 0:
        raise ConnectorError(
            "Ce fichier n'a pas d'identifiant Kodi valide. Synchronisez la source Kodi."
        )
    id_name = "movieid" if detail["kind"] == "movie" else "episodeid"
    method = (
        "VideoLibrary.GetMovieDetails"
        if detail["kind"] == "movie"
        else "VideoLibrary.GetEpisodeDetails"
    )
    result_key = "moviedetails" if detail["kind"] == "movie" else "episodedetails"
    async with connection(config) as rpc:
        item = (await rpc(method, {id_name: kodi_id, "properties": ["uniqueid"]})).get(
            result_key, {}
        )
        if not item:
            raise ConnectorError(
                "This media ID is no longer in the Kodi target library. Synchronize the catalogue."
            )
        remote_ids = identifiers(item.get("uniqueid", {}))
        local_ids = edition["identifiers"]
        if any(k in remote_ids and remote_ids[k] != value for k, value in local_ids.items()):
            raise ConnectorError(
                "The Kodi target uses a different library or stale media IDs. Playback was refused."
            )
        # An explicit file selection opens its exact path, including default
        # editions on Kodi versions that otherwise open a version chooser.
        media = {id_name: kodi_id}
        file = edition["metadata"].get("file")
        alternate = detail["kind"] == "movie" and not edition["metadata"].get("is_default", True)
        if edition_id or alternate:
            if file:
                media = {"file": file}
            elif alternate:
                raise ConnectorError("This Kodi version has no playable file path.")
        await rpc("Player.Open", {"item": media, "options": {"resume": resume}})
    return {
        "player_id": player["id"],
        "media_id": detail["id"],
        "edition_id": edition["id"],
        "status": "started",
    }
