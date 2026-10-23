"""Incremental local vector index. Synopses are embedded through OpenAI; vectors stay on NAS."""

import asyncio
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import httpx


class SemanticIndex:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "ai-vectors.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vectors ("
                "id TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL,"
                "vector TEXT NOT NULL, PRIMARY KEY(id,model))"
            )
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
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def status(self, documents: list[dict], model: str) -> dict:
        with self._connect() as conn:
            hashes = {
                row["id"]: row["hash"]
                for row in conn.execute("SELECT id,hash FROM vectors WHERE model=?", (model,))
            }
        count = sum(hashes.get(doc["id"]) == self.digest(doc["text"]) for doc in documents)
        return {"indexed": count, "total": len(documents), "complete": count == len(documents)}

    def missing(self, documents: list[dict], model: str) -> list[dict]:
        with self._connect() as conn:
            hashes = {
                row["id"]: row["hash"]
                for row in conn.execute("SELECT id,hash FROM vectors WHERE model=?", (model,))
            }
        return [doc for doc in documents if hashes.get(doc["id"]) != self.digest(doc["text"])]

    async def refresh(
        self, documents: list[dict], model: str, key: str, limit: int | None = None
    ) -> int:
        missing = self.missing(documents, model)
        if limit is not None:
            missing = missing[:limit]
        indexed = 0
        for offset in range(0, len(missing), 48):
            batch = missing[offset : offset + 48]
            vectors = await embed([doc["text"] for doc in batch], model, key)
            with self._connect() as conn:
                conn.executemany(
                    "INSERT INTO vectors(id,model,hash,vector) VALUES(?,?,?,?) "
                    "ON CONFLICT(id,model) DO UPDATE SET hash=excluded.hash,"
                    "vector=excluded.vector",
                    [
                        (doc["id"], model, self.digest(doc["text"]), json.dumps(vector))
                        for doc, vector in zip(batch, vectors, strict=True)
                    ],
                )
            indexed += len(batch)
            await asyncio.sleep(0)
        if limit is None:
            active_ids = {doc["id"] for doc in documents}
            with self._connect() as conn:
                conn.executemany(
                    "DELETE FROM vectors WHERE model=? AND id=?",
                    [
                        (model, row["id"])
                        for row in conn.execute("SELECT id FROM vectors WHERE model=?", (model,))
                        if row["id"] not in active_ids
                    ],
                )
        return indexed

    def rank(
        self, documents: list[dict], model: str, query: list[float]
    ) -> list[tuple[str, float]]:
        hashes = {doc["id"]: self.digest(doc["text"]) for doc in documents}
        query_norm = math.sqrt(sum(v * v for v in query))
        if not query_norm:
            return []
        results = []
        with self._connect() as conn:
            for row in conn.execute("SELECT id,hash,vector FROM vectors WHERE model=?", (model,)):
                if hashes.get(row["id"]) != row["hash"]:
                    continue
                vector = json.loads(row["vector"])
                if len(vector) != len(query):
                    continue
                norm = math.sqrt(sum(v * v for v in vector))
                if norm:
                    score = sum(x * y for x, y in zip(vector, query, strict=True))
                    results.append((row["id"], score / (norm * query_norm)))
        return sorted(results, key=lambda pair: pair[1], reverse=True)


async def embed(texts: list[str], model: str, key: str) -> list[list[float]]:
    """Embed only requested synopsis/title text; no source credentials are transmitted."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": "Bearer " + key},
            json={"model": model, "input": texts},
        )
        response.raise_for_status()
        entries = sorted(response.json().get("data", []), key=lambda row: row["index"])
    vectors = [row["embedding"] for row in entries]
    if len(vectors) != len(texts) or any(not vector for vector in vectors):
        raise ValueError("Invalid or incomplete embedding response.")
    return vectors
