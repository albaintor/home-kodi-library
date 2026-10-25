from conftest import READ


def test_playback_locations_auth_and_persistence(client, admin):
    endpoint = "/api/v1/playback/locations"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=admin).json() == []
    viewer = {"Authorization": f"Bearer {READ}"}
    assert client.post(endpoint, headers=viewer, json={
        "name": "NAS", "kind": "smb", "target": "smb://nas/Movies"
    }).status_code == 403

    created = client.post(endpoint, headers=admin, json={
        "name": "NAS", "kind": "smb", "target": "smb://nas/Movies"
    })
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]
    assert client.get(endpoint, headers=viewer).json()[0]["name"] == "NAS"
    updated = client.put(f"{endpoint}/{location_id}", headers=admin, json={
        "name": "NAS 2", "kind": "nfs", "target": "nfs://nas/export/Movies"
    })
    assert updated.status_code == 200, updated.text
    assert client.app.state.store.playback_locations()[0]["kind"] == "nfs"
    assert client.delete(f"{endpoint}/{location_id}", headers=viewer).status_code == 403
    assert client.delete(f"{endpoint}/{location_id}", headers=admin).status_code == 204
    assert client.get(endpoint, headers=admin).json() == []
    assert client.delete(f"{endpoint}/{location_id}", headers=admin).status_code == 404


def test_playback_location_rejects_embedded_credentials(client, admin):
    endpoint = "/api/v1/playback/locations"
    response = client.post(endpoint, headers=admin, json={
        "name": "Unsafe", "kind": "ftp", "target": "ftp://user:secret@nas/videos"
    })
    assert response.status_code == 422
    assert "secret" not in response.text
    assert client.post(endpoint, headers=admin, json={
        "name": "Unknown", "kind": "telnet", "target": "telnet://nas"
    }).status_code == 422
