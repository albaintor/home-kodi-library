"""Snapshot Kodi's built-in SQLite or shared SQL video library through JSON-RPC.

A full, paginated snapshot is published only once every method succeeds.
No access to Kodi's on-disk database or externally hosted MariaDB is required.
"""

from datetime import datetime

from .base import ConnectorError, Record
from .kodi import artwork_candidates
from ..utils import date_value, identifiers, positive, plain_text
from ..players import connection

PAGE_SIZE = 200
FIELDS = {
    "movie": (
        "VideoLibrary.GetMovies", "movies", "movieid",
        ["title", "originaltitle", "year", "plot", "runtime", "rating",
         "userrating", "playcount", "lastplayed", "dateadded", "file", "art",
         "thumbnail", "fanart", "genre", "uniqueid", "resume", "streamdetails",
         "cast", "director", "writer", "set", "trailer"],
    ),
    "tvshow": (
        "VideoLibrary.GetTVShows", "tvshows", "tvshowid",
        ["title", "originaltitle", "year", "plot", "rating", "userrating",
         "playcount", "lastplayed", "dateadded", "art", "thumbnail", "fanart",
         "genre", "uniqueid", "episode", "watchedepisodes", "cast", "studio"],
    ),
    "episode": (
        "VideoLibrary.GetEpisodes", "episodes", "episodeid",
        ["title", "showtitle", "season", "episode", "tvshowid", "plot", "runtime",
         "rating", "userrating", "playcount", "lastplayed", "dateadded", "file",
         "art", "thumbnail", "fanart", "uniqueid", "resume", "streamdetails",
         "cast", "director", "writer"],
    ),
}


def _artwork(row, kind):
    art = row.get("art") or {}
    value = art.get(kind) or (row.get("thumbnail") if kind == "poster" else row.get("fanart"))
    return artwork_candidates(value)


def normalize_jsonrpc(kind, row):
    """Translate common Kodi 20+ JSON-RPC video properties to existing Record."""
    id_field = FIELDS[kind][2]
    media_id = row.get(id_field)
    if type(media_id) is not int or media_id < 0:
        raise ConnectorError(f"Kodi returned a {kind} without a valid {id_field}.")
    season = row.get("season") if kind == "episode" else None
    episode = row.get("episode") if kind == "episode" else None
    parent_id = row.get("tvshowid") if kind == "episode" else None
    if kind == "episode" and (type(parent_id) is not int or parent_id < 0):
        raise ConnectorError("Kodi returned an episode without a TV show identifier.")
    play_count = row.get("playcount")
    if type(play_count) is not int:
        play_count = None
    resume = row.get("resume") if isinstance(row.get("resume"), dict) else {}
    runtime = row.get("runtime")
    if not isinstance(runtime, (float, int)) or runtime <= 0:
        runtime = 0
    raw_people = row.get("cast") if isinstance(row.get("cast"), list) else []
    people = [
        {"name": person.get("name"), "role": person.get("role"), "category": "cast",
         "order": person.get("order")}
        for person in raw_people if isinstance(person, dict) and person.get("name")
    ]
    for category in ("director", "writer"):
        for name in row.get(category) or []:
            if isinstance(name, str) and name:
                people.append({"name": name, "category": category})
    streams = row.get("streamdetails") if isinstance(row.get("streamdetails"), dict) else {}
    video = streams.get("video", []) if isinstance(streams.get("video"), list) else []
    audio = streams.get("audio", []) if isinstance(streams.get("audio"), list) else []
    subtitles = streams.get("subtitle", []) if isinstance(streams.get("subtitle"), list) else []
    title = row.get("title") or row.get("label") or "Untitled"
    uniqueid = row.get("uniqueid") if isinstance(row.get("uniqueid"), dict) else {}
    ids = identifiers(uniqueid)
    total_episodes = row.get("episode") if kind == "tvshow" else None
    watched_episodes = row.get("watchedepisodes") if kind == "tvshow" else None
    watched = ((watched_episodes >= total_episodes) if type(watched_episodes) is int
               and type(total_episodes) is int and total_episodes > 0 else None
               ) if kind == "tvshow" else (play_count > 0 if play_count is not None else None)
    movie_set = row.get("set") or ""
    return Record(
        external_key=f"{kind}:{media_id}",
        kind=kind,
        title=str(title),
        original_title=row.get("originaltitle") or None,
        year=row.get("year") if type(row.get("year")) is int and row["year"] > 0 else None,
        plot=plain_text(row.get("plot")),
        runtime_minutes=int((runtime + 59) // 60) if runtime else None,
        rating=positive(row.get("rating")),
        user_rating=positive(row.get("userrating")),
        identifiers=ids,
        genres=[str(x) for x in row.get("genre", []) if isinstance(x, str)],
        collections=[str(movie_set)] if movie_set else [],
        date_added=date_value(row.get("dateadded")),
        last_played=date_value(row.get("lastplayed")),
        watched=watched,
        play_count=play_count,
        resume_seconds=float(resume.get("position") or 0),
        total_seconds=float(resume.get("total") or 0) or float(runtime),
        poster=_artwork(row, "poster"),
        fanart=_artwork(row, "fanart"),
        parent_key=f"tvshow:{parent_id}" if kind == "episode" else None,
        season=season,
        episode=episode,
        metadata={
            "kodi_id": media_id,
            "file": row.get("file") or "",
            "is_default": True,
            "cast": people,
            "people": people,
            "show_title": row.get("showtitle"),
            "director": ", ".join(row.get("director") or []),
            "audio_streams": audio,
            "video_streams": video,
            "subtitles": subtitles,
            "streamdetails": streams,
            "trailers": [row["trailer"]] if row.get("trailer") else [],
            "total_episodes": total_episodes,
            "watched_episodes": watched_episodes,
        },
    )


async def fetch_kodi_snapshot(player_config, progress=None):
    """Read an entire consistent-as-possible API snapshot, never a partial one.

    Caller probes reachability with a 1-second deadline before this potentially
    long transfer. Once it starts, use normal request timeout and reject any
    truncated page/total inconsistency so local records are never erased.
    """
    records = []
    async with connection(player_config, timeout=30.0) as rpc:
        for kind, (method, key, _id_field, properties) in FIELDS.items():
            start = 0
            while True:
                payload = await rpc(method, {
                    "properties": properties,
                    "limits": {"start": start, "end": start + PAGE_SIZE},
                })
                if not isinstance(payload, dict) or not isinstance(payload.get("limits"), dict):
                    raise ConnectorError(f"Kodi returned an incomplete {method} response.")
                items = payload.get(key, [])
                limits = payload["limits"]
                total = limits.get("total")
                if (not isinstance(items, list) or type(total) is not int or total < 0
                        or len(items) > PAGE_SIZE or (total > start and not items)):
                    raise ConnectorError(f"Kodi returned an incomplete {method} page.")
                records.extend(normalize_jsonrpc(kind, item) for item in items)
                start += len(items)
                if progress:
                    progress({"movie": "movies", "tvshow": "series", "episode": "episodes"}[kind],
                             min(start, total), total)
                if start >= total:
                    break
    return records
