"""Automatic MyVideos selection must work for explicitly enabled SQL writeback.

Every update still verifies the Kodi media identity before SQL mutation.
"""
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from home_media_catalog.connectors.base import ConnectorError
from home_media_catalog.kodi_mariadb_write import write_kodi_mariadb


class FakeCursor:
    def __init__(self):
        self.statements = []
        self.mismatch = False

    def execute(self, sql, parameters=None):
        self.statements.append((sql, parameters))

    def fetchall(self):
        sql = self.statements[-1][0]
        if sql == "SHOW DATABASES":
            return [{"Database": name} for name in
                    ("mysql", "MyVideos131", "MyVideos135_old", "MyVideos136", "MyVideos99")]
        if sql.startswith("SELECT type, value FROM uniqueid"):
            return [{"type": "tmdb", "value": "wrong" if self.mismatch else "123"}]
        if sql == "SHOW COLUMNS FROM movie_view":
            return []
        return []

    def fetchone(self):
        sql = self.statements[-1][0]
        if sql.startswith("SELECT idMovie, c00"):
            return {"idMovie": 11, "c00": "Film", "userrating": 0, "idFile": 7}
        return None


class FakeConnection:
    def __init__(self):
        self.query = FakeCursor()
        self.selected_database = None
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def cursor(self):
        yield self.query

    def select_db(self, value):
        self.selected_database = value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def update(source, connection):
    with patch("home_media_catalog.kodi_mariadb_write.pymysql.connect",
               return_value=connection) as connect:
        result = write_kodi_mariadb(
            {"config": source}, {"kind": "movie", "title": "Film"},
            [{"metadata": {"kodi_id": 11}, "identifiers": {"tmdb": "123"}}],
            {"user_rating": 8},
        )
    return result, connect


def test_empty_database_selects_highest_numeric_myvideos_and_checks_identity():
    config = {"host": "nas", "username": "kodi", "mariadb_write_enabled": True, "database": ""}
    connection = FakeConnection()
    result, connect = update(config, connection)
    assert connect.call_args.kwargs["database"] is None
    assert connection.selected_database == "MyVideos136"
    assert "MyVideos136" in result
    assert "sélection automatique" in result
    assert connection.committed
    assert any(sql.startswith("UPDATE movie") for sql, _ in connection.query.statements)


def test_explicit_database_never_auto_discovers():
    config = {"host": "nas", "username": "kodi", "mariadb_write_enabled": True,
              "database": "MyVideos131"}
    connection = FakeConnection()
    result, connect = update(config, connection)
    assert connect.call_args.kwargs["database"] == "MyVideos131"
    assert connection.selected_database is None
    assert "sélection automatique" not in result


def test_auto_database_refuses_mismatched_identifiers_before_update():
    config = {"host": "nas", "username": "kodi", "mariadb_write_enabled": True, "database": ""}
    connection = FakeConnection()
    connection.query.mismatch = True
    with patch("home_media_catalog.kodi_mariadb_write.pymysql.connect",
               return_value=connection):
        with pytest.raises(ConnectorError, match="identifiants externes contradictoires"):
            write_kodi_mariadb(
                {"config": config}, {"kind": "movie", "title": "Film"},
                [{"metadata": {"kodi_id": 11}, "identifiers": {"tmdb": "123"}}],
                {"user_rating": 8},
            )
    assert connection.rolled_back
    assert not connection.committed
    assert not any(sql.startswith("UPDATE ") for sql, _ in connection.query.statements)
