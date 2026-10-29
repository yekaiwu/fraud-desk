"""Append-only audit log (docs/review-workflow-and-audit.md sections 4.7 and 4.8).

This module contains only ``INSERT`` and ``SELECT`` statements on ``audit_log``, plus the single
``UPDATE audit_log SET case_ref = ? WHERE case_ref = ?`` in ``AuditLog.pseudonymise_case`` (used
after a case is deleted). The database triggers (core/db.py) back that up: ``DELETE`` always aborts
and ``UPDATE`` aborts unless only ``case_ref`` changes.

Audit ``detail`` carries ids, hashes, enums and counts only. Free text, names, NRIC/FIN numbers and
evidence never go in (``GLDB_PSEUDONYMISE_LOGS``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.db import Database, ts
from app.core.models import utcnow

Clock = Callable[[], datetime]

# --------------------------------------------------------------------------------------
# Scrubbing
# --------------------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ENTITY_UEN = re.compile(r"\b[TSRtsr]\d{2}[A-Za-z]{2}\d{4}[A-Za-z]\b")
_NRIC = re.compile(r"\b[STFGMstfgm]\d{7}[A-Za-z]\b")
_BUSINESS_UEN = re.compile(r"\b\d{8,9}[A-Za-z]\b")
_PLACEHOLDER = "[id]"


def redact_ids(text: str) -> str:
    """Replace e-mail addresses, UEN-like tokens and NRIC/FIN numbers with ``[id]``."""
    for pattern in (_EMAIL, _ENTITY_UEN, _NRIC, _BUSINESS_UEN):
        text = pattern.sub(_PLACEHOLDER, text)
    return text


def scrub(text: str, *, max_len: int = 200) -> str:
    """Redact identifiers, collapse whitespace and truncate (with ``...``) to ``max_len``."""
    cleaned = " ".join(redact_ids(text or "").split())
    if len(cleaned) > max_len:
        return cleaned[: max(0, max_len - 3)] + "..."
    return cleaned


def case_ref_hash(case_id: str) -> str:
    """Stable pseudonym of a case id: ``h_`` + first 16 hex of sha256("gldb-audit|" + id)."""
    return "h_" + hashlib.sha256(("gldb-audit|" + case_id).encode("utf-8")).hexdigest()[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TRAILING_N = re.compile(r"#\d+$")


def redact_flag_id(flag_id: str) -> str:
    """Flag ids can embed a person's name as discriminator (``x_directors.d9.-~tan_wei_ming#2``).
    The discriminator is replaced by a short hash: ``x_directors.d9.-~#<sha256(disc)[:8]>``.
    Ids without ``~`` are returned unchanged."""
    if "~" not in flag_id:
        return flag_id
    head, _, tail = flag_id.partition("~")
    disc = _TRAILING_N.sub("", tail)
    return f"{head}~#{hashlib.sha256(disc.encode('utf-8')).hexdigest()[:8]}"


def _backstop(value: Any) -> Any:
    """Redact identifier patterns in every string of a detail structure (defence in depth)."""
    if isinstance(value, str):
        return redact_ids(value)
    if isinstance(value, Mapping):
        return {str(k): _backstop(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_backstop(v) for v in value]
    return value


def iso(value: datetime) -> str:
    """ISO 8601 UTC with ``Z`` (matches the API models: no fraction when zero)."""
    value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------------------


def read_audit(db: Database, case_id: str) -> list[dict[str, Any]]:
    """Audit rows of a case, oldest first; matches the plain id and the pseudonym (SELECT only)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, ts, actor, action, detail FROM audit_log "
            "WHERE case_ref = ? OR case_ref = ? ORDER BY id",
            (case_id, case_ref_hash(case_id)),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ts": r["ts"],
            "actor": r["actor"],
            "action": r["action"],
            "detail": json.loads(r["detail"] or "{}"),
        }
        for r in rows
    ]


class AuditLog:
    def __init__(self, db: Database, settings: Settings, *, clock: Clock = utcnow):
        self.db = db
        self.settings = settings
        self.clock = clock

    def write(
        self,
        *,
        actor: str,
        action: str,
        case_id: str | None,
        detail: Mapping[str, Any],
        conn: sqlite3.Connection | None = None,
        hashed_ref: bool = False,
    ) -> int:
        """INSERT one row and return its id. Pass ``conn`` to join the caller's transaction.
        ``hashed_ref=True`` stores ``case_ref_hash(case_id)`` instead of the id (delete-case)."""
        ref = None
        if case_id is not None:
            ref = case_ref_hash(case_id) if hashed_ref else case_id
        payload = json.dumps(_backstop(dict(detail)), ensure_ascii=False, sort_keys=True)
        params = (ts(self.clock()), actor or "anonymous", action, ref, payload)
        sql = "INSERT INTO audit_log (ts, actor, action, case_ref, detail) VALUES (?,?,?,?,?)"
        if conn is not None:
            return int(conn.execute(sql, params).lastrowid)
        with self.db.conn() as c:
            return int(c.execute(sql, params).lastrowid)

    def for_case(self, case_id: str) -> list[dict[str, Any]]:
        """Rows for the case, oldest first; matches the plain id and the pseudonym."""
        return read_audit(self.db, case_id)

    def pseudonymise_case(self, case_id: str, *, conn: sqlite3.Connection | None = None) -> int:
        """The ONLY update of the log: rewrite ``case_ref`` from the id to its hash."""
        sql = "UPDATE audit_log SET case_ref = ? WHERE case_ref = ?"
        params = (case_ref_hash(case_id), case_id)
        if conn is not None:
            return conn.execute(sql, params).rowcount
        with self.db.conn() as c:
            return c.execute(sql, params).rowcount


# --------------------------------------------------------------------------------------
# Application log filter
# --------------------------------------------------------------------------------------

_LOGGER_PREFIXES = ("gldb", "app")
_FACTORY_MARK = "_gldb_pseudonymise"
_state: dict[str, Any] = {"original": None}


class PseudonymiseFilter(logging.Filter):
    """Redacts NRIC/FIN, UEN-like tokens and e-mail addresses from a record's message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not break logging
            return True
        redacted = redact_ids(message)
        if redacted != message or record.args:
            record.msg = redacted
            record.args = None
        return True


_FILTER = PseudonymiseFilter()


def _wants_redaction(name: str) -> bool:
    return any(name == p or name.startswith(p + ".") for p in _LOGGER_PREFIXES)


def install_log_filter(settings: Settings) -> None:
    """When ``GLDB_PSEUDONYMISE_LOGS`` is true, redact identifiers from records of the ``gldb`` and
    ``app`` loggers (a record factory, so handlers added later are covered too). Idempotent; with
    the setting off the redaction is removed again."""
    current = logging.getLogRecordFactory()
    if not settings.pseudonymise_logs:
        uninstall_log_filter()
        return
    if getattr(current, _FACTORY_MARK, False):
        return
    _state["original"] = current

    def factory(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        record = current(name, *args, **kwargs)
        if _wants_redaction(str(name)):
            _FILTER.filter(record)
        return record

    setattr(factory, _FACTORY_MARK, True)
    logging.setLogRecordFactory(factory)


def uninstall_log_filter() -> None:
    """Restore the record factory that was active before ``install_log_filter`` (tests)."""
    current = logging.getLogRecordFactory()
    if getattr(current, _FACTORY_MARK, False) and _state.get("original") is not None:
        logging.setLogRecordFactory(_state["original"])
        _state["original"] = None
