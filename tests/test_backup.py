"""Portable catalogue backup: authentication, exact restore, redaction and atomicity."""

from copy import deepcopy

from conftest import READ

from kodi_library.connectors.base import Record


def _library(client):
    store = client.app.state.store
    source = store.save_source({
        "name": "Disc archive",
        "type": "clz",
        "config": {"password": "server-only-secret", "host": "nas"},
    })
    store.apply_snapshot(source["id"], [
        Record("disc:1", "movie", "Example film", year=2020),
        Record("disc:2", "movie", "Another film", year=2021),
    ])
    work = next(item for item in store.media()["items"]
                if item["title"] == "Example film")["id"]
    store.update_personal(work, {"user_rating": 9.0, "watched": 1})
    playlist = store.save_list({"name": "Favorites", "description": "My selection"})
    store.change_list_item(playlist["id"], work)
    return store, source["id"], work, playlist["id"]


def test_export_import_round_trip_and_thumbnail_revision(client, admin):
    store, source_id, work_id, list_id = _library(client)
    store.register_device_token("test-device", "iPhone", "admin")
    assert client.get("/api/v1/backup").status_code == 401
    read = {"Authorization": f"Bearer {READ}"}
    assert client.get("/api/v1/backup", headers=read).status_code == 200
    assert client.post("/api/v1/backup", headers=read, json={}).status_code == 403

    exported = client.get("/api/v1/backup", headers=admin)
    assert exported.status_code == 200
    document = exported.json()
    assert document["format"] == "home-kodi-library"
    assert "device_tokens" not in document["tables"]
    assert "writeback_pending" not in document["tables"]
    assert "server-only-secret" not in exported.text
    assert document["credentials_omitted"] is True
    original_revision = store.lists()[0]["revision"]

    store.update_personal(work_id, {"user_rating": 1.0, "watched": 0})
    store.delete_list(list_id)
    response = client.post("/api/v1/backup", headers=admin, json=document)
    assert response.status_code == 200, response.text
    assert response.json()["lists"] == 1
    assert store.detail(work_id)["personal"]["user_rating"] == 9.0
    assert store.detail(work_id)["personal"]["watched"] == 1
    restored = store.lists()[0]
    assert restored["id"] == list_id
    assert restored["revision"] == original_revision + 1
    assert store.list_page(list_id)["items"][0]["id"] == work_id
    assert list(client.app.state.list_thumbnails.directory.glob(f"{list_id}-*.jpg"))
    assert store.source(source_id, private=True)["config"]["password"] == "server-only-secret"
    assert store.device_tokens()[0]["token_hash"] == "test-device"
    assert any(item["id"] == work_id for item in store.media(q="Example")["items"])


def test_invalid_backup_is_rejected_without_partial_import(client, admin):
    store, _, work_id, _ = _library(client)
    original = client.get("/api/v1/backup", headers=admin).json()
    incomplete = deepcopy(original)
    del incomplete["tables"]["works"]
    assert client.post("/api/v1/backup", headers=admin, json=incomplete).status_code == 422

    broken = deepcopy(original)
    broken["tables"]["list_items"][0]["work_id"] = "unknown-work"
    assert client.post("/api/v1/backup", headers=admin, json=broken).status_code == 422
    assert store.detail(work_id)["title"] == "Example film"
    assert len(store.lists()) == 1
