"""Combine server-authoritative structured predicates, FTS5 and semantic episode hits."""

from collections import defaultdict

from .ai_vectors import SemanticIndex, embed


async def hybrid_page(store, plan, index: SemanticIndex, model: str, key: str) -> dict:
    """Return only existing catalogue entries. Episodes are projected onto their TV shows."""
    documents = store.ai_documents()
    by_id = {doc["id"]: doc for doc in documents}
    indexed = index.status(documents, model)
    params = {
        "kind": "tvshow" if plan.kind == "episode" else plan.kind,
        "watch_status": None if plan.kind == "episode" else plan.watch_status,
        "genre": plan.genre,
        "format": plan.format,
        "year_min": plan.year_min,
        "year_max": plan.year_max,
        "rating_min": plan.rating_min,
    }
    semantic = []
    note = None
    if plan.semantic_query and indexed["indexed"]:
        query_vector = (await embed([plan.semantic_query], model, key))[0]
        semantic = index.rank(documents, model, query_vector)
    elif plan.semantic_query:
        note = "Semantic index incomplete; results may be limited until indexing finishes."

    lexical = store.ai_lexical_hits(plan.keywords) if plan.keywords else []
    relevance: dict[str, float] = {}
    episode_hits: dict[str, list[tuple[float, dict]]] = defaultdict(list)

    def include(doc):
        if plan.kind == "movie" and doc["kind"] != "movie":
            return False
        if plan.kind == "episode" and doc["kind"] != "episode":
            return False
        if plan.kind == "tvshow" and doc["kind"] not in ("tvshow", "episode"):
            return False
        if (
            plan.kind == "episode"
            and plan.watch_status
            and doc["watch_status"] != plan.watch_status
        ):
            return False
        return True

    def add(work_id: str, score: float):
        doc = by_id.get(work_id)
        if not doc or not include(doc):
            return
        result_id = doc["parent_id"] if doc["kind"] == "episode" else work_id
        if not result_id or result_id not in by_id:
            return
        relevance[result_id] = max(relevance.get(result_id, -1), score)
        if doc["kind"] == "episode":
            episode_hits[result_id].append((score, doc))

    for work_id, score in semantic[:400]:
        if score >= 0.20:
            add(work_id, score)
    for rank, work_id in enumerate(lexical):
        add(work_id, 0.70 + 0.15 / (rank + 1))

    if relevance:
        ids = sorted(relevance, key=lambda item: -relevance[item])[:400]
        page = store.media(**params, ids=ids, limit=200)
        page["items"].sort(key=lambda item: -relevance[item["id"]])
        page["items"] = page["items"][:60]
        page["has_more"] = page["total"] > 60
    else:
        if plan.semantic_query:
            page = {"items": [], "total": 0, "limit": 60, "offset": 0, "has_more": False}
        else:
            page = store.media(
                **params, q=plan.keywords, deep_search=bool(plan.keywords), limit=60
            )
        if plan.semantic_query and not indexed["indexed"]:
            note = "Semantic index is building; showing structured filters only."
        elif plan.semantic_query and not relevance:
            note = "No matching semantic documents; showing structured filters."

    matches = {}
    for item in page["items"]:
        candidates = sorted(episode_hits.get(item["id"], []), key=lambda hit: -hit[0])
        seen, selected = set(), []
        for score, doc in candidates:
            if doc["id"] in seen:
                continue
            seen.add(doc["id"])
            selected.append(
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "season": doc["season"],
                    "episode": doc["episode"],
                    "plot": doc["plot"][:260],
                    "score": round(score, 3),
                }
            )
            if len(selected) == 3:
                break
        if selected:
            matches[item["id"]] = selected

    return {
        **page,
        "plan": plan.model_dump(),
        "mode": "hybrid" if relevance else "filters",
        "episode_matches": matches,
        "assistant_message": plan.assistant_message,
        "note": note,
        "index": indexed,
    }
