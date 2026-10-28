"""Kodi credentials are admin-only and never part of public player views."""

from conftest import ADMIN, READ


def test_kodi_credentials_are_admin_only_uncached_and_not_in_player_list(client):
    admin = {"Authorization": f"Bearer {ADMIN}"}
    readonly = {"Authorization": f"Bearer {READ}"}
    source = client.post(
        "/api/v1/sources",
        headers=admin,
        json={"name": "Kodi catalogue", "type": "kodi"},
    )
    assert source.status_code == 201
    player = client.post(
        "/api/v1/players",
        headers=admin,
        json={
            "name": "Living room",
            "source_id": source.json()["id"],
            "host": "192.168.1.30",
            "port": 8080,
            "websocket_port": 9090,
            "username": "kodi",
            "password": "local-kodi-secret",
        },
    )
    assert player.status_code == 201
    player_id = player.json()["id"]
    path = f"/api/v1/players/{player_id}/device-credentials"

    listed = client.get("/api/v1/players", headers=readonly)
    assert listed.status_code == 200
    assert listed.json()[0]["has_password"] is True
    assert "password" not in listed.text
    assert "local-kodi-secret" not in listed.text

    assert client.get(path).status_code == 401
    assert client.get(path, headers=readonly).status_code == 403
    missing = client.get(
        "/api/v1/players/00000000-0000-0000-0000-000000000000/device-credentials",
        headers=admin,
    )
    assert missing.status_code == 404

    response = client.get(path, headers=admin)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.json() == {
        "player_id": player_id,
        "username": "kodi",
        "password": "local-kodi-secret",
    }
    schema = client.get("/api/v1/openapi.json", headers=admin).json()
    assert "/api/v1/players/{player_id}/device-credentials" not in schema["paths"]

    # Empty password updates must remain consistent with the existing server
    # semantics: null/omitted preserves, explicit empty clears the password.
    updated = client.put(
        f"/api/v1/players/{player_id}",
        headers=admin,
        json={
            "name": "Living room",
            "source_id": source.json()["id"],
            "host": "192.168.1.30",
            "username": "kodi",
            "password": "",
        },
    )
    assert updated.status_code == 200
    assert client.get(path, headers=admin).json()["password"] == ""
