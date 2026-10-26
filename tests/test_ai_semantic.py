"""Hybrid film/episode indexing and contextual search without external network requests."""

import asyncio

from conftest import READ

from home_media_catalog.ai import AISearchPlan, normalize_plan
from home_media_catalog.ai_hybrid import hybrid_page
from home_media_catalog.ai_vectors import SemanticIndex
from home_media_catalog.connectors.base import Record


def test_episode_scope_survives_structured_validation():
    requested = AISearchPlan(kind="episode", semantic_query="une boucle temporelle")
    resolved = normalize_plan(requested, {"genres": [], "formats": []})
    assert resolved.kind == "episode"


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
        return [[1.0, 0.0] if "boucle temporelle" in text else [0.0, 1.0] for text in texts]

    async def query_embed(texts, model, key):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("home_media_catalog.ai_vectors.embed", fake_embed)
    monkeypatch.setattr("home_media_catalog.ai_hybrid.embed", query_embed)
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

    monkeypatch.setattr("home_media_catalog.app.interpret_prompt", interpret)
    result = client.post(
        "/api/v1/ai/search",
        headers={"Authorization": f"Bearer {READ}"},
        json={"prompt": "plutôt les années 90", "previous_plan": previous.model_dump()},
    )
    assert result.status_code == 200, result.text
    assert received["previous"].year_min == 2015
    assert received["prompt"] == "plutôt les années 90"
    assert result.json()["plan"]["year_min"] == 1990


def test_style_search_excludes_unrelated_barbie_match(store, monkeypatch, tmp_path):
    """High cosine alone must not include a stylistically unrelated title."""
    source = store.save_source({"name": "Style Test", "type": "clz"})
    store.apply_snapshot(
        source["id"],
        [
            Record(
                "myth",
                "movie",
                "La forêt des esprits",
                genres=["Animation", "Fantasy"],
                plot="Un film d'animation poétique sur les esprits de la forêt.",
                watched=False,
            ),
            Record(
                "barbie",
                "movie",
                "Barbie",
                genres=["Comedy", "Fantasy"],
                plot="Une poupée découvre le monde réel dans une comédie.",
                watched=False,
            ),
        ],
    )
    index = SemanticIndex(tmp_path)
    docs = store.ai_documents()

    async def fake_embed(texts, model, key):
        return [[1.0, 0.0] for _ in texts]

    async def strict_judge(prompt, candidates, model, key):
        assert prompt == "liste des films du style Miyazaki que je n'ai pas vu"
        assert any(item["title"] == "Barbie" for item in candidates)
        return [
            {"id": item["id"], "reason": "Animation et nature, proches du style recherché."}
            for item in candidates
            if item["title"] == "La forêt des esprits"
        ]

    monkeypatch.setattr("home_media_catalog.ai_vectors.embed", fake_embed)
    monkeypatch.setattr("home_media_catalog.ai_hybrid.embed", fake_embed)
    monkeypatch.setattr("home_media_catalog.ai_hybrid.rerank_candidates", strict_judge)
    asyncio.run(index.refresh(docs, "test-style", "key"))
    plan = AISearchPlan(kind="movie", watch_status="unwatched", semantic_query="style Miyazaki")
    results = asyncio.run(
        hybrid_page(
            store,
            plan,
            index,
            "test-style",
            "key",
            prompt="liste des films du style Miyazaki que je n'ai pas vu",
            language_model="test-model",
        )
    )
    assert [item["title"] for item in results["items"]] == ["La forêt des esprits"]
    assert results["total"] == 1
    assert "Barbie" not in str(results["items"])
    assert len(results["relevance_reasons"]) == 1


def test_roles_and_ratings_are_embedded_and_filterable(store):
    source = store.save_source({"name": "Credited films", "type": "clz"})
    store.apply_snapshot(
        source["id"],
        [
            Record(
                "one",
                "movie",
                "Le voyage",
                plot="Une expédition dans l'espace.",
                rating=8.5,
                user_rating=9,
                metadata={
                    "people": [
                        {"name": "Alice Martin", "category": "cast"},
                        {"name": "Claire Moreau", "category": "director"},
                        {"name": "Jean Dupont", "category": "composer"},
                    ]
                },
            ),
            Record(
                "two",
                "movie",
                "Le retour",
                rating=6,
                user_rating=7,
                metadata={
                    "people": [
                        {"name": "Jean Dupont", "category": "cast"},
                        {"name": "Claire Moreau", "category": "director"},
                    ]
                },
            ),
        ],
    )
    docs = {doc["title"]: doc for doc in store.ai_documents()}
    assert "Acteurs: Alice Martin" in docs["Le voyage"]["text"]
    assert "Réalisateurs: Claire Moreau" in docs["Le voyage"]["text"]
    assert "Compositeurs: Jean Dupont" in docs["Le voyage"]["text"]
    assert "Note générale: 8.5/10" in docs["Le voyage"]["text"]
    assert "Note personnelle: 9.0/10" in docs["Le voyage"]["text"]
    expected = docs["Le voyage"]["id"]
    assert store.ai_person_ids("Jean Dupont", "composer") == {expected}
    assert [
        item["id"]
        for item in store.media(
            person_name="Jean Dupont",
            person_role="composer",
            rating_min=8,
            user_rating_min=8,
        )["items"]
    ] == [expected]
    assert store.media(person_name="Jean Dupont", person_role="cast", rating_min=8)["total"] == 0
    assert (
        store.media(person_name="Jean Dupont", person_role="composer", user_rating_min=9.5)["total"]
        == 0
    )


def test_credit_role_migration_rebuilds_from_existing_records(store):
    source = store.save_source({"name": "Old films", "type": "clz"})
    store.apply_snapshot(
        source["id"],
        [
            Record(
                "old",
                "movie",
                "Ancien film",
                metadata={"people": [{"name": "Claude Durand", "category": "composer"}]},
            )
        ],
    )
    with store.connect() as conn:
        conn.execute("DELETE FROM work_person_roles")
        conn.execute("DELETE FROM catalogue_meta WHERE key='person_roles_index_v1'")
    store.initialize()
    assert len(store.ai_person_ids("Claude Durand", "composer")) == 1


def test_hnsw_cache_and_scoped_search(tmp_path, monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("numpy")
    pytest.importorskip("usearch.index")
    import home_media_catalog.ai_vectors as vectors

    async def fake_embed(texts, model, key):
        lookup = {
            "first": [1.0, 0.0],
            "second": [0.0, 1.0],
            "third": [-1.0, 0.0],
            "changed": [0.9, 0.1],
        }
        return [lookup[text] for text in texts]

    monkeypatch.setattr(vectors, "embed", fake_embed)
    monkeypatch.setattr(vectors, "ANN_MIN_DOCUMENTS", 2)
    docs = [
        {"id": "a", "text": "first"},
        {"id": "b", "text": "second"},
        {"id": "c", "text": "third"},
    ]
    index = SemanticIndex(tmp_path)
    assert asyncio.run(index.refresh(docs, "test", "dummy")) == 3
    assert index.rank(docs, "test", [1.0, 0.0])[0][0] == "a"
    assert index._rank_cache["backend"] == "hnsw"
    assert [work_id for work_id, _ in index.rank(docs[1:], "test", [1.0, 0.0])] == ["b", "c"]
    assert index._rank_cache["backend"] == "hnsw"
    assert asyncio.run(index.refresh(docs, "test", "dummy")) == 0
    docs[0] = {"id": "a", "text": "changed"}
    assert index.missing(docs, "test") == docs[:1]
    assert asyncio.run(index.refresh(docs, "test", "dummy")) == 1
    assert index._rank_cache is None
    assert index.rank(docs, "test", [1.0, 0.0])[0][0] == "a"
