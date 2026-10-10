"""Optional playback targets. Browsing never depends on player availability."""

from urllib.parse import urlsplit

import httpx

from .connectors.base import ConnectorError
from .utils import identifiers


async def play(player: dict, detail: dict, edition_id: str | None, resume: bool) -> dict:
    config = player["config"]
    host = config["host"]
    if any(c in host for c in ("/", "@", "?", "#")):
        raise ConnectorError("Configure a Kodi hostname, not a URL.")
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
    kodi_id = edition["metadata"]["kodi_id"]
    id_name = "movieid" if detail["kind"] == "movie" else "episodeid"
    method = (
        "VideoLibrary.GetMovieDetails"
        if detail["kind"] == "movie"
        else "VideoLibrary.GetEpisodeDetails"
    )
    result_key = "moviedetails" if detail["kind"] == "movie" else "episodedetails"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"{'https' if config.get('tls') else 'http'}://{host}:{config['port']}/jsonrpc"
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise ConnectorError("Invalid Kodi hostname.")
    auth = (config["username"], config.get("password", "")) if config.get("username") else None
    try:
        async with httpx.AsyncClient(timeout=15, auth=auth, trust_env=False) as client:

            async def rpc(method, params):
                response = await client.post(
                    url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
                response.raise_for_status()
                value = response.json()
                if value.get("error"):
                    raise ConnectorError(
                        "Kodi rejected the request. Check the target and resynchronize its library."
                    )
                return value.get("result", {})

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
            # Default versions use library IDs; explicit alternative versions use
            # their own stored path instead of playing a different default file.
            media = {id_name: kodi_id}
            if detail["kind"] == "movie" and not edition["metadata"].get("is_default", True):
                file = edition["metadata"].get("file")
                if not file:
                    raise ConnectorError("This Kodi version has no playable file path.")
                media = {"file": file}
            await rpc("Player.Open", {"item": media, "options": {"resume": resume}})
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectorError(
            "Cannot reach Kodi. Start Kodi and enable authenticated HTTP remote control."
        ) from exc
    return {
        "player_id": player["id"],
        "media_id": detail["id"],
        "edition_id": edition["id"],
        "status": "started",
    }
