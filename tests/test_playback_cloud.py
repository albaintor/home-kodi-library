"""Protocol configurations and OAuth credentials must remain private."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import ADMIN, READ

from kodi_library.playback_cloud import CloudOAuth, CloudOAuthError
from kodi_library.settings import Settings


def test_network_fields_persist_and_password_is_redacted(client, admin):
    path = "/api/v1/playback/locations"
    password = "the-secret-password"
    data = {
        "name": "JEDI",
        "kind": "smb",
        "target": "smb://jedi:445/Series",
        "host": "jedi",
        "port": 445,
        "share": "Series",
        "username": "damien",
        "workgroup": "WORKGROUP",
        "password": password,
    }
    response = client.post(path, headers=admin, json=data)
    assert response.status_code == 201, response.text
    location_id = response.json()["id"]
    assert response.json()["has_password"] is True
    assert "password" not in response.json()
    assert password not in response.text
    assert response.json()["host"] == "jedi"
    assert response.json()["port"] == 445
    assert response.json()["workgroup"] == "WORKGROUP"

    connection = client.app.state.store.connect()
    with connection as db:
        row = db.execute(
            "SELECT password_cipher FROM playback_location_options WHERE location_id=?",
            (location_id,),
        ).fetchone()
        encrypted = row["password_cipher"]
    assert password not in encrypted
    assert encrypted

    # No password in response; PUT without password preserves stored credential.
    data.pop("password")
    data["name"] = "JEDI edited"
    updated = client.put(f"{path}/{location_id}", headers=admin, json=data)
    assert updated.status_code == 200, updated.text
    assert updated.json()["has_password"] is True
    read = {"Authorization": f"Bearer {READ}"}
    snapshot = client.get(path, headers=read)
    assert snapshot.status_code == 200
    assert password not in snapshot.text
    assert "password_cipher" not in snapshot.text
    assert client.get("/api/v1/status", headers=read).status_code == 200
    assert password not in client.get("/api/v1/status", headers=read).text


def test_cloud_not_configured_and_icloud_uses_files(client, admin):
    path = "/api/v1/playback/locations"
    cloud = client.post(
        path, headers=admin, json={"name": "Dropbox", "kind": "dropbox", "target": ""}
    ).json()
    state = client.get("/api/v1/playback/cloud/status", headers=admin).json()
    assert state[0]["id"] == cloud["id"]
    assert state[0]["configured"] is False and state[0]["connected"] is False
    result = client.post(f"/api/v1/playback/cloud/{cloud['id']}/authorize", headers=admin)
    assert result.status_code == 422
    assert client.post("/api/v1/playback/cloud/bad/authorize", headers=admin).status_code == 404
    icloud = client.post(
        path, headers=admin, json={"name": "iCloud", "kind": "icloud", "target": ""}
    ).json()
    assert (
        client.post(f"/api/v1/playback/cloud/{icloud['id']}/authorize", headers=admin).status_code
        == 422
    )


def test_oauth_pkce_and_single_use_callback(tmp_path):
    class FakeStore:
        def __init__(self):
            self.saved = []

        def playback_location(self, location_id):
            return {"id": location_id, "kind": "dropbox"}

        def set_playback_cloud_token(self, location_id, provider, cipher):
            self.saved.append((location_id, provider, cipher))

    settings = Settings(
        data_dir=tmp_path,
        api_token=ADMIN,
        cloud_oauth_redirect_url="https://example.org/api/v1/playback/cloud/callback",
        cloud_oauth_clients={"dropbox": {"client_id": "test-client"}},
    )
    settings.validate()
    store = FakeStore()
    manager = CloudOAuth(settings, store)
    url = manager.start("a", "dropbox")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    state = query["state"][0]
    assert query["client_id"] == ["test-client"]
    assert query["token_access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert "access_token" not in url

    class FakeReply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "private-cloud-token", "refresh_token": "refresh-private"}

    class FakeHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, data):
            assert url == "https://api.dropboxapi.com/oauth2/token"
            assert data["code_verifier"]
            assert data["code"] == "one-time-code"
            return FakeReply()

    import asyncio

    with patch("kodi_library.playback_cloud.httpx.AsyncClient", return_value=FakeHttp()):
        asyncio.run(manager.complete(state=state, code="one-time-code", error=""))
    assert len(store.saved) == 1
    assert "private-cloud-token" not in store.saved[0][2]
    assert "refresh-private" not in store.saved[0][2]
    assert (
        manager.fernet.decrypt(store.saved[0][2].encode()).decode().find("private-cloud-token") >= 0
    )

    with pytest.raises(CloudOAuthError):
        asyncio.run(manager.complete(state=state, code="one-time-code", error=""))
