"""Optional TMDB credits, portraits and trailers, matched only by known IDs."""

import re

import httpx

from .connectors.base import ConnectorError
from .people import category, youtube_url


async def enrich_tmdb(detail: dict, token: str) -> dict:
    if not token:
        raise ConnectorError("Configure TMDB_API_TOKEN to enrich credits and portraits.")
    kind = {"movie": "movie", "tvshow": "tv"}.get(detail["kind"])
    if not kind:
        raise ConnectorError("TMDB enrichment currently supports movies and TV shows.")
    ids = detail["identifiers"]
    tmdb = ids.get("tmdb", [])
    try:
        async with httpx.AsyncClient(
            base_url="https://api.themoviedb.org/3/",
            headers={"Authorization": "Bearer " + token},
            timeout=20,
            trust_env=False,
        ) as client:

            async def get(path, **params):
                response = await client.get(path, params=params)
                response.raise_for_status()
                return response.json()

            if len(tmdb) == 1 and re.fullmatch(r"\d+", tmdb[0]):
                tmdb_id = tmdb[0]
            else:
                imdb = ids.get("imdb", [])
                if len(imdb) != 1 or not re.fullmatch(r"tt\d+", imdb[0]):
                    raise ConnectorError("A unique IMDb or TMDB identifier is required.")
                found = await get("find/" + imdb[0], external_source="imdb_id")
                matches = found.get("movie_results" if kind == "movie" else "tv_results", [])
                if len(matches) != 1:
                    raise ConnectorError("TMDB could not uniquely match this media's IMDb ID.")
                tmdb_id = str(matches[0]["id"])
            data = await get(
                f"{kind}/{tmdb_id}",
                append_to_response="credits,videos",
                language="fr-FR",
                include_video_language="fr,en,null",
            )
            people = []
            for group in ("cast", "crew"):
                for row in data.get("credits", {}).get(group, []):
                    if not row.get("name"):
                        continue
                    path = row.get("profile_path") or ""
                    photo = (
                        "https://image.tmdb.org/t/p/w185" + path
                        if re.fullmatch(r"/[A-Za-z0-9_.-]+", path)
                        else None
                    )
                    people.append(
                        {
                            "name": row["name"],
                            "role": row.get("character") if group == "cast" else row.get("job"),
                            "category": "cast"
                            if group == "cast"
                            else category(row.get("job") or "crew"),
                            "order": row.get("order"),
                            "tmdb_id": str(row["id"]),
                            "photos": [photo] if photo else [],
                        }
                    )
            videos = sorted(
                data.get("videos", {}).get("results", []),
                key=lambda v: (v.get("iso_639_1") != "fr", not v.get("official", False)),
            )
            trailers = [
                url
                for v in videos
                if v.get("site") == "YouTube" and v.get("type") == "Trailer"
                if (url := youtube_url("https://youtu.be/" + str(v.get("key", ""))))
            ]
            return {"people": people, "trailers": list(dict.fromkeys(trailers)), "tmdb_id": tmdb_id}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ConnectorError(
            "TMDB enrichment failed. Check the API token, DNS and TMDB availability."
        ) from exc
