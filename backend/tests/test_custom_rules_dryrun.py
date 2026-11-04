"""Dry run of a custom rule on stored cases (app/custom_rules/dryrun.py)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.db import Database
from app.core.models import Case, Document
from app.core.testing import FIXED_NOW, FIXED_TODAY, make_doc
from app.custom_rules import dryrun
from app.custom_rules.dryrun import dry_run
from tests.custom_rules_helpers import (
    DEBT,
    EQUITY,
    invoice,
    scan_file,
    spec_of,
    statement_doc,
    t1,
    t3,
    t4,
)


def store_case(
    db: Database,
    case_id: str,
    docs: list[Document],
    *,
    minutes: int = 0,
    status: str = "open",
    scan: bool = False,
) -> Case:
    stamp = FIXED_NOW + timedelta(minutes=minutes)
    case = Case(
        id=case_id,
        use_case="loan",
        status=status,  # type: ignore[arg-type]
        today=FIXED_TODAY,
        created_at=stamp,
        updated_at=stamp,
        documents=docs,
        files=[scan_file("f1")] if scan else [],
    )
    db.create_case(case)
    return case


BIG = spec_of(t1("invoice.total", "gt", "100000"))


@pytest.fixture
def db(mem_db: Database) -> Database:
    return mem_db


def test_counts_over_stored_cases(db: Database) -> None:
    store_case(db, "c_a", [invoice(total="500000.00")], minutes=1)
    store_case(db, "c_b", [invoice(total="5.00")], minutes=2)
    store_case(db, "c_c", [invoice(total=None)], minutes=3)  # invoice there, but no total
    store_case(db, "c_d", [make_doc("receipt", id="d1")], minutes=4)  # no invoice at all
    store_case(db, "c_e", [invoice(total="7.00")], minutes=5)
    got = dry_run(db, BIG, limit=50)
    assert (got.cases_considered, got.evaluated, got.fired, got.skipped_missing) == (5, 4, 1, 1)
    assert got.fire_rate == 0.25
    assert got.examples == ["c_a"] and got.spec_hash == BIG.spec_hash()
    assert got.warnings == [] and got.needs_confirmation is False


def test_scanned_documents_are_counted_separately(db: Database) -> None:
    store_case(db, "c_a", [invoice(total="500000.00")], minutes=1, scan=True)
    store_case(db, "c_b", [invoice(total="500000.00")], minutes=2)
    store_case(db, "c_c", [invoice(total="5.00")], minutes=3, scan=True)
    got = dry_run(db, BIG, limit=50)
    assert (got.evaluated, got.fired, got.scanned_evaluated, got.scanned_fired) == (3, 2, 2, 1)


def test_no_stored_case_with_the_document_warns(db: Database) -> None:
    got = dry_run(db, BIG, limit=10)
    assert (got.evaluated, got.fired, got.fire_rate) == (0, 0, 0.0)
    assert [w.code for w in got.warnings] == ["no_cases"]
    assert got.warnings[0].message == "No stored case has a invoice to test this on."
    assert got.needs_confirmation is True
    store_case(db, "c_a", [make_doc("receipt", id="d1")])
    assert [w.code for w in dry_run(db, BIG, limit=10).warnings] == ["no_cases"]


def test_a_rule_that_fires_on_none_warns(db: Database) -> None:
    store_case(db, "c_a", [invoice(total="5.00")])
    got = dry_run(db, BIG, limit=10)
    assert [w.code for w in got.warnings] == ["fires_on_none"] and got.needs_confirmation


def test_a_rule_that_fires_too_often_warns(db: Database) -> None:
    for i in range(10):
        store_case(db, f"c_{i}", [invoice(total="500000.00" if i < 4 else "5.00")], minutes=i)
    got = dry_run(db, BIG, limit=50)
    assert (got.evaluated, got.fired, got.fire_rate) == (10, 4, 0.4)
    assert [w.code for w in got.warnings] == ["fires_too_often"]
    assert got.warnings[0].message == (
        "It fired on 4 of 10 cases (40%); a rule that fires that often is noise."
    )


def test_exactly_thirty_percent_is_fine(db: Database) -> None:
    for i in range(10):
        store_case(db, f"c_{i}", [invoice(total="500000.00" if i < 3 else "5.00")], minutes=i)
    got = dry_run(db, BIG, limit=50)
    assert got.fire_rate == 0.3 and got.warnings == [] and got.needs_confirmation is False


def test_only_the_newest_cases_up_to_the_limit_are_read(db: Database) -> None:
    for i in range(6):
        store_case(db, f"c_{i}", [invoice(total="500000.00")], minutes=i)
    got = dry_run(db, BIG, limit=3)
    assert got.cases_considered == 3 and got.fired == 3
    assert got.examples == ["c_5", "c_4", "c_3"]  # newest first
    assert len(dry_run(db, BIG, limit=100).examples) == 5  # at most five examples


def test_cases_still_being_analysed_are_skipped(db: Database) -> None:
    store_case(db, "c_a", [invoice(total="500000.00")], status="processing")
    store_case(db, "c_b", [invoice(total="500000.00")], status="open", minutes=1)
    got = dry_run(db, BIG, limit=10)
    assert got.cases_considered == 1 and got.examples == ["c_b"]


def test_a_case_that_cannot_be_loaded_is_left_out_of_every_count(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_case(db, "c_a", [invoice(total="500000.00")], minutes=1)
    store_case(db, "c_b", [invoice(total="500000.00")], minutes=2)
    real = db.load_case

    def flaky(case_id: str, version: int | None = None) -> Case:
        if case_id == "c_b":
            raise ValueError("corrupt body")
        return real(case_id, version)

    monkeypatch.setattr(db, "load_case", flaky)
    got = dry_run(db, BIG, limit=10)
    assert (got.cases_considered, got.evaluated, got.examples) == (1, 1, ["c_a"])


def test_nothing_is_written_to_any_case(db: Database) -> None:
    store_case(db, "c_a", [invoice(total="500000.00")])
    with db.conn() as c:
        before = c.execute("SELECT body, version, updated_at FROM cases").fetchall()
        before_rows = [tuple(r) for r in before]
    dry_run(db, BIG, limit=10)
    with db.conn() as c:
        assert [
            tuple(r) for r in c.execute("SELECT body, version, updated_at FROM cases").fetchall()
        ] == before_rows
        assert c.execute("SELECT COUNT(*) FROM custom_rule_changes").fetchone()[0] == 0


def test_works_for_each_template(db: Database) -> None:
    store_case(
        db,
        "c_a",
        [statement_doc(borrowings_current="900.00", total_equity_sub="100.00")],
        minutes=1,
    )
    store_case(db, "c_b", [make_doc("receipt", id="d1")], minutes=2)
    ratio = dry_run(db, spec_of(t4(DEBT, EQUITY)), limit=10)
    assert (ratio.evaluated, ratio.fired) == (1, 1)
    missing = dry_run(db, spec_of(t3("receipt.receipt_number")), limit=10)
    assert (missing.evaluated, missing.fired, missing.skipped_missing) == (1, 1, 0)


def test_the_dry_run_rule_id_is_a_reserved_custom_id() -> None:
    from app.custom_rules.spec import RULE_ID_RE

    assert RULE_ID_RE.match(dryrun.DRY_RUN_RULE_ID)
