"""Protocol configurations and OAuth credentials must remain private."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import ADMIN, READ

from home_media_catalog.playback_cloud import CloudOAuth, CloudOAuthError
from home_media_catalog.settings import Settings


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
    # Readiness is available BEFORE a location is added and never leaks IDs
    # or OAuth secrets. The browser can avoid opening a useless blank popup.
    readiness = client.get("/api/v1/playback/cloud/providers", headers=admin)
    assert readiness.status_code == 200
    dropbox = next(p for p in readiness.json() if p["provider"] == "dropbox")
    assert dropbox["configured"] is False
    assert "application OAuth" in dropbox["reason"]
    assert "client_secret" not in readiness.text
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

        def playback_oauth_app(self, provider):
            return None

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
    url = manager.start("a", "dropbox", return_to="ios")
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

    with patch("home_media_catalog.playback_cloud.httpx.AsyncClient", return_value=FakeHttp()):
        result = asyncio.run(manager.complete(state=state, code="one-time-code", error=""))
    assert result == "ios"
    assert len(store.saved) == 1
    assert "private-cloud-token" not in store.saved[0][2]
    assert "refresh-private" not in store.saved[0][2]
    assert (
        manager.fernet.decrypt(store.saved[0][2].encode()).decode().find("private-cloud-token") >= 0
    )

    with pytest.raises(CloudOAuthError):
        asyncio.run(manager.complete(state=state, code="one-time-code", error=""))


def test_cloud_oauth_configuration_diagnostics(tmp_path):
    class FakeStore:
        def playback_oauth_app(self, provider):
            return None

    settings = Settings(
        data_dir=tmp_path,
        api_token=ADMIN,
        cloud_oauth_clients={"onedrive": {"client_id": "1234"}},
        cloud_oauth_redirect_url="",
    )
    manager = CloudOAuth(settings, FakeStore())
    assert "secret" in manager.configuration_error("onedrive")
    manager.clients["onedrive"]["client_secret"] = "server-only-secret"
    assert "HTTPS" in manager.configuration_error("onedrive")
    manager.redirect = "https://example.org/api/v1/playback/cloud/callback"
    assert manager.configuration_error("onedrive") is None
    assert manager.configured("onedrive")
    with pytest.raises(CloudOAuthError):
        manager.start("id", "unknown")


def test_cloud_token_auto_refresh(tmp_path):
    import asyncio
    import json

    class FakeStore:
        def __init__(self):
            self.cipher = None

        def playback_oauth_app(self, provider):
            return None

        def playback_cloud_credentials(self, location_id):
            return {"provider": "onedrive", "token_cipher": self.cipher}

        def set_playback_cloud_token(self, location_id, provider, cipher):
            assert location_id == "onedrive-1"
            assert provider == "onedrive"
            self.cipher = cipher

    settings = Settings(
        data_dir=tmp_path,
        api_token=ADMIN,
        cloud_oauth_redirect_url="https://example.org/api/v1/playback/cloud/callback",
        cloud_oauth_clients={
            "onedrive": {"client_id": "test-client", "client_secret": "server-secret"}
        },
    )
    store = FakeStore()
    oauth = CloudOAuth(settings, store)
    store.cipher = oauth.encrypt_password(
        json.dumps(
            {"access_token": "expired", "refresh_token": "rotating-refresh", "expires_at": 1}
        )
    )

    class FakeReply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "new-token", "expires_in": 3600}

    class FakeHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, data):
            assert url.endswith("/token")
            assert data["refresh_token"] == "rotating-refresh"
            assert data["client_secret"] == "server-secret"
            return FakeReply()

    with patch("home_media_catalog.playback_cloud.httpx.AsyncClient", return_value=FakeHttp()):
        credential = asyncio.run(oauth.credentials("onedrive-1", "onedrive"))
    assert credential["access_token"] == "new-token"
    assert credential["refresh_token"] == "rotating-refresh"
    assert "new-token" not in store.cipher
    assert json.loads(oauth.decrypt_secret(store.cipher))["access_token"] == "new-token"


def test_onedrive_device_configured_without_domain(tmp_path):
    from home_media_catalog.playback_cloud import ONEDRIVE_PUBLIC_CLIENT_ID

    class Store:
        def playback_oauth_app(self, provider):
            return None

    manager = CloudOAuth(Settings(data_dir=tmp_path, api_token=ADMIN), Store())
    assert manager.device_configuration_error("onedrive") is None
    assert manager.device_client_id("onedrive") == ONEDRIVE_PUBLIC_CLIENT_ID
    assert not manager.configured("onedrive")
    assert manager.device_configuration_error("dropbox")


def test_onedrive_device_code_start_pending_and_success(tmp_path):
    import asyncio
    import json
    from unittest.mock import patch

    from home_media_catalog.playback_cloud import (
        MICROSOFT_DEVICE_ENDPOINT,
        MICROSOFT_DEVICE_VERIFICATION_URL,
        MICROSOFT_TOKEN_ENDPOINT,
    )

    class Store:
        cipher = None

        def playback_oauth_app(self, provider):
            return None

        def playback_location(self, location_id):
            return {"id": location_id, "kind": "onedrive"}

        def set_playback_cloud_token(self, location_id, provider, cipher):
            assert location_id == "test-location"
            assert provider == "onedrive"
            self.cipher = cipher

        def playback_cloud_credentials(self, location_id):
            return {"provider": "onedrive", "token_cipher": self.cipher}

    class Reply:
        def __init__(self, status, payload):
            self.status_code = status
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("bad status")

    class Http:
        attempts = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, data):
            if url == MICROSOFT_DEVICE_ENDPOINT:
                assert data["scope"] == "offline_access Files.Read"
                return Reply(
                    200,
                    {
                        "device_code": "a-private-code",
                        "user_code": "QWER-1234",
                        # Microsoft may return a different verification domain.
                        # No provider-supplied URL may become a user-facing link.
                        "verification_uri": "https://aka.ms/devicelogin",
                        "expires_in": 900,
                        "interval": 5,
                    },
                )
            assert url == MICROSOFT_TOKEN_ENDPOINT
            assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
            self.attempts += 1
            if self.attempts == 1:
                return Reply(400, {"error": "authorization_pending"})
            return Reply(
                200,
                {
                    "access_token": "secret-access",
                    "refresh_token": "secret-refresh",
                    "expires_in": 3600,
                },
            )

    store = Store()
    oauth = CloudOAuth(Settings(data_dir=tmp_path, api_token=ADMIN), store)
    transport = Http()

    async def scenario():
        with patch("home_media_catalog.playback_cloud.httpx.AsyncClient", return_value=transport):
            started = await oauth.start_device("test-location", "onedrive")
            assert started["user_code"] == "QWER-1234"
            assert started["verification_uri"] == MICROSOFT_DEVICE_VERIFICATION_URL
            assert "aka.ms" not in started["verification_uri"]
            assert "device_code" not in started
            assert "access_token" not in json.dumps(started)
            request_id = started["request_id"]
            assert (await oauth.poll_device("test-location", request_id))["status"] == "pending"
            assert transport.attempts == 0  # obey 5s Microsoft interval
            oauth.pending_devices[request_id]["next_poll"] = 0
            assert (await oauth.poll_device("test-location", request_id))["status"] == "pending"
            assert transport.attempts == 1
            oauth.pending_devices[request_id]["next_poll"] = 0
            assert await oauth.poll_device("test-location", request_id) == {"status": "connected"}
            assert (await oauth.poll_device("test-location", request_id))["status"] == "expired"

    asyncio.run(scenario())
    assert "secret-access" not in store.cipher
    token = json.loads(oauth.decrypt_secret(store.cipher))
    assert token["access_token"] == "secret-access"
    assert token["_hkl_client_id"] == oauth.device_client_id("onedrive")


def test_onedrive_public_client_api_without_redirect(client, admin):
    from home_media_catalog.playback_cloud import ONEDRIVE_PUBLIC_CLIENT_ID

    saved = client.put(
        "/api/v1/playback/cloud/apps/onedrive",
        headers=admin,
        json={"client_id": ONEDRIVE_PUBLIC_CLIENT_ID, "redirect_url": ""},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["device_configured"] is True
    providers = client.get("/api/v1/playback/cloud/providers", headers=admin)
    assert providers.status_code == 200
    one = next(row for row in providers.json() if row["provider"] == "onedrive")
    assert one["device_supported"] is True
    assert one["device_configured"] is True
    assert one["configured"] is False
    assert one["device_client_id"] == ONEDRIVE_PUBLIC_CLIENT_ID


def test_onedrive_device_routes_do_not_expose_microsoft_secrets(client, admin, monkeypatch):
    from home_media_catalog.playback_cloud import CloudOAuth

    cloud = client.post(
        "/api/v1/playback/locations",
        headers=admin,
        json={"name": "OneDrive", "kind": "onedrive", "target": ""},
    ).json()

    async def mock_start(self, location_id, provider):
        assert location_id == cloud["id"] and provider == "onedrive"
        return {
            "request_id": "x" * 32,
            "user_code": "BCDF-HJKL",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        }

    async def mock_poll(self, location_id, request_id):
        assert location_id == cloud["id"] and request_id == "x" * 32
        return {"status": "pending", "retry_after": 5}

    monkeypatch.setattr(CloudOAuth, "start_device", mock_start)
    monkeypatch.setattr(CloudOAuth, "poll_device", mock_poll)
    start = client.post(f"/api/v1/playback/cloud/{cloud['id']}/device/start", headers=admin)
    assert start.status_code == 200
    assert "user_code" in start.json()
    assert "device_code" not in start.text
    assert "access_token" not in start.text
    polling = client.post(
        f"/api/v1/playback/cloud/{cloud['id']}/device/poll",
        headers=admin,
        json={"request_id": "x" * 32},
    )
    assert polling.status_code == 200
    assert polling.json() == {"status": "pending", "retry_after": 5}
    invalid = client.post(
        f"/api/v1/playback/cloud/{cloud['id']}/device/poll",
        headers=admin,
        json={"request_id": "../bad"},
    )
    assert invalid.status_code == 422


def test_cloud_account_email_uses_location_identity_and_never_exposes_tokens(tmp_path):
    import asyncio

    class Store:
        def playback_oauth_app(self, _provider):
            return None

    oauth = CloudOAuth(Settings(data_dir=tmp_path, api_token=ADMIN), Store())

    async def credentials(location_id, provider):
        assert provider == "onedrive"
        return {"access_token": f"private-token-{location_id}"}

    oauth.credentials = credentials

    class Reply:
        status_code = 200

        def __init__(self, token):
            self.token = token

        def json(self):
            return {
                "owner": {"user": {"email": f"{self.token}@example.net"}}
            }

    class HTTP:
        calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, headers):
            assert url == "https://graph.microsoft.com/v1.0/me/drive"
            token = headers["Authorization"].removeprefix("Bearer private-token-")
            self.calls.append(token)
            return Reply(token)

    http = HTTP()

    async def check():
        with patch("home_media_catalog.playback_cloud.httpx.AsyncClient", return_value=http):
            assert await oauth.account_email("one", "onedrive") == "one@example.net"
            assert await oauth.account_email("two", "onedrive") == "two@example.net"
            assert await oauth.account_email("one", "onedrive") == "one@example.net"
        assert http.calls == ["one", "two"]
        # The profile cache stores only display emails, never OAuth tokens.
        assert all(
            "private-token" not in str(value)
            for value in oauth._account_labels.values()
        )

    asyncio.run(check())
