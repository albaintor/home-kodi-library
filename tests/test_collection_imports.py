"""Collection parser, API and atomic replacement coverage with representative XML fixtures."""

import time
from pathlib import Path

import pytest

from home_media_catalog.connectors.base import ConnectorError, Record
from home_media_catalog.connectors.collection_xml import parse_collection

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("provider", ["filmotech", "mymovies"])
def test_collection_metadata(provider):
    (record,) = parse_collection((FIXTURES / f"{provider}.xml").read_bytes(), provider)
    assert record.title == "Le Voyage" and record.original_title == "The Journey"
    assert record.year == 2020 and record.runtime_minutes == 123
    assert record.identifiers == {"imdb": "tt1234567"}
    assert record.watched is True and record.user_rating == 8
    assert record.genres == ["Drame", "Aventure"]
    assert "Blu-ray" in record.formats
    assert record.metadata["barcode"] == "1234567890123"
    assert {person["category"] for person in record.metadata["people"]} == {"cast", "director"}


@pytest.mark.parametrize("provider", ["filmotech", "mymovies"])
def test_invalid_xml_cannot_erase_collection(provider):
    for invalid in (
        b"<unrelated/>",
        b"<Titles/>",
        b"<Filmotech/>",
        b"<broken",
        b'<!DOCTYPE Titles [<!ENTITY x "secret">]><Titles>&x;</Titles>',
    ):
        with pytest.raises(ConnectorError):
            parse_collection(invalid, provider)
    valid = (FIXTURES / f"{provider}.xml").read_bytes()
    start, end = (
        (b"<Film ID=", b"</Film>") if provider == "filmotech" else (b"<Title>", b"</Title>")
    )
    entry = valid[valid.index(start) : valid.index(end) + len(end)]
    with pytest.raises(ConnectorError, match="Duplicate"):
        parse_collection(valid.replace(entry, entry + entry), provider)


def wait_job(client, admin, job):
    for _ in range(200):
        result = client.get(f"/api/v1/jobs/{job['id']}", headers=admin).json()
        if result["status"] in {"completed", "failed"}:
            return result
        time.sleep(0.01)
    raise AssertionError("Import did not complete")


@pytest.mark.parametrize("provider", ["filmotech", "mymovies"])
def test_import_api_reimport_filters_and_snapshot_preservation(client, admin, provider):
    source = client.post(
        "/api/v1/sources", headers=admin, json={"name": provider, "type": provider}
    ).json()
    other = client.post(
        "/api/v1/sources", headers=admin, json={"name": "CLZ", "type": "clz"}
    ).json()
    store = client.app.state.store
    store.apply_snapshot(other["id"], [Record(external_key="other", kind="movie", title="Other")])
    data = (FIXTURES / f"{provider}.xml").read_bytes()

    def upload(payload, source_id=source["id"]):
        return client.post(
            f"/api/v1/imports/{provider}",
            headers=admin,
            data={"source_id": source_id},
            files={"file": ("export.xml", payload)},
        )

    assert upload(data, other["id"]).status_code == 422
    assert upload(b"").status_code == 422
    response = upload(data)
    assert response.status_code == 202
    assert wait_job(client, admin, response.json())["status"] == "completed"
    page = client.get(f"/api/v1/media?source_type={provider}", headers=admin).json()
    assert page["total"] == 1
    work_id = page["items"][0]["id"]
    assert page["items"][0]["media_sources"][0]["type"] == provider
    assert wait_job(client, admin, upload(data).json())["status"] == "completed"
    assert (
        client.get(f"/api/v1/media?source_type={provider}", headers=admin).json()["items"][0]["id"]
        == work_id
    )
    assert wait_job(client, admin, upload(b"<invalid/>").json())["status"] == "failed"
    assert client.get("/api/v1/media", headers=admin).json()["total"] == 2
    assert client.get(f"/api/v1/media/{work_id}", headers=admin).status_code == 200


@pytest.mark.parametrize("provider", ["filmotech", "mymovies"])
def test_collection_merges_with_kodi_and_survives_kodi_removal(store, provider):
    kodi = store.save_source({"name": "Kodi", "type": "kodi", "config": {}})
    collection = store.save_source({"name": provider, "type": provider, "config": {}})
    (record,) = parse_collection((FIXTURES / f"{provider}.xml").read_bytes(), provider)
    store.apply_snapshot(
        kodi["id"],
        [
            Record(
                external_key="kodi:1",
                kind="movie",
                title=record.title,
                year=record.year,
                identifiers=record.identifiers,
            )
        ],
    )
    store.apply_snapshot(collection["id"], [record])
    assert store.media()["total"] == 1
    store.apply_snapshot(kodi["id"], [])
    page = store.media()
    assert page["total"] == 1
    assert page["items"][0]["source_types"] == [provider]
