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
            rows = [{"Tables": "movie_view"}]
        elif query.startswith("SHOW COLUMNS"):
            rows = [{"Field": "VideoVersionTypeItemType"}]
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
    assert "SELECT * FROM `movie_view` WHERE videoVersionTypeItemType = 0" in queries
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
