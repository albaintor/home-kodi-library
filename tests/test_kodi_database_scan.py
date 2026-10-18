"""Safeguards for Kodi SQL writes and JSON-RPC scan event delivery."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from kodi_library.connectors.base import ConnectorError
from kodi_library.kodi_mariadb_write import write_kodi_mariadb
from kodi_library.kodi_scan import scan_until_finished, websocket_url


def sql_fixture(*, remote_id="123", title="Film"):
    source = {
        "config": {
            "host": "nas",
            "port": 3306,
            "database": "MyVideos131",
            "username": "reader",
            "write_username": "writer",
            "write_password": "private-writer-secret",
            "mariadb_write_enabled": True,
        },
    }
    detail = {"title": "Film", "kind": "movie"}
    edition = {"metadata": {"kodi_id": 11}, "identifiers": {"tmdb": "123"}}
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value

    def execute(statement, _params=None):
        if statement.startswith("SELECT idMovie"):
            cursor.fetchone.return_value = {
                "idMovie": 11,
                "c00": title,
                "userrating": 4,
                "idFile": 27,
            }
        elif statement.startswith("SELECT type, value FROM uniqueid"):
            cursor.fetchall.return_value = [{"type": "tmdb", "value": remote_id}]
        elif statement == "SHOW COLUMNS FROM movie_view":
            cursor.fetchall.return_value = [{"Field": "idMovie"}]
    cursor.execute.side_effect = execute
    return source, detail, [edition], conn, cursor


def test_sql_fallback_updates_confirmed_id_and_commits():
    source, detail, editions, conn, cursor = sql_fixture()
    with patch("kodi_library.kodi_mariadb_write.pymysql.connect", return_value=conn):
        result = write_kodi_mariadb(source, detail, editions, {"user_rating": 7.5, "watched": True})
    assert "MariaDB" in result
    updates = [entry for entry in cursor.execute.call_args_list if entry.args[0].startswith("UPDATE ")]
    assert any("UPDATE movie SET userrating" in entry.args[0] and entry.args[1] == (8, 11) for entry in updates)
    assert any("UPDATE files SET playCount" in entry.args[0] and entry.args[1] == [27] for entry in updates)
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_sql_fallback_wrong_identity_aborts_without_mutation():
    source, detail, editions, conn, cursor = sql_fixture(remote_id="different")
    with patch("kodi_library.kodi_mariadb_write.pymysql.connect", return_value=conn):
        with pytest.raises(ConnectorError, match="contradictoires"):
            write_kodi_mariadb(source, detail, editions, {"watched": False})
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()
    assert not any(item.args[0].startswith("UPDATE ") for item in cursor.execute.call_args_list)


def test_sql_fallback_requires_explicit_writer():
    source, detail, editions, _conn, _cursor = sql_fixture()
    source["config"]["mariadb_write_enabled"] = False
    with pytest.raises(ConnectorError, match="non activé"):
        write_kodi_mariadb(source, detail, editions, {"watched": True})


class FakeSocket:
    def __init__(self, notifications):
        self.notifications = list(notifications)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send_json(self, data):
        self.sent.append(data)

    async def receive(self):
        if not self.notifications:
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data="")
        return SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps(self.notifications.pop(0)),
        )


class FakeSession:
    def __init__(self, socket):
        self.socket = socket
        self.url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def ws_connect(self, url, **_kwargs):
        self.url = url
        return self.socket


def test_websocket_scan_waits_for_start_finish_and_ack():
    socket = FakeSocket(
        [
            {"id": 1, "result": {"UpdateData": True}},
            {"method": "VideoLibrary.OnScanStarted"},
            {"method": "VideoLibrary.OnScanFinished"},
            {"id": 2, "result": "OK"},
        ]
    )
    session = FakeSession(socket)
    with patch("kodi_library.kodi_scan.aiohttp.ClientSession", return_value=session):
        asyncio.run(scan_until_finished({"host": "kodi", "websocket_port": 9091}))
    assert session.url == "ws://kodi:9091/jsonrpc"
    assert [message["method"] for message in socket.sent] == [
        "JSONRPC.Permission",
        "VideoLibrary.Scan",
    ]


def test_websocket_scan_does_not_claim_success_without_finish():
    socket = FakeSocket([
        {"id": 1, "result": {"UpdateData": True}},
        {"id": 2, "result": "OK"},
        {"method": "VideoLibrary.OnScanStarted"},
    ])
    with patch("kodi_library.kodi_scan.aiohttp.ClientSession", return_value=FakeSession(socket)):
        with pytest.raises(ConnectorError, match="interrompue"):
            asyncio.run(scan_until_finished({"host": "kodi"}))


def test_websocket_host_input_rejects_urls():
    with pytest.raises(ConnectorError):
        websocket_url({"host": "http://external.example"})
