"""Provider routing must preserve OpenAI's stored vector index and credentials."""

import asyncio
import json

import httpx

from home_media_catalog.ai import AIConfiguration, AISearchPlan, AISettingsInput
from home_media_catalog.ai_providers import generate_json
from home_media_catalog.ai_vectors import SemanticIndex


def test_switching_providers_preserves_openai_embedding_configuration(tmp_path):
    config = AIConfiguration(tmp_path)
    config.update(
        AISettingsInput(
            provider="openai",
            model="gpt-4o-mini",
            api_key="old-openai-key",
            embedding_model="text-embedding-3-large",
        )
    )
    # An existing stored vector must survive changing the active provider.
    index = SemanticIndex(tmp_path)
    documents = [{"id": "film-1", "text": "A science-fiction mystery"}]
    with index._connect() as conn:
        conn.execute(
            "INSERT INTO vectors(id,model,hash,vector) VALUES(?,?,?,?)",
            (
                documents[0]["id"],
                "text-embedding-3-large",
                index.digest(documents[0]["text"]),
                json.dumps([1.0, 0.0]),
            ),
        )
    assert index.status(documents, "text-embedding-3-large")["indexed"] == 1
    config.update(
        AISettingsInput(
            provider="anthropic",
            model="claude-haiku-4-5",
            api_key="anthropic-secret",
        )
    )
    assert index.status(documents, "text-embedding-3-large")["indexed"] == 1
    assert config.status()["search_mode"] == "local_ai"
    assert config.status()["embedding_model"] == "text-embedding-3-large"
    assert config.credentials() == ("claude-haiku-4-5", "anthropic-secret")

    config.update(AISettingsInput(provider="openai", model="gpt-4o-mini"))
    assert config.status()["search_mode"] == "hybrid_vector"
    assert config.status()["embedding_model"] == "text-embedding-3-large"
    assert config.credentials() == ("gpt-4o-mini", "old-openai-key")
    assert index.status(documents, "text-embedding-3-large")["indexed"] == 1
    assert "old-openai-key" not in json.dumps(config.status())


def test_other_provider_json_adapters_use_only_selected_provider(monkeypatch):
    calls = []

    async def fake_post(self, url, *, headers, json):
        calls.append((url, headers))
        request = httpx.Request("POST", url)
        if "anthropic" in url:
            body = {"content": [{"type": "text", "text": '{"kind": "movie"}'}]}
        else:
            body = {
                "candidates": [
                    {"content": {"parts": [{"text": '{"kind": "movie"}'}]}}
                ]
            }
        return httpx.Response(200, request=request, json=body)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    for provider in ("anthropic", "gemini"):
        result = asyncio.run(
            generate_json(
                "Return JSON", "Find films", {"type": "object"},
                "claude-haiku-4-5" if provider == "anthropic" else "gemini-2.5-flash",
                "test-secret", provider=provider,
            )
        )
        assert result == {"kind": "movie"}
    assert len(calls) == 2
    assert "api.anthropic.com" in calls[0][0]
    assert "generativelanguage.googleapis.com" in calls[1][0]
    assert all("api.openai.com" not in url for url, _ in calls)


def test_claude_search_does_not_call_vector_workflow(client, admin, monkeypatch):
    saved = client.put(
        "/api/v1/ai/settings",
        headers=admin,
        json={
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "api_key": "test-anthropic-key",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["index"] is None

    async def fake_interpret(prompt, model, key, facets, *, provider):
        assert provider == "anthropic"
        assert model == "claude-haiku-4-5"
        return AISearchPlan(kind="movie", assistant_message="Films correspondants.")

    async def fake_local(store, plan, **kwargs):
        assert kwargs["provider"] == "anthropic"
        return {
            "items": [], "total": 0, "has_more": False,
            "plan": plan.model_dump(), "mode": "local_ai",
            "episode_matches": {}, "relevance_reasons": {},
            "assistant_message": plan.assistant_message, "note": None,
        }

    async def forbidden_hybrid(*args, **kwargs):
        raise AssertionError("Claude must not call OpenAI vector search.")

    monkeypatch.setattr("home_media_catalog.app.interpret_prompt", fake_interpret)
    monkeypatch.setattr("home_media_catalog.app.single_model_page", fake_local)
    monkeypatch.setattr("home_media_catalog.app.hybrid_page", forbidden_hybrid)
    response = client.post(
        "/api/v1/ai/search", headers=admin,
        json={"prompt": "Films avec une intrigue historique"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "local_ai"
    assert "index" not in response.json()
