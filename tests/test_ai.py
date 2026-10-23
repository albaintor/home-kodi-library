"""Opt-in AI search: secrets, authorization, normal-search fallback and SQL filters."""
from conftest import READ

from kodi_library.ai import AIConfiguration, AISearchPlan, normalize_plan
from kodi_library.connectors.base import Record


def test_configuration_persists_and_never_discloses_secret(client, admin, tmp_path):
    endpoint = "/api/v1/ai/settings"
    reader = {"Authorization": f"Bearer {READ}"}
    assert client.get("/api/v1/ai/status", headers=reader).json()["configured"] is False
    assert client.get(endpoint, headers=reader).status_code == 403
    assert client.put(endpoint, headers=reader, json={"model": "gpt-4o-mini"}).status_code == 403
    assert client.post("/api/v1/ai/search", headers=reader, json={"prompt": "Un film"}).status_code == 503

    key = "test-openai-key-should-never-leak"
    saved = client.put(endpoint, headers=admin, json={"model": "gpt-4o-mini", "api_key": key})
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert key not in saved.text
    assert key not in client.get(endpoint, headers=admin).text
    assert key not in client.get("/api/v1/ai/status", headers=reader).text
    assert (tmp_path / "ai-settings.json").stat().st_mode & 0o777 == 0o600

    updated = client.put(endpoint, headers=admin, json={"model": "gpt-4o"})
    assert updated.status_code == 200
    assert AIConfiguration(tmp_path).credentials() == ("gpt-4o", key)

    assert client.put(endpoint, headers=admin, json={"model": "../../etc/passwd"}).status_code == 422
    cleared = client.put(endpoint, headers=admin, json={"model": "gpt-4o-mini", "clear_api_key": True})
    assert cleared.json()["configured"] is False
    assert client.post("/api/v1/ai/search", headers=admin, json={"prompt": "Science fiction"}).status_code == 503
    assert client.get("/api/v1/media", headers=admin).status_code == 200


def test_filter_normalization_does_not_accept_invented_labels():
    plan = AISearchPlan(
        kind="movie", watch_status="unwatched", genre="Science Fiction",
        format="HDR9999", year_min=2010, year_max=2201,
        rating_min=22, keywords="",
    )
    normalized = normalize_plan(plan, {
        "genres": ["Science fiction"], "formats": ["Blu-ray 4K"]
    })
    assert normalized.genre == "Science fiction"
    assert normalized.format is None
    assert normalized.year_max is None
    assert normalized.rating_min is None


def test_ai_filters_are_applied_only_to_real_media(client, admin, monkeypatch):
    sid = client.post("/api/v1/sources", headers=admin,
                      json={"name": "Sample", "type": "clz"}).json()["id"]
    client.app.state.store.apply_snapshot(sid, [
        Record("f1", "movie", "Orbital A", year=2022, rating=8.2,
               genres=["Science Fiction"], formats=["4K UHD"], watched=False),
        Record("f2", "movie", "Orbital B", year=2001, rating=8.9,
               genres=["Science Fiction"], formats=["4K UHD"], watched=False),
        Record("f3", "movie", "Orbital C", year=2023, rating=9.3,
               genres=["Science Fiction"], formats=["4K UHD"], watched=True),
        Record("f4", "movie", "Orbital D", year=2024, rating=6.0,
               genres=["Science Fiction"], formats=["4K UHD"], watched=False),
    ])
    saved = client.put("/api/v1/ai/settings", headers=admin, json={
        "model": "gpt-4o-mini", "api_key": "dummy"
    })
    assert saved.status_code == 200

    async def fake_interpret(prompt, model, key, facets):
        assert prompt == "SF récente non vue bien notée"
        assert key == "dummy"
        return AISearchPlan(kind="movie", watch_status="unwatched",
                            genre="Science Fiction", format="Blu-ray 4K",
                            year_min=2015, year_max=None, rating_min=7.5,
                            keywords="")

    monkeypatch.setattr("kodi_library.app.interpret_prompt", fake_interpret)
    result = client.post("/api/v1/ai/search", headers=admin,
                         json={"prompt": "SF récente non vue bien notée"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["mode"] == "filters"
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["Orbital A"]
    assert body["plan"]["year_min"] == 2015
