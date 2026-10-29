import re
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.db import CaseFilters, CaseNotFound, Database, new_case_id
from app.core.models import (
    Applicant,
    Case,
    Coverage,
    Finding,
    ReviewEntry,
    Score,
    StageRecord,
    utcnow,
)
from app.core.testing import FIXED_NOW, FIXED_TODAY


def new_case(**kw) -> Case:  # noqa: ANN003
    base = dict(
        id=new_case_id(),
        use_case="loan",
        today=FIXED_TODAY,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    base.update(kw)
    return Case(**base)


def finding(n: int, severity: str) -> Finding:
    return Finding(
        id=f"fnd_{n}",
        group_key=f"flag:{n}",
        title="t",
        severity=severity,
        flag_ids=[f"f{n}"],  # type: ignore[arg-type]
        document_ids=[],
        rules=["r"],
        weight=0,
        summary="s",
    )


def test_create_save_load_round_trip(db: Database) -> None:
    case = new_case(reference="REF-1")
    db.create_case(case)
    case.status = "open"
    case.findings = [finding(1, "high"), finding(2, "info"), finding(3, "medium")]
    case.score = Score(value=45, band="medium", partial=True)
    case.coverage = Coverage(applicable=10, ran=7, ratio=0.7)
    case.top_finding = "top"
    db.save_case(case)
    loaded = db.load_case(case.id)
    assert loaded.reference == "REF-1" and loaded.status == "open"
    assert loaded.score == case.score and loaded.coverage == case.coverage
    with db.conn() as c:
        row = c.execute("SELECT * FROM cases WHERE id = ?", (case.id,)).fetchone()
    # denormalised columns
    assert row["findings_count"] == 2  # info excluded
    assert row["high_count"] == 1
    assert row["partial"] == 1  # score.partial counts
    assert row["coverage_ratio"] == 0.7
    assert (row["score"], row["band"], row["top_finding"]) == (45, "medium", "top")


def test_partial_column_from_case_flag(db: Database) -> None:
    case = new_case(partial=True)
    db.create_case(case)
    with db.conn() as c:
        assert c.execute("SELECT partial FROM cases").fetchone()[0] == 1


def test_load_missing_raises(db: Database) -> None:
    with pytest.raises(CaseNotFound):
        db.load_case("c_nope")
    with pytest.raises(CaseNotFound):
        db.save_case(new_case())
    assert not db.case_exists("c_nope")


def test_version_snapshot_and_version_filtered_review(db: Database) -> None:
    case = new_case()
    db.create_case(case)
    case.top_finding = "v1"
    db.save_case(case, snapshot_version=True)
    case.version = 2
    case.top_finding = "v2"
    db.save_case(case)
    entries = [
        ReviewEntry(
            id="r1",
            kind="verdict",
            reviewer="a.tan",
            decision="approve",
            reason="fine here",
            case_version=1,
            created_at=FIXED_NOW,
        ),
        ReviewEntry(
            id="r2",
            kind="verdict",
            reviewer="a.tan",
            decision="request_documents",
            reason="need more",
            case_version=2,
            created_at=FIXED_NOW,
        ),
    ]
    with db.conn() as c:
        for e in entries:
            c.execute(
                "INSERT INTO review_entries (id, case_id, case_version, created_at, body) "
                "VALUES (?,?,?,?,?)",
                (
                    f"{case.id}/{e.id}",
                    case.id,
                    e.case_version,
                    "2026-10-27T09:00:00.000000Z",
                    e.model_dump_json(),
                ),
            )
    assert db.load_case(case.id).top_finding == "v2"
    assert [r.id for r in db.load_case(case.id).review] == ["r2"]
    old = db.load_case(case.id, version=1)
    assert old.top_finding == "v1" and [r.id for r in old.review] == ["r1"]
    with pytest.raises(CaseNotFound):
        db.load_case(case.id, version=9)
    assert [v["version"] for v in db.list_versions(case.id)] == [1, 2]


def test_save_touches_updated_at(db: Database) -> None:
    case = new_case()
    db.create_case(case)
    before = case.updated_at
    db.save_case(case)
    assert case.updated_at > before


def test_threads_can_write_concurrently(db: Database) -> None:
    ids = [new_case_id() for _ in range(8)]
    errors: list[BaseException] = []

    def work(cid: str) -> None:
        try:
            case = new_case(id=cid)
            db.create_case(case)
            for i in range(5):
                case.top_finding = f"t{i}"
                db.save_case(case)
            assert db.load_case(cid).top_finding == "t4"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert db.list_cases(None, "priority", 1, 100)[1] == 8


def test_memory_database_is_shared_across_threads(mem_db: Database) -> None:
    case = new_case()
    mem_db.create_case(case)
    seen: list[str] = []
    t = threading.Thread(target=lambda: seen.append(mem_db.load_case(case.id).id))
    t.start()
    t.join()
    assert seen == [case.id]


def test_wal_and_foreign_keys(db: Database) -> None:
    with db.conn() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.row_factory is sqlite3.Row


def test_init_schema_is_idempotent(db: Database) -> None:
    db.init_schema()
    db.init_schema()
    with db.conn() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "cases",
        "case_versions",
        "jobs",
        "review_entries",
        "audit_log",
        "fingerprints",
        "llm_cache",
    } <= names


def test_no_string_built_sql_in_core() -> None:
    """Only parameterised SQL: no f-string, % or .format() builds a query."""
    core = Path(__file__).resolve().parents[1] / "app" / "core"
    sql_word = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b")
    offenders = []
    for path in core.glob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"""')):
                continue
            if sql_word.search(line) and (
                re.search(r"\bf[\"']", line)
                or ".format(" in line
                or re.search(r"[\"']\s*%\s", line)
            ):
                offenders.append(f"{path.name}:{n}: {stripped}")
    assert offenders == []


def test_jobs_lifecycle_and_interrupted_recovery(db: Database) -> None:
    case = new_case()
    db.create_case(case)
    job = db.create_job(case.id, "analyse")
    assert job.id.startswith("j_") and job.state == "queued"
    db.update_job(
        job.id,
        state="running",
        started_at=utcnow(),
        stages=[StageRecord(stage="ingest", status="ok", seconds=0.5)],
    )
    got = db.get_job(job.id)
    assert got and got.state == "running" and got.stages[0].stage == "ingest"
    assert db.latest_job(case.id).id == job.id  # type: ignore[union-attr]
    done = db.create_job(case.id, "rerun")
    db.update_job(done.id, state="succeeded", finished_at=utcnow())

    recovered = db.recover_interrupted_jobs()
    assert recovered == [job.id]
    after = db.get_job(job.id)
    assert after and after.state == "failed" and after.error == "interrupted"
    assert db.load_case(case.id).status == "error"
    assert db.get_job(done.id).state == "succeeded"  # type: ignore[union-attr]
    assert db.get_job("j_missing") is None
    assert db.recover_interrupted_jobs() == []


# ---------------------------------------------------------------- list_cases / priority sort


def add(
    db: Database,
    label: str,
    *,
    status: str,
    score: int | None,
    cov: float | None,
    minutes: int = 0,
    **kw,
) -> Case:  # noqa: ANN003
    case = new_case(
        reference=label,
        status=status,
        created_at=FIXED_NOW + timedelta(minutes=minutes),
        updated_at=FIXED_NOW,
        **kw,
    )
    if score is not None:
        case.score = Score(
            value=score, band="high" if score >= 60 else "medium" if score >= 30 else "low"
        )
    if cov is not None:
        case.coverage = Coverage(applicable=10, ran=int(cov * 10), ratio=cov)
    db.create_case(case)
    return case


def test_priority_sort_six_row_fixture(db: Database) -> None:
    add(db, "closed99", status="closed", score=99, cov=1.0)
    add(db, "open40", status="open", score=40, cov=0.9)
    add(db, "processing", status="processing", score=None, cov=None)
    add(db, "open80", status="open", score=80, cov=0.9)
    add(db, "awaiting90", status="awaiting_documents", score=90, cov=0.9)
    add(db, "open80-lowcov", status="open", score=80, cov=0.5)
    rows, total = db.list_cases(None, "priority", 1, 25)
    assert total == 6
    assert [r["reference"] for r in rows] == [
        "open80-lowcov",
        "open80",
        "open40",
        "processing",
        "awaiting90",
        "closed99",
    ]


def test_priority_partial_case_is_not_buried_under_low_score(db: Database) -> None:
    add(db, "low-flag", status="open", score=5, cov=1.0)
    add(db, "unreadable", status="open", score=0, cov=0.14, partial=True)
    add(db, "clean", status="open", score=0, cov=1.0)
    add(db, "high", status="open", score=80, cov=0.9)
    add(db, "unscored-partial", status="open", score=None, cov=None, partial=True)
    rows, _ = db.list_cases(None, "priority", 1, 25)
    assert [r["reference"] for r in rows] == [
        "high",
        "unreadable",
        "unscored-partial",
        "low-flag",
        "clean",
    ]


def test_priority_ties_oldest_first_and_error_last(db: Database) -> None:
    add(db, "new", status="open", score=50, cov=0.8, minutes=10)
    add(db, "old", status="open", score=50, cov=0.8, minutes=0)
    add(db, "err", status="error", score=None, cov=None)
    rows, _ = db.list_cases(None, "priority", 1, 25)
    assert [r["reference"] for r in rows] == ["old", "new", "err"]


def test_filters_pagination_and_other_sorts(db: Database) -> None:
    add(
        db,
        "AAA_100%",
        status="open",
        score=10,
        cov=0.5,
        minutes=1,
        applicant=Applicant(name="Zed Pte Ltd"),
    )
    add(db, "bbb", status="open", score=70, cov=0.9, minutes=2, use_case="kyc")
    add(db, "ccc", status="closed", score=20, cov=0.7, minutes=3)
    add(db, "ddd", status="open", score=None, cov=None, minutes=4, partial=True)

    def refs(**kw) -> list[str]:  # noqa: ANN003
        rows, _ = db.list_cases(kw.pop("filters", None), **kw)
        return [r["reference"] for r in rows]

    assert refs(filters=CaseFilters(status=["closed"]), sort="priority") == ["ccc"]
    assert refs(filters=CaseFilters(use_case=["kyc"]), sort="priority") == ["bbb"]
    assert refs(filters=CaseFilters(min_score=20), sort="score") == ["bbb", "ccc"]
    assert refs(filters=CaseFilters(partial=True), sort="priority") == ["ddd"]
    assert refs(filters=CaseFilters(q="zed"), sort="priority") == ["AAA_100%"]
    assert refs(filters=CaseFilters(q="100%"), sort="priority") == ["AAA_100%"]  # % is escaped
    assert refs(filters=CaseFilters(q="%"), sort="priority") == ["AAA_100%"]
    assert refs(sort="score") == ["bbb", "ccc", "AAA_100%", "ddd"]  # NULL score last
    assert refs(sort="score", order="asc") == ["AAA_100%", "ccc", "bbb", "ddd"]
    assert refs(sort="created") == ["ddd", "ccc", "bbb", "AAA_100%"]
    assert refs(sort="age") == ["AAA_100%", "bbb", "ccc", "ddd"]  # oldest first
    assert refs(sort="coverage") == ["AAA_100%", "ccc", "bbb", "ddd"]  # least coverage first
    rows, total = db.list_cases(None, "created", page=2, page_size=3)
    assert total == 4 and [r["reference"] for r in rows] == ["AAA_100%"]
    with pytest.raises(ValueError):
        db.list_cases(None, "bogus")


# ---------------------------------------------------------------- audit triggers


def test_audit_log_is_append_only(db: Database) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO audit_log (ts, actor, action, case_ref, detail) VALUES (?,?,?,?,?)",
            ("2026-10-27T09:00:00Z", "a.tan", "review.verdict", "c_1", "{}"),
        )
    with pytest.raises(sqlite3.IntegrityError), db.conn() as c:
        c.execute("DELETE FROM audit_log")
    for column in ("ts", "actor", "action", "detail"):
        with pytest.raises(sqlite3.IntegrityError), db.conn() as c:
            c.execute("UPDATE audit_log SET " + column + " = 'x'")
    with pytest.raises(sqlite3.IntegrityError), db.conn() as c:
        c.execute("UPDATE audit_log SET id = 99")
    with db.conn() as c:  # pseudonymisation after delete: only case_ref may change
        c.execute("UPDATE audit_log SET case_ref = 'hash'")
        assert c.execute("SELECT case_ref FROM audit_log").fetchone()[0] == "hash"
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


# ---------------------------------------------------------------- delete_case


def test_delete_case_removes_everything_and_frees_files(db: Database) -> None:
    from app.core.cases import add_file

    keep = new_case()
    gone = new_case()
    shared = b"%PDF-1.4 shared content"
    only_gone = b"%PDF-1.4 only in the deleted case"
    for case in (keep, gone):
        db.create_case(case)
    add_file(keep, db.files, name="a.pdf", data=shared)
    ref_shared = add_file(gone, db.files, name="b.pdf", data=shared)
    ref_only = add_file(gone, db.files, name="c.pdf", data=only_gone)
    db.save_case(keep)
    db.save_case(gone, snapshot_version=True)
    job = db.create_job(gone.id)
    with db.conn() as c:
        for kind, fp in (("file_sha256", ref_only.sha256),):
            c.execute(
                "INSERT INTO fingerprints (kind, fingerprint, case_id, document_id, created_at) "
                "VALUES (?,?,?,?,?)",
                (kind, fp, gone.id, None, "2026-10-27T09:00:00.000000Z"),
            )
        for sha, name in ((ref_only.sha256, "gone"), (ref_shared.sha256, "shared")):
            c.execute(
                "INSERT INTO llm_cache (key, value, created_at, model, purpose) VALUES (?,?,?,?,?)",
                (
                    name,
                    '{"file_sha256": "' + sha + '"}',
                    "2026-10-27T09:00:00.000000Z",
                    "m",
                    "extract:x",
                ),
            )
        c.execute(
            "INSERT INTO llm_cache (key, value, created_at, model, purpose) VALUES (?,?,?,?,?)",
            (
                "classify",
                '{"file_sha256": null}',
                "2026-10-27T09:00:00.000000Z",
                "m",
                "classify:C1",
            ),
        )
        c.execute(
            "INSERT INTO audit_log (ts, actor, action, case_ref, detail) VALUES (?,?,?,?,?)",
            ("2026-10-27T09:00:00Z", "a.tan", "case.created", gone.id, "{}"),
        )

    result = db.delete_case(gone.id)
    assert result.files_removed == [ref_only.sha256]
    assert result.cache_rows_removed == 1
    assert result.versions == 1
    assert not db.case_exists(gone.id) and db.case_exists(keep.id)
    assert db.get_job(job.id) is None
    assert not db.files.exists(ref_only.sha256)
    assert db.files.exists(ref_shared.sha256)  # still referenced by the other case
    with db.conn() as c:
        keys = {r[0] for r in c.execute("SELECT key FROM llm_cache")}
        assert keys == {"shared", "classify"}
        assert c.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1  # kept
    with pytest.raises(CaseNotFound):
        db.delete_case(gone.id)
