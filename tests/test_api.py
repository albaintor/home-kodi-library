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
    assert client.get("/api/v1/facets", headers=admin).json()["formats"] == ["Blu-ray 4K"]
    assert client.get("/api/v1/media?format=4K", headers=admin).json()["total"] == 0
    assert client.get("/api/v1/media?format=Blu-ray%204K", headers=admin).json()["total"] == 1
    assert client.get("/api/v1/media?format=4K&format=HDR10%2B", headers=admin).json()["total"] == 0
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


def test_jobs_feed_is_authorized_and_lists_active_or_completed_work(client, admin):
    assert client.get("/api/v1/jobs").status_code == 401
    sid = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi",
            "type": "kodi",
            "config": {"host": "localhost", "username": "test"},
        },
    ).json()["id"]
    store = client.app.state.store
    job = store.new_job(sid)
    store.start_job(job["id"])
    read = {"Authorization": f"Bearer {READ}"}
    listed = client.get("/api/v1/jobs", headers=read)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == job["id"]
    assert listed.json()[0]["status"] == "running"
    store.finish_job(job["id"], result={"removed_editions": 1, "database": "MyVideos131"})
    latest = client.get("/api/v1/jobs", headers=admin).json()[0]
    assert latest["status"] == "completed"
    assert latest["result"]["removed_editions"] == 1


def test_media_diagnostics_checks_movie_and_reports_all_sources(client, admin, monkeypatch):
    sid = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "NAS Kodi",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader", "database": "MyVideos131"},
        },
    ).json()["id"]
    store = client.app.state.store
    store.apply_snapshot(
        sid,
        [
            Record(
                "movie:99:77",
                "movie",
                "Removed title",
                metadata={"kodi_id": 99, "file_id": 77, "database": "MyVideos131"},
            )
        ],
    )
    item = store.media()["items"][0]
    from kodi_library.connectors.kodi import KodiConnector

    monkeypatch.setattr(
        KodiConnector,
        "inspect_movie_links",
        lambda self, ids: {
            "database": "MyVideos131",
            "movies": {},
            "library_visible_movie_ids": [],
            "library_rule": "movie_view.isDefaultVersion = 1",
            "library_verified": True,
        },
    )
    read = {"Authorization": f"Bearer {READ}"}
    assert client.get(f"/api/v1/media/{item['id']}/diagnostics", headers=read).status_code == 403
    response = client.get(f"/api/v1/media/{item['id']}/diagnostics", headers=admin)
    assert response.status_code == 200, response.text
    diag = response.json()
    edition = diag["sources"][0]["editions"][0]
    assert diag["sources"][0]["queried_database"] == "MyVideos131"
    assert edition["exists_in_movie"] is False


def test_media_diagnostics_identifies_sql_movie_absent_from_navigation(client, admin, monkeypatch):
    from kodi_library.connectors.kodi import KodiConnector

    sid = client.post(
        "/api/v1/sources",
        headers=admin,
        json={
            "name": "Kodi",
            "type": "kodi",
            "config": {"host": "nas", "username": "reader", "database": "MyVideos131"},
        },
    ).json()["id"]
    store = client.app.state.store
    store.apply_snapshot(
        sid,
        [Record("movie:7:8", "movie", "Phantom", metadata={"kodi_id": 7, "file_id": 8})],
    )
    movie = store.media()["items"][0]
    store.save_player({"name": "Salon", "source_id": sid, "host": "192.168.1.3", "port": 8080})
    monkeypatch.setattr(
        KodiConnector,
        "inspect_movie_links",
        lambda self, ids: {
            "database": "MyVideos131",
            "library_visible_movie_ids": [],
            "library_rule": "movie_view.isDefaultVersion = 1",
            "library_verified": True,
            "movies": {
                7: {
                    "title": "Phantom",
                    "default_file_id": 8,
                    "file_row_exists": True,
                    "path_row_exists": True,
                    "file": "smb://nas/films/phantom.mkv",
                    "excluded_from_scan": 0,
                    "scan_disabled": 0,
                    "versions": [],
                }
            },
        },
    )

    response = client.get(f"/api/v1/media/{movie['id']}/diagnostics", headers=admin)
    assert response.status_code == 200, response.text
    info = response.json()["sources"][0]
    assert info["editions"][0]["sql_links"]["file_row_exists"] is True
    assert info["editions"][0]["exists_in_movie"] is True
    assert info["editions"][0]["in_library"] is False
    assert info["library_verified"] is True


def test_mobile_contract_announced_without_changing_versioned_endpoint(client, admin):
    # Existing mobile builds keep using /api/v1/status; new builds can
    # negotiate a future breaking data-model change before catalogue requests.
    status = client.get("/api/v1/status", headers=admin)
    assert status.status_code == 200
    assert status.json()["api_version"] == "v1"
    assert status.json()["mobile_compatibility"] == {"min_contract": 1, "max_contract": 1}
    assert status.json()["data_model_version"] == 3
    assert status.json()["version"].endswith("+model.3")

    discovery = client.get("/api/v1/discovery")
    assert discovery.status_code == 200
    assert discovery.json()["mobile_compatibility"] == {"min_contract": 1, "max_contract": 1}
    assert discovery.json()["data_model_version"] == status.json()["data_model_version"]
    assert discovery.json()["server_version"] == status.json()["version"]

    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/media", headers=admin).status_code == 200


def test_on_open_refresh_only_after_24_hours_and_never_duplicates(client, admin):
    """The web/mobile opening endpoint must not create jobs for recent sources."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from conftest import READ

    def source(label):
        response = client.post(
            "/api/v1/sources",
            headers=admin,
            json={
                "name": label,
                "type": "kodi",
                "config": {"host": "nas", "username": "reader"},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    stale_id = source("Older than 24 hours")
    recent_id = source("Updated an hour ago")
    store = client.app.state.store
    now = datetime.now(UTC)
    with store.connect() as conn:
        conn.execute(
            "UPDATE sources SET last_sync=? WHERE id=?",
            ((now - timedelta(hours=25)).isoformat(timespec="seconds"), stale_id),
        )
        conn.execute(
            "UPDATE sources SET last_sync=? WHERE id=?",
            ((now - timedelta(hours=1)).isoformat(timespec="seconds"), recent_id),
        )

    sync = client.app.state.sync
    recorded = []

    async def submit(source_id):
        recorded.append(source_id)
        sync.active.add(source_id)
        return {
            "id": "test-job",
            "source_id": source_id,
            "status": "pending",
            "created_at": now.isoformat(timespec="seconds"),
        }

    readonly = {"Authorization": f"Bearer {READ}"}
    with patch.object(sync, "submit", side_effect=submit):
        first = client.post("/api/v1/sources/sync-stale", headers=readonly)
        assert first.status_code == 202, first.text
        assert [item["source_id"] for item in first.json()] == [stale_id]
        # A second client can open while the first import is still running.
        second = client.post("/api/v1/sources/sync-stale", headers=readonly)
        assert second.status_code == 202
        assert second.json() == []
        assert recorded == [stale_id]

        sync.active.discard(stale_id)
        with store.connect() as conn:
            conn.execute(
                "UPDATE sources SET last_sync=? WHERE id=?",
                (datetime.now(UTC).isoformat(timespec="seconds"), stale_id),
            )
        third = client.post("/api/v1/sources/sync-stale", headers=readonly)
        assert third.status_code == 202
        assert third.json() == []
        assert recorded == [stale_id]


def test_default_scheduler_is_off_and_model_part_of_release():
    from kodi_library import DATA_MODEL_VERSION, __version__
    from kodi_library.settings import Settings

    assert Settings().scheduler_enabled is False
    assert __version__.endswith(f"+model.{DATA_MODEL_VERSION}")
