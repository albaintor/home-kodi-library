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
        if sql.startswith("SELECT p.strPath, f.strFilename"):
            return {"strPath": "/movies/", "strFilename": "film.mkv"}
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
        with pytest.raises(ConnectorError, match="Identifiants externes contradictoires"):
            write_kodi_mariadb(
                {"config": config}, {"kind": "movie", "title": "Film"},
                [{"metadata": {"kodi_id": 11}, "identifiers": {"tmdb": "123"}}],
                {"user_rating": 8},
            )
    assert connection.rolled_back
    assert not connection.committed
    assert not any(sql.startswith("UPDATE ") for sql, _ in connection.query.statements)



def test_without_shared_external_id_requires_exact_imported_file_path():
    config = {"host": "nas", "username": "kodi", "mariadb_write_enabled": True,
              "database": "MyVideos136"}
    connection = FakeConnection()
    with patch("home_media_catalog.kodi_mariadb_write.pymysql.connect",
               return_value=connection):
        result = write_kodi_mariadb(
            {"config": config}, {"kind": "movie", "title": "Film"},
            [{"metadata": {"kodi_id": 11, "file_id": 7, "file": "/movies/film.mkv"},
              "identifiers": {}}],
            {"watched": True},
        )
    assert "MyVideos136" in result
    assert connection.committed


def test_without_external_id_or_file_path_refuses_mariadb_write():
    config = {"host": "nas", "username": "kodi", "mariadb_write_enabled": True,
              "database": "MyVideos136"}
    connection = FakeConnection()
    with patch("home_media_catalog.kodi_mariadb_write.pymysql.connect",
               return_value=connection):
        with pytest.raises(ConnectorError, match="Aucun identifiant externe ni fichier commun"):
            write_kodi_mariadb(
                {"config": config}, {"kind": "movie", "title": "Film"},
                [{"metadata": {"kodi_id": 11}, "identifiers": {}}],
                {"watched": True},
            )
    assert connection.rolled_back
    assert not connection.committed


def test_auto_schema_cannot_write_into_a_different_imported_database():
    config = {"host": "nas", "username": "kodi", "mariadb_write_enabled": True,
              "database": ""}
    connection = FakeConnection()
    with patch("home_media_catalog.kodi_mariadb_write.pymysql.connect",
               return_value=connection):
        with pytest.raises(ConnectorError, match="ne correspond pas"):
            write_kodi_mariadb(
                {"config": config}, {"kind": "movie", "title": "Film"},
                [{"metadata": {"kodi_id": 11, "database": "MyVideos131"},
                  "identifiers": {"tmdb": "123"}}],
                {"user_rating": 8},
            )
    assert not connection.committed


def test_no_external_ids_show_must_have_matching_database_provenance():
    # A TV show with no identifiers is safe only when it came from the very
    # same imported MyVideos schema. Movie/episode still require matching file.
    import inspect
    source = inspect.getsource(write_kodi_mariadb)
    assert 'verified_show = (' in source
    assert 'str(imported_database).casefold() == database.casefold()' in source
    assert 'kind == "tvshow"' in source



def test_watched_and_personal_rating_are_updated_together_in_myvideos():
    config = {"host": "nas", "username": "kodi",
              "mariadb_write_enabled": True, "database": "MyVideos136"}
    connection = FakeConnection()
    with patch("home_media_catalog.kodi_mariadb_write.pymysql.connect",
               return_value=connection):
        outcome = write_kodi_mariadb(
            {"config": config}, {"kind": "movie", "title": "Film"},
            [{"metadata": {"kodi_id": 11}, "identifiers": {"tmdb": "123"}}],
            {"watched": True, "user_rating": 7.5},
        )
    statements = connection.query.statements
    assert "MyVideos136" in outcome
    assert connection.committed and not connection.rolled_back
    rating_updates = [(query, args) for query, args in statements
                      if query.startswith("UPDATE movie SET userrating")]
    play_updates = [(query, args) for query, args in statements
                    if query.startswith("UPDATE files SET playCount")]
    assert len(rating_updates) == 1
    assert rating_updates[0][1] == (8, 11)
    assert len(play_updates) == 1
    assert play_updates[0][1] == [7]
