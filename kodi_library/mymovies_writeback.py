"""Opt-in My Movies Remote Server API write-back (legacy/professional editions).

The XML export is never changed. Only the documented watched/unwatched
commands are available; there is no documented personal rating setter.
"""

import re

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from .connectors.base import ConnectorError
from .utils import fold

MAX_RESPONSE = 1_000_000


def _child_text(node, *names: str) -> str:
    names = {name.casefold() for name in names}
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1].casefold()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def _response(data: bytes):
    if len(data) > MAX_RESPONSE:
        raise ConnectorError("Réponse My Movies trop volumineuse.")
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ParseError, ValueError) as exc:
        raise ConnectorError("La réponse My Movies ne contient pas un XML valide.") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "response":
        raise ConnectorError("Réponse inattendue de l'API My Movies.")
    if root.attrib.get("status", "").casefold() != "ok":
        # Never include upstream text: it could echo credentials.
        raise ConnectorError("La commande a été refusée par l'API My Movies.")
    return root


def _match_title(response, edition: dict, title_id: str) -> bool:
    # Check the title before mutating it; a numerical export ID may not match
    # the remote installation's database ID.
    candidates = [
        child for child in response.iter() if child.tag.rsplit("}", 1)[-1].casefold() == "title"
    ]
    if len(candidates) != 1:
        return False
    remote = candidates[0]
    metadata = edition.get("metadata") or {}
    local_title = metadata.get("mymovies_title") or ""
    remote_title = _child_text(remote, "LocalTitle", "Title", "OriginalTitle")
    if not local_title or not remote_title or fold(local_title) != fold(remote_title):
        return False
    remote_id = _child_text(remote, "ID", "TitleID") or remote.get("ID") or remote.get("id")
    if remote_id and remote_id != title_id:
        return False
    local_barcode = metadata.get("barcode")
    remote_barcode = _child_text(remote, "Barcode")
    if local_barcode and remote_barcode and local_barcode != remote_barcode:
        return False
    # In addition to the title, require an explicit matching identifier or
    # barcode. Never write on a title-only match from a different database.
    return bool(
        remote_id == title_id
        or (local_barcode and remote_barcode and local_barcode == remote_barcode)
    )


async def write_mymovies(source: dict, editions: list[dict], changes: dict) -> str | None:
    config = source["config"]
    host = str(config.get("remote_host") or "").strip()
    api_key = config.get("remote_api_key") or ""
    if not host or not api_key:
        raise ConnectorError("Configurez l'hôte et la clé API My Movies pour activer l'écriture.")
    if any(c in host for c in ("/", "@", "?", "#", "\\")):
        raise ConnectorError("L'hôte My Movies doit être un nom DNS ou une adresse IP.")
    port = config.get("remote_port", 51414)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConnectorError("Port My Movies invalide.")

    targets: dict[str, dict] = {}
    for edition in editions:
        if edition["source_type"] != "mymovies":
            continue
        title_id = str(edition.get("metadata", {}).get("mymovies_remote_title_id") or "")
        if not re.fullmatch(r"[1-9][0-9]*", title_id):
            raise ConnectorError(
                "Identifiant de titre My Movies absent de l'export XML. Aucune écriture effectuée."
            )
        targets.setdefault(title_id, edition)
    if not targets:
        return None

    method = "ElementWatched" if changes["watched"] else "ClearElementWatched"
    # My Movies documents an HTTP GET query-string API (not a JSON or REST API).
    # Keep the API restricted to the user's explicitly configured host.
    url = f"http://{host}:{port}/"
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            for title_id, edition in targets.items():
                credentials = {"apikey": api_key, "id": title_id}
                details = await client.get(url, params={"command": "GetTitle", **credentials})
                details.raise_for_status()
                if not _match_title(_response(details.content), edition, title_id):
                    raise ConnectorError(
                        "Identité My Movies non confirmée : titre et identifiants divergents."
                    )
                response = await client.get(url, params={"command": method, **credentials})
                response.raise_for_status()
                _response(response.content)
    except httpx.HTTPError as exc:
        # Never display HTTP URLs: My Movies authenticates with a query-string
        # API key and a URL in the exception could expose that secret.
        raise ConnectorError(
            "API My Movies inaccessible ou réponse HTTP en échec. Vérifiez le réseau et les droits."
        ) from exc
    return None
