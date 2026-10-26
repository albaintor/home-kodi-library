"""Read-only snapshots of Kodi's versioned MyVideos MariaDB databases.

The SQL views, artwork and identifiers are queried directly. No running Kodi
instance or Home Assistant entity is required to browse the resulting catalogue.
"""

import math
import re
import socket
from collections import defaultdict
from collections.abc import Callable
from time import monotonic
from typing import Any
from urllib.parse import unquote
from xml.etree.ElementTree import ParseError

import pymysql
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from ..media_formats import stream_metadata
from ..people import youtube_url
from ..utils import date_value, identifiers, integer, number, plain_text, positive
from .base import ConnectorError, Record


def artwork_candidates(value: Any) -> list[str]:
    if not value:
        return []
    value = str(value).strip()
    if value.startswith("image://"):
        value = unquote(value[8:].rstrip("/")).split("|", 1)[0]
    if not value.startswith("<"):
        return [value]
    try:
        root = fromstring("<images>" + value + "</images>", forbid_dtd=True)
        return [x.text.strip() for x in root.iter("thumb") if x.text and x.text.strip()]
    except (ParseError, DefusedXmlException):
        return []


def genre_list(value: Any) -> list[str]:
    return [v.strip() for v in str(value or "").split(" / ") if v.strip()]


def normalize_row(
    kind: str,
    row: dict[str, Any],
    art: dict[str, str] | None = None,
    all_ids: dict[str, str] | None = None,
) -> Record:
    """Keep file-specific movie versions and preserve unknown optional columns."""
    art = art or {}
    media_id = row[{"movie": "idMovie", "tvshow": "idShow", "episode": "idEpisode"}[kind]]
    file_id = row.get("videoVersionIdFile") or row.get("idFile")
    key = f"{kind}:{media_id}" + (f":{file_id}" if kind == "movie" else "")
    ids = dict(all_ids or {})
    if row.get("uniqueid_type") and row.get("uniqueid_value"):
        ids.setdefault(row["uniqueid_type"], row["uniqueid_value"])
    runtime_seconds = (
        number(row.get("c09" if kind == "episode" else "c11")) if kind != "tvshow" else 0
    )
    premiered = str(row.get("premiered") or row.get("c05" if kind != "movie" else "c07") or "")
    play_count = int(number(row.get("playCount"))) if kind != "tvshow" else None
    if kind == "tvshow":
        total_count = int(number(row.get("totalCount")))
        watched_count = int(number(row.get("watchedcount")))
        watched = watched_count >= total_count if total_count > 0 else None
    else:
        watched = play_count > 0
    original = row.get("c16" if kind == "movie" else "c14" if kind == "episode" else "c09")
    path = str(row.get("strPath") or "") + str(
        row.get("strFileName") or row.get("strFilename") or ""
    )
    genres = genre_list(
        row.get("genre") if kind == "episode" else row.get("c14" if kind == "movie" else "c08")
    )
    poster = artwork_candidates(art.get("poster") or art.get("thumb"))
    poster.extend(artwork_candidates(row.get("c08" if kind == "movie" else "c06")))
    fanart = artwork_candidates(art.get("fanart"))
    fanart.extend(
        artwork_candidates(row.get("c20" if kind == "movie" else "c11" if kind == "tvshow" else ""))
    )
    rating = positive(row.get("rating"))
    record = Record(
        external_key=key,
        kind=kind,
        title=str(row.get("c00") or row.get("strTitle") or "Untitled"),
        original_title=str(original) if original else None,
        sort_title=str(row.get("c10") or row.get("c00") or "") if kind == "movie" else None,
        year=integer(premiered[:4]),
        plot=plain_text(row.get("c01")),
        runtime_minutes=math.ceil(runtime_seconds / 60) if runtime_seconds > 0 else None,
        rating=rating,
        user_rating=positive(row.get("userrating")),
        identifiers=identifiers(ids),
        genres=genres,
        collections=[str(row["strSet"])] if row.get("strSet") else [],
        date_added=date_value(row.get("dateAdded")),
        last_played=date_value(row.get("lastPlayed")),
        watched=watched,
        play_count=play_count,
        resume_seconds=number(row.get("resumeTimeInSeconds")),
        total_seconds=number(row.get("totalTimeInSeconds")) or runtime_seconds,
        poster=list(dict.fromkeys(poster)),
        fanart=list(dict.fromkeys(fanart)),
        parent_key=f"tvshow:{row['idShow']}"
        if kind == "episode" and row.get("idShow") is not None
        else None,
        season=int(number(row.get("c12"))) if kind == "episode" else None,
        episode=int(number(row.get("c13"))) if kind == "episode" else None,
        metadata={
            "kodi_id": int(media_id),
            "file_id": int(file_id) if file_id is not None else None,
            "file": path,
            "version": row.get("videoVersionTypeName"),
            "is_default": bool(row["isDefaultVersion"])
            if row.get("isDefaultVersion") is not None
            else True,
            "show_title": row.get("strTitle") if kind == "episode" else None,
            "director": row.get("c15" if kind == "movie" else "c10" if kind == "episode" else ""),
            "trailers": [trailer]
            if kind == "movie" and (trailer := youtube_url(str(row.get("c19") or "")))
            else [],
            "studio": row.get("c18" if kind == "movie" else "studio"),
            "rating_source": row.get("rating_type"),
            "total_episodes": int(number(row.get("totalCount"))) if kind == "tvshow" else None,
            "watched_episodes": int(number(row.get("watchedcount"))) if kind == "tvshow" else None,
        },
    )
    return record


class KodiConnector:
    def __init__(
        self,
        config: dict[str, Any],
        progress: Callable[[str, int, int], None] | None = None,
    ):
        self.config = config
        self.database: str | None = None
        self.snapshot_report: dict[str, Any] = {}
        self.progress = progress
        self._progress_phase = None
        self._last_progress_write = 0.0

    def _report(self, phase: str, current: int = 0, total: int = 0) -> None:
        # Never open a separate SQLite transaction for every hundredth row.
        # Persist phase changes and completion, otherwise at most twice/second.
        if self.progress is None:
            return
        timestamp = monotonic()
        if (
            phase == self._progress_phase
            and timestamp - self._last_progress_write < 0.5
            and (not total or current < total)
        ):
            return
        self._progress_phase = phase
        self._last_progress_write = timestamp
        self.progress(phase, current, total)

    def fetch(self) -> list[Record]:
        return self._read(self._snapshot)

    def probe(self) -> dict[str, Any]:
        """Check authentication, the selected schema and view SELECT permissions."""
        return self._read(self._probe)

    def discover_databases(self) -> dict[str, Any]:
        """List the user's visible MyVideos schemas without choosing or changing one.

        Intentionally bypasses SELECTing the configured database, which might
        not exist after an upgrade. This also works with a read-only SQL account.
        """
        return self._read(self._database_discovery, listing_only=True)

    @staticmethod
    def _visible_video_databases(cursor: Any) -> list[tuple[int, str]]:
        cursor.execute("SHOW DATABASES")
        visible = [str(next(iter(row.values()))) for row in cursor.fetchall()]
        choices = []
        for name in visible:
            match = re.fullmatch(r"MyVideos(\d+)", name, flags=re.IGNORECASE)
            if match:
                choices.append((int(match.group(1)), name))
        return sorted(choices, key=lambda item: (item[0], item[1]), reverse=True)

    def _database_discovery(self, cursor: Any) -> dict[str, Any]:
        databases = self._visible_video_databases(cursor)
        selected = self.config.get("database") or None
        return {
            "databases": [{"name": name, "version": number} for number, name in databases],
            "automatic_database": databases[0][1] if databases else None,
            "selected_database": selected,
        }

    def check_movie_ids(self, movie_ids: list[int]) -> dict[str, Any]:
        """Check a film's IDs in Kodi's real movie table, not its derived view.

        The same database-selection logic is used by synchronization. Values
        are bound SQL parameters and no user-supplied SQL identifiers are used.
        """
        ids = sorted({int(value) for value in movie_ids if int(value) > 0})
        if len(ids) > 100:
            raise ConnectorError("At most 100 Kodi movie IDs may be checked at once.")

        def check(cursor):
            if not ids:
                return {"database": self.database, "movie_ids_present": []}
            marks = ",".join("%s" for _ in ids)
            cursor.execute(f"SELECT idMovie FROM movie WHERE idMovie IN ({marks})", ids)
            present = sorted({int(row["idMovie"]) for row in cursor.fetchall()})
            return {"database": self.database, "movie_ids_present": present}

        return self._read(check)

    def inspect_movie_links(self, movie_ids: list[int]) -> dict[str, Any]:
        """Inspect retained movie rows and their database file/path links.

        A Kodi database does not track the physical existence of SMB/NFS
        files. Missing SQL links are authoritative; existing links are not
        proof that a file remains available on storage.
        """
        ids = sorted({int(value) for value in movie_ids if int(value) > 0})
        if len(ids) > 100:
            raise ConnectorError("At most 100 Kodi movie IDs may be inspected.")

        def inspect(cursor):
            if not ids:
                return {"database": self.database, "movies": {}}
            cursor.execute("SHOW FULL TABLES")
            tables = {str(next(iter(row.values()))) for row in cursor.fetchall()}
            if not {"movie", "files", "path"}.issubset(tables):
                raise ConnectorError(
                    "The Kodi database must expose movie, files and path for diagnostics."
                )
            cursor.execute("SHOW COLUMNS FROM `path`")
            path_cols = {str(row["Field"]).casefold() for row in cursor.fetchall()}
            optional = [
                f"p.`{column}` AS `{alias}`"
                for column, alias in (
                    ("exclude", "excluded_from_scan"),
                    ("noUpdate", "scan_disabled"),
                )
                if column.casefold() in path_cols
            ]
            marks = ",".join("%s" for _ in ids)
            sql = (
                "SELECT m.idMovie, m.c00 AS title, m.idFile AS default_file_id, "
                "f.idFile AS resolved_file_id, f.strFileName, p.strPath"
                + (", " + ", ".join(optional) if optional else "")
                + " FROM movie m "
                "LEFT JOIN files f ON f.idFile=m.idFile "
                "LEFT JOIN path p ON p.idPath=f.idPath "
                f"WHERE m.idMovie IN ({marks})"
            )
            cursor.execute(sql, ids)
            movies = {}
            for row in cursor.fetchall():
                movie_id = int(row["idMovie"])
                filename = str(row.get("strFileName") or "")
                path = str(row.get("strPath") or "")
                movies[movie_id] = {
                    "title": str(row.get("title") or ""),
                    "default_file_id": row.get("default_file_id"),
                    "file_row_exists": row.get("resolved_file_id") is not None,
                    "path_row_exists": row.get("strPath") is not None,
                    "file": path + filename if filename else None,
                    "excluded_from_scan": row.get("excluded_from_scan"),
                    "scan_disabled": row.get("scan_disabled"),
                    "versions": [],
                }
            if "videoversion" in tables:
                cursor.execute(
                    "SELECT v.idMedia, v.idFile, v.itemType, "
                    "f.idFile AS resolved_file_id, f.strFileName, p.strPath "
                    "FROM videoversion v "
                    "LEFT JOIN files f ON f.idFile=v.idFile "
                    "LEFT JOIN path p ON p.idPath=f.idPath "
                    f"WHERE v.media_type='movie' AND v.idMedia IN ({marks})",
                    ids,
                )
                for row in cursor.fetchall():
                    movie = movies.get(int(row["idMedia"]))
                    if movie is not None:
                        filename = str(row.get("strFileName") or "")
                        movie["versions"].append(
                            {
                                "file_id": row.get("idFile"),
                                "item_type": row.get("itemType"),
                                "file_row_exists": row.get("resolved_file_id") is not None,
                                "path_row_exists": row.get("strPath") is not None,
                                "file": str(row.get("strPath") or "") + filename
                                if filename
                                else None,
                            }
                        )
            # Exactly the default Kodi movie-navigation predicate on MyVideos:
            # CVideoDatabase::GetFilter(videodb://movies/titles/) adds
            # movie_view.isDefaultVersion = 1 (non-flattened versions).
            if "movie_view" not in tables:
                raise ConnectorError("Kodi movie_view is unavailable.")
            cursor.execute("SHOW COLUMNS FROM `movie_view`")
            view_columns = {str(row["Field"]).casefold() for row in cursor.fetchall()}
            has_default = "isdefaultversion" in view_columns
            rule = (
                "movie_view.isDefaultVersion = 1"
                if has_default
                else "legacy movie_view (no isDefaultVersion column)"
            )
            sql = f"SELECT DISTINCT idMovie FROM movie_view WHERE idMovie IN ({marks})" + (
                " AND isDefaultVersion = 1" if has_default else ""
            )
            cursor.execute(sql, ids)
            listed = sorted({int(row["idMovie"]) for row in cursor.fetchall()})
            return {
                "database": self.database,
                "movies": movies,
                "library_visible_movie_ids": listed,
                "library_rule": rule,
                "library_verified": has_default,
            }

        return self._read(inspect)

    def _read(self, operation, *, listing_only: bool = False):
        config = self.config
        if not config.get("host") or not config.get("username"):
            raise ConnectorError("Configure the MariaDB hostname and read-only username.")
        if any(c in config["host"] for c in ("/", "@", "?", "#")):
            raise ConnectorError("Enter a MariaDB hostname or IP address, not a URL.")
        requested = config.get("database", "")
        if requested and not re.fullmatch(r"[A-Za-z0-9_]+", requested):
            raise ConnectorError("Invalid Kodi database name.")
        ssl = {"check_hostname": True, "verify_mode": True} if config.get("tls") else None
        if ssl and config.get("ca_file"):
            ssl["ca"] = config["ca_file"]
        try:
            with pymysql.connect(
                host=config["host"],
                port=config.get("port", 3306),
                user=config["username"],
                password=config.get("password", ""),
                connect_timeout=10,
                read_timeout=60,
                write_timeout=10,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
                ssl=ssl,
            ) as conn:
                with conn.cursor() as cursor:
                    if listing_only:
                        return operation(cursor)
                    if requested:
                        database = requested
                    else:
                        choices = self._visible_video_databases(cursor)
                        if not choices:
                            raise ConnectorError(
                                "No accessible MyVideos database. Specify its name or grant SELECT permission."
                            )
                        database = choices[0][1]
                    conn.select_db(database)
                    self.database = database
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                    result = operation(cursor)
                    conn.rollback()  # this connector never commits to the Kodi database
                    return result
        except pymysql.MySQLError as exc:
            code = exc.args[0] if exc.args else "unknown"
            original = getattr(exc, "original_exception", None)
            if isinstance(original, socket.gaierror) or "name resolution" in str(exc).lower():
                message = "MariaDB hostname cannot be resolved. Check container DNS or use its IP address."
            else:
                message = {
                    1044: "MariaDB account cannot access this database. Check its SELECT grants.",
                    1045: "MariaDB rejected authentication. Check username/password and the account's permitted client hosts.",
                    1049: "The specified MariaDB database does not exist. Check the MyVideos name.",
                    1142: "MariaDB denied SELECT access to a Kodi view or table. Check read-only grants.",
                    1146: "A required Kodi view or table is missing. Check the MyVideos schema.",
                    1356: "A Kodi view is invalid or inaccessible. Check its schema and SELECT grants.",
                    2003: "Cannot connect to MariaDB. Check host, port, server listening address and NAS firewall.",
                    2005: "MariaDB hostname cannot be resolved. Check container DNS or use its IP address.",
                    2013: "MariaDB connection was interrupted or timed out.",
                    2026: "MariaDB TLS verification failed. Check TLS settings and the CA certificate.",
                }.get(
                    code, "MariaDB query failed. Check database version, schema and SELECT grants."
                )
            raise ConnectorError(f"{message} (MariaDB code {code})") from exc

    def _probe(self, cursor) -> dict[str, Any]:
        cursor.execute("SHOW FULL TABLES")
        tables = {str(next(iter(r.values()))) for r in cursor.fetchall()}
        if "movie_view" not in tables:
            raise ConnectorError(
                "The selected database has no movie_view. Select a Kodi MyVideos database."
            )
        available = []
        for kind, view, id_field in (
            ("movie", "movie_view", "idMovie"),
            ("tvshow", "tvshow_view", "idShow"),
            ("episode", "episode_view", "idEpisode"),
        ):
            if view not in tables:
                continue
            cursor.execute(f"SELECT `{id_field}`,c00 FROM `{view}` LIMIT 1")
            cursor.fetchone()
            available.append(kind)
        for table, columns in (
            ("art", "media_id,media_type,type,url"),
            ("uniqueid", "media_id,media_type,type,value"),
            ("streamdetails", "idFile,iStreamType"),
            ("actor", "actor_id,name"),
            ("actor_link", "actor_id,media_id,media_type,role,cast_order"),
            ("director_link", "actor_id,media_id,media_type"),
            ("writer_link", "actor_id,media_id,media_type"),
        ):
            if table in tables:
                cursor.execute(f"SELECT {columns} FROM `{table}` LIMIT 1")
                cursor.fetchone()
        return {"database": self.database, "available_media": available}

    def _snapshot(self, cursor: Any) -> list[Record]:
        cursor.execute("SHOW FULL TABLES")
        tables = {str(next(iter(r.values()))) for r in cursor.fetchall()}
        if "movie_view" not in tables or "movie" not in tables:
            raise ConnectorError(
                "This database must expose both Kodi movie and movie_view. "
                "Grant read-only SELECT access to the movie table."
            )
        # movie_view is a derived view which varies between Kodi versions.
        # movie is authoritative for deletions, so never publish an ID absent
        # from the real table, even if a view contains an obsolete row.
        cursor.execute("SELECT idMovie FROM movie")
        canonical_movie_ids = {int(row["idMovie"]) for row in cursor.fetchall()}
        orphan_count = 0
        orphan_examples = []
        not_in_library_count = 0
        not_in_library_examples = []
        library_movie_ids: set[int] | None = None
        library_rule = "legacy movie_view (no isDefaultVersion column)"
        self._report("loading")
        artwork: dict[tuple[str, int], dict[str, str]] = defaultdict(dict)
        if "art" in tables:
            cursor.execute(
                "SELECT media_id, media_type, type, url FROM art WHERE media_type IN ('movie','tvshow','episode') AND type IN ('poster','thumb','fanart')"
            )
            for row in cursor.fetchall():
                artwork[(row["media_type"], int(row["media_id"]))][row["type"]] = row["url"]
        uniqueids: dict[tuple[str, int], dict[str, str]] = defaultdict(dict)
        if "uniqueid" in tables:
            cursor.execute(
                "SELECT media_id, media_type, type, value FROM uniqueid WHERE media_type IN ('movie','tvshow','episode')"
            )
            for row in cursor.fetchall():
                uniqueids[(row["media_type"], int(row["media_id"]))][row["type"]] = row["value"]
        result = []
        streams: dict[int, list[dict]] = defaultdict(list)
        if "streamdetails" in tables:
            cursor.execute("SELECT * FROM streamdetails")
            for row in cursor.fetchall():
                streams[int(row["idFile"])].append(row)
        casts: dict[tuple[str, int], list[dict]] = defaultdict(list)
        portraits = defaultdict(list)
        if "art" in tables:
            cursor.execute("SELECT media_id,url FROM art WHERE media_type='actor' AND type='thumb'")
            for row in cursor.fetchall():
                portraits[int(row["media_id"])].extend(artwork_candidates(row["url"]))
        if {"actor", "actor_link"}.issubset(tables):
            cursor.execute(
                "SELECT l.media_type,l.media_id,a.actor_id,a.name,l.role,l.cast_order "
                "FROM actor_link l JOIN actor a ON a.actor_id=l.actor_id "
                "WHERE l.media_type IN ('movie','tvshow','episode')"
            )
            for row in cursor.fetchall():
                if row.get("name"):
                    casts[(row["media_type"], int(row["media_id"]))].append(
                        {
                            "name": str(row["name"]),
                            "role": row.get("role") or None,
                            "order": row.get("cast_order"),
                            "category": "cast",
                            "photos": portraits.get(int(row.get("actor_id") or 0), []),
                        }
                    )
        # Global ORDER BY over all actor links forces a massive MariaDB sort.
        # Sorting the small cast list for each title is cheaper and preserves
        # the visible cast order (cast_order NULLs last, then name).
        for members in casts.values():
            members.sort(
                key=lambda member: (
                    member["order"] is None,
                    member["order"] if member["order"] is not None else 0,
                    member["name"],
                )
            )
        crews: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for table, category in (("director_link", "director"), ("writer_link", "writer")):
            if {"actor", table}.issubset(tables):
                cursor.execute(
                    f"SELECT l.media_type,l.media_id,a.actor_id,a.name FROM {table} l JOIN actor a ON a.actor_id=l.actor_id WHERE l.media_type IN ('movie','tvshow','episode')"
                )
                for row in cursor.fetchall():
                    crews[(row["media_type"], int(row["media_id"]))].append(
                        {
                            "name": row["name"],
                            "category": category,
                            "photos": portraits.get(int(row["actor_id"]), []),
                        }
                    )
        for members in crews.values():
            members.sort(key=lambda member: member["name"])
        view_fetch_ms = {}
        for kind, view, id_field in (
            ("movie", "movie_view", "idMovie"),
            ("tvshow", "tvshow_view", "idShow"),
            ("episode", "episode_view", "idEpisode"),
        ):
            if view not in tables:
                continue
            phase = {"movie": "movies", "tvshow": "series", "episode": "episodes"}[kind]
            self._report(phase)
            # View names are fixed constants, never client-supplied SQL identifiers.
            cursor.execute(f"SHOW COLUMNS FROM `{view}`")
            columns = {row["Field"].casefold() for row in cursor.fetchall()}
            condition = ""
            if kind == "movie":
                has_default = "isdefaultversion" in columns
                has_asset_type = "videoversiontypeitemtype" in columns
                # CVideoDatabase::GetFilter, videodb://movies/titles/, adds
                # "isDefaultVersion = 1" to movie_view (non-flattened view).
                # The view may still contain rows for a movie whose default
                # version has been detached. Such a movie is NOT listed by Kodi.
                if has_default:
                    default_fields = (
                        "idMovie, videoVersionTypeItemType" if has_asset_type else "idMovie"
                    )
                    cursor.execute(
                        f"SELECT {default_fields} FROM `movie_view` WHERE isDefaultVersion = 1"
                    )
                    default_rows = cursor.fetchall()
                    library_movie_ids = {int(row["idMovie"]) for row in default_rows}
                    library_rule = "movie_view.isDefaultVersion = 1"
                if has_asset_type and has_default:
                    # Import all versions of listed movies, not only their
                    # default file. Exclude EXTRAs by using the Kodi schema's
                    # version item type, taken from its default version rows.
                    version_types = {
                        int(row["videoVersionTypeItemType"])
                        for row in default_rows
                        if row["videoVersionTypeItemType"] is not None
                    }
                    if len(version_types) > 1 or not version_types.issubset({0, 1}):
                        raise ConnectorError(
                            "Cannot identify movie versions in this Kodi schema. "
                            "Default movie files have inconsistent asset types."
                        )
                    condition = " WHERE videoVersionTypeItemType IS NULL"
                    if version_types:
                        condition += f" OR videoVersionTypeItemType = {version_types.pop()}"
                elif has_asset_type:
                    # Older/nonstandard Kodi views cannot reliably tell
                    # which movie is in the usual library navigation.
                    library_rule = "legacy movie_view (no isDefaultVersion column)"
            fetch_started = monotonic()
            cursor.execute(f"SELECT * FROM `{view}`{condition}")
            rows = cursor.fetchall()
            view_fetch_ms[kind] = round((monotonic() - fetch_started) * 1000)
            total = len(rows)
            self._report(phase, 0, total)
            report_every = max(1, total // 100)
            seen = set()
            for index, row in enumerate(rows, start=1):
                identity = (kind, int(row[id_field]))
                if kind == "movie" and identity[1] not in canonical_movie_ids:
                    orphan_count += 1
                    if len(orphan_examples) < 10:
                        orphan_examples.append(
                            {
                                "kodi_id": identity[1],
                                "title": str(row.get("c00") or "Untitled")[:160],
                            }
                        )
                    if index % report_every == 0 or index == total:
                        self._report(phase, index, total)
                    continue
                if (
                    kind == "movie"
                    and library_movie_ids is not None
                    and identity[1] not in library_movie_ids
                ):
                    not_in_library_count += 1
                    if len(not_in_library_examples) < 10:
                        not_in_library_examples.append(
                            {"kodi_id": identity[1], "title": str(row.get("c00") or "")[:160]}
                        )
                    if index % report_every == 0 or index == total:
                        self._report(phase, index, total)
                    continue
                record = normalize_row(kind, row, artwork.get(identity), uniqueids.get(identity))
                crew = crews.get(identity, [])
                if not any(p["category"] == "director" for p in crew):
                    crew = [
                        *crew,
                        *(
                            {"name": name, "category": "director"}
                            for name in genre_list(record.metadata.get("director"))
                        ),
                    ]
                record.metadata["people"] = [*casts.get(identity, []), *crew]
                file_id = record.metadata.get("file_id")
                technical = stream_metadata(
                    streams.get(file_id, []), filename=record.metadata.get("file")
                )
                record.formats = technical.pop("formats")
                record.metadata.update(technical)
                record.metadata["cast"] = casts.get(identity, [])
                record.metadata["audio_tracks"] = [
                    " · ".join(str(v) for v in (s["codec"], s["channels"], s["language"]) if v)
                    for s in technical["audio_streams"]
                ]
                if record.external_key not in seen:
                    seen.add(record.external_key)
                    record.metadata["database"] = self.database
                    result.append(record)
                if index % report_every == 0 or index == total:
                    self._report(phase, index, total)
            if not total:
                self._report(phase, 0, 0)
        self.snapshot_report = {
            "movie_table_count": len(canonical_movie_ids),
            "orphaned_movie_view_rows": orphan_count,
            "orphaned_movie_view_examples": orphan_examples,
            "library_verified": library_movie_ids is not None,
            "library_rule": library_rule,
            "library_movie_count": len(library_movie_ids)
            if library_movie_ids is not None
            else None,
            "library_excluded_editions": not_in_library_count,
            "library_excluded_examples": not_in_library_examples,
            "sql_view_fetch_ms": view_fetch_ms,
        }
        return result
