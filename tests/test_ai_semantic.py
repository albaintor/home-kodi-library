"""Hybrid film/episode indexing and contextual search without external network requests."""

import asyncio

from conftest import READ

from kodi_library.ai import AISearchPlan
from kodi_library.ai_hybrid import hybrid_page
from kodi_library.ai_vectors import SemanticIndex
from kodi_library.connectors.base import Record


def test_episode_plot_is_indexed_and_returned_on_parent_series(store, monkeypatch, tmp_path):
    source = store.save_source({"name": "Kodi", "type": "kodi"})
    sid = source["id"]
    store.apply_snapshot(
        sid,
        [
            Record("show", "tvshow", "Chroniques orbitales", genres=["Science Fiction"]),
            Record(
                "episode-3",
                "episode",
                "Le réveil",
                parent_key="show",
                season=2,
                episode=3,
                plot="Une station revit la même boucle temporelle.",
                watched=False,
            ),
            Record("film", "movie", "Un film de montagne", plot="Ascension d'une montagne."),
        ],
    )
    docs = store.ai_documents()
    episode = next(doc for doc in docs if doc["kind"] == "episode")
    assert episode["series_title"] == "Chroniques orbitales"
    assert episode["season"] == 2 and episode["episode"] == 3
    assert "boucle temporelle" in episode["text"]
    assert episode["id"] in store.ai_lexical_hits("boucle temporelle")

    index = SemanticIndex(tmp_path)

    async def fake_embed(texts, model, key):
        return [
            [1.0, 0.0] if "boucle temporelle" in text else [0.0, 1.0]
            for text in texts
        ]

    async def query_embed(texts, model, key):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("kodi_library.ai_vectors.embed", fake_embed)
    monkeypatch.setattr("kodi_library.ai_hybrid.embed", query_embed)
    assert asyncio.run(index.refresh(docs, "test-embedding", "dummy")) == len(docs)
    assert asyncio.run(index.refresh(docs, "test-embedding", "dummy")) == 0

    plan = AISearchPlan(kind="episode", semantic_query="épisode avec une boucle temporelle")
    page = asyncio.run(hybrid_page(store, plan, index, "test-embedding", "dummy"))
    assert page["mode"] == "hybrid"
    assert page["items"][0]["title"] == "Chroniques orbitales"
    assert page["episode_matches"][page["items"][0]["id"]][0]["id"] == episode["id"]
    assert "boucle temporelle" in page["episode_matches"][page["items"][0]["id"]][0]["plot"]

    # Only the changed episode is embedded again after the next Kodi snapshot.
    store.apply_snapshot(
        sid,
        [
            Record("show", "tvshow", "Chroniques orbitales", genres=["Science Fiction"]),
            Record(
                "episode-3",
                "episode",
                "Le réveil",
                parent_key="show",
                season=2,
                episode=3,
                plot="Une station revit une seconde boucle temporelle.",
            ),
            Record("film", "movie", "Un film de montagne", plot="Ascension d'une montagne."),
        ],
    )
    revised_docs = store.ai_documents()
    assert len(index.missing(revised_docs, "test-embedding")) == 1
    assert asyncio.run(index.refresh(revised_docs, "test-embedding", "dummy")) == 1


def test_conversational_refinement_passes_previous_plan(client, admin, monkeypatch):
    client.put(
        "/api/v1/ai/settings",
        headers=admin,
        json={"model": "gpt-4o-mini", "api_key": "dummy"},
    )
    previous = AISearchPlan(kind="movie", year_min=2015, semantic_query="mystery")
    received = {}

    async def interpret(prompt, model, key, facets, context):
        received["prompt"] = prompt
        received["previous"] = context
        return AISearchPlan(kind="movie", year_min=1990, year_max=1999, semantic_query="mystery")

    monkeypatch.setattr("kodi_library.app.interpret_prompt", interpret)
    result = client.post(
        "/api/v1/ai/search",
        headers={"Authorization": f"Bearer {READ}"},
        json={"prompt": "plutôt les années 90", "previous_plan": previous.model_dump()},
    )
    assert result.status_code == 200, result.text
    assert received["previous"].year_min == 2015
    assert received["prompt"] == "plutôt les années 90"
    assert result.json()["plan"]["year_min"] == 1990
