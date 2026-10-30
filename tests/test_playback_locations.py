from conftest import READ


def test_playback_locations_auth_and_persistence(client, admin):
    endpoint = "/api/v1/playback/locations"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=admin).json() == []
    viewer = {"Authorization": f"Bearer {READ}"}
    assert (
        client.post(
            endpoint,
            headers=viewer,
            json={"name": "NAS", "kind": "smb", "target": "smb://nas/Movies"},
        ).status_code
        == 403
    )

    created = client.post(
        endpoint, headers=admin, json={"name": "NAS", "kind": "smb", "target": "smb://nas/Movies"}
    )
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]
    assert client.get(endpoint, headers=viewer).json()[0]["name"] == "NAS"
    updated = client.put(
        f"{endpoint}/{location_id}",
        headers=admin,
        json={"name": "NAS 2", "kind": "nfs", "target": "nfs://nas/export/Movies"},
    )
    assert updated.status_code == 200, updated.text
    assert client.app.state.store.playback_locations()[0]["kind"] == "nfs"
    assert client.delete(f"{endpoint}/{location_id}", headers=viewer).status_code == 403
    assert client.delete(f"{endpoint}/{location_id}", headers=admin).status_code == 204
    assert client.get(endpoint, headers=admin).json() == []
    assert client.delete(f"{endpoint}/{location_id}", headers=admin).status_code == 404


def test_playback_location_rejects_embedded_credentials(client, admin):
    endpoint = "/api/v1/playback/locations"
    response = client.post(
        endpoint,
        headers=admin,
        json={"name": "Unsafe", "kind": "ftp", "target": "ftp://user:secret@nas/videos"},
    )
    assert response.status_code == 422
    assert "secret" not in response.text
    assert (
        client.post(
            endpoint,
            headers=admin,
            json={"name": "Unknown", "kind": "telnet", "target": "telnet://nas"},
        ).status_code
        == 422
    )


def test_smb_hostname_alias_roundtrip_and_validation(client, admin):
    endpoint = "/api/v1/playback/locations"
    payload = {
        "name": "NAS via IP",
        "kind": "smb",
        "target": "smb://192.168.1.20/Movies",
        "host": "192.168.1.20",
        "hostname": "Kodi-NAS.local.",
        "share": "Movies",
    }
    created = client.post(endpoint, headers=admin, json=payload)
    assert created.status_code == 201, created.text
    location = created.json()
    assert location["host"] == "192.168.1.20"
    assert location["hostname"] == "Kodi-NAS.local"
    location_id = location["id"]
    assert client.get(endpoint, headers=admin).json()[0]["hostname"] == "Kodi-NAS.local"

    payload["hostname"] = "Other-NAS"
    updated = client.put(f"{endpoint}/{location_id}", headers=admin, json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["hostname"] == "Other-NAS"
    assert client.app.state.store.playback_location(location_id)["hostname"] == "Other-NAS"

    for invalid in ("smb://other", "a/b", "host:445", "some host"):
        response = client.put(
            f"{endpoint}/{location_id}",
            headers=admin,
            json={**payload, "hostname": invalid},
        )
        assert response.status_code == 422, (invalid, response.text)
    assert client.get(endpoint, headers=admin).json()[0]["hostname"] == "Other-NAS"


def test_sftp_source_without_server_password_has_actionable_browse_error(client, admin):
    endpoint = "/api/v1/playback/locations"
    created = client.post(
        endpoint,
        headers=admin,
        json={
            "name": "SFTP on iOS",
            "kind": "sftp",
            "target": "sftp://192.168.1.10/Movies",
            "host": "192.168.1.10",
            "share": "Movies",
            "username": "media",
        },
    )
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]
    assert created.json()["has_password"] is False

    response = client.get(f"/api/v1/playback/browse/{location_id}", headers=admin)
    assert response.status_code == 409
    assert "server-side username and password" in response.json()["detail"]


def test_sftp_server_password_is_encrypted_and_kept_on_descriptor_updates(client, admin):
    endpoint = "/api/v1/playback/locations"
    location = {
        "name": "SFTP",
        "kind": "sftp",
        "host": "192.168.1.10",
        "target": "sftp://192.168.1.10/Movies",
        "share": "Movies",
        "username": "media",
        "password": "private-test-password",
    }
    created = client.post(endpoint, headers=admin, json=location)
    assert created.status_code == 201, created.text
    assert created.json()["has_password"] is True
    assert "private-test-password" not in created.text
    location_id = created.json()["id"]

    updated = client.put(
        f"{endpoint}/{location_id}",
        headers=admin,
        json={key: value for key, value in location.items() if key != "password"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["has_password"] is True
    assert "private-test-password" not in updated.text


def test_mobile_network_credentials_provisioning(client, admin):
    """Ordinary source lists redact passwords; only admins can provision a device."""
    from conftest import READ

    endpoint = "/api/v1/playback/locations"
    password = "ios-device-only-test"
    created = client.post(
        endpoint, headers=admin,
        json={
            "name": "Mobile SMB", "kind": "smb",
            "target": "smb://nas/Movies", "username": "media",
            "password": password,
        },
    )
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]
    route = f"{endpoint}/{location_id}/device-credentials"
    assert password not in client.get(endpoint, headers=admin).text
    assert client.get(route).status_code == 401
    assert client.get(
        route, headers={"Authorization": f"Bearer {READ}"}
    ).status_code == 403
    answer = client.get(route, headers=admin)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"id": location_id, "password": password}
    assert answer.headers["cache-control"] == "no-store"
