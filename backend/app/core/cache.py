"""SHA-256 keyed response cache on table ``llm_cache`` (docs/cost-efficiency.md section 5).

Key purity is the invalidation story: nothing about the case, applicant, reviewer, ``today`` or
time goes into the key. Only validated results are ever stored.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.models import Strict, UsdDecimal, UtcDatetime, utcnow

if TYPE_CHECKING:
    from app.core.db import Database
    from app.core.llm import LLMRequest

LLM_CACHE_SCHEMA = 1
VALUE_FORMAT = 1


def canonical_json(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _field(x: str) -> str:
    return f"{len(x)}:{x}"


def prompt_hash(req: LLMRequest) -> str:
    h = hashlib.sha256()
    h.update(_field(req.system).encode("utf-8"))
    h.update(_field(req.user).encode("utf-8"))
    h.update(_field(canonical_json(req.json_schema)).encode("utf-8"))
    for img in req.images:
        h.update(_field(hashlib.sha256(img).hexdigest()).encode("utf-8"))
    return h.hexdigest()


def cache_key(
    req: LLMRequest,
    model: str,
    client_version: str,
    max_output_tokens: int | None = None,
) -> str:
    """Section 5.1. ``model`` is the resolved model name (not the role)."""
    if max_output_tokens is None:
        from app.core.llm import MAX_OUTPUT_TOKENS

        max_output_tokens = MAX_OUTPUT_TOKENS
    parts = [
        "gldb-llm-cache",
        str(LLM_CACHE_SCHEMA),
        req.purpose,
        model,
        req.schema_name,
        prompt_hash(req),
        req.cache_extra,
        client_version,
        "t0",
        str(max_output_tokens),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class CachedResponse(Strict):
    data: dict
    model: str
    purpose: str
    prompt_tokens: int
    completion_tokens: int
    usd: UsdDecimal
    created_at: UtcDatetime


class CacheStats(Strict):
    enabled: bool
    entries: int
    hits: int
    misses: int


class ResponseCache:
    def __init__(self, db: Database, *, enabled: bool = True):
        self._db = db
        self.enabled = enabled
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> CachedResponse | None:
        """Cached response, or ``None`` on a miss (always ``None`` when disabled)."""
        if not self.enabled:
            return None
        with self._db.conn() as c:
            row = c.execute(
                "SELECT value, created_at, model, purpose FROM llm_cache WHERE key = ?", (key,)
            ).fetchone()
        with self._lock:
            if row is None:
                self._misses += 1
                return None
            self._hits += 1
        value = json.loads(row["value"])
        if value.get("v") != VALUE_FORMAT:
            return None
        return CachedResponse(
            data=value["data"],
            model=row["model"],
            purpose=row["purpose"],
            prompt_tokens=int(value.get("prompt_tokens", 0)),
            completion_tokens=int(value.get("completion_tokens", 0)),
            usd=Decimal(value.get("usd", "0")),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def put(
        self,
        key: str,
        data: dict,
        *,
        model: str,
        purpose: str,
        prompt_tokens: int,
        completion_tokens: int,
        usd: Decimal,
        file_sha256: str | None,
    ) -> None:
        """INSERT OR REPLACE; a no-op when disabled."""
        if not self.enabled:
            return
        from app.core.db import ts

        value = {
            "v": VALUE_FORMAT,
            "data": data,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "usd": format(usd, "f"),
            "file_sha256": file_sha256,
        }
        with self._db.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO llm_cache (key, value, created_at, model, purpose) "
                "VALUES (?,?,?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), ts(utcnow()), model, purpose),
            )

    def clear(self, *, purpose: str | None = None, model: str | None = None) -> int:
        """Delete rows (all, or filtered by purpose and/or model); returns the number deleted."""
        clauses: list[str] = []
        params: list[str] = []
        if purpose is not None:
            clauses.append("purpose = ?")
            params.append(purpose)
        if model is not None:
            clauses.append("model = ?")
            params.append(model)
        sql = "DELETE FROM llm_cache"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._db.conn() as c:
            return c.execute(sql, params).rowcount

    def prune(self, *, older_than: timedelta) -> int:
        """Delete rows older than ``older_than`` (by ``created_at``); called by retention."""
        from app.core.db import ts

        cutoff = ts(utcnow() - older_than)
        with self._db.conn() as c:
            return c.execute("DELETE FROM llm_cache WHERE created_at < ?", (cutoff,)).rowcount

    def stats(self) -> CacheStats:
        with self._db.conn() as c:
            entries = c.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        with self._lock:
            return CacheStats(
                enabled=self.enabled, entries=entries, hits=self._hits, misses=self._misses
            )
