"""Source-independent credits and safe external links."""

import re
from urllib.parse import parse_qs, quote, urlsplit


def category(value: str) -> str:
    value = value.casefold().strip()
    if any(v in value for v in ("compos", "music", "musique")):
        return "composer"
    if any(v in value for v in ("director", "réalis", "realis")) and "photograph" not in value:
        return "director"
    if any(v in value for v in ("writer", "screenplay", "scénar", "scenar")):
        return "writer"
    if any(v in value for v in ("producer", "producteur", "production")):
        return "producer"
    if any(v in value for v in ("photograph", "cinematograph")):
        return "photography"
    if value in ("", "cast", "actor", "acteur", "acting"):
        return "cast"
    return "crew"


def person_view(member: dict, media_id: str, index: int) -> dict:
    result = dict(member)
    result["category"] = member.get("category", "cast")
    imdb = str(member.get("imdb_id") or "")
    tmdb = str(member.get("tmdb_id") or "")
    links = {}
    if re.fullmatch(r"nm\d+", imdb):
        links["imdb"] = f"https://www.imdb.com/name/{imdb}/"
    if re.fullmatch(r"\d+", tmdb):
        links["tmdb"] = f"https://www.themoviedb.org/person/{tmdb}"
    if not links:
        links["imdb_search"] = "https://www.imdb.com/find/?s=nm&q=" + quote(member["name"])
    result["links"] = links
    result["thumbnail"] = (
        f"/api/v1/media/{media_id}/people/{index}/artwork" if member.get("photos") else None
    )
    return result


def youtube_url(value: str) -> str | None:
    """Convert known Kodi YouTube plugin URLs, never expose arbitrary schemes."""
    try:
        parsed = urlsplit(value.strip())
        query = parse_qs(parsed.query)
        key = None
        if parsed.scheme == "plugin" and parsed.netloc == "plugin.video.youtube":
            key = (query.get("video_id") or query.get("videoid") or [None])[0]
        elif parsed.scheme in ("http", "https") and not parsed.username and not parsed.password:
            if parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
                if parsed.path == "/watch":
                    key = (query.get("v") or [None])[0]
                elif parsed.path.startswith(("/embed/", "/shorts/")):
                    key = parsed.path.split("/")[2]
            elif parsed.hostname == "youtu.be":
                key = parsed.path.lstrip("/")
        if key and re.fullmatch(r"[A-Za-z0-9_-]{11}", key):
            return "https://www.youtube.com/watch?v=" + key
    except (ValueError, IndexError):
        pass
    return None
