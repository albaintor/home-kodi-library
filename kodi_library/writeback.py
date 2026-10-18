"""Propagate personal media changes to writable source connectors.

Source snapshots remain read-only; Kodi writes use a configured JSON-RPC player
associated with the same MariaDB library. The dispatch boundary can host other
writable connector types without changing the personal-edit API.
"""

import logging
import math
from collections import defaultdict
from typing import Any

from .connectors.base import ConnectorError
from .mymovies_writeback import write_mymovies
from .players import connection
from .utils import identifiers

_LOG = logging.getLogger(__name__)

# Null clears the local override ("use source value"), not the source itself.
# Extend this registry when another connector supports updates.
WRITABLE_FIELDS = {
    "kodi": frozenset({"user_rating", "watched"}),
    "mymovies": frozenset({"watched"}),
}

_KODI_METHODS = {
    "movie": ("movieid", "moviedetails", "GetMovieDetails", "SetMovieDetails"),
    "tvshow": ("tvshowid", "tvshowdetails", "GetTVShowDetails", "SetTVShowDetails"),
    "episode": ("episodeid", "episodedetails", "GetEpisodeDetails", "SetEpisodeDetails"),
}


def _verify_identity(item: dict, edition: dict, detail: dict) -> None:
    """Refuse writes against a stale ID or an unrelated Kodi library."""
    local = edition.get("identifiers") or {}
    remote = identifiers(item.get("uniqueid") or {})
    shared = local.keys() & remote.keys()
    if any(local[key] != remote[key] for key in shared):
        raise ConnectorError(
            "Les identifiants du média diffèrent dans Kodi. Synchronisez la source."
        )
    # If there is no shared external ID, require an exact title match; IDs
    # alone are not safe across distinct MyVideos databases.
    if (
        not shared
        and (item.get("title") or "").strip().casefold()
        != (detail.get("title") or "").strip().casefold()
    ):
        raise ConnectorError(
            "Le titre du média ne correspond pas dans Kodi. Synchronisez la source."
        )
    if detail["kind"] == "episode":
        for field in ("season", "episode"):
            expected = detail.get(field)
            actual = item.get(field)
            if expected is not None and actual is not None and expected != actual:
                raise ConnectorError(
                    "La saison ou le numéro d'épisode ne correspond pas dans Kodi."
                )


async def _write_kodi(
    player: dict, detail: dict, editions: list[dict], changes: dict
) -> str | None:
    kind = detail["kind"]
    if kind not in _KODI_METHODS:
        raise ConnectorError("Ce type de média ne peut pas être modifié dans Kodi.")
    id_field, response_key, read_method, write_method = _KODI_METHODS[kind]
    # Movie versions share the same Kodi movieid: issue just one update.
    distinct: dict[int, dict] = {}
    for edition in editions:
        kodi_id = edition.get("metadata", {}).get("kodi_id")
        if type(kodi_id) is not int or kodi_id < 0:
            raise ConnectorError("Identifiant Kodi manquant. Synchronisez la source.")
        distinct.setdefault(kodi_id, edition)

    updates: dict[str, Any] = {}
    warning = None
    if changes.get("user_rating") is not None:
        # Kodi's userrating property is an integer, whereas the local UI
        # permits halves. Preserve the precise value locally.
        rating = float(changes["user_rating"])
        rounded = math.floor(rating + 0.5)
        updates["userrating"] = rounded
        if rounded != rating:
            warning = f"Kodi accepte uniquement les notes entières : note arrondie à {rounded}/10."
    if type(changes.get("watched")) is bool:
        updates["watched"] = changes["watched"]
    if not updates:
        return None

    async with connection(player["config"]) as rpc:
        permissions = await rpc("JSONRPC.Permission", {})
        if (
            not isinstance(permissions, dict)
            or permissions.get("UpdateData", permissions.get("updatedata")) is not True
        ):
            raise ConnectorError(
                "Kodi n'autorise pas la modification de la bibliothèque (UpdateData)."
            )
        for kodi_id, edition in distinct.items():
            result = await rpc(
                "VideoLibrary." + read_method,
                {
                    id_field: kodi_id,
                    "properties": ["uniqueid", "title", "playcount", "season", "episode"]
                    if kind == "episode"
                    else ["uniqueid", "title", "playcount"],
                },
            )
            item = result.get(response_key) if isinstance(result, dict) else None
            if not isinstance(item, dict):
                raise ConnectorError(
                    "Ce média n'existe plus dans la bibliothèque Kodi cible. Synchronisez la source."
                )
            _verify_identity(item, edition, detail)
            params = {id_field: kodi_id}
            if "userrating" in updates:
                params["userrating"] = updates["userrating"]
            if "watched" in updates:
                # Retain existing positive play counts when marking as watched.
                count = item.get("playcount")
                params["playcount"] = max(1, int(count or 0)) if updates["watched"] else 0
            response = await rpc("VideoLibrary." + write_method, params)
            if response != "OK":
                raise ConnectorError("Kodi n'a pas confirmé la modification du média.")
    return warning


async def propagate_personal(
    detail: dict,
    changes: dict,
    sources: list[dict],
    players: list[dict],
) -> list[dict]:
    """Return explicit statuses; source XML files are never modified.

    The caller persists local preferences even if an upstream service fails.
    Adding new source types only requires a field capability and adapter.
    """
    editions_by_source: dict[str, list[dict]] = defaultdict(list)
    for edition in detail["editions"]:
        if edition["source_type"] in WRITABLE_FIELDS:
            editions_by_source[edition["source_id"]].append(edition)
    source_info = {source["id"]: source for source in sources}
    results = []
    for source_id, editions in editions_by_source.items():
        kind = editions[0]["source_type"]
        fields = WRITABLE_FIELDS[kind] & changes.keys()
        if not any(changes[field] is not None for field in fields):
            continue
        source = source_info.get(source_id, {})
        if kind == "mymovies" and not source.get("config", {}).get("remote_enabled"):
            # An XML-only source is intentionally read-only, not an error.
            continue
        entry = {
            "source_id": source_id,
            "source_name": source.get("name") or editions[0]["source_name"],
            "source_type": kind,
        }
        if not source.get("enabled", True):
            results.append({
                **entry, "status": "unavailable", "message": "La source est désactivée."
            })
            continue
        if kind == "mymovies":
            if type(changes.get("watched")) is not bool:
                continue
            try:
                warning = await write_mymovies(source, editions, changes)
                results.append({**entry, "status": "updated", "message": warning})
            except ConnectorError as exc:
                results.append({**entry, "status": "failed", "message": str(exc)})
            except Exception:
                _LOG.exception("Unexpected My Movies writeback error for source %s", source_id)
                results.append({
                    **entry, "status": "failed",
                    "message": "Erreur inattendue lors de la mise à jour My Movies.",
                })
            continue

        candidates = [player for player in players if player["source_id"] == source_id]
        if not candidates:
            results.append({
                **entry, "status": "unavailable",
                "message": "Aucune instance Kodi JSON-RPC n'est configurée pour cette source.",
            })
            continue
        # Multiple players can share one Kodi database. Only one success needed.
        errors = []
        for player in candidates:
            try:
                warning = await _write_kodi(player, detail, editions, changes)
                results.append({**entry, "status": "updated", "message": warning})
                break
            except ConnectorError as exc:
                errors.append(str(exc))
            except Exception:
                _LOG.exception("Unexpected Kodi writeback error for source %s", source_id)
                errors.append("Erreur inattendue lors de la mise à jour Kodi.")
        else:
            results.append({
                **entry, "status": "failed",
                "message": errors[-1] if errors else "Échec de la synchronisation Kodi.",
            })
    return results
