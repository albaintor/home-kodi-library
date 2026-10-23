"""OpenAI connectivity tests: both models, admin-only, and secret-safe errors."""

import httpx

from conftest import READ

from kodi_library.ai import AISearchPlan


def test_openai_connection_requires_configured_admin(client, admin):
    endpoint = "/api/v1/ai/connection/test"
    readonly = {"Authorization": f"Bearer {READ}"}
    assert client.post(endpoint, headers=readonly).status_code == 403
    assert client.post(endpoint, headers=admin).status_code == 503


def test_openai_connection_checks_both_models(client, admin, monkeypatch):
    client.put(
        "/api/v1/ai/settings",
        headers=admin,
        json={
            "model": "gpt-6-luna",
            "embedding_model": "text-embedding-3-large",
            "api_key": "secret-connection-test",
        },
    )
    checked = []

    async def check_language(prompt, model, key, facets):
        assert "2015" in prompt
        assert model == "gpt-6-luna"
        assert key == "secret-connection-test"
        checked.append("language")
        return AISearchPlan(kind="movie", year_min=2015)

    async def check_embedding(texts, model, key):
        assert texts == ["Test de recherche sémantique."]
        assert model == "text-embedding-3-large"
        assert key == "secret-connection-test"
        checked.append("embedding")
        return [[0.1, 0.2]]

    monkeypatch.setattr("kodi_library.app.interpret_prompt", check_language)
    monkeypatch.setattr("kodi_library.app.embed", check_embedding)
    response = client.post("/api/v1/ai/connection/test", headers=admin)
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "language": {"ok": True, "model": "gpt-6-luna"},
        "embedding": {"ok": True, "model": "text-embedding-3-large"},
    }
    assert checked == ["language", "embedding"]
    assert "secret-connection-test" not in response.text


def test_openai_connection_reports_partial_failure_without_provider_body(
    client, admin, monkeypatch
):
    client.put(
        "/api/v1/ai/settings",
        headers=admin,
        json={
            "model": "gpt-6-luna",
            "embedding_model": "text-embedding-3-small",
            "api_key": "private-api-token",
        },
    )

    async def broken_language(prompt, model, key, facets):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(401, json={"error": "private-api-token"}, request=request)
        raise httpx.HTTPStatusError("private-api-token", request=request, response=response)

    async def working_embedding(texts, model, key):
        return [[0.5, 0.5]]

    monkeypatch.setattr("kodi_library.app.interpret_prompt", broken_language)
    monkeypatch.setattr("kodi_library.app.embed", working_embedding)
    response = client.post("/api/v1/ai/connection/test", headers=admin)
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is False
    assert result["language"] == {
        "ok": False,
        "model": "gpt-6-luna",
        "reason": "invalid_key",
    }
    assert result["embedding"]["ok"] is True
    assert "private-api-token" not in response.text
