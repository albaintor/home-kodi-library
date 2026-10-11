from unittest.mock import MagicMock, patch

import pytest

from kodi_library.connectors.base import ConnectorError
from kodi_library.connectors.clz import parse_clz
from kodi_library.connectors.kodi import KodiConnector, artwork_candidates, normalize_row


def test_kodi_movie_file_version_artwork_and_identifiers():
    row = {
        "idMovie": 42,
        "idFile": 10,
        "videoVersionIdFile": 11,
        "c00": "Un film",
        "c07": "2024-01-01",
        "c11": "7200",
        "c14": "Aventure / Science-fiction",
        "playCount": None,
        "c08": "<thumb>https://example.org/fallback.jpg</thumb>",
        "strPath": "smb://nas/films/",
        "strFilename": "movie.mkv",
        "isDefaultVersion": 0,
        "videoVersionTypeName": "Longue",
        "dateAdded": "2026-10-01 11:00:00",
    }
    r = normalize_row(
        "movie",
        row,
        {"poster": "image://smb%3A%2F%2Fnas%2Fposters%2Fa.jpg/"},
        {"imdb": "1234567", "tmdb": "123"},
    )
    assert r.external_key == "movie:42:11"
    assert r.runtime_minutes == 120 and r.year == 2024
    assert r.identifiers == {"imdb": "tt1234567", "tmdb": "123"}
    assert r.poster == ["smb://nas/posters/a.jpg", "https://example.org/fallback.jpg"]
    assert r.watched is False
    assert r.metadata["is_default"] is False


def test_kodi_series_and_episode():
    r = normalize_row(
        "tvshow",
        {"idShow": 5, "c00": "Série", "c05": "2023-04-02", "totalCount": 3, "watchedcount": 1},
    )
    assert r.year == 2023 and r.watched is False
    e = normalize_row(
        "episode",
        {
            "idEpisode": 2,
            "idShow": 5,
            "c00": "Spécial",
            "c12": "0",
            "c13": "2",
            "c09": "3600",
            "playCount": 1,
        },
    )
    assert e.parent_key == "tvshow:5" and e.season == 0 and e.episode == 2
    assert e.watched is True and e.runtime_minutes == 60


def test_xml_entities_and_invalid_exports_are_rejected():
    with pytest.raises(ConnectorError):
        parse_clz(b'<!DOCTYPE collectorz [<!ENTITY x "secret">]><collectorz>&x;</collectorz>')
    with pytest.raises(ConnectorError):
        parse_clz(b"<something />")
    assert artwork_candidates("<thumb>https://example.org/a.jpg</thumb>") == [
        "https://example.org/a.jpg"
    ]
    assert artwork_candidates("<broken") == []


def test_mariadb_snapshot_chooses_highest_version_and_never_writes():
    cursor = MagicMock()
    queries = []

    def execute(query):
        queries.append(query)
        if query == "SHOW DATABASES":
            rows = [{"Database": "MyVideos121"}, {"Database": "MyVideos131"}]
        elif query == "SHOW FULL TABLES":
            rows = [{"Tables": "movie_view"}, {"Tables": "movie"}]
        elif query == "SELECT idMovie FROM movie":
            rows = [{"idMovie": 1}]
        elif query.startswith("SHOW COLUMNS"):
            rows = [{"Field": "VideoVersionTypeItemType"}]
        elif query.startswith("SELECT DISTINCT videoVersionTypeItemType"):
            rows = [{"videoVersionTypeItemType": 0}]
        elif query.startswith("SELECT *"):
            rows = [{"idMovie": 1, "idFile": 2, "c00": "Film"}]
        else:
            rows = []
        cursor.fetchall.return_value = rows

    cursor.execute.side_effect = execute
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    with patch("kodi_library.connectors.kodi.pymysql.connect", return_value=conn):
        connector = KodiConnector({"host": "nas", "username": "reader"})
        assert len(connector.fetch()) == 1
    conn.select_db.assert_called_once_with("MyVideos131")
    assert "SET SESSION TRANSACTION READ ONLY" in queries
    assert "START TRANSACTION WITH CONSISTENT SNAPSHOT" in queries
    assert (
        "SELECT * FROM `movie_view` WHERE videoVersionTypeItemType IS NULL OR videoVersionTypeItemType = 0"
        in queries
    )
    assert not any(q.startswith(("INSERT", "DELETE", "UPDATE", "ALTER")) for q in queries)
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


def test_invalid_database_is_rejected_before_connect():
    with patch("kodi_library.connectors.kodi.pymysql.connect") as connect:
        with pytest.raises(ConnectorError):
            KodiConnector(
                {"host": "nas", "username": "reader", "database": "MyVideos; DROP TABLE movie"}
            ).fetch()
        connect.assert_not_called()


def test_kodi_skips_orphaned_movie_view_rows_missing_from_movie_table():
    """A derived view must not resurrect a movie deleted in Kodi's movie table."""
    cursor = MagicMock()
    calls = []

    def execute(query):
        calls.append(query)
        if query == "SHOW FULL TABLES":
            rows = [{"Tables": "movie"}, {"Tables": "movie_view"}]
        elif query == "SELECT idMovie FROM movie":
            rows = [{"idMovie": 10}]
        elif query.startswith("SHOW COLUMNS"):
            rows = [{"Field": "idMovie"}]
        elif query.startswith("SELECT * FROM `movie_view`"):
            rows = [
                {"idMovie": 10, "idFile": 55, "c00": "Still in Kodi"},
                {"idMovie": 99, "idFile": 77, "c00": "Deleted from Kodi"},
            ]
        else:
            rows = []
        cursor.fetchall.return_value = rows

    cursor.execute.side_effect = execute
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    updates = []
    with patch("kodi_library.connectors.kodi.pymysql.connect", return_value=connection):
        connector = KodiConnector(
            {"host": "nas", "username": "reader", "database": "MyVideos131"},
            progress=lambda phase, current, total: updates.append((phase, current, total)),
        )
        records = connector.fetch()
    assert [record.external_key for record in records] == ["movie:10:55"]
    assert connector.snapshot_report["movie_table_count"] == 1
    assert connector.snapshot_report["orphaned_movie_view_rows"] == 1
    assert connector.snapshot_report["orphaned_movie_view_examples"][0]["kodi_id"] == 99
    assert ("movies", 2, 2) in updates
    assert "SELECT idMovie FROM movie" in calls


def test_diagnostic_checks_real_movie_table_and_selected_database():
    cursor = MagicMock()

    def execute(query, *params):
        if query.startswith("SELECT idMovie FROM movie WHERE"):
            cursor.fetchall.return_value = [{"idMovie": 10}]
        return None

    cursor.execute.side_effect = execute
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    with patch("kodi_library.connectors.kodi.pymysql.connect", return_value=connection):
        result = KodiConnector(
            {"host": "nas", "username": "reader", "database": "MyVideos131"}
        ).check_movie_ids([10, 99])
    assert result == {"database": "MyVideos131", "movie_ids_present": [10]}
    connection.select_db.assert_called_once_with("MyVideos131")


def test_kodi_progress_callbacks_are_throttled_to_avoid_sqlite_write_storm(monkeypatch):
    from kodi_library.connectors import kodi as kodi_module

    timestamps = iter([10.0, 10.1, 10.2, 10.6, 10.7, 10.8])
    monkeypatch.setattr(kodi_module, "monotonic", lambda: next(timestamps))
    progress = []
    connector = KodiConnector(
        {},
        progress=lambda phase, current, total: progress.append((phase, current, total)),
    )
    connector._report("movies", 0, 100)
    connector._report("movies", 1, 100)
    connector._report("movies", 2, 100)
    connector._report("movies", 50, 100)
    connector._report("movies", 100, 100)
    connector._report("episodes", 0, 5)
    assert progress == [
        ("movies", 0, 100),
        ("movies", 50, 100),
        ("movies", 100, 100),
        ("episodes", 0, 5),
    ]


def test_diagnostic_inspects_movie_file_paths_without_implying_disk_existence():
    cursor = MagicMock()

    def execute(query, *params):
        if query == "SHOW FULL TABLES":
            rows = [{"Tables": x} for x in ("movie", "files", "path", "videoversion")]
        elif query == "SHOW COLUMNS FROM `path`":
            rows = [{"Field": "noUpdate"}, {"Field": "exclude"}]
        elif "FROM movie m" in query:
            assert params == ([7, 8],)
            rows = [
                {
                    "idMovie": 7,
                    "title": "Movie",
                    "default_file_id": 42,
                    "resolved_file_id": 42,
                    "strFileName": "film.mkv",
                    "strPath": "smb://nas/",
                    "excluded_from_scan": 0,
                    "scan_disabled": 1,
                },
                {
                    "idMovie": 8,
                    "title": "Orphan",
                    "default_file_id": 101,
                    "resolved_file_id": None,
                    "strFileName": None,
                    "strPath": None,
                    "excluded_from_scan": None,
                    "scan_disabled": None,
                },
            ]
        elif "FROM videoversion v" in query:
            assert params == ([7, 8],)
            rows = [
                {
                    "idMedia": 7,
                    "idFile": 43,
                    "itemType": 0,
                    "resolved_file_id": 43,
                    "strFileName": "long.mkv",
                    "strPath": "smb://nas/",
                },
            ]
        else:
            rows = []
        cursor.fetchall.return_value = rows

    cursor.execute.side_effect = execute
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    with patch("kodi_library.connectors.kodi.pymysql.connect", return_value=connection):
        report = KodiConnector(
            {"host": "nas", "username": "reader", "database": "MyVideos131"}
        ).inspect_movie_links([7, 8, 7])
    movies = report["movies"]
    assert movies[7]["file"] == "smb://nas/film.mkv"
    assert movies[7]["scan_disabled"] == 1
    assert movies[7]["versions"][0]["file"] == "smb://nas/long.mkv"
    assert movies[8]["file_row_exists"] is False
    assert movies[8]["path_row_exists"] is False
