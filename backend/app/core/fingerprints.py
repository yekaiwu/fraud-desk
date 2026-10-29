"""Fingerprints for duplicate detection (docs/data-model.md section 4.1, checks-financial.md 3.5).

Checks only call ``lookup``; the pipeline records after ``score``. A store hit is reported by
case id, document id and date only (never another case's applicant or amounts).
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.core.models import Strict, UtcDatetime, utcnow

if TYPE_CHECKING:
    from app.core.db import Database

KINDS = ("file_sha256", "invoice", "crop_phash")


class FingerprintHit(Strict):
    kind: str
    fingerprint: str
    case_id: str
    document_id: str | None = None
    created_at: UtcDatetime
    meta: dict[str, Any] = {}


class FingerprintStore(Protocol):
    def lookup(
        self, kind: str, fingerprint: str, *, exclude_case_id: str | None = None
    ) -> list[FingerprintHit]: ...

    def record(
        self,
        kind: str,
        fingerprint: str,
        *,
        case_id: str,
        document_id: str | None,
        meta: dict | None = None,
    ) -> None: ...


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_uen(uen: str) -> str:
    return "".join((uen or "").split()).upper()


def _norm_number(number: str) -> str:
    # leading zeros and hyphens are kept on purpose
    return "".join((number or "").split()).upper()


def _norm_total(total: str) -> str:
    try:
        d = Decimal((total or "").strip())
    except InvalidOperation:
        return (total or "").strip()
    out = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(abs(out) if out == 0 else out, "f")


def invoice_fingerprint(
    supplier_uen: str, invoice_number: str, invoice_date: str, total: str
) -> str:
    """sha256 hex of ``"inv|" + "|".join([uen, number, ISO date, total at 2 dp])``."""
    parts = [
        _norm_uen(supplier_uen),
        _norm_number(invoice_number),
        (invoice_date or "").strip(),
        _norm_total(total),
    ]
    return hashlib.sha256(("inv|" + "|".join(parts)).encode("utf-8")).hexdigest()


def invoice_key_fingerprint(supplier_uen: str, invoice_number: str) -> str:
    """sha256 hex of ``"invkey|" + uen + "|" + number`` (same supplier and invoice number)."""
    pre = "invkey|" + _norm_uen(supplier_uen) + "|" + _norm_number(invoice_number)
    return hashlib.sha256(pre.encode("utf-8")).hexdigest()


class SqliteFingerprintStore:
    """Fingerprints in the ``fingerprints`` table of the case database."""

    def __init__(self, db: Database):
        self._db = db

    def lookup(
        self, kind: str, fingerprint: str, *, exclude_case_id: str | None = None
    ) -> list[FingerprintHit]:
        sql = (
            "SELECT kind, fingerprint, case_id, document_id, created_at, meta "
            "FROM fingerprints WHERE kind = ? AND fingerprint = ?"
        )
        params: list[Any] = [kind, fingerprint]
        if exclude_case_id is not None:
            sql += " AND case_id != ?"
            params.append(exclude_case_id)
        sql += " ORDER BY created_at, case_id, COALESCE(document_id, '')"
        with self._db.conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [
            FingerprintHit(
                kind=r["kind"],
                fingerprint=r["fingerprint"],
                case_id=r["case_id"],
                document_id=r["document_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                meta=json.loads(r["meta"]) if r["meta"] else {},
            )
            for r in rows
        ]

    def record(
        self,
        kind: str,
        fingerprint: str,
        *,
        case_id: str,
        document_id: str | None,
        meta: dict | None = None,
    ) -> None:
        """Idempotent: the same (kind, fingerprint, case, document) is stored once."""
        from app.core.db import ts

        with self._db.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO fingerprints "
                "(kind, fingerprint, case_id, document_id, created_at, meta) VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    fingerprint,
                    case_id,
                    document_id,
                    ts(utcnow()),
                    json.dumps(meta or {}, sort_keys=True),
                ),
            )

    def forget_case(self, case_id: str) -> int:
        """Remove every fingerprint a case recorded (a rerun re-records them)."""
        with self._db.conn() as c:
            return c.execute("DELETE FROM fingerprints WHERE case_id = ?", (case_id,)).rowcount


class MemoryFingerprintStore:
    """In-memory store with the same behaviour (default for tests and ``make_ctx``)."""

    def __init__(self) -> None:
        self._rows: list[FingerprintHit] = []
        self._lock = threading.Lock()

    def lookup(
        self, kind: str, fingerprint: str, *, exclude_case_id: str | None = None
    ) -> list[FingerprintHit]:
        with self._lock:
            return [
                h
                for h in self._rows
                if h.kind == kind
                and h.fingerprint == fingerprint
                and (exclude_case_id is None or h.case_id != exclude_case_id)
            ]

    def record(
        self,
        kind: str,
        fingerprint: str,
        *,
        case_id: str,
        document_id: str | None,
        meta: dict | None = None,
    ) -> None:
        with self._lock:
            for h in self._rows:
                if (h.kind, h.fingerprint, h.case_id, h.document_id) == (
                    kind,
                    fingerprint,
                    case_id,
                    document_id,
                ):
                    return
            self._rows.append(
                FingerprintHit(
                    kind=kind,
                    fingerprint=fingerprint,
                    case_id=case_id,
                    document_id=document_id,
                    created_at=utcnow(),
                    meta=dict(meta or {}),
                )
            )

    def forget_case(self, case_id: str) -> int:
        with self._lock:
            before = len(self._rows)
            self._rows = [h for h in self._rows if h.case_id != case_id]
            return before - len(self._rows)
