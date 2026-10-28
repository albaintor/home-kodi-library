"""Provider-specific JSON generation. Credentials are used only on the server."""

import json
import re

import httpx

from .ai_usage import tracked_post

PROVIDERS = ("openai", "anthropic", "gemini")
LANGUAGE_DEFAULTS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-3.5-flash-lite",
}


def parse_json_text(text: str) -> dict:
    """Accept provider JSON (including an optional markdown code fence)."""
    value = text.strip()
    value = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", value, flags=re.I)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("AI provider returned a non-object JSON payload.")
    return parsed


async def generate_json(
    instructions: str, user_input: str, schema: dict, model: str,
    key: str, *, provider: str = "openai", max_output_tokens: int = 1800,
    schema_name: str = "catalogue_filter",
) -> dict:
    """Normalize structured model output from OpenAI, Anthropic or Gemini."""
    if provider not in PROVIDERS:
        raise ValueError("Unsupported language provider.")
    async with httpx.AsyncClient(timeout=45.0) as client:
        if provider == "openai":
            response = await tracked_post(
                client, "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key},
                json={
                    "model": model, "instructions": instructions, "input": user_input,
                    "text": {"format": {"type": "json_schema", "name": schema_name,
                                        "strict": True, "schema": schema}},
                    "store": False, "max_output_tokens": max_output_tokens,
                },
            )
            response.raise_for_status()
            chunks = [
                piece.get("text", "")
                for item in response.json().get("output", [])
                for piece in item.get("content", [])
                if piece.get("type") == "output_text"
            ]
            text = "".join(chunks)
        elif provider == "anthropic":
            response = await tracked_post(
                client, "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={
                    "model": model, "max_tokens": max_output_tokens,
                    "system": instructions + "\nReply only with a JSON object satisfying: "
                              + json.dumps(schema, ensure_ascii=False),
                    "messages": [{"role": "user", "content": user_input}],
                    **(
                        {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
                        if model == "claude-haiku-5-5"
                        else {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
                        if model == "claude-sonnet-5-5"
                        else {}
                    ),
                },
            )
            response.raise_for_status()
            text = "".join(x.get("text", "") for x in response.json().get("content", [])
                           if x.get("type") == "text")
        else:
            response = await tracked_post(
                client,
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"role": "user", "parts": [{"text":
                        instructions + "\nReturn exactly one JSON object matching this schema: "
                        + json.dumps(schema, ensure_ascii=False) + "\n" + user_input}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": max_output_tokens * 2 if model.startswith("gemini-3") else max_output_tokens,
                        **(
                            {"thinkingConfig": {"thinkingBudget": 0}}
                            if model.startswith("gemini-2.5-flash")
                            else {}
                        ),
                    },
                },
                model=model,
            )
            response.raise_for_status()
            text = "".join(
                part.get("text", "")
                for candidate in response.json().get("candidates", [])
                for part in candidate.get("content", {}).get("parts", [])
            )
    if not text.strip():
        raise ValueError("The AI provider returned no structured response.")
    return parse_json_text(text)
