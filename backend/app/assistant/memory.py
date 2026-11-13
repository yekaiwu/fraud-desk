"""The last four turns of one officer on one case version.

A new version starts with an empty memory. The question text is stored here so the next turn can
be read in context, and nowhere else: the audit log keeps a length and a hash. When encryption at
rest is on, the question and the verified answer are sealed with the case vault.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.audit import sha256_text
from app.core.db import Database

TURNS_KEPT = 4


def _ctx(account: str, case_id: str, version: int, digest: str) -> str:
    return f"assistant|{account}|{case_id}|{version}|{digest}"


def recent(db: Database, *, account: str, case_id: str, version: int) -> list[dict[str, Any]]:
    """The last four turns, oldest first. A turn that cannot be opened is skipped."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT question, question_sha256, answer_json, status FROM ("
            " SELECT question, question_sha256, answer_json, status, id FROM assistant_turns"
            " WHERE account = ? AND case_id = ? AND case_version = ?"
            " ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (account, case_id, version, TURNS_KEPT),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        digest = row["question_sha256"]
        ctx = _ctx(account, case_id, version, digest)
        try:
            question = db.vault.open_text(row["question"], ctx)
            answer = db.vault.open_text(row["answer_json"], ctx)
        except Exception:  # noqa: BLE001 - a turn we cannot open is not shown to the model
            continue
        try:
            parsed = json.loads(answer)
        except ValueError:
            parsed = {"status": row["status"], "sentences": []}
        out.append({"question": question, "status": row["status"], "answer": parsed})
    return out


def remember(
    db: Database,
    *,
    account: str,
    case_id: str,
    version: int,
    scope: str,
    file_id: str | None,
    page: int | None,
    question: str,
    status: str,
    answer: dict[str, Any],
    citations_kept: int,
    citations_dropped: int,
    dropped: dict[str, int],
    model: str,
    prompt_sha256: str,
    prompt_tokens: int,
    completion_tokens: int,
    usd: str,
    seconds: float,
    called: bool,
    ts: str,
) -> None:
    """Append one turn and drop any older than the four we keep for this officer and version."""
    digest = sha256_text(question)
    ctx = _ctx(account, case_id, version, digest)
    payload = json.dumps(answer, ensure_ascii=False, sort_keys=True)
    with db.conn() as c:
        c.execute(
            "INSERT INTO assistant_turns ("
            " ts, account, case_id, case_version, scope, file_id, page,"
            " question, question_sha256, question_len, status, answer_json,"
            " citations_kept, citations_dropped, drop_reasons, model, prompt_sha256,"
            " prompt_tokens, completion_tokens, usd, seconds, called"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts,
                account,
                case_id,
                version,
                scope,
                file_id,
                page,
                db.vault.seal_text(question, ctx),
                digest,
                len(question),
                status,
                db.vault.seal_text(payload, ctx),
                citations_kept,
                citations_dropped,
                json.dumps(dropped, sort_keys=True),
                model,
                prompt_sha256,
                prompt_tokens,
                completion_tokens,
                usd,
                seconds,
                1 if called else 0,
            ),
        )
        c.execute(
            "DELETE FROM assistant_turns WHERE id IN ("
            " SELECT id FROM assistant_turns"
            " WHERE account = ? AND case_id = ? AND case_version = ?"
            " ORDER BY id DESC LIMIT -1 OFFSET ?"
            ")",
            (account, case_id, version, TURNS_KEPT),
        )


def prior_spend(db: Database, case_id: str) -> tuple[int, Decimal]:
    """Model calls already made by Ask on this case, across versions: (calls, dollars)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT usd FROM assistant_turns WHERE case_id = ? AND called = 1",
            (case_id,),
        ).fetchall()
    total = sum((Decimal(r["usd"]) for r in rows), Decimal("0"))
    return len(rows), total


def cost_rows(db: Database, case_id: str, version: int) -> list[dict[str, Any]]:
    """Calls to merge into the case view so the timeline can list them. Not stored on the case."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT model, prompt_tokens, completion_tokens, usd, seconds, called"
            " FROM assistant_turns"
            " WHERE case_id = ? AND case_version = ? AND called = 1 ORDER BY id",
            (case_id, version),
        ).fetchall()
    return [
        {
            "stage": "score",
            "purpose": "assistant:ask",
            "model": r["model"],
            "prompt_tokens": int(r["prompt_tokens"]),
            "completion_tokens": int(r["completion_tokens"]),
            "usd": r["usd"],
            "seconds": float(r["seconds"]),
            "cache_hit": False,
            "document_id": None,
            "page": None,
        }
        for r in rows
    ]
