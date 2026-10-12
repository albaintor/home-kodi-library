"""Transactional local catalogue. SQLite never shares Kodi's schema or writes to MariaDB."""

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .connectors.base import Record
from .media_formats import canonical_format
from .people import person_view, youtube_url
from .utils import fold, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL DEFAULT '{}',
 last_sync TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS works (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
 sort_title TEXT NOT NULL DEFAULT '', original_title TEXT, year INTEGER, plot TEXT,
 runtime_minutes INTEGER, rating REAL, user_rating REAL, date_added TEXT, last_played TEXT,
 watch_status TEXT NOT NULL DEFAULT 'unknown', play_count INTEGER, resume_seconds REAL DEFAULT 0,
 total_seconds REAL DEFAULT 0, parent_id TEXT REFERENCES works(id) ON DELETE SET NULL,
 season INTEGER, episode INTEGER, genres TEXT DEFAULT '[]', formats TEXT DEFAULT '[]',
 collections TEXT DEFAULT '[]', source_types TEXT DEFAULT '[]', poster TEXT DEFAULT '[]',
 fanart TEXT DEFAULT '[]', search_text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS records (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 external_key TEXT NOT NULL, payload TEXT NOT NULL,
 UNIQUE(source_id,external_key)
);
CREATE INDEX IF NOT EXISTS records_work ON records(work_id);
CREATE INDEX IF NOT EXISTS records_source ON records(source_id);
CREATE TABLE IF NOT EXISTS identifiers (
 work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, namespace TEXT NOT NULL, value TEXT NOT NULL,
 PRIMARY KEY(work_id,namespace,value)
);
CREATE INDEX IF NOT EXISTS identifier_lookup ON identifiers(kind,namespace,value);
CREATE INDEX IF NOT EXISTS work_sort_title ON works(sort_title,id);
CREATE INDEX IF NOT EXISTS work_date ON works(date_added,id);
CREATE INDEX IF NOT EXISTS work_kind_watch ON works(kind,watch_status);
CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(work_id UNINDEXED, search_text, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS players (
 id TEXT PRIMARY KEY, name TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, result TEXT, error TEXT,
 progress_phase TEXT, progress_current INTEGER NOT NULL DEFAULT 0,
 progress_total INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS personal (
 work_id TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
 user_rating REAL, watched INTEGER
);
CREATE TABLE IF NOT EXISTS device_tokens (
 token_hash TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrichment (
 work_id TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
 payload TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lists (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS list_items (
 list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
 work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, PRIMARY KEY(list_id,work_id)
);
CREATE INDEX IF NOT EXISTS list_order ON list_items(list_id,position);
PRAGMA user_version = 2;
"""


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError(
                    "Catalogue schema is newer than this application; do not downgrade."
                )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            # Compatible migration for existing NAS installations (schema v2).
            job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for column, definition in (
                ("progress_phase", "TEXT"),
                ("progress_current", "INTEGER NOT NULL DEFAULT 0"),
                ("progress_total", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in job_columns:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
            conn.execute(
                "UPDATE jobs SET status='failed',finished_at=?,error='Application restarted before the job completed' WHERE status IN ('pending','running')",
                (now(),),
            )
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def device_tokens(self) -> list[dict[str, str]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT token_hash,name,role,created_at FROM device_tokens ORDER BY created_at DESC"
                )
            ]

    def register_device_token(self, token_hash: str, name: str, role: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO device_tokens(token_hash,name,role,created_at) VALUES(?,?,?,?)",
                (token_hash, name, role, now()),
            )

    def revoke_device_token(self, token_hash: str) -> bool:
        with self.connect() as conn:
            return bool(
                conn.execute("DELETE FROM device_tokens WHERE token_hash=?", (token_hash,)).rowcount
            )

    def sources(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT s.*,COUNT(r.id) AS item_count FROM sources s LEFT JOIN records r ON r.source_id=s.id GROUP BY s.id ORDER BY s.name"
            ).fetchall()
            return [self._source_view(r) for r in rows]

    @staticmethod
    def _source_view(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        config = json.loads(value["config"])
        config["has_password"] = bool(config.pop("password", ""))
        value["config"] = config
        value["enabled"] = bool(value["enabled"])
        return value

    def source(self, source_id: str, *, private: bool = False) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM records r WHERE r.source_id=s.id) AS item_count FROM sources s WHERE id=?",
                (source_id,),
            ).fetchone()
            if row is None:
                return None
            if private:
                value = dict(row)
                value["config"] = json.loads(value["config"])
                value["enabled"] = bool(value["enabled"])
                return value
            return self._source_view(row)

    def save_source(self, data: dict[str, Any], source_id: str | None = None) -> dict[str, Any]:
        source_id = source_id or str(uuid4())
        config = dict(data.get("config", {}))
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT type,config FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if existing and existing["type"] != data["type"]:
                raise ValueError("The source type cannot be changed.")
            if config.get("password") is None:
                config["password"] = (
                    json.loads(existing["config"]).get("password", "") if existing else ""
                )
            conn.execute(
                "INSERT INTO sources(id,name,type,enabled,config) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,config=excluded.config",
                (
                    source_id,
                    data["name"],
                    data["type"],
                    bool(data.get("enabled", True)),
                    encode(config),
                ),
            )
        return self.source(source_id)

    def delete_source(self, source_id: str) -> None:
        with self.connect() as conn:
            affected = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT work_id FROM records WHERE source_id=?", (source_id,)
                )
            ]
            conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
            for work_id in affected:
                self._rebuild(conn, work_id)
            self._prune(conn)

    def apply_snapshot(self, source_id: str, records: list[Record]) -> dict[str, Any]:
        """Atomic replacement per source; stable edition IDs across subsequent imports."""
        with self.connect() as conn:
            source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source:
                raise ValueError("Source no longer exists.")
            old = {
                r["external_key"]: r
                for r in conn.execute("SELECT * FROM records WHERE source_id=?", (source_id,))
            }
            # Only changed/removed works need their metadata and FTS rebuilt.
            # A no-op snapshot must not rewrite thousands of SQLite rows.
            affected = set()
            seen = set()
            resolved = {}
            inserted = updated = unchanged = conflicts = reassigned = 0
            # Parents first ensures episode links are available in the same import.
            for record in sorted(records, key=lambda r: r.kind == "episode"):
                if record.external_key in seen:
                    raise ValueError("The source snapshot contains duplicate identifiers.")
                seen.add(record.external_key)
                payload = asdict(record)
                existing = old.get(record.external_key)
                if existing:
                    work_id, record_id = existing["work_id"], existing["id"]
                    # Identifiers can change after a rescrape: never overwrite metadata
                    # on a previously linked CLZ work if the Kodi IDs were recycled.
                    previous = json.loads(existing["payload"])
                    changed_identity = previous["kind"] != record.kind or any(
                        k in record.identifiers and v != record.identifiers[k]
                        for k, v in previous.get("identifiers", {}).items()
                    )
                    if changed_identity:
                        work_id, conflict = self._match_work(conn, record, exclude=work_id)
                        conflicts += conflict
                        affected.add(existing["work_id"])
                        reassigned += work_id != existing["work_id"]
                    updated += 1
                else:
                    work_id, conflict = self._match_work(conn, record)
                    record_id = str(uuid4())
                    conflicts += conflict
                    inserted += 1
                resolved[record.external_key] = work_id
                if record.parent_key:
                    payload["parent_id"] = resolved.get(record.parent_key)
                    if not payload["parent_id"]:
                        parent = conn.execute(
                            "SELECT work_id FROM records WHERE source_id=? AND external_key=?",
                            (source_id, record.parent_key),
                        ).fetchone()
                        payload["parent_id"] = parent[0] if parent else None
                encoded = encode(payload)
                if existing and existing["work_id"] == work_id and existing["payload"] == encoded:
                    unchanged += 1
                    continue
                affected.add(work_id)
                conn.execute(
                    "INSERT INTO records(id,work_id,source_id,external_key,payload) VALUES(?,?,?,?,?) ON CONFLICT(source_id,external_key) DO UPDATE SET work_id=excluded.work_id,payload=excluded.payload",
                    (record_id, work_id, source_id, record.external_key, encoded),
                )
                # Register immediately so two physical editions in this same import
                # can resolve to one work. Rebuild removes obsolete identifiers.
                for namespace, value in record.identifiers.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO identifiers VALUES(?,?,?,?)",
                        (work_id, record.kind, namespace, value),
                    )
            deleted = 0
            removed_work_ids = set()
            removed_titles = []
            removed_title_set = set()
            for key, row in old.items():
                if key not in seen:
                    conn.execute("DELETE FROM records WHERE id=?", (row["id"],))
                    removed_work_ids.add(row["work_id"])
                    affected.add(row["work_id"])
                    title = json.loads(row["payload"]).get("title") or key
                    if title not in removed_title_set:
                        removed_title_set.add(title)
                        if len(removed_titles) < 20:
                            removed_titles.append(title)
                    deleted += 1
            # Assert a complete source snapshot, including removals. A failure
            # rolls back the transaction rather than publishing a stale edition.
            actual_keys = {
                row[0]
                for row in conn.execute(
                    "SELECT external_key FROM records WHERE source_id=?", (source_id,)
                )
            }
            if actual_keys != seen:
                raise RuntimeError("Snapshot reconciliation mismatch; transaction rolled back.")
            for work_id in affected:
                self._rebuild(conn, work_id)
            if deleted or reassigned:
                self._prune(conn)

            # An absent Kodi edition may have been the last source of a work,
            # or one of several editions of a film also owned in CLZ.
            removed_works = kept_clz_works = 0
            if source["type"] == "kodi":
                for work_id in removed_work_ids:
                    remaining_types = {
                        row[0]
                        for row in conn.execute(
                            "SELECT DISTINCT s.type FROM records r "
                            "JOIN sources s ON s.id=r.source_id WHERE r.work_id=?",
                            (work_id,),
                        )
                    }
                    if not remaining_types:
                        removed_works += 1
                    elif remaining_types == {"clz"}:
                        kept_clz_works += 1

            conn.execute(
                "UPDATE sources SET last_sync=?,last_error=NULL WHERE id=?", (now(), source_id)
            )
            return {
                "imported": len(records),
                "created_editions": inserted,
                "updated_editions": updated,
                "unchanged_editions": unchanged,
                "rebuilt_works": len(affected),
                "removed_editions": deleted,
                "removed_titles": removed_titles,
                "removed_titles_total": len(removed_title_set),
                "removed_works": removed_works,
                "kept_clz_works": kept_clz_works,
                "source_records_before": len(old),
                "source_records_after": len(actual_keys),
                "matching_conflicts": conflicts,
            }

    @staticmethod
    def _match_work(
        conn: sqlite3.Connection, record: Record, exclude: str | None = None
    ) -> tuple[str, int]:
        candidates = set()
        for namespace, value in record.identifiers.items():
            candidates.update(
                r[0]
                for r in conn.execute(
                    "SELECT work_id FROM identifiers WHERE kind=? AND namespace=? AND value=?",
                    (record.kind, namespace, value),
                )
                if r[0] != exclude
            )
        if len(candidates) == 1:
            candidate = next(iter(candidates))
            known: dict[str, set[str]] = defaultdict(set)
            for row in conn.execute(
                "SELECT namespace,value FROM identifiers WHERE work_id=?", (candidate,)
            ):
                known[row[0]].add(row[1])
            if not any(
                namespace in known and value not in known[namespace]
                for namespace, value in record.identifiers.items()
            ):
                return candidate, 0
        work_id = str(uuid4())
        conn.execute("INSERT INTO works(id,kind) VALUES(?,?)", (work_id, record.kind))
        # Similar titles are deliberately not enough to merge remakes/editions.
        return work_id, int(bool(candidates))

    @staticmethod
    def _rebuild(conn: sqlite3.Connection, work_id: str) -> None:
        rows = conn.execute(
            "SELECT r.payload,s.type FROM records r JOIN sources s ON s.id=r.source_id WHERE r.work_id=?",
            (work_id,),
        ).fetchall()
        conn.execute("DELETE FROM identifiers WHERE work_id=?", (work_id,))
        conn.execute("DELETE FROM works_fts WHERE work_id=?", (work_id,))
        if not rows:
            return
        entries = [(json.loads(row["payload"]), row["type"]) for row in rows]
        entries.sort(
            key=lambda p: (p[1] != "kodi", not p[0].get("metadata", {}).get("is_default", True))
        )
        primary = entries[0][0]

        def choose(name: str):
            return next(
                (p[name] for p, _ in entries if p.get(name) not in (None, "", [], {})), None
            )

        known_watch = [p for p, _ in entries if p.get("watched") is not None]
        kodi_watch = [p for p, t in entries if t == "kodi" and p.get("watched") is not None]
        progress = next(
            (p for p in kodi_watch if not p["watched"] and p.get("resume_seconds", 0) > 0), None
        )
        partial_show = any(
            p.get("metadata", {}).get("watched_episodes", 0) and not p["watched"]
            for p in known_watch
        )
        status = (
            "watched"
            if any(p["watched"] for p in known_watch)
            else "in_progress"
            if progress or partial_show
            else "unwatched"
            if known_watch
            else "unknown"
        )
        playback = progress or (kodi_watch[0] if kodi_watch else {})
        genres = sorted({v for p, _ in entries for v in p.get("genres", [])}, key=fold)
        fmts = sorted(
            {canonical_format(v) for p, _ in entries for v in p.get("formats", [])}, key=fold
        )
        collections = sorted({v for p, _ in entries for v in p.get("collections", [])}, key=fold)
        types = sorted({t for _, t in entries})
        search_text = " ".join(
            str(p.get(n) or "") for p, _ in entries for n in ("title", "original_title", "plot")
        )
        search_text += " " + " ".join(genres + collections)
        search_text += " " + " ".join(
            str(p.get("metadata", {}).get(n) or "")
            for p, _ in entries
            for n in ("director", "show_title", "tags", "cast", "people")
        )
        enrichment = conn.execute(
            "SELECT payload FROM enrichment WHERE work_id=?", (work_id,)
        ).fetchone()
        if enrichment:
            search_text += " " + " ".join(
                p["name"] for p in json.loads(enrichment[0]).get("people", [])
            )
        posters = list(dict.fromkeys(v for p, _ in entries for v in p.get("poster", [])))
        fanarts = list(dict.fromkeys(v for p, _ in entries for v in p.get("fanart", [])))
        values = {
            "kind": primary["kind"],
            "title": choose("title"),
            "sort_title": fold(choose("sort_title") or choose("title") or ""),
            **{
                n: choose(n)
                for n in (
                    "original_title",
                    "year",
                    "plot",
                    "runtime_minutes",
                    "rating",
                    "user_rating",
                    "date_added",
                    "parent_id",
                    "season",
                    "episode",
                )
            },
            "last_played": max(
                (p["last_played"] for p, _ in entries if p.get("last_played")), default=None
            ),
            "watch_status": status,
            "play_count": max((p.get("play_count") or 0 for p in known_watch), default=None),
            "resume_seconds": playback.get("resume_seconds", 0),
            "total_seconds": playback.get("total_seconds", 0),
            "genres": encode(genres),
            "formats": encode(fmts),
            "collections": encode(collections),
            "source_types": encode(types),
            "poster": encode(posters),
            "fanart": encode(fanarts),
            "search_text": fold(search_text),
        }
        personal = conn.execute("SELECT * FROM personal WHERE work_id=?", (work_id,)).fetchone()
        if personal:
            if personal["user_rating"] is not None:
                values["user_rating"] = personal["user_rating"]
            if personal["watched"] is not None:
                values["watch_status"] = "watched" if personal["watched"] else "unwatched"
                values["resume_seconds"] = 0
        conn.execute(
            "UPDATE works SET " + ",".join(f"{n}=?" for n in values) + " WHERE id=?",
            (*values.values(), work_id),
        )
        conn.execute(
            "INSERT INTO works_fts(work_id,search_text) VALUES(?,?)",
            (work_id, values["search_text"]),
        )
        for payload, _ in entries:
            for namespace, value in payload.get("identifiers", {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO identifiers VALUES(?,?,?,?)",
                    (work_id, primary["kind"], namespace, value),
                )

    @staticmethod
    def _prune(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE lists SET revision=revision+1 WHERE id IN (SELECT i.list_id FROM list_items i WHERE NOT EXISTS(SELECT 1 FROM records r WHERE r.work_id=i.work_id))"
        )
        conn.execute(
            "DELETE FROM works_fts WHERE work_id IN (SELECT id FROM works w WHERE NOT EXISTS(SELECT 1 FROM records r WHERE r.work_id=w.id))"
        )
        conn.execute(
            "DELETE FROM works WHERE NOT EXISTS(SELECT 1 FROM records r WHERE r.work_id=works.id)"
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for name in ("genres", "formats", "collections", "source_types"):
            value[name] = json.loads(value[name])
        value["formats"] = sorted({canonical_format(v) for v in value["formats"]}, key=fold)
        value["artwork"] = {
            name: f"/api/v1/media/{value['id']}/artwork/{name}" if json.loads(value[name]) else None
            for name in ("poster", "fanart")
        }
        for name in ("poster", "fanart", "search_text", "sort_title"):
            value.pop(name, None)
        return value

    def media(
        self,
        *,
        q: str = "",
        kind: str | None = None,
        watch_status: str | None = None,
        genre: str | None = None,
        format: str | list[str] | None = None,
        collection: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        parent_id: str | None = None,
        year: int | None = None,
        recent_days: int | None = None,
        sort: str = "date_added",
        order: str = "desc",
        limit: int = 60,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses, params = [], []
        words = re.findall(r"\w+", q, flags=re.UNICODE)
        if words:
            clauses.append("w.id IN (SELECT work_id FROM works_fts WHERE works_fts MATCH ?)")
            params.append(" AND ".join('"' + word.replace('"', '""') + '"*' for word in words))
        for name, value in (
            ("kind", kind),
            ("watch_status", watch_status),
            ("parent_id", parent_id),
            ("year", year),
        ):
            if value is not None:
                clauses.append(f"w.{name}=?")
                params.append(value)
        for name, value in (
            ("genres", genre),
            ("collections", collection),
            ("source_types", source_type),
        ):
            if value is not None:
                clauses.append(
                    f"EXISTS(SELECT 1 FROM json_each(w.{name}) WHERE value=? COLLATE NOCASE)"
                )
                params.append(value)
        requested_formats = [format] if isinstance(format, str) else (format or [])
        if len(requested_formats) > 20 or any(
            not isinstance(label, str) or not 1 <= len(label.strip()) <= 120
            for label in requested_formats
        ):
            raise ValueError("Invalid format filters.")
        for label in dict.fromkeys(canonical_format(value) for value in requested_formats):
            aliases = ("4K", "4K UHD") if label == "4K" else (label,)
            placeholders = ",".join("?" for _ in aliases)
            clauses.append(
                f"EXISTS(SELECT 1 FROM json_each(w.formats) "
                f"WHERE value COLLATE NOCASE IN ({placeholders}))"
            )
            params.extend(aliases)
        if source_id:
            clauses.append("EXISTS(SELECT 1 FROM records r WHERE r.work_id=w.id AND r.source_id=?)")
            params.append(source_id)
        if recent_days:
            clauses.append("w.date_added>=?")
            params.append(
                (datetime.now(UTC) - timedelta(days=recent_days)).isoformat(timespec="seconds")[:19]
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        fields = {
            "title": "sort_title",
            "date_added": "date_added",
            "year": "year",
            "rating": "rating",
            "user_rating": "user_rating",
            "last_played": "last_played",
        }
        if (
            sort not in fields
            or order not in ("asc", "desc")
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise ValueError("Invalid pagination or sort option.")
        column = fields[sort]
        with self.connect() as conn:
            conn.execute("BEGIN")  # count and page see the same snapshot
            total = conn.execute("SELECT COUNT(*) FROM works w" + where, params).fetchone()[0]
            rows = conn.execute(
                "SELECT w.* FROM works w"
                + where
                + f" ORDER BY w.{column} IS NULL,w.{column} {order},w.id ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return {
                "items": [self._summary(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
            }

    def detail(self, work_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
            if row is None:
                return None
            value = self._summary(row)
            ids: dict[str, list[str]] = defaultdict(list)
            for identity in conn.execute(
                "SELECT namespace,value FROM identifiers WHERE work_id=? ORDER BY namespace,value",
                (work_id,),
            ):
                ids[identity[0]].append(identity[1])
            editions = []
            for record in conn.execute(
                "SELECT r.*,s.name,s.type FROM records r JOIN sources s ON s.id=r.source_id WHERE work_id=? ORDER BY s.type DESC,r.external_key",
                (work_id,),
            ):
                payload = json.loads(record["payload"])
                editions.append(
                    {
                        "id": record["id"],
                        "source_id": record["source_id"],
                        "source_name": record["name"],
                        "source_type": record["type"],
                        "external_key": record["external_key"],
                        "identifiers": payload["identifiers"],
                        "metadata": {
                            **payload["metadata"],
                            "formats": payload["formats"],
                            "collections": payload["collections"],
                        },
                        **{
                            n: payload.get(n)
                            for n in (
                                "watched",
                                "play_count",
                                "resume_seconds",
                                "total_seconds",
                                "date_added",
                                "last_played",
                            )
                        },
                    }
                )
            source_people = []
            for edition in editions:
                metadata = edition["metadata"]
                source_people.extend(metadata.get("people", metadata.get("cast", [])))
            cached = conn.execute("SELECT * FROM enrichment WHERE work_id=?", (work_id,)).fetchone()
            enriched = json.loads(cached["payload"]) if cached else {}
            # Preserve source roles, fill portraits/IDs from matching TMDB names.
            people = []
            seen = set()
            for member in source_people + enriched.get("people", []):
                key = (
                    fold(member["name"]),
                    member.get("category", "cast"),
                )
                if key in seen:
                    if key[1] != "cast" and member.get("role"):
                        previous = next(
                            p for p in people if (fold(p["name"]), p.get("category", "cast")) == key
                        )
                        roles = list(
                            dict.fromkeys(filter(None, [previous.get("role"), member["role"]]))
                        )
                        previous["role"] = " · ".join(roles)
                    continue
                seen.add(key)
                match = next(
                    (
                        p
                        for p in enriched.get("people", [])
                        if fold(p["name"]) == key[0]
                        and not (
                            p.get("tmdb_id")
                            and member.get("tmdb_id")
                            and p["tmdb_id"] != member["tmdb_id"]
                        )
                    ),
                    {},
                )
                merged = {**match, **member}
                for field in ("imdb_id", "tmdb_id"):
                    merged[field] = member.get(field) or match.get(field)
                merged["photos"] = list(
                    dict.fromkeys([*(member.get("photos") or []), *(match.get("photos") or [])])
                )
                people.append(merged)
            people = [person_view(p, work_id, index) for index, p in enumerate(people)]
            trailers = list(
                dict.fromkeys(
                    url
                    for candidate in [
                        *(t for e in editions for t in e["metadata"].get("trailers", [])),
                        *enriched.get("trailers", []),
                    ]
                    if (url := youtube_url(candidate))
                )
            )
            personal = conn.execute(
                "SELECT user_rating,watched FROM personal WHERE work_id=?", (work_id,)
            ).fetchone()
            return {
                **value,
                "identifiers": dict(ids),
                "editions": editions,
                "people": people,
                "cast": [p for p in people if p["category"] == "cast"],
                "trailers": trailers,
                "personal": dict(personal) if personal else {},
                "lists": self._lists(conn, work_id),
                "enriched_at": cached["updated_at"] if cached else None,
            }

    def update_personal(self, work_id: str, changes: dict) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
                return False
            conn.execute("INSERT OR IGNORE INTO personal(work_id) VALUES(?)", (work_id,))
            for name, value in changes.items():
                if name not in ("user_rating", "watched"):
                    raise ValueError("Unknown personal field.")
                conn.execute(f"UPDATE personal SET {name}=? WHERE work_id=?", (value, work_id))
            self._rebuild(conn, work_id)
        return True

    def save_enrichment(self, work_id: str, payload: dict) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
                return False
            conn.execute(
                "INSERT INTO enrichment VALUES(?,?,?) ON CONFLICT(work_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (work_id, encode(payload), now()),
            )
            self._rebuild(conn, work_id)
        return True

    @staticmethod
    def _lists(conn, work_id=None):
        where = (
            " WHERE EXISTS(SELECT 1 FROM list_items i WHERE i.list_id=l.id AND i.work_id=?)"
            if work_id
            else ""
        )
        return [
            dict(r)
            for r in conn.execute(
                "SELECT l.*,(SELECT COUNT(*) FROM list_items i WHERE i.list_id=l.id) AS item_count FROM lists l"
                + where
                + " ORDER BY l.name COLLATE NOCASE,l.id",
                (work_id,) if work_id else (),
            )
        ]

    def lists(self):
        with self.connect() as conn:
            return self._lists(conn)

    def save_list(self, data, list_id=None):
        with self.connect() as conn:
            if list_id:
                result = conn.execute(
                    "UPDATE lists SET name=?,description=?,revision=revision+1 WHERE id=?",
                    (data["name"], data["description"], list_id),
                )
                if not result.rowcount:
                    raise KeyError("Unknown list.")
            else:
                list_id = str(uuid4())
                conn.execute(
                    "INSERT INTO lists(id,name,description,created_at) VALUES(?,?,?,?)",
                    (list_id, data["name"], data["description"], now()),
                )
            return next(item for item in self._lists(conn) if item["id"] == list_id)

    def delete_list(self, list_id):
        with self.connect() as conn:
            if not conn.execute("DELETE FROM lists WHERE id=?", (list_id,)).rowcount:
                raise KeyError("Unknown list.")

    def change_list_item(self, list_id, work_id, *, remove=False):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM lists WHERE id=?", (list_id,)).fetchone():
                raise KeyError("Unknown list.")
            if remove:
                count = conn.execute(
                    "DELETE FROM list_items WHERE list_id=? AND work_id=?", (list_id, work_id)
                ).rowcount
            else:
                if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
                    raise KeyError("Unknown media.")
                count = conn.execute(
                    "INSERT OR IGNORE INTO list_items VALUES(?,?,COALESCE((SELECT MAX(position)+1 FROM list_items WHERE list_id=?),0))",
                    (list_id, work_id, list_id),
                ).rowcount
            if count:
                conn.execute("UPDATE lists SET revision=revision+1 WHERE id=?", (list_id,))

    def reorder_list(self, list_id, media_ids, revision):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision FROM lists WHERE id=?", (list_id,)).fetchone()
            if not row:
                raise KeyError("Unknown list.")
            if row[0] != revision:
                raise RuntimeError("This list changed. Reload it before reordering.")
            current = {
                r[0]
                for r in conn.execute("SELECT work_id FROM list_items WHERE list_id=?", (list_id,))
            }
            if len(media_ids) != len(set(media_ids)) or set(media_ids) != current:
                raise ValueError("Supply every current media ID exactly once.")
            conn.executemany(
                "UPDATE list_items SET position=? WHERE list_id=? AND work_id=?",
                ((index, list_id, mid) for index, mid in enumerate(media_ids)),
            )
            conn.execute("UPDATE lists SET revision=revision+1 WHERE id=?", (list_id,))

    def list_page(self, list_id, *, sort="position", order="asc", limit=60, offset=0):
        columns = {
            "position": "i.position",
            "title": "w.sort_title",
            "year": "w.year",
            "rating": "w.rating",
            "user_rating": "w.user_rating",
            "date_added": "w.date_added",
        }
        if (
            sort not in columns
            or order not in ("asc", "desc")
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise ValueError("Invalid list sort or pagination.")
        with self.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT revision FROM lists WHERE id=?", (list_id,)).fetchone()
            if not row:
                raise KeyError("Unknown list.")
            total = conn.execute(
                "SELECT COUNT(*) FROM list_items WHERE list_id=?", (list_id,)
            ).fetchone()[0]
            column = columns[sort]
            rows = conn.execute(
                f"SELECT w.* FROM list_items i JOIN works w ON w.id=i.work_id WHERE i.list_id=? ORDER BY {column} IS NULL,{column} {order},w.id LIMIT ? OFFSET ?",
                (list_id, limit, offset),
            ).fetchall()
            return {
                "items": [self._summary(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
                "revision": row[0],
            }

    def artwork(self, work_id: str, aspect: str) -> list[str]:
        if aspect not in ("poster", "fanart"):
            return []
        with self.connect() as conn:
            row = conn.execute(f"SELECT {aspect} FROM works WHERE id=?", (work_id,)).fetchone()
            return json.loads(row[0]) if row else []

    def facets(self) -> dict[str, Any]:
        with self.connect() as conn:
            value = {
                name: [
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT j.value FROM works w,json_each(w.{name}) j ORDER BY j.value COLLATE NOCASE"
                    )
                ]
                for name in ("genres", "formats", "collections")
            }
            value["formats"] = sorted(
                {canonical_format(label) for label in value["formats"]}, key=fold
            )
            value["years"] = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT year FROM works WHERE year IS NOT NULL ORDER BY year DESC"
                )
            ]
        value["sources"] = self.sources()
        return value

    def status(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = {
                r[0]: r[1] for r in conn.execute("SELECT kind,COUNT(*) FROM works GROUP BY kind")
            }
            editions = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {
            "works": sum(counts.values()),
            "editions": editions,
            "counts": counts,
            "sources": self.sources(),
        }

    def new_job(self, source_id: str) -> dict[str, Any]:
        job_id = str(uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO jobs(id,source_id,status,created_at) VALUES(?,?,'pending',?)",
                (job_id, source_id, now()),
            )
        return self.job(job_id)

    @staticmethod
    def _job_view(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["result"] = json.loads(row["result"]) if row["result"] else None
        value["overall_percent"] = Store._progress_percent(value)
        return value

    @staticmethod
    def _progress_percent(job: dict[str, Any]) -> int:
        """Monotonic estimated overall progress; never restarts per media kind.

        The time required for MariaDB queries and the final SQLite publication
        cannot be known in advance, so this is explicitly an estimate.
        """
        if job["status"] == "completed":
            return 100
        phase = job.get("progress_phase")
        start, end = {
            "connecting": (1, 4),
            "loading": (4, 8),
            "movies": (8, 70),
            "series": (70, 80),
            "episodes": (80, 95),
            "verifying_library": (95, 97),
            "parsing_clz": (2, 92),
            "publishing": (97, 99),
        }.get(phase, (0, 0))
        current = max(0, int(job.get("progress_current") or 0))
        total = max(0, int(job.get("progress_total") or 0))
        if total and phase in ("movies", "series", "episodes"):
            return start + (end - start) * min(current, total) // total
        if phase == "publishing":
            return start
        if phase in ("connecting", "loading"):
            return start
        return start

    def jobs(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Durable feed of manual and automatically scheduled jobs."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT ?",
                (min(100, max(1, limit)),),
            ).fetchall()
            return [self._job_view(row) for row in rows]

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            return self._job_view(row)

    def update_job_progress(
        self, job_id: str, phase: str, current: int = 0, total: int = 0
    ) -> None:
        """Commit phase counters independently of the Kodi read transaction."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET progress_phase=?,progress_current=?,progress_total=? "
                "WHERE id=? AND status='running'",
                (phase, max(0, current), max(0, total), job_id),
            )

    def finish_job(
        self, job_id: str, *, result: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?,finished_at=?,result=?,error=?,progress_phase=? WHERE id=?",
                (
                    "failed" if error else "completed",
                    now(),
                    encode(result) if result else None,
                    error,
                    "failed" if error else "completed",
                    job_id,
                ),
            )
            if error:
                conn.execute(
                    "UPDATE sources SET last_error=? WHERE id=(SELECT source_id FROM jobs WHERE id=?)",
                    (error, job_id),
                )

    def start_job(self, job_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='running',progress_phase='connecting',"
                "progress_current=0,progress_total=0 WHERE id=?",
                (job_id,),
            )

    def players(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM players ORDER BY name").fetchall()
            return [self._player_view(r) for r in rows]

    @staticmethod
    def _player_view(row: sqlite3.Row) -> dict[str, Any]:
        config = json.loads(row["config"])
        return {
            "id": row["id"],
            "name": row["name"],
            "source_id": row["source_id"],
            "has_password": bool(config.pop("password", "")),
            **config,
        }

    def player(self, player_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
            return {**dict(row), "config": json.loads(row["config"])} if row else None

    def save_player(self, data: dict[str, Any], player_id: str | None = None) -> dict[str, Any]:
        player_id = player_id or str(uuid4())
        config = {k: v for k, v in data.items() if k not in ("name", "source_id")}
        with self.connect() as conn:
            if config.get("password") is None:
                old = conn.execute("SELECT config FROM players WHERE id=?", (player_id,)).fetchone()
                config["password"] = json.loads(old[0]).get("password", "") if old else ""
            conn.execute(
                "INSERT INTO players(id,name,source_id,config) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,source_id=excluded.source_id,config=excluded.config",
                (player_id, data["name"], data["source_id"], encode(config)),
            )
        return next(p for p in self.players() if p["id"] == player_id)

    def delete_player(self, player_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM players WHERE id=?", (player_id,))
