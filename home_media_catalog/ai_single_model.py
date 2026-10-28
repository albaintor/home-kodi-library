"""Single-LLM media discovery over local full-text and metadata indexes.

No second AI model is used: SQLite FTS5 selects candidates and the user's chosen
language model interprets and judges them. Existing vectors remain untouched.
"""

import asyncio
from collections import defaultdict

from .ai_rerank import rerank_candidates


async def single_model_page(
    store,
    plan,
    *,
    prompt: str,
    model: str,
    key: str,
    provider: str = "openai",
    exclude_ids: set[str] | None = None,
) -> dict:
    """Discover candidates locally and verify them with exactly one selected LLM."""
    excluded = exclude_ids or set()
    docs = await asyncio.to_thread(store.ai_documents)
    by_id = {doc["id"]: doc for doc in docs}
    params = {
        "kind": "tvshow" if plan.kind == "episode" else plan.kind,
        "watch_status": None if plan.kind == "episode" else plan.watch_status,
        "genre": plan.genre,
        "format": plan.format,
        "year_min": plan.year_min,
        "year_max": plan.year_max,
        "rating_min": plan.rating_min,
        "user_rating_min": plan.user_rating_min,
        "person_name": plan.person_name,
        "person_role": plan.person_role,
    }
    semantic = bool(plan.semantic_query)
    scores: dict[str, float] = defaultdict(float)
    episode_hits: dict[str, list[tuple[float, dict]]] = defaultdict(list)

    # Each FTS query is a short phrase (AND). Union across independently
    # generated synonyms so entire natural-language questions are not forced
    # into one impossible conjunction.
    phrases = list(dict.fromkeys(
        [plan.keywords, *plan.retrieval_terms, plan.semantic_query]
    ))[:14]
    for index, phrase in enumerate(phrases):
        if not phrase or (index and phrase == plan.keywords):
            continue
        hits = await asyncio.to_thread(store.ai_lexical_hits, phrase, 160)
        for rank, media_id in enumerate(hits):
            doc = by_id.get(media_id)
            if not doc:
                continue
            if plan.kind == "movie" and doc["kind"] != "movie":
                continue
            if plan.kind == "episode" and doc["kind"] != "episode":
                continue
            if plan.kind == "tvshow" and doc["kind"] not in ("episode", "tvshow"):
                continue
            if (
                plan.kind == "episode"
                and plan.watch_status
                and doc["watch_status"] != plan.watch_status
            ):
                continue
            parent = doc.get("parent_id") if doc["kind"] == "episode" else media_id
            if not parent or parent not in by_id or parent in excluded:
                continue
            score = (4.0 if phrase == plan.keywords and plan.keywords else 1.0)
            score /= (1 + rank * 0.08)
            scores[parent] += score
            if doc["kind"] == "episode":
                episode_hits[parent].append((score, doc))

    if not semantic and not plan.keywords:
        page = await asyncio.to_thread(store.media, **params, limit=60)
        page["items"] = [item for item in page["items"] if item["id"] not in excluded]
        page["total"] = len(page["items"])
        page["has_more"] = False
        return {
            **page, "plan": plan.model_dump(), "mode": "filters",
            "episode_matches": {}, "relevance_reasons": {},
            "assistant_message": plan.assistant_message, "note": None,
        }

    candidate_ids = sorted(scores, key=lambda media_id: -scores[media_id])[:400]
    # Without a lexical clue (e.g. a mood with no exact matching words),
    # allow the same model to judge a bounded set of SQL-filtered candidates.
    # Never present these unverified as semantic matches.
    if not candidate_ids and semantic:
        pool = await asyncio.to_thread(store.media, **params, limit=60)
        candidate_ids = [item["id"] for item in pool["items"] if item["id"] not in excluded]

    if candidate_ids:
        # SQLite's default media ordering is not the FTS relevance ordering.
        # Fetch every shortlisted ID in bounded chunks before ranking, so a
        # strong older match is not discarded by the SQL limit.
        shortlisted = []
        for offset in range(0, len(candidate_ids), 200):
            subset = await asyncio.to_thread(
                store.media, **params, ids=candidate_ids[offset : offset + 200], limit=200
            )
            shortlisted.extend(subset["items"])
        shortlisted.sort(key=lambda item: -scores.get(item["id"], 0.0))
        shortlisted = [item for item in shortlisted if item["id"] not in excluded][:60]
        page = {
            "items": shortlisted,
            "total": len(shortlisted),
            "limit": 60,
            "offset": 0,
            "has_more": False,
        }
    else:
        page = {
            "items": [], "total": 0, "limit": 60, "offset": 0, "has_more": False,
        }

    explanations: dict[str, str] = {}
    note = None
    if semantic and page["items"]:
        candidates = []
        for item in page["items"]:
            doc = by_id.get(item["id"])
            if not doc:
                continue
            candidates.append({
                "id": item["id"],
                "title": item["title"],
                "kind": doc["kind"],
                "year": item.get("year"),
                "genres": item.get("genres", []),
                "credits": [
                    {"category": role, "name": name}
                    for role, name in doc.get("credits", [])
                    if role in ("cast", "director", "composer", "writer")
                ][:40],
                "rating": doc.get("rating"),
                "user_rating": doc.get("user_rating"),
                "original_title": item.get("original_title"),
                "synopsis": doc.get("plot", "")[:850],
                "matched_episodes": [
                    {"title": episode["title"], "plot": episode["plot"][:400]}
                    for _, episode in sorted(
                        episode_hits.get(item["id"], []), key=lambda pair: -pair[0]
                    )[:2]
                ],
            })
        verified = await rerank_candidates(
            prompt or plan.semantic_query, candidates, model, key, provider=provider
        )
        explanations = {item["id"]: item["reason"] for item in verified}
        page["items"] = [item for item in page["items"] if item["id"] in explanations]
        page["total"] = len(page["items"])
        if not page["items"]:
            note = "No convincing match in the local catalog for this description."
    elif semantic:
        note = "No local candidates matched the description."

    matches = {}
    for item in page["items"]:
        episodes = sorted(episode_hits.get(item["id"], []), key=lambda pair: -pair[0])
        if episodes:
            seen = set()
            selection = []
            for score, episode in episodes:
                if episode["id"] in seen:
                    continue
                seen.add(episode["id"])
                selection.append({
                    "id": episode["id"],
                    "title": episode["title"],
                    "season": episode["season"],
                    "episode": episode["episode"],
                    "plot": episode["plot"][:260],
                    "score": round(min(score / 4, 1.0), 3),
                })
                if len(selection) == 3:
                    break
            matches[item["id"]] = selection
    return {
        **page, "plan": plan.model_dump(),
        "mode": "local_ai" if semantic else "filters",
        "episode_matches": matches, "relevance_reasons": explanations,
        "assistant_message": plan.assistant_message, "note": note,
    }
