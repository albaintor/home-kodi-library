"""Integration tests for Kodi's remote scan and guarded MariaDB fallback."""

import time
from unittest.mock import AsyncMock, patch

from kodi_library.connectors.base import ConnectorError, Record
from kodi_library.players import KodiUnavailable


def kodi_fixture(client, admin):
    source = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi NAS",
            "type": "kodi",
            "config": {
                "host": "nas",
                "database": "MyVideos131",
                "username": "reader",
                "password": "a-nonempty-private-password",
                "mariadb_write_enabled": True,
            },
        },
    )
    assert source.status_code == 201, source.text
    sid = source.json()["id"]
    client.app.state.store.apply_snapshot(
        sid,
        [
            Record(
                "movie:11",
                "movie",
                "Film",
                identifiers={"tmdb": "123"},
                metadata={"kodi_id": 11},
            )
        ],
    )
    mid = client.get("/api/v1/media", headers=admin).json()["items"][0]["id"]
    player = client.post(
        "/api/v1/players",
        headers=admin,
        json={"name": "Salon", "source_id": sid, "host": "kodi", "websocket_port": 9091},
    )
    assert player.status_code == 201
    return sid, mid


def test_fallback_is_used_only_on_unavailable_jsonrpc_and_audited(client, admin):
    sid, mid = kodi_fixture(client, admin)
    with (
        patch(
            "kodi_library.writeback._write_kodi",
            new_callable=AsyncMock,
            side_effect=KodiUnavailable("JSON-RPC indisponible"),
        ),
        patch(
            "kodi_library.writeback.write_kodi_mariadb",
            return_value="Écriture MariaDB effectuée",
        ) as sql,
    ):
        response = client.patch(
            f"/api/v1/media/{mid}/personal", json={"watched": True}, headers=admin
        )
    assert response.status_code == 200
    result = response.json()["writeback"][0]
    assert result["status"] == "updated"
    assert result["method"] == "mariadb"
    assert result["attempts"] == ["jsonrpc:unavailable", "mariadb:updated"]
    sql.assert_called_once()
    audit = client.get(f"/api/v1/sources/{sid}/writeback", headers=admin).json()
    assert len(audit) == 1
    assert audit[0]["method"] == "mariadb"
    assert audit[0]["fields"] == ["watched"]
    assert "a-nonempty-private-password" not in str(audit)


def test_jsonrpc_rejection_never_triggers_sql_fallback(client, admin):
    sid, mid = kodi_fixture(client, admin)
    with (
        patch(
            "kodi_library.writeback._write_kodi",
            new_callable=AsyncMock,
            side_effect=ConnectorError("UpdateData refusé"),
        ),
        patch("kodi_library.writeback.write_kodi_mariadb") as sql,
    ):
        response = client.patch(
            f"/api/v1/media/{mid}/personal", json={"user_rating": 9}, headers=admin
        )
    assert response.status_code == 200
    result = response.json()["writeback"][0]
    assert result["status"] == "failed"
    assert result["attempts"] == ["jsonrpc:rejected"]
    sql.assert_not_called()
    assert (
        client.get(f"/api/v1/media/{mid}/writeback", headers=admin).json()[0]["status"] == "failed"
    )


def test_kodi_scan_finished_triggers_local_sync(client, admin):
    sid, _mid = kodi_fixture(client, admin)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def reachable(_config, **_kwargs):
        async def rpc(method, _params):
            assert method == "JSONRPC.Ping"
            return "pong"

        yield rpc

    with (
        patch("kodi_library.sync.connection", reachable),
        patch(
            "kodi_library.sync.scan_until_finished",
            new_callable=AsyncMock,
            return_value="websocket",
        ) as scan,
        patch(
            "kodi_library.sync.KodiConnector.fetch",
            return_value=[
                Record(
                    "movie:11",
                    "movie",
                    "Film",
                    identifiers={"tmdb": "123"},
                    metadata={"kodi_id": 11},
                )
            ],
        ) as fetch,
    ):
        response = client.post(f"/api/v1/sources/{sid}/scan", headers=admin)
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        for _ in range(200):
            job = client.get(f"/api/v1/jobs/{job_id}", headers=admin).json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.01)
        assert job["status"] == "completed", job
        assert job["result"]["kodi_scan"] == "finished"
        scan.assert_awaited_once()
        assert scan.call_args.args[0]["websocket_port"] == 9091
        fetch.assert_called_once()


def test_kodi_scan_disconnection_never_resynchronizes(client, admin):
    sid, _mid = kodi_fixture(client, admin)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def reachable(_config, **_kwargs):
        async def rpc(method, _params):
            assert method == "JSONRPC.Ping"
            return "pong"

        yield rpc

    with (
        patch("kodi_library.sync.connection", reachable),
        patch(
            "kodi_library.sync.scan_until_finished",
            new_callable=AsyncMock,
            side_effect=ConnectorError("WebSocket disconnected"),
        ),
        patch("kodi_library.sync.KodiConnector.fetch") as fetch,
    ):
        response = client.post(f"/api/v1/sources/{sid}/scan", headers=admin)
        assert response.status_code == 202
        for _ in range(200):
            job = client.get(f"/api/v1/jobs/{response.json()['id']}", headers=admin).json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.01)
        assert job["status"] == "failed"
        fetch.assert_not_called()


def test_mariadb_writer_secret_redacted(client, admin):
    sid, _ = kodi_fixture(client, admin)
    response = client.get("/api/v1/sources", headers=admin)
    assert "a-nonempty-private-password" not in response.text
    source = next(value for value in response.json() if value["id"] == sid)
    assert source["config"]["has_password"] is True
    assert "has_write_password" not in source["config"]
    assert source["config"]["mariadb_write_enabled"] is True
