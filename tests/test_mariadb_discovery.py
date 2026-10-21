"""MyVideos discovery is read-only, scoped to entered SQL credentials and safe for v1 clients."""

from unittest.mock import patch

from kodi_library.connectors.kodi import KodiConnector
from conftest import READ


class FakeCursor:
    def __init__(self, names):
        self.names = names
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)

    def fetchall(self):
        return [{"Database": name} for name in self.names]


def test_discover_sorts_exact_video_database_names_only():
    cursor = FakeCursor(["mysql", "MyVideos99", "MyVideos131", "MyVideos135", "MyVideos33", "MyVideos131_old", "MyMusic82"])
    connector = KodiConnector({"database": "MyVideos131"})
    discovered = connector._database_discovery(cursor)
    assert [item["name"] for item in discovered["databases"]] == ["MyVideos135", "MyVideos131", "MyVideos99", "MyVideos33"]
    assert discovered["automatic_database"] == "MyVideos135"
    assert discovered["selected_database"] == "MyVideos131"
    assert cursor.sql == ["SHOW DATABASES"]


def test_discover_empty_does_not_guess():
    result = KodiConnector({})._database_discovery(FakeCursor(["mysql", "MyMusic82"]))
    assert result == {"databases": [], "automatic_database": None, "selected_database": None}


def test_discovery_endpoint_is_admin_only_and_does_not_persist_credentials(client, admin):
    body = {"config": {"host": "nas", "username": "reader", "password": "sensitive"}}
    path = "/api/v1/connections/mariadb/databases"
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"Authorization": f"Bearer {READ}"}).status_code == 403
    with patch.object(KodiConnector, "discover_databases", return_value={
        "databases": [{"name": "MyVideos131", "version": 131}],
        "automatic_database": "MyVideos131", "selected_database": None,
    }) as probe:
        result = client.post(path, headers=admin, json=body)
    assert result.status_code == 200
    assert result.json()["databases"][0]["name"] == "MyVideos131"
    assert "sensitive" not in result.text
    assert probe.call_count == 1
    assert client.app.state.store.sources() == []


def test_discovery_uses_saved_secret_on_edit_without_changing_source(client, admin):
    saved = {"config": {"host": "nas", "username": "reader", "password": "protected"}}
    sid = client.post("/api/v1/sources", headers=admin, json={"name": "Kodi", "type": "kodi", **saved}).json()["id"]
    payload = {"source_id": sid, "config": {"host": "nas-new", "username": "reader", "password": None}}
    captured = []

    def discover(connector):
        captured.append(connector.config)
        return {"databases": [], "automatic_database": None, "selected_database": None}

    with patch.object(KodiConnector, "discover_databases", discover):
        result = client.post("/api/v1/connections/mariadb/databases", headers=admin, json=payload)
    assert result.status_code == 200
    assert captured[0]["password"] == "protected"
    assert client.app.state.store.source(sid, private=True)["config"]["host"] == "nas"
