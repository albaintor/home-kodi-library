"""Server startup reconciles AI vectors without blocking HTTP availability."""

import asyncio
import time
from threading import Event

from conftest import ADMIN, INGRESS, READ
from fastapi.testclient import TestClient

from home_media_catalog.ai import AIConfiguration, AISettingsInput
from home_media_catalog.ai_vectors import SemanticIndex
from home_media_catalog.app import create_app
from home_media_catalog.connectors.base import Record
from home_media_catalog.settings import Settings
from home_media_catalog.store import Store


def server(tmp_path):
    return TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                api_token=ADMIN,
                read_token=READ,
                ingress_token=INGRESS,
            )
        )
    )


def wait_for_index(client, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(
            "/api/v1/ai/status", headers={"Authorization": f"Bearer {ADMIN}"}
        ).json()["index"]
        if status["complete"] and not status["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("The automatic startup index did not finish.")


def test_startup_reindexes_changed_vectors_but_not_unchanged_ones(tmp_path, monkeypatch):
    store = Store(tmp_path / "catalogue.db")
    store.initialize()
    source = store.save_source({"name": "Films", "type": "clz"})
    store.apply_snapshot(source["id"], [Record("film", "movie", "Voyage", plot="Ancien synopsis")])
    AIConfiguration(tmp_path).update(AISettingsInput(api_key="test-only-key"))
    index = SemanticIndex(tmp_path)
    calls = []
    embedded = Event()

    async def fake_embed(texts, model, key):
        calls.extend(texts)
        embedded.set()
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("home_media_catalog.ai_vectors.embed", fake_embed)
    assert (
        asyncio.run(index.refresh(store.ai_documents(), "text-embedding-3-small", "test-only-key"))
        == 1
    )
    calls.clear()
    embedded.clear()

    # No API request or manual refresh: entering the application lifespan
    # must notice that the only changed document has a different hash.
    store.apply_snapshot(source["id"], [Record("film", "movie", "Voyage", plot="Nouveau synopsis")])
    with server(tmp_path) as client:
        assert client.get("/healthz").status_code == 200
        assert embedded.wait(5)
        status = wait_for_index(client)
        assert status["indexed"] == status["total"] == 1
        assert len(calls) == 1
        assert "Nouveau synopsis" in calls[0]

    # Restart with a complete index: startup still checks hashes but never
    # contacts the embeddings API if none of the documents changed.
    calls.clear()
    with server(tmp_path) as client:
        assert wait_for_index(client)["complete"]
        assert calls == []


def test_startup_skips_indexing_when_ai_is_not_configured(tmp_path, monkeypatch):
    store = Store(tmp_path / "catalogue.db")
    store.initialize()
    source = store.save_source({"name": "Films", "type": "clz"})
    store.apply_snapshot(source["id"], [Record("film", "movie", "Voyage")])
    attempted = Event()

    async def forbidden_embed(texts, model, key):
        attempted.set()
        raise AssertionError("Indexing without an API key must not be attempted")

    monkeypatch.setattr("home_media_catalog.ai_vectors.embed", forbidden_embed)
    with server(tmp_path) as client:
        status = client.get(
            "/api/v1/ai/status", headers={"Authorization": f"Bearer {ADMIN}"}
        ).json()
        assert not status["configured"]
        assert not status["index"]["running"]
        assert status["index"]["indexed"] == 0
        assert status["index"]["total"] == 1
        assert not attempted.is_set()


def test_slow_startup_index_does_not_block_api_and_cancels_on_shutdown(tmp_path, monkeypatch):
    store = Store(tmp_path / "catalogue.db")
    store.initialize()
    source = store.save_source({"name": "Films", "type": "clz"})
    store.apply_snapshot(source["id"], [Record("film", "movie", "Voyage")])
    AIConfiguration(tmp_path).update(AISettingsInput(api_key="test-only-key"))
    started = Event()

    async def slow_embed(texts, model, key):
        started.set()
        await asyncio.sleep(60)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("home_media_catalog.ai_vectors.embed", slow_embed)
    with server(tmp_path) as client:
        assert started.wait(5)
        assert client.get("/healthz").status_code == 200
        status = client.get(
            "/api/v1/ai/status", headers={"Authorization": f"Bearer {ADMIN}"}
        ).json()["index"]
        assert status["running"]
    # Leaving TestClient must cancel the pending index, not wait 60 seconds.
