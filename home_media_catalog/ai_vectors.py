"""Incremental local embeddings with a cached, optional HNSW nearest-neighbor index.

SQLite is the authoritative vector store; the disposable in-memory index is built
only from vectors matching the current catalogue document hashes. Synopses are
embedded via OpenAI. No index files or credentials are sent to a vector provider.
"""

import asyncio
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

import httpx

from .ai_usage import tracked_post

ANN_MIN_DOCUMENTS = 2000
DEFAULT_RANK_LIMIT = 400


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
        self._rank_lock = RLock()
        self._rank_cache: dict | None = None

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

    def _invalidate(self):
        with self._rank_lock:
            self._rank_cache = None

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
            self._invalidate()
            indexed += len(batch)
            await asyncio.sleep(0)
        if limit is None:
            active_ids = {doc["id"] for doc in documents}
            with self._connect() as conn:
                stale = [
                    (model, row["id"])
                    for row in conn.execute("SELECT id FROM vectors WHERE model=?", (model,))
                    if row["id"] not in active_ids
                ]
                if stale:
                    conn.executemany("DELETE FROM vectors WHERE model=? AND id=?", stale)
            if stale:
                self._invalidate()
        return indexed

    def rank(
        self,
        documents: list[dict],
        model: str,
        query: list[float],
        limit: int = DEFAULT_RANK_LIMIT,
    ) -> list[tuple[str, float]]:
        """Exact SIMD ranking for small corpora; HNSW for large ones.

        Cache identity includes the selected document IDs and content hashes.
        This also makes optional person-scoped searches safe: unrelated works
        cannot enter their ANN index or receive scores.
        """
        if not query or not limit:
            return []
        query_norm = math.sqrt(sum(v * v for v in query))
        if not query_norm:
            return []
        expected = {doc["id"]: self.digest(doc["text"]) for doc in documents}
        with self._rank_lock:
            with self._connect() as conn:
                valid = sorted(
                    (row["id"], row["hash"])
                    for row in conn.execute("SELECT id,hash FROM vectors WHERE model=?", (model,))
                    if expected.get(row["id"]) == row["hash"]
                )
            if not valid:
                return []
            signature = (model, len(query), tuple(valid))
            if self._rank_cache is None or self._rank_cache["signature"] != signature:
                accepted = dict(valid)
                with self._connect() as conn:
                    records = [
                        (row["id"], json.loads(row["vector"]))
                        for row in conn.execute(
                            "SELECT id,vector FROM vectors WHERE model=?", (model,)
                        )
                        if row["id"] in accepted
                    ]
                records = [
                    (work_id, vector)
                    for work_id, vector in records
                    if len(vector) == len(query) and any(vector)
                ]
                ids = [work_id for work_id, _ in records]
                vectors = [vector for _, vector in records]
                cache: dict = {
                    "signature": signature,
                    "ids": ids,
                    "vectors": vectors,
                    "backend": "python",
                }
                try:
                    import numpy as np
                except ImportError:
                    np = None
                if np is not None and vectors:
                    matrix = np.asarray(vectors, dtype=np.float32)
                    norms = np.linalg.norm(matrix, axis=1)
                    matrix = matrix / np.maximum(norms[:, None], 1e-12)
                    cache["matrix"] = matrix
                    cache["backend"] = "numpy"
                    if len(ids) >= ANN_MIN_DOCUMENTS:
                        try:
                            from usearch.index import Index

                            index = Index(
                                ndim=len(query),
                                metric="cos",
                                dtype="f32",
                                connectivity=16,
                                expansion_add=128,
                                expansion_search=128,
                            )
                            index.add(np.arange(len(ids), dtype=np.uint64), matrix, threads=2)
                            cache["ann"] = index
                            cache["backend"] = "hnsw"
                        except ImportError:
                            # Source checkout without the optional native accelerator.
                            pass
                self._rank_cache = cache
            cache = self._rank_cache
            ids = cache["ids"]
            if not ids:
                return []
            count = min(limit, len(ids))
            if cache["backend"] == "hnsw":
                import numpy as np

                matches = cache["ann"].search(np.asarray(query, dtype=np.float32), count)
                return sorted(
                    ((ids[int(match.key)], 1.0 - float(match.distance)) for match in matches),
                    key=lambda pair: pair[1],
                    reverse=True,
                )[:count]
            if cache["backend"] == "numpy":
                import numpy as np

                q = np.asarray(query, dtype=np.float32) / query_norm
                scores = cache["matrix"] @ q
                order = np.argsort(scores)[::-1][:count]
                return [(ids[int(i)], float(scores[i])) for i in order]
            results = []
            for work_id, vector in zip(ids, cache["vectors"], strict=True):
                norm = math.sqrt(sum(v * v for v in vector))
                if norm:
                    score = sum(x * y for x, y in zip(vector, query, strict=True))
                    results.append((work_id, score / (norm * query_norm)))
            return sorted(results, key=lambda pair: pair[1], reverse=True)[:count]


async def embed(texts: list[str], model: str, key: str) -> list[list[float]]:
    """Embed only requested synopsis/metadata text; no source credentials are transmitted."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await tracked_post(
            client,
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
