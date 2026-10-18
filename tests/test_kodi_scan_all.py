"""Batch scan drives each configured Kodi player, with SQL per source."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from kodi_library.kodi_scan import KodiScanUnavailable, scan_until_finished


def test_websocket_unavailable_uses_correct_http_jsonrpc_scan_path():
    with (
        patch(
            "kodi_library.kodi_scan._websocket_scan",
            side_effect=KodiScanUnavailable("Kodi WebSocket unavailable"),
        ) as ws,
        patch(
            "kodi_library.kodi_scan._http_scan", new_callable=AsyncMock
        ) as http,
    ):
        result = asyncio.run(scan_until_finished({"host": "kodi", "port": 8080}))
    assert result == "http"
    ws.assert_called_once()
    http.assert_awaited_once()


def test_http_scan_invokes_kodi_jsonrpc_and_checks_scan_completion():
    from kodi_library.kodi_scan import _http_scan

    calls = []

    @asynccontextmanager
    async def fake_connection(_config):
        async def rpc(method, params):
            calls.append((method, params))
            if method == "JSONRPC.Permission":
                return {"UpdateData": True}
            if method == "VideoLibrary.Scan":
                return "OK"
            if method == "XBMC.GetInfoBooleans":
                return {"Library.IsScanningVideo": False}
            raise AssertionError(method)

        yield rpc

    with (
        patch("kodi_library.kodi_scan.connection", fake_connection),
        patch("kodi_library.kodi_scan.monotonic", side_effect=[0, 5, 6, 7]),
        patch("kodi_library.kodi_scan.asyncio.sleep", new_callable=AsyncMock),
    ):
        asyncio.run(_http_scan({"host": "kodi", "port": 8080}))
    assert [method for method, _params in calls[:2]] == [
        "JSONRPC.Permission",
        "VideoLibrary.Scan",
    ]
    assert [method for method, _params in calls[2:]] == [
        "XBMC.GetInfoBooleans"
    ] * 3


def test_scan_all_groups_kodi_instances_by_source(client, admin):
    source = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi MariaDB",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader"},
        },
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    for name in ("Living Room", "Bedroom"):
        result = client.post(
            "/api/v1/players",
            headers=admin,
            json={"name": name, "source_id": source_id, "host": "kodi"},
        )
        assert result.status_code == 201, result.text

    async def fake_submit(sid, players):
        assert sid == source_id
        assert len(players) == 2
        assert {p["name"] for p in players} == {"Living Room", "Bedroom"}
        return {
            "id": "scan-job-1",
            "source_id": sid,
            "created_at": "2026-10-18T00:00:00+00:00",
            "status": "pending",
        }

    with patch.object(
        client.app.state.sync, "submit_scans", side_effect=fake_submit
    ) as submit:
        response = client.post("/api/v1/sources/scan-all", headers=admin)
    assert response.status_code == 202, response.text
    assert [job["id"] for job in response.json()] == ["scan-job-1"]
    submit.assert_awaited_once()
    assert client.post("/api/v1/sources/scan-all").status_code == 401
