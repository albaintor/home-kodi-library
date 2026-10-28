"""Server-side multi-provider usage accounting and monthly spend guard.

Every provider call reserves a conservatively estimated cost before sending any
tokens, then reconciles reported usage from the active provider. No access keys or
prompt text are stored in the ledger. Prices are USD per million tokens.
"""

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# OpenAI standard text/embedding prices (checked October 2026).
# Cached inputs are billed at the model's discounted rate.
PRICES = {
    "gpt-6-luna": (0.10, 0.50, 0.01),
    "gpt-6.1-sol": (2.00, 10.00, 0.10),
    "gpt-6-sol": (2.00, 10.00, 0.20),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "text-embedding-3-small": (0.02, 0.00, 0.02),
    "text-embedding-3-large": (0.13, 0.00, 0.13),
    "claude-haiku-4-5": (1.0, 5.0, 0.1),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.2),
    "claude-sonnet-4-5": (3.0, 15.0, 0.3),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "gemini-3.8-flash": (1.50, 7.50, 0.15),
    "gemini-2.5-flash-lite": (0.1, 0.4, 0.01),
    "gemini-2.5-flash": (0.3, 2.5, 0.03),
}


class AIBudgetExceeded(ValueError):
    """No provider request was submitted due to the configured monthly limit."""


class AIPriceUnavailable(ValueError):
    """A custom model cannot be safely budgeted without a known tariff."""


_active: ContextVar[tuple | None] = ContextVar("ai_usage_session", default=None)


@contextmanager
def usage_session(ledger, monthly_budget, category):
    token = _active.set((ledger, monthly_budget, category))
    try:
        yield
    finally:
        _active.reset(token)


class AIUsageLedger:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "ai-usage.db"
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS calls ("
                "id TEXT PRIMARY KEY, month TEXT NOT NULL, created_at TEXT NOT NULL,"
                "category TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,"
                "input_tokens INTEGER NOT NULL DEFAULT 0,"
                "output_tokens INTEGER NOT NULL DEFAULT 0,"
                "cached_tokens INTEGER NOT NULL DEFAULT 0,"
                "estimated_cost_usd REAL NOT NULL DEFAULT 0,"
                "reserved_cost_usd REAL NOT NULL DEFAULT 0,"
                "cost_usd REAL NOT NULL DEFAULT 0)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS usage_month ON calls(month,status)")
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _month():
        return datetime.now(UTC).strftime("%Y-%m")

    def reserve(self, category: str, model: str, estimate_tokens: int, max_output: int, budget):
        pricing = PRICES.get(model)
        if pricing is None:
            raise AIPriceUnavailable(
                "No known pricing for the selected model; choose a supported model "
                "before using the monthly budget."
            )
        input_price, output_price, _ = pricing
        amount = (estimate_tokens * input_price + max_output * output_price) / 1_000_000
        # Reserve full price even when inputs may be cached.
        reference = str(uuid4())
        month = self._month()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            used = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved_cost_usd "
                "ELSE cost_usd END),0) FROM calls WHERE month=?",
                (month,),
            ).fetchone()[0]
            if budget is not None and used + amount > budget + 1e-10:
                raise AIBudgetExceeded(
                    "Monthly AI spending limit reached. Update the AI budget in settings."
                )
            conn.execute(
                "INSERT INTO calls(id,month,created_at,category,model,status,"
                "estimated_cost_usd,reserved_cost_usd) VALUES(?,?,?,?,?,?,?,?)",
                (
                    reference,
                    month,
                    datetime.now(UTC).isoformat(),
                    category,
                    model,
                    "reserved",
                    amount,
                    amount,
                ),
            )
        return reference

    def settle(self, reference, payload: dict, model: str):
        usage = payload.get("usage") or {}
        if not usage and payload.get("usageMetadata"):
            usage = payload["usageMetadata"]
        prompt = int(usage.get("input_tokens", usage.get("prompt_tokens",
                     usage.get("promptTokenCount", 0))) or 0)
        completion = int(usage.get("output_tokens", usage.get("candidatesTokenCount", 0)) or 0)
        cache = int((usage.get("input_tokens_details") or {}).get("cached_tokens",
                    usage.get("cache_read_input_tokens",
                    usage.get("cachedContentTokenCount", 0))) or 0)
        cache = min(max(cache, 0), prompt)
        input_rate, output_rate, cached_rate = PRICES[model]
        amount = (
            (prompt - cache) * input_rate + cache * cached_rate + completion * output_rate
        ) / 1_000_000
        with self._connect() as conn:
            # Missing usage must not turn a sent provider request into a free call:
            # retain its conservative reservation until it can be reconciled.
            if prompt == 0 and completion == 0:
                conn.execute(
                    "UPDATE calls SET status='estimated',cost_usd=reserved_cost_usd WHERE id=?",
                    (reference,),
                )
            else:
                conn.execute(
                    "UPDATE calls SET status='completed',input_tokens=?,output_tokens=?,"
                    "cached_tokens=?,cost_usd=? WHERE id=?",
                    (prompt, completion, cache, amount, reference),
                )

    def release(self, reference: str, *, uncertain: bool = False):
        with self._connect() as conn:
            if uncertain:
                conn.execute(
                    "UPDATE calls SET status='estimated',cost_usd=reserved_cost_usd WHERE id=?",
                    (reference,),
                )
            else:
                conn.execute("DELETE FROM calls WHERE id=?", (reference,))

    def status(self, budget) -> dict:
        month = self._month()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category,model,status,SUM(input_tokens) AS input_tokens,"
                "SUM(output_tokens) AS output_tokens,"
                "SUM(cached_tokens) AS cached_tokens,"
                "SUM(CASE WHEN status='reserved' THEN reserved_cost_usd ELSE cost_usd END)"
                " AS cost_usd,COUNT(*) AS requests "
                "FROM calls WHERE month=? GROUP BY category,model,status",
                (month,),
            ).fetchall()
        grouped = {}
        for row in rows:
            name = row["category"]
            result = grouped.setdefault(
                name,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            for field in ("requests", "input_tokens", "output_tokens", "cached_tokens"):
                result[field] += row[field]
            result["cost_usd"] += row["cost_usd"] or 0.0
        total = sum(x["cost_usd"] for x in grouped.values())
        return {
            "month": month,
            "currency": "USD",
            "monthly_budget_usd": budget,
            "cost_usd": round(total, 8),
            "remaining_usd": None if budget is None else round(max(0, budget - total), 8),
            "categories": grouped,
            "pricing_note": "Estimated using standard USD API prices; excludes taxes and other provider usage.",
        }


async def tracked_post(client, url, *, headers, json: dict, model: str | None = None):
    """Meter all provider calls using the enclosing request's context."""
    session = _active.get()
    if session is None:
        return await client.post(url, headers=headers, json=json)
    ledger, budget, category = session
    model = model or json["model"]
    estimate_input = max(20, len(__import__("json").dumps(json, ensure_ascii=False)) * 2)
    config = json.get("generationConfig") or {}
    output_bound = int(json.get("max_output_tokens", json.get("max_tokens",
                       config.get("maxOutputTokens", 0))))
    reference = ledger.reserve(category, model, estimate_input, output_bound, budget)
    try:
        response = await client.post(url, headers=headers, json=json)
    except Exception:
        ledger.release(reference, uncertain=True)
        raise
    if response.is_success:
        try:
            ledger.settle(reference, response.json(), model)
        except (ValueError, KeyError, TypeError):
            ledger.release(reference, uncertain=True)
    else:
        ledger.release(reference)
    return response
