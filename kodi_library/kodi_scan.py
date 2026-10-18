"""Launch a Kodi scan and wait for its WebSocket completion notification.

A scan is never declared complete when the socket disconnects or times out:
the local catalogue is only imported after VideoLibrary.OnScanFinished.
"""
import asyncio
import json
from urllib.parse import urlsplit

import aiohttp

from .connectors.base import ConnectorError


def websocket_url(config: dict) -> str:
    host = config.get("host", "")
    if not host or any(c in host for c in ("/", "@", "?", "#", "\\")):
        raise ConnectorError("Adresse Kodi WebSocket invalide.")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = config.get("websocket_port", 9090)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConnectorError("Port WebSocket Kodi invalide.")
    url = f"ws://{host}:{port}/jsonrpc"
    if urlsplit(url).hostname is None:
        raise ConnectorError("Adresse WebSocket Kodi invalide.")
    return url


async def scan_until_finished(config: dict) -> None:
    """Start and monitor Kodi's scan on one WebSocket session.

    Notifications can interleave JSON-RPC responses. Listening is established
    before the scan request, so fast scans cannot race the subscription.
    """
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None), trust_env=False
        ) as client:
            async with client.ws_connect(
                websocket_url(config),
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
                heartbeat=30,
                max_msg_size=256_000,
            ) as ws:
                async def rpc(method, rpc_id, params=None):
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "method": method,
                            "params": params or {},
                        }
                    )

                async def read():
                    message = await ws.receive()
                    if message.type != aiohttp.WSMsgType.TEXT:
                        raise ConnectorError(
                            "Connexion Kodi WebSocket interrompue pendant l'analyse."
                        )
                    try:
                        value = json.loads(message.data)
                    except (ValueError, TypeError) as exc:
                        raise ConnectorError("Notification WebSocket Kodi invalide.") from exc
                    if not isinstance(value, dict):
                        raise ConnectorError("Réponse WebSocket Kodi inattendue.")
                    return value

                # Do not trigger any potentially expensive scan without UpdateData.
                await rpc("JSONRPC.Permission", 1)
                async with asyncio.timeout(10):
                    while True:
                        response = await read()
                        if response.get("id") != 1:
                            continue
                        permissions = response.get("result")
                        if (
                            not isinstance(permissions, dict)
                            or permissions.get("UpdateData", permissions.get("updatedata"))
                            is not True
                        ):
                            raise ConnectorError(
                                "Kodi ne permet pas VideoLibrary.Scan (UpdateData)."
                            )
                        break

                await rpc("VideoLibrary.Scan", 2, {"showdialogs": False})
                acknowledged = False
                started = False
                finished = False
                # Kodi emits OnScanStarted/OnScanFinished on the JSON-RPC socket.
                # Require both, to avoid treating an unrelated scan as ours.
                async with asyncio.timeout(1800):
                    while not (acknowledged and started and finished):
                        response = await read()
                        method = response.get("method")
                        if method == "VideoLibrary.OnScanStarted":
                            started = True
                        elif method == "VideoLibrary.OnScanFinished" and started:
                            finished = True
                        elif response.get("id") == 2:
                            if response.get("result") != "OK":
                                raise ConnectorError("Kodi a refusé la commande VideoLibrary.Scan.")
                            acknowledged = True
    except TimeoutError as exc:
        raise ConnectorError(
            "Délai d'attente de fin d'analyse Kodi dépassé : "
            "aucune synchronisation locale automatique n'a été lancée."
        ) from exc
    except (aiohttp.ClientError, OSError) as exc:
        raise ConnectorError(
            "WebSocket Kodi indisponible ou interrompu ; "
            "la fin de l'analyse n'a pas pu être confirmée."
        ) from exc
