import time

from conftest import ADMIN, INGRESS, READ

from kodi_library.connectors.base import Record


def test_auth_required_and_read_only_token(client):
    assert client.get("/api/v1/media").status_code == 401
    read = {"Authorization": f"Bearer {READ}"}
    assert client.get("/api/v1/media", headers=read).status_code == 200
    assert (
        client.post("/api/v1/sources", headers=read, json={"name": "X", "type": "clz"}).status_code
        == 403
    )
    assert client.get("/api/v1/openapi.json").status_code == 401
    assert (
        client.get("/api/v1/media", headers={"X-Kodi-Library-Ingress-Token": INGRESS}).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/media",
            headers={"X-Ingress-Path": "/spoof", "X-Kodi-Library-Ingress-Token": "wrong"},
        ).status_code
        == 401
    )


def test_browser_session_csrf_logout_and_no_bearer_fallback(client):
    login = client.post("/api/v1/auth/session", json={"token": ADMIN})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "samesite=lax" in login.headers["set-cookie"].lower()
    assert client.get("/api/v1/media").status_code == 200
    assert client.get("/api/v1/media", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/v1/sources", json={"name": "X", "type": "clz"}).status_code == 403
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert (
        client.post("/api/v1/sources", headers=csrf, json={"name": "X", "type": "clz"}).status_code
        == 201
    )
    assert client.delete("/api/v1/auth/session", headers=csrf).status_code == 204
    assert client.get("/api/v1/media").status_code == 401


def test_read_session_can_logout_and_rate_limit(client):
    login = client.post("/api/v1/auth/session", json={"token": READ})
    assert (
        client.delete(
            "/api/v1/auth/session", headers={"X-CSRF-Token": login.json()["csrf_token"]}
        ).status_code
        == 204
    )
    for _ in range(10):
        assert client.post("/api/v1/auth/session", json={"token": "invalid"}).status_code == 401
    assert client.post("/api/v1/auth/session", json={"token": "invalid"}).status_code == 429


def test_source_secrets_redacted_and_null_password_preserves_existing(client, admin):
    body = {
        "name": "Kodi",
        "type": "kodi",
        "config": {
            "host": "nas",
            "username": "reader",
            "password": "private-secret",
            "database": "MyVideos131",
        },
    }
    response = client.post("/api/v1/sources", headers=admin, json=body)
    assert response.status_code == 201
    sid = response.json()["id"]
    assert "private-secret" not in response.text
    assert response.json()["config"]["has_password"]
    body["config"]["password"] = None
    assert client.put(f"/api/v1/sources/{sid}", headers=admin, json=body).status_code == 200
    assert (
        client.app.state.store.source(sid, private=True)["config"]["password"] == "private-secret"
    )
    assert "private-secret" not in client.get("/api/v1/status", headers=admin).text


def test_validation_parameters_and_redacted_errors(client, admin):
    assert client.get("/api/v1/media?limit=201", headers=admin).status_code == 422
    assert client.get("/api/v1/media?sort=drop", headers=admin).status_code == 422
    response = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "X",
            "type": "kodi",
            "config": {
                "host": "nas",
                "username": "u",
                "password": "private-secret",
                "database": "MyVideos;DROP",
            },
        },
    )
    assert response.status_code == 422
    assert "private-secret" not in response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_api_pagination_detail_facets_and_artwork_auth(client, admin):
    sid = client.post("/api/v1/sources", headers=admin, json={"name": "X", "type": "clz"}).json()[
        "id"
    ]
    client.app.state.store.apply_snapshot(
        sid,
        [
            Record(
                "one",
                "movie",
                "Example",
                genres=["Adventure"],
                formats=["4K UHD"],
                poster=["https://example.com/cover.jpg"],
            )
        ],
    )
    page = client.get("/api/v1/media?genre=Adventure&format=4K%20UHD", headers=admin).json()
    assert page["total"] == 1 and not page["has_more"]
    mid = page["items"][0]["id"]
    assert (
        client.get(f"/api/v1/media/{mid}", headers=admin).json()["editions"][0]["external_key"]
        == "one"
    )
    assert client.get("/api/v1/facets", headers=admin).json()["formats"] == ["4K UHD"]
    assert client.get(page["items"][0]["artwork"]["poster"]).status_code == 401
    assert client.get("/api/v1/media/missing", headers=admin).status_code == 404


def test_async_import_and_invalid_xml_retains_previous_data(client, admin, clz_xml):
    sid = client.post("/api/v1/sources", headers=admin, json={"name": "CLZ", "type": "clz"}).json()[
        "id"
    ]

    def upload(data):
        response = client.post(
            "/api/v1/imports/clz",
            headers=admin,
            data={"source_id": sid},
            files={"file": ("export.xml", data, "application/xml")},
        )
        assert response.status_code == 202
        for _ in range(200):
            job = client.get("/api/v1/jobs/" + response.json()["id"], headers=admin).json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.01)
        raise AssertionError("job did not complete")

    assert upload(clz_xml)["status"] == "completed"
    assert client.get("/api/v1/status", headers=admin).json()["editions"] == 4
    assert upload(b"not xml")["status"] == "failed"
    assert client.get("/api/v1/status", headers=admin).json()["editions"] == 4


def test_openapi_contract_is_independent_of_ui(client, admin):
    schema = client.get("/api/v1/openapi.json", headers=admin).json()
    assert schema["components"]["securitySchemes"]["BearerToken"]["scheme"] == "bearer"
    assert "/api/v1/media" in schema["paths"]
    assert "/api/v1/imports/clz" in schema["paths"]
    assert "/api/v1/players/{player_id}/play" in schema["paths"]
    assert schema["components"]["schemas"]["MediaPage"]["properties"]["items"]["items"][
        "$ref"
    ].endswith("MediaSummary")


def test_api_does_not_require_frontend_cookie(client, admin):
    assert not client.cookies
    response = client.get("/api/v1/media?q=anything&sort=title&order=asc", headers=admin)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert not client.cookies
