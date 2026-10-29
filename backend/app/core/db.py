"""SQLite layer (docs/architecture.md section 6). JSON columns, WAL, parameterised SQL only.

``Database.init_schema()`` creates every table idempotently, so later agents never edit this file.
One connection per call (threads are safe); ``:memory:`` uses a single locked shared connection.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from app.core.models import (
    Case,
    ReviewEntry,
    RuleOverride,
    StageRecord,
    Strict,
    UtcDatetime,
    utcnow,
)
from app.core.storage import FileStore

MEMORY = ":memory:"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    use_case TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    reference TEXT,
    applicant_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    score INTEGER,
    band TEXT,
    lane TEXT,
    coverage_ratio REAL,
    partial INTEGER NOT NULL DEFAULT 0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    high_count INTEGER NOT NULL DEFAULT 0,
    decision TEXT,
    top_finding TEXT,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases (status);
CREATE INDEX IF NOT EXISTS idx_cases_created ON cases (created_at);

CREATE TABLE IF NOT EXISTS case_versions (
    case_id TEXT NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (case_id, version)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    stages TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_jobs_case ON jobs (case_id);

CREATE TABLE IF NOT EXISTS review_entries (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    case_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_case ON review_entries (case_id, case_version);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    case_ref TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log (case_ref);

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: delete not allowed');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_case_ref_only
BEFORE UPDATE ON audit_log
WHEN NEW.id IS NOT OLD.id OR NEW.ts IS NOT OLD.ts OR NEW.actor IS NOT OLD.actor
     OR NEW.action IS NOT OLD.action OR NEW.detail IS NOT OLD.detail
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: only case_ref may change');
END;

-- Check catalogue overrides (docs/data-model.md). Global configuration: no case id, so delete-case
-- and retention never touch these tables. rule_changes is the append-only change log.
CREATE TABLE IF NOT EXISTS rule_overrides (
    rule TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    severity TEXT,
    name TEXT,
    description TEXT,
    note TEXT,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    revision INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule TEXT NOT NULL,
    at TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('update', 'reset')),
    "before" TEXT NOT NULL,
    "after" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rule_changes_rule ON rule_changes (rule, id);

CREATE TRIGGER IF NOT EXISTS rule_changes_no_delete
BEFORE DELETE ON rule_changes
BEGIN
    SELECT RAISE(ABORT, 'rule_changes is append-only: delete not allowed');
END;

CREATE TRIGGER IF NOT EXISTS rule_changes_no_update
BEFORE UPDATE ON rule_changes
BEGIN
    SELECT RAISE(ABORT, 'rule_changes is append-only: update not allowed');
END;

CREATE TABLE IF NOT EXISTS fingerprints (
    kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    case_id TEXT NOT NULL,
    document_id TEXT,
    created_at TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_fingerprints_lookup ON fingerprints (kind, fingerprint);
CREATE INDEX IF NOT EXISTS idx_fingerprints_case ON fingerprints (case_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fingerprints_unique
    ON fingerprints (kind, fingerprint, case_id, COALESCE(document_id, ''));

CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_spend (
    day TEXT PRIMARY KEY,
    micro_usd INTEGER NOT NULL DEFAULT 0
);
"""

# A partial case (pages that could not be read) has an understated score: silence is not safety.
# For the priority sort it ranks as at least a medium case, so it never sinks below a clean,
# fully covered case with a single low flag.
PARTIAL_PRIORITY_FLOOR = 30

# status rank for the frozen `priority` sort (architecture.md section 6)
PRIORITY_ORDER_SQL = (
    "CASE status WHEN 'open' THEN 0 WHEN 'processing' THEN 1 WHEN 'awaiting_documents' THEN 2 "
    "WHEN 'closed' THEN 3 ELSE 4 END, "
    f"CASE WHEN partial = 1 THEN MAX(COALESCE(score, 0), {PARTIAL_PRIORITY_FLOOR}) "
    "ELSE score END DESC NULLS LAST, "
    "coverage_ratio ASC NULLS LAST, created_at ASC, id ASC"
)
SORTS = ("priority", "score", "age", "coverage", "created")


def ts(value: datetime) -> str:
    """Fixed-width UTC timestamp so that text order equals time order."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_case_id() -> str:
    return "c_" + uuid.uuid4().hex[:12]


def new_job_id() -> str:
    return "j_" + uuid.uuid4().hex[:12]


class CaseNotFound(KeyError):
    pass


class CaseFilters(Strict):
    use_case: list[str] | None = None
    status: list[str] | None = None
    lane: list[str] | None = None
    band: list[str] | None = None
    min_score: int | None = None
    q: str | None = None
    partial: bool | None = None


class JobRecord(Strict):
    id: str
    case_id: str
    kind: Literal["analyse", "rerun"]
    state: Literal["queued", "running", "succeeded", "failed"] = "queued"
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error: str | None = None
    stages: list[StageRecord] = Field(default_factory=list)


class DeleteResult(Strict):
    case_id: str
    versions: int = 0
    files_removed: list[str] = []
    cache_rows_removed: int = 0


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    def __init__(self, path: str | Path = MEMORY, *, data_dir: Path | str | None = None):
        self.path = str(path)
        self._memory = self.path == MEMORY
        self._lock = threading.RLock()
        self._shared: sqlite3.Connection | None = None
        if self._memory:
            self._shared = self._connect(MEMORY, check_same_thread=False)
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if data_dir is not None:
            self._data_dir: Path | None = Path(data_dir)
        elif not self._memory:
            self._data_dir = Path(self.path).parent
        else:
            self._data_dir = None
        self._files: FileStore | None = None

    # ----------------------------------------------------------------------------------
    # connections
    # ----------------------------------------------------------------------------------

    @staticmethod
    def _connect(path: str, *, check_same_thread: bool = True) -> sqlite3.Connection:
        c = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        if path != MEMORY:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
        return c

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection (row factory ``sqlite3.Row``); commit on success, rollback on error."""
        if self._memory:
            assert self._shared is not None
            with self._lock:
                try:
                    yield self._shared
                    self._shared.commit()
                except BaseException:
                    self._shared.rollback()
                    raise
            return
        c = self._connect(self.path)
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def init_schema(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)

    @property
    def files(self) -> FileStore:
        """Content-addressed file store next to the database (or a temp dir for ``:memory:``)."""
        if self._files is None:
            if self._data_dir is None:
                self._data_dir = Path(tempfile.mkdtemp(prefix="gldb-data-"))
            self._files = FileStore(self._data_dir)
        return self._files

    # ----------------------------------------------------------------------------------
    # cases
    # ----------------------------------------------------------------------------------

    @staticmethod
    def _body(case: Case) -> str:
        return case.model_dump_json(exclude={"review"})

    @staticmethod
    def _denormalised(case: Case) -> dict[str, Any]:
        non_info = [f for f in case.findings if f.severity != "info"]
        return {
            "use_case": case.use_case,
            "status": case.status,
            "version": case.version,
            "reference": case.reference,
            "applicant_name": case.applicant.name,
            "created_at": ts(case.created_at),
            "updated_at": ts(case.updated_at),
            "score": case.score.value if case.score else None,
            "band": case.score.band if case.score else None,
            "lane": case.lane,
            "coverage_ratio": case.coverage.ratio if case.coverage else None,
            "partial": int(case.partial or (case.score is not None and case.score.partial)),
            "findings_count": len(non_info),
            "high_count": sum(1 for f in case.findings if f.severity == "high"),
            "decision": case.decision,
            "top_finding": case.top_finding,
        }

    def create_case(self, case: Case) -> None:
        cols = self._denormalised(case)
        names = ["id", *cols, "body"]
        values = [case.id, *cols.values(), self._body(case)]
        marks = ",".join("?" for _ in names)
        with self.conn() as c:
            c.execute("INSERT INTO cases (" + ",".join(names) + ") VALUES (" + marks + ")", values)

    def save_case(self, case: Case, *, snapshot_version: bool = False, touch: bool = True) -> None:
        """Persist the case and refresh the denormalised columns.

        ``snapshot_version=True`` also writes the body into ``case_versions`` at
        ``(case.id, case.version)`` (used when a version is finished or about to be superseded).
        """
        if touch:
            case.updated_at = utcnow()
        cols = self._denormalised(case)
        body = self._body(case)
        assignments = ", ".join(k + " = ?" for k in cols)
        with self.conn() as c:
            cur = c.execute(
                "UPDATE cases SET " + assignments + ", body = ? WHERE id = ?",
                [*cols.values(), body, case.id],
            )
            if cur.rowcount == 0:
                raise CaseNotFound(case.id)
            if snapshot_version:
                c.execute(
                    "INSERT OR REPLACE INTO case_versions (case_id, version, created_at, body) "
                    "VALUES (?,?,?,?)",
                    (case.id, case.version, ts(utcnow()), body),
                )

    def case_exists(self, case_id: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM cases WHERE id = ?", (case_id,)).fetchone() is not None

    def load_case(self, case_id: str, version: int | None = None) -> Case:
        """Load a case (or one of its snapshotted versions).

        ``Case.review`` holds only the review entries whose ``case_version`` equals the loaded
        version. Raises ``CaseNotFound``.
        """
        with self.conn() as c:
            row = c.execute("SELECT body, version FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            body, current = row["body"], row["version"]
            if version is not None and version != current:
                vrow = c.execute(
                    "SELECT body FROM case_versions WHERE case_id = ? AND version = ?",
                    (case_id, version),
                ).fetchone()
                if vrow is None:
                    raise CaseNotFound(f"{case_id} v{version}")
                body = vrow["body"]
            loaded_version = current if version is None else version
            reviews = c.execute(
                "SELECT body FROM review_entries WHERE case_id = ? AND case_version = ? "
                "ORDER BY created_at, rowid",
                (case_id, loaded_version),
            ).fetchall()
        case = Case.model_validate_json(body)
        case.review = [ReviewEntry.model_validate_json(r["body"]) for r in reviews]
        return case

    def list_versions(self, case_id: str) -> list[dict[str, Any]]:
        """Snapshotted versions (``version``, ``created_at``) plus the current one."""
        with self.conn() as c:
            cur = c.execute("SELECT version FROM cases WHERE id = ?", (case_id,)).fetchone()
            if cur is None:
                raise CaseNotFound(case_id)
            rows = c.execute(
                "SELECT version, created_at FROM case_versions WHERE case_id = ? ORDER BY version",
                (case_id,),
            ).fetchall()
        out = [{"version": r["version"], "created_at": r["created_at"]} for r in rows]
        if all(v["version"] != cur["version"] for v in out):
            out.append({"version": cur["version"], "created_at": None})
        return out

    def list_cases(
        self,
        filters: CaseFilters | None = None,
        sort: str = "priority",
        page: int = 1,
        page_size: int = 25,
        *,
        order: str | None = None,
    ) -> tuple[list[sqlite3.Row], int]:
        """Filtered, sorted page of ``cases`` rows plus the total count (api.md section 4).

        ``priority`` is frozen (status rank, score desc, coverage asc, created asc) and ignores
        ``order``. ``score``/``created`` default to desc, ``age`` lists oldest first by default,
        ``coverage`` lists least coverage first by default; ``order`` reverses them.
        """
        if sort not in SORTS:
            raise ValueError(f"unknown sort {sort!r}")
        f = filters or CaseFilters()
        where: list[str] = []
        params: list[Any] = []

        def in_clause(column: str, values: Sequence[str] | None) -> None:
            if values:
                where.append(column + " IN (" + ",".join("?" for _ in values) + ")")
                params.extend(values)

        in_clause("use_case", f.use_case)
        in_clause("status", f.status)
        in_clause("lane", f.lane)
        in_clause("band", f.band)
        if f.min_score is not None:
            where.append("score >= ?")
            params.append(f.min_score)
        if f.partial is not None:
            where.append("partial = ?")
            params.append(int(f.partial))
        if f.q:
            like = f"%{_like_escape(f.q.lower())}%"
            where.append(
                "(LOWER(COALESCE(reference,'')) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(applicant_name,'')) LIKE ? ESCAPE '\\')"
            )
            params.extend([like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        asc = (order or "").lower() == "asc"
        desc = (order or "").lower() == "desc"
        if sort == "priority":
            order_sql = PRIORITY_ORDER_SQL
        elif sort == "score":
            order_sql = (
                "score " + ("ASC" if asc else "DESC") + " NULLS LAST, created_at ASC, id ASC"
            )
        elif sort == "created":
            order_sql = "created_at " + ("ASC" if asc else "DESC") + ", id ASC"
        elif sort == "age":
            order_sql = "created_at " + ("DESC" if desc else "ASC") + ", id ASC"
        else:  # coverage
            order_sql = (
                "coverage_ratio "
                + ("DESC" if desc else "ASC")
                + " NULLS LAST, created_at ASC, id ASC"
            )

        page = max(1, page)
        page_size = max(1, page_size)
        with self.conn() as c:
            total = c.execute("SELECT COUNT(*) FROM cases" + clause, params).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM cases" + clause + " ORDER BY " + order_sql + " LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return rows, total

    def delete_case(self, case_id: str) -> DeleteResult:
        """Remove the case, versions, review entries, jobs, fingerprints, unreferenced files and
        renders, and the ``llm_cache`` rows tied to files whose reference count drops to zero.
        Audit rows are kept (``audit.py`` pseudonymises them)."""
        with self.conn() as c:
            row = c.execute("SELECT id FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            shas: set[str] = set()
            for r in c.execute(
                "SELECT j.value AS f FROM cases, json_each(cases.body, '$.files') AS j "
                "WHERE cases.id = ?",
                (case_id,),
            ):
                shas.add(json.loads(r["f"])["sha256"])
            for r in c.execute(
                "SELECT j.value AS f FROM case_versions v, json_each(v.body, '$.files') AS j "
                "WHERE v.case_id = ?",
                (case_id,),
            ):
                shas.add(json.loads(r["f"])["sha256"])
            versions = c.execute(
                "SELECT COUNT(*) FROM case_versions WHERE case_id = ?", (case_id,)
            ).fetchone()[0]

            c.execute("DELETE FROM review_entries WHERE case_id = ?", (case_id,))
            c.execute("DELETE FROM jobs WHERE case_id = ?", (case_id,))
            c.execute("DELETE FROM case_versions WHERE case_id = ?", (case_id,))
            c.execute("DELETE FROM fingerprints WHERE case_id = ?", (case_id,))
            c.execute("DELETE FROM cases WHERE id = ?", (case_id,))

            freed: list[str] = []
            cache_removed = 0
            for sha in sorted(shas):
                still_used = (
                    c.execute(
                        "SELECT 1 FROM cases, json_each(cases.body, '$.files') AS j "
                        "WHERE json_extract(j.value, '$.sha256') = ? LIMIT 1",
                        (sha,),
                    ).fetchone()
                    or c.execute(
                        "SELECT 1 FROM case_versions v, json_each(v.body, '$.files') AS j "
                        "WHERE json_extract(j.value, '$.sha256') = ? LIMIT 1",
                        (sha,),
                    ).fetchone()
                )
                if still_used:
                    continue
                freed.append(sha)
                cur = c.execute(
                    "DELETE FROM llm_cache WHERE json_extract(value, '$.file_sha256') = ?",
                    (sha,),
                )
                cache_removed += cur.rowcount
        for sha in freed:
            self.files.delete(sha)
        return DeleteResult(
            case_id=case_id,
            versions=versions,
            files_removed=freed,
            cache_rows_removed=cache_removed,
        )

    # ----------------------------------------------------------------------------------
    # jobs
    # ----------------------------------------------------------------------------------

    def create_job(self, case_id: str, kind: Literal["analyse", "rerun"] = "analyse") -> JobRecord:
        job = JobRecord(id=new_job_id(), case_id=case_id, kind=kind, created_at=utcnow())
        with self.conn() as c:
            c.execute(
                "INSERT INTO jobs (id, case_id, kind, state, created_at, stages) VALUES (?,?,?,?,?,?)",
                (job.id, case_id, kind, job.state, ts(job.created_at), "[]"),
            )
        return job

    def update_job(
        self,
        job_id: str,
        *,
        state: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
        stages: Sequence[StageRecord] | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if state is not None:
            sets.append("state = ?")
            params.append(state)
        if started_at is not None:
            sets.append("started_at = ?")
            params.append(ts(started_at))
        if finished_at is not None:
            sets.append("finished_at = ?")
            params.append(ts(finished_at))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if stages is not None:
            sets.append("stages = ?")
            params.append(json.dumps([s.model_dump(mode="json") for s in stages]))
        if not sets:
            return
        with self.conn() as c:
            c.execute("UPDATE jobs SET " + ", ".join(sets) + " WHERE id = ?", [*params, job_id])

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return None if r is None else _job_from_row(r)

    def latest_job(self, case_id: str) -> JobRecord | None:
        with self.conn() as c:
            r = c.execute(
                "SELECT * FROM jobs WHERE case_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        return None if r is None else _job_from_row(r)

    def recover_interrupted_jobs(self) -> list[str]:
        """Jobs left ``queued``/``running`` at startup become ``failed("interrupted")`` and their
        ``processing`` case ``status="error"`` (api.md section 7). Returns the affected job ids."""
        now = ts(utcnow())
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, case_id FROM jobs WHERE state IN ('queued', 'running')"
            ).fetchall()
            for r in rows:
                c.execute(
                    "UPDATE jobs SET state = 'failed', error = 'interrupted', finished_at = ? "
                    "WHERE id = ?",
                    (now, r["id"]),
                )
        for case_id in sorted({r["case_id"] for r in rows}):
            try:
                case = self.load_case(case_id)
            except CaseNotFound:
                continue
            if case.status == "processing":
                case.status = "error"
                self.save_case(case)
        return [r["id"] for r in rows]

    # ----------------------------------------------------------------------------------
    # check catalogue overrides (docs/scoring-and-queue.md "Catalogue overrides")
    # ----------------------------------------------------------------------------------

    def rule_overrides(self) -> dict[str, RuleOverride]:
        """Every override row, by rule id (one cheap query; read when an analysis starts)."""
        with self.conn() as c:
            rows = c.execute("SELECT * FROM rule_overrides ORDER BY rule").fetchall()
        return {r["rule"]: _override_from_row(r) for r in rows}

    def get_rule_override(self, rule: str) -> RuleOverride | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM rule_overrides WHERE rule = ?", (rule,)).fetchone()
        return None if r is None else _override_from_row(r)

    def write_rule_override(
        self,
        rule: str,
        fields: dict[str, Any] | None,
        *,
        reviewer: str,
        reason: str,
        at: datetime,
    ) -> RuleOverride | None:
        """Set (``fields`` = enabled/severity/name/description/note) or reset (``fields=None``) the
        override of ``rule`` and append the change to ``rule_changes``, in one transaction.
        Returns the new override (``None`` after a reset). The revision counts the changes ever
        logged for the rule, so it keeps rising across resets."""
        stamp = ts(at)
        with self.conn() as c:
            row = c.execute("SELECT * FROM rule_overrides WHERE rule = ?", (rule,)).fetchone()
            before = _override_from_row(row) if row is not None else None
            revision = (
                c.execute("SELECT COUNT(*) FROM rule_changes WHERE rule = ?", (rule,)).fetchone()[0]
                + 1
            )
            after: RuleOverride | None = None
            if fields is None:
                c.execute("DELETE FROM rule_overrides WHERE rule = ?", (rule,))
            else:
                after = RuleOverride(
                    **fields,
                    updated_by=reviewer,
                    updated_at=stamp,
                    reason=reason,
                    revision=revision,
                )
                c.execute(
                    "INSERT OR REPLACE INTO rule_overrides (rule, enabled, severity, name, "
                    "description, note, updated_by, updated_at, reason, revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        rule,
                        int(after.enabled),
                        after.severity,
                        after.name,
                        after.description,
                        after.note,
                        after.updated_by,
                        after.updated_at,
                        after.reason,
                        after.revision,
                    ),
                )
            c.execute(
                'INSERT INTO rule_changes (rule, at, reviewer, reason, action, "before", "after") '
                "VALUES (?,?,?,?,?,?,?)",
                (
                    rule,
                    stamp,
                    reviewer,
                    reason,
                    "reset" if after is None else "update",
                    json.dumps(before.model_dump() if before else None, ensure_ascii=False),
                    json.dumps(after.model_dump() if after else None, ensure_ascii=False),
                ),
            )
        return after

    def rule_changes(self, rule: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """The change log, newest first, optionally for one rule."""
        sql = 'SELECT id, rule, at, reviewer, reason, action, "before", "after" FROM rule_changes'
        args: list[Any] = []
        if rule is not None:
            sql += " WHERE rule = ?"
            args.append(rule)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [
            {
                "id": r["id"],
                "rule": r["rule"],
                "at": r["at"],
                "reviewer": r["reviewer"],
                "reason": r["reason"],
                "action": r["action"],
                "before": json.loads(r["before"]),
                "after": json.loads(r["after"]),
            }
            for r in rows
        ]


def _override_from_row(r: sqlite3.Row) -> RuleOverride:
    return RuleOverride(
        enabled=bool(r["enabled"]),
        severity=r["severity"],
        name=r["name"],
        description=r["description"],
        note=r["note"],
        updated_by=r["updated_by"],
        updated_at=r["updated_at"],
        reason=r["reason"],
        revision=r["revision"],
    )


def _job_from_row(r: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=r["id"],
        case_id=r["case_id"],
        kind=r["kind"],
        state=r["state"],
        created_at=datetime.fromisoformat(r["created_at"]),
        started_at=datetime.fromisoformat(r["started_at"]) if r["started_at"] else None,
        finished_at=datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None,
        error=r["error"],
        stages=[StageRecord.model_validate(s) for s in json.loads(r["stages"] or "[]")],
    )
