import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pymysql
import pytest
from conftest import READ

from home_media_catalog.connectors.base import ConnectorError
from home_media_catalog.connectors.kodi import KodiConnector
from home_media_catalog.enrichment import probe_tmdb
from home_media_catalog.players import probe

MARIADB = {"config": {"host": "nas", "username": "reader", "password": "private-secret"}}


def test_tmdb_status_and_probe_are_explicit_and_admin_only(client, admin):
    assert client.get("/api/v1/status", headers=admin).json()["tmdb_available"] is False
    path = "/api/v1/connections/tmdb/test"
    assert client.post(path).status_code == 401
    assert client.post(path, headers={"Authorization": f"Bearer {READ}"}).status_code == 403
    assert client.post(path, headers=admin).status_code == 422
    with patch("home_media_catalog.app.probe_tmdb", new_callable=AsyncMock) as tested:
        assert client.post(path, headers=admin).status_code == 422
        tested.assert_not_called()


@pytest.mark.parametrize("token", ["read-access-token-private", "a" * 32])
def test_tmdb_probe_supports_both_credentials_and_keeps_errors_private(token):
    calls = []
    code = 200

    def upstream(request):
        calls.append(request)
        return httpx.Response(
            code,
            json={"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}, "secret": token},
        )

    original = httpx.AsyncClient
    with patch(
        "home_media_catalog.enrichment.httpx.AsyncClient",
        side_effect=lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs),
    ):
        asyncio.run(probe_tmdb(token))
        if len(token) == 32:
            assert calls[0].url.params["api_key"] == token
            assert "authorization" not in calls[0].headers
        else:
            assert calls[0].headers["authorization"] == "Bearer " + token
        for http_code, fragment in [
            (401, "refuse"),
            (403, "refuse"),
            (429, "limite"),
            (503, "indisponible"),
        ]:
            code = http_code
            with pytest.raises(ConnectorError, match=fragment) as error:
                asyncio.run(probe_tmdb(token))
            assert token not in str(error.value)


def test_mariadb_probe_does_not_save_a_source(client, admin):
    with patch.object(
        KodiConnector,
        "probe",
        return_value={"database": "MyVideos131", "available_media": ["movie", "tvshow", "episode"]},
    ):
        response = client.post("/api/v1/connections/mariadb/test", headers=admin, json=MARIADB)
    assert response.status_code == 200 and response.json()["success"]
    assert response.json()["database"] == "MyVideos131"
    assert "private-secret" not in response.text
    assert client.app.state.store.sources() == []


def test_mariadb_edit_probe_reuses_password_without_saving_edits(client, admin):
    sid = client.post(
        "/api/v1/sources", headers=admin, json={"name": "Kodi", "type": "kodi", **MARIADB}
    ).json()["id"]
    captured = []

    def probe_connection(connector):
        captured.append(connector.config)
        return {"database": "MyVideos131", "available_media": ["movie"]}

    with patch.object(KodiConnector, "probe", probe_connection):
        response = client.post(
            "/api/v1/connections/mariadb/test",
            headers=admin,
            json={
                "source_id": sid,
                "config": {"host": "new-nas", "username": "reader", "password": None},
            },
        )
    assert response.status_code == 200
    assert captured[0]["password"] == "private-secret"
    assert client.app.state.store.source(sid, private=True)["config"]["host"] == "nas"


def test_connection_probes_require_admin_and_validate_input(client, admin):
    for route, body in [
        ("mariadb", MARIADB),
        ("kodi", {"host": "kodi", "password": "private-secret"}),
    ]:
        assert client.post(f"/api/v1/connections/{route}/test", json=body).status_code == 401
        assert (
            client.post(
                f"/api/v1/connections/{route}/test",
                headers={"Authorization": f"Bearer {READ}"},
                json=body,
            ).status_code
            == 403
        )
    response = client.post(
        "/api/v1/connections/kodi/test",
        headers=admin,
        json={"host": "kodi", "port": 70000, "password": "private-secret"},
    )
    assert response.status_code == 422 and "private-secret" not in response.text


def test_probe_failure_and_saved_target_password(client, admin):
    with patch.object(
        KodiConnector, "probe", side_effect=ConnectorError("MariaDB authentication rejected")
    ):
        response = client.post("/api/v1/connections/mariadb/test", headers=admin, json=MARIADB)
    assert response.status_code == 502
    assert client.app.state.store.sources() == []
    sid = client.post(
        "/api/v1/sources", headers=admin, json={"name": "Kodi", "type": "kodi", **MARIADB}
    ).json()["id"]
    pid = client.post(
        "/api/v1/players",
        headers=admin,
        json={"name": "Salon", "source_id": sid, "host": "kodi", "password": "private-secret"},
    ).json()["id"]
    with patch(
        "home_media_catalog.app.probe_player",
        new_callable=AsyncMock,
        return_value={"version": "22.0", "playback_allowed": True},
    ) as tested:
        response = client.post(
            "/api/v1/connections/kodi/test",
            headers=admin,
            json={"player_id": pid, "host": "new-kodi", "password": None},
        )
        assert tested.call_args.args[0]["password"] == "private-secret"
    assert response.status_code == 200 and "private-secret" not in response.text
    assert client.app.state.store.player(pid)["config"]["host"] == "kodi"
    assert (
        client.post(
            "/api/v1/connections/kodi/test",
            headers=admin,
            json={"host": "kodi", "player_id": "missing"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        (1044, "access"),
        (1045, "authentication"),
        (1049, "does not exist"),
        (1142, "SELECT"),
        (2003, "Cannot connect"),
        (2005, "resolved"),
        (2013, "timed out"),
        (2026, "TLS"),
    ],
)
def test_mariadb_error_categories_do_not_echo_server_message(code, fragment):
    with patch(
        "home_media_catalog.connectors.kodi.pymysql.connect",
        side_effect=pymysql.OperationalError(code, "server message containing private-secret"),
    ):
        with pytest.raises(ConnectorError) as error:
            KodiConnector(MARIADB["config"]).probe()
    assert fragment in str(error.value) and "private-secret" not in str(error.value)


def test_mariadb_probe_checks_views_without_importing_or_writing():
    cursor = MagicMock()
    queries = []

    def execute(query):
        queries.append(query)
        if query == "SHOW DATABASES":
            rows = [{"Database": "MyVideos131"}]
        elif query == "SHOW FULL TABLES":
            rows = [
                {"Tables": view}
                for view in ("movie_view", "tvshow_view", "episode_view", "art", "uniqueid")
            ]
        else:
            rows = []
        cursor.fetchall.return_value = rows

    cursor.execute.side_effect = execute
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    with patch("home_media_catalog.connectors.kodi.pymysql.connect", return_value=conn):
        assert KodiConnector(MARIADB["config"]).probe() == {
            "database": "MyVideos131",
            "available_media": ["movie", "tvshow", "episode"],
        }
    assert "SET SESSION TRANSACTION READ ONLY" in queries
    assert "SELECT `idMovie`,c00 FROM `movie_view` LIMIT 1" in queries
    assert not any(q.startswith(("INSERT", "UPDATE", "DELETE", "SELECT *")) for q in queries)
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


@pytest.mark.parametrize("permission_key", ["ControlPlayback", "controlplayback"])
def test_kodi_probe_uses_only_non_playback_calls(permission_key):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        values = {
            "JSONRPC.Ping": "pong",
            "JSONRPC.Permission": {"ReadData": True, permission_key: True, "ControlGUI": True},
            "Application.GetProperties": {"version": {"major": 22, "minor": 0}, "name": "Kodi"},
        }
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": values[body["method"]]}
        )

    actual_client = httpx.AsyncClient
    with patch(
        "home_media_catalog.players.httpx.AsyncClient",
        side_effect=lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw),
    ):
        assert asyncio.run(probe({"host": "kodi", "port": 8080})) == {
            "version": "22.0",
            "playback_allowed": True,
        }
    assert calls == ["JSONRPC.Ping", "JSONRPC.Permission", "Application.GetProperties"]


@pytest.mark.parametrize(
    ("status", "payload", "fragment"),
    [
        (401, {}, "authentication"),
        (404, {}, "HTTP 404"),
        (200, {"result": "not-kodi"}, "not a responding"),
        (200, {"error": {"code": -32601, "message": "private-secret"}}, "-32601"),
    ],
)
def test_kodi_probe_reports_http_and_rpc_errors_safely(status, payload, fragment):
    actual_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    with patch(
        "home_media_catalog.players.httpx.AsyncClient",
        side_effect=lambda **kw: actual_client(transport=transport, **kw),
    ):
        with pytest.raises(ConnectorError) as error:
            asyncio.run(probe({"host": "kodi", "port": 8080}))
    assert fragment in str(error.value) and "private-secret" not in str(error.value)


@pytest.mark.parametrize(
    "permissions",
    [
        {"ControlPlayback": False},
        {"controlplayback": False},
        {"ControlPlayback": False, "controlplayback": True},
    ],
)
def test_kodi_probe_refuses_denied_playback_permission(permissions):
    def handle(request):
        method = json.loads(request.content)["method"]
        return httpx.Response(
            200, json={"result": "pong" if method == "JSONRPC.Ping" else permissions}
        )

    actual_client = httpx.AsyncClient
    with patch(
        "home_media_catalog.players.httpx.AsyncClient",
        side_effect=lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw),
    ):
        with pytest.raises(ConnectorError, match="ControlPlayback=false"):
            asyncio.run(probe({"host": "kodi", "port": 8080}))


@pytest.mark.parametrize(
    "permissions",
    [
        {},
        {"ReadData": True},
        {"ControlPlayback": "false"},
        {"ControlPlayback": "true"},
        {"ControlPlayback": 1},
        {"ControlPlayback": None},
        {"ControlPlayback": None, "controlplayback": True},
        [],
        "private-secret",
        None,
    ],
)
def test_kodi_probe_reports_unexpected_permissions_without_claiming_access_is_denied(permissions):
    calls = []

    def handle(request):
        method = json.loads(request.content)["method"]
        calls.append(method)
        return httpx.Response(
            200, json={"result": "pong" if method == "JSONRPC.Ping" else permissions}
        )

    actual_client = httpx.AsyncClient
    with patch(
        "home_media_catalog.players.httpx.AsyncClient",
        side_effect=lambda **kw: actual_client(transport=httpx.MockTransport(handle), **kw),
    ):
        with pytest.raises(ConnectorError, match="Réponse JSONRPC.Permission inattendue") as error:
            asyncio.run(probe({"host": "kodi", "port": 8080}))
    assert "private-secret" not in str(error.value)
    assert calls == ["JSONRPC.Ping", "JSONRPC.Permission"]
