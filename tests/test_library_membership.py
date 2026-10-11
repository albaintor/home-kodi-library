"""Kodi's SQL movie navigation must not rely on a running Kodi player."""

import asyncio
import sqlite3
from unittest.mock import patch

from kodi_library.connectors.base import Record
from kodi_library.connectors.kodi import KodiConnector
from kodi_library.settings import Settings
from kodi_library.sync import SyncManager


class SQLCursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def execute(self, query):
        if query == "SHOW FULL TABLES":
            self.rows = [
                {"Tables": row[0]}
                for row in self.database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        elif query.startswith("SHOW COLUMNS FROM"):
            table = query.split("`")[1]
            self.rows = [
                {"Field": row[1]}
                for row in self.database.execute(f"PRAGMA table_info(`{table}`)")
            ]
        else:
            self.rows = [dict(row) for row in self.database.execute(query)]

    def fetchall(self):
        return self.rows


def movie_database(*, legacy=False):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    if legacy:
        db.execute("CREATE TABLE movie_view (idMovie INTEGER,idFile INTEGER,c00 TEXT)")
        db.execute("INSERT INTO movie_view VALUES(1,10,'Legacy movie')")
    else:
        db.execute(
            "CREATE TABLE movie_view ("
            "idMovie INTEGER,idFile INTEGER,c00 TEXT,"
            "videoVersionIdFile INTEGER,isDefaultVersion INTEGER,"
            "videoVersionTypeItemType INTEGER)"
        )
        db.executemany(
            "INSERT INTO movie_view VALUES(?,?,?,?,?,?)",
            [
                (1, 10, "Listed film", 10, 1, 1),
                (1, 11, "Listed film", 11, 0, 1),
                (1, 12, "Listed film bonus", 12, 0, 2),
                (2, 20, "Residual movie", 20, 0, 1),
                (3, 30, "Orphan version", 30, 0, 1),
            ],
        )
    db.execute("CREATE TABLE movie(idMovie INTEGER PRIMARY KEY)")
    db.executemany("INSERT INTO movie VALUES(?)", [(1,), (2,), (3,)])
    return db


def test_movie_nav_sql_omits_rows_without_default_but_keeps_all_valid_versions():
    db = movie_database()
    connector = KodiConnector({})
    records = connector._snapshot(SQLCursor(db))
    assert [record.external_key for record in records] == [
        "movie:1:10",
        "movie:1:11",
    ]
    assert connector.snapshot_report["library_verified"] is True
    assert connector.snapshot_report["library_rule"] == "movie_view.isDefaultVersion = 1"
    assert connector.snapshot_report["library_movie_count"] == 1
    assert connector.snapshot_report["library_excluded_editions"] == 2
    assert connector.snapshot_report["library_excluded_examples"][0]["kodi_id"] == 2
    db.close()


def test_legacy_movie_view_without_default_column_preserves_previous_behavior():
    db = movie_database(legacy=True)
    connector = KodiConnector({})
    records = connector._snapshot(SQLCursor(db))
    assert len(records) == 1
    assert connector.snapshot_report["library_verified"] is False
    db.close()


def test_sql_navigation_removes_residual_movie_even_if_movie_row_is_present(store):
    source_id = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    old = [
        Record("movie:1:10", "movie", "Listed film"),
        Record("movie:2:20", "movie", "Residual movie"),
    ]
    store.apply_snapshot(source_id, old)
    db = movie_database()
    imported = KodiConnector({})._snapshot(SQLCursor(db))
    result = store.apply_snapshot(source_id, imported)
    assert result["removed_editions"] == 1
    assert store.media(kind="movie")["total"] == 1
    assert store.media(kind="movie")["items"][0]["title"] == "Listed film"
    db.close()


def test_sync_uses_mariadb_only_even_when_player_is_configured(store):
    source_id = store.save_source({"name": "Kodi", "type": "kodi"})["id"]
    store.save_player(
        {"name": "Salon", "source_id": source_id, "host": "kodi", "port": 8080}
    )
    store.apply_snapshot(
        source_id,
        [
            Record("movie:1:10", "movie", "Listed film"),
            Record("movie:2:20", "movie", "Residual movie"),
        ],
    )
    db = movie_database()
    manager = SyncManager(store, Settings(scheduler_enabled=False))

    def fake_fetch(connector):
        connector.database = "MyVideos131"
        return connector._snapshot(SQLCursor(db))

    async def run():
        job = await manager.submit(source_id)
        await asyncio.gather(*manager.tasks)
        return store.job(job["id"])

    with (
        patch.object(KodiConnector, "fetch", fake_fetch),
        patch.object(store, "player", side_effect=AssertionError("Should not query Kodi players")),
    ):
        job = asyncio.run(run())
    assert job["status"] == "completed"
    assert job["result"]["library_verified"] is True
    assert job["result"]["removed_editions"] == 1
    assert store.media(kind="movie")["total"] == 1
    db.close()
