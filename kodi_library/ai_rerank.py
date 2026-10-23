"""Strict, evidence-based reranking for semantic catalogue retrieval.

Embeddings retrieve possible matches, but cannot certify style or authorship.
A structured language-model pass removes false positives before the UI sees them.
"""

import json

import httpx

from .ai_usage import tracked_post

_RERANK_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}


async def rerank_candidates(
    user_prompt: str, candidates: list[dict], model: str, key: str
) -> list[dict]:
    """Return only confidently relevant IDs from the supplied catalogue subset."""
    if not candidates:
        return []
    instructions = (
        "You are a strict film and TV catalogue relevance verifier. "
        "Evaluate EACH candidate against the user's COMPLETE request, including "
        "style, filmmaker influences, visual style, genre, tone, and episode themes. "
        "A shared vague theme (adventure, friendship, fantasy) is NOT sufficient. "
        "For an artistic director reference like Miyazaki, prioritize animation, "
        "hand-crafted worlds, nature, myth, folklore and the actual film aesthetic; "
        "exclude unrelated live-action films such as Barbie. Apply the same strict "
        "standard to ANY reference or genre, not just Miyazaki. "
        "Use the supplied title, synopsis and matched episode plot as evidence. "
        "If there is not enough evidence to establish a credible match, exclude it. "
        "Do not claim knowledge of unavailable works or invent catalogue IDs. "
        "Output only compelling matches, with a short accurate reason in the "
        "user's language; an empty array is better than irrelevant results."
    )
    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await tracked_post(
            client,
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer " + key},
            json={
                "model": model,
                "instructions": instructions,
                "input": json.dumps(
                    {"request": user_prompt, "candidates": candidates[:60]},
                    ensure_ascii=False,
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "media_relevance",
                        "strict": True,
                        "schema": _RERANK_SCHEMA,
                    }
                },
                "store": False,
                "max_output_tokens": 5000,
            },
        )
        response.raise_for_status()
        data = response.json()
    fragments = [
        part.get("text", "")
        for output in data.get("output", [])
        for part in output.get("content", [])
        if part.get("type") == "output_text"
    ]
    if not fragments:
        raise ValueError("OpenAI returned no relevance judgments.")
    parsed = json.loads("".join(fragments))
    allowed = {candidate["id"] for candidate in candidates[:60]}
    matched = {}
    for item in parsed["matches"]:
        media_id = item.get("id")
        reason = str(item.get("reason", "")).strip()[:220]
        if media_id in allowed and reason and media_id not in matched:
            matched[media_id] = {"id": media_id, "reason": reason}
    return list(matched.values())
