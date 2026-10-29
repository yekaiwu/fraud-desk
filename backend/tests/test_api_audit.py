"""Audit log: append-only, pseudonymised, scrubbed (review-workflow tests 22 to 24, section 4.7, 4.8)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path

import pytest

import app.audit as audit_module
from app.audit import (
    AuditLog,
    case_ref_hash,
    install_log_filter,
    redact_flag_id,
    scrub,
    uninstall_log_filter,
)
from app.core.config import Settings
from app.core.db import Database
from tests.test_api_support import Env, make_env, mk_flag, review_body

NAME = "Tan Wei Ming"
NRIC = "S1234567D"
DISC_FLAG = mk_flag("x_directors", "high", doc="d9", locator="-", disc="tan_wei_ming")
LOW = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def test_audit_source_contains_only_the_permitted_sql() -> None:
    source = Path(audit_module.__file__).read_text(encoding="utf-8")
    updates = re.findall(r"UPDATE\s+audit_log[^\n`\"]*", source, flags=re.IGNORECASE)
    assert updates, "pseudonymise_case must exist"
    assert all("SET case_ref = ? WHERE case_ref = ?" in u for u in updates), updates
    assert not re.search(r"DELETE\s+FROM\s+audit_log", source, flags=re.IGNORECASE)
    assert not re.search(r"DROP\s+(TABLE|TRIGGER)", source, flags=re.IGNORECASE)


def test_database_triggers_abort_update_and_delete_of_audit_rows(env: Env) -> None:
    case = env.seed(flags=[])
    env.review(case.id, **review_body("verdict", "approve"))
    row_id = env.audit(case.id)[0]["id"]
    with env.db.conn() as c, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM audit_log WHERE id = ?", (row_id,))
    with env.db.conn() as c:
        for column in ("actor", "action", "ts", "detail"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                c.execute(f"UPDATE audit_log SET {column} = 'tampered' WHERE id = ?", (row_id,))
    with env.db.conn() as c, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM audit_log")
    assert len(env.audit(case.id)) == 1


def test_only_case_ref_may_change_through_the_api_of_the_module(env: Env) -> None:
    case = env.seed(flags=[])
    env.review(case.id, **review_body("verdict", "approve"))
    log = AuditLog(env.db, env.settings)
    assert log.pseudonymise_case(case.id) == 1
    assert log.pseudonymise_case(case.id) == 0
    rows = env.audit_rows()
    assert rows[0]["case_ref"] == case_ref_hash(case.id)
    assert [r["action"] for r in log.for_case(case.id)] == ["review.verdict"]  # still found by id


def test_audit_endpoint_lists_rows_oldest_first_and_404s_for_unknown(env: Env) -> None:
    case = env.seed(flags=[LOW])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id]))
    env.review(case.id, **review_body("verdict", "approve"))
    entries = env.client.get(f"/cases/{case.id}/audit").json()["entries"]
    assert [e["action"] for e in entries] == ["review.flag_decision", "review.verdict"]
    assert [e["id"] for e in entries] == sorted(e["id"] for e in entries)
    assert set(entries[0]) == {"id", "ts", "actor", "action", "detail"}
    assert entries[0]["actor"] == "a.tan"
    assert env.client.get("/cases/c_unknown/audit").status_code == 404


def test_pseudonymisation_keeps_names_nric_and_reason_text_out_of_audit_logs_and_pack(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    reason = f"{NRIC} is the director"
    case = env.seed(
        use_case="kyc",
        flags=[DISC_FLAG, LOW],
        applicant_name=NAME,
        applicant_uen="201912345R",
        reference="ACME-2026-014",
    )
    caplog.set_level(logging.DEBUG)
    env.review(
        case.id, **review_body("flag_decision", "dismiss", flag_ids=[DISC_FLAG.id], reason=reason)
    )
    env.review(
        case.id,
        **review_body(
            "second_approval", "approve", target_entry_id="r1", reviewer="b.lim", reason=reason
        ),
    )
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[LOW.id], reason=reason))
    env.review(case.id, **review_body("verdict", "refer_edd", reason=reason))
    env.review(case.id, **review_body("verdict", "approve", reason=reason))  # rejected: closed
    env.client.get(f"/cases/{case.id}/pack")
    logging.getLogger("gldb.test").warning("applicant %s holds %s", NAME, NRIC)

    pack = env.client.get(f"/cases/{case.id}/pack").json()
    blobs = [json.dumps(row) for row in env.audit_rows()]
    blobs.append(json.dumps(pack["audit"]))
    for blob in blobs:
        for forbidden in (NAME, "Tan Wei", NRIC, reason, "tan_wei_ming", "201912345R"):
            assert forbidden not in blob, (forbidden, blob)
    assert NRIC not in caplog.text and "[id]" in caplog.text

    rows = {r["action"]: r for r in pack["audit"]}
    flag_row = next(r for r in pack["audit"] if r["action"] == "review.flag_decision")
    ref = flag_row["detail"]["flag_refs"][0]
    assert re.fullmatch(r"x_directors\.d9\.-~#[0-9a-f]{8}", ref)
    assert ref.endswith(hashlib.sha256(b"tan_wei_ming").hexdigest()[:8])
    for action in ("review.flag_decision", "review.second_approval", "review.verdict"):
        detail = rows[action]["detail"]
        assert detail["reason_sha256"] and detail["reason_len"] == len(reason)
        assert "reason" not in detail
    assert flag_row["detail"]["reason_sha256"] == hashlib.sha256(reason.encode()).hexdigest()
    # the clear-text reason does live in the review log (case data, removed with the case)
    assert pack["review"][0]["reason"] == reason


def test_with_pseudonymisation_off_the_scrubbed_reason_is_audited(
    settings: Settings, db: Database
) -> None:
    env = make_env(settings.model_copy(update={"pseudonymise_logs": False}), db)
    case = env.seed(flags=[])
    env.review(case.id, **review_body("verdict", "approve", reason=f"{NRIC} verified by branch"))
    detail = env.audit(case.id)[0]["detail"]
    assert detail["reason"] == "[id] verified by branch"  # scrubbed, never the NRIC itself
    assert NRIC not in json.dumps(env.audit_rows())


# -- helpers -----------------------------------------------------------------------------------


def test_case_ref_hash_is_stable_and_prefixed() -> None:
    expected = "h_" + hashlib.sha256(b"gldb-audit|c_abc").hexdigest()[:16]
    assert case_ref_hash("c_abc") == expected
    assert case_ref_hash("c_abc") != case_ref_hash("c_abd")


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("duplicate upload S1234567D", "duplicate upload [id]"),
        ("fin f1234567n and G7654321K", "fin [id] and [id]"),
        ("uen 201912345R and T08LL1234D", "uen [id] and [id]"),
        ("mail tan.wm@example.com now", "mail [id] now"),
        ("  lots   of\n space ", "lots of space"),
        ("", ""),
    ],
)
def test_scrub_replaces_identifiers_and_collapses_whitespace(raw: str, clean: str) -> None:
    assert scrub(raw) == clean


def test_scrub_truncates_with_ellipsis() -> None:
    out = scrub("word " * 100, max_len=20)
    assert len(out) == 20 and out.endswith("...")


def test_redact_flag_id() -> None:
    assert (
        redact_flag_id("bs_running_balance.d3.transactions:17")
        == "bs_running_balance.d3.transactions:17"
    )
    plain = redact_flag_id("x_directors.d9.-~tan_wei_ming")
    numbered = redact_flag_id("x_directors.d9.-~tan_wei_ming#2")
    assert plain == numbered and plain.startswith("x_directors.d9.-~#") and "tan" not in plain


def test_write_redacts_identifier_patterns_in_any_detail_string(env: Env) -> None:
    log = AuditLog(env.db, env.settings, clock=env.clock)
    log.write(
        actor="a.tan",
        action="test.x",
        case_id=None,
        detail={"note": f"saw {NRIC}", "n": [f"{NRIC}"]},
    )
    row = env.audit_rows()[-1]
    assert NRIC not in row["detail"] and row["case_ref"] is None


def test_log_filter_redacts_gldb_loggers_only_and_can_be_removed(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    uninstall_log_filter()
    try:
        install_log_filter(settings)
        install_log_filter(settings)  # idempotent
        caplog.set_level(logging.INFO)
        logging.getLogger("gldb.api").info("case for %s", NRIC)
        logging.getLogger("other.library").info("case for %s", NRIC)
        assert "case for [id]" in caplog.text
        assert f"case for {NRIC}" in caplog.text  # third-party loggers are left alone
        install_log_filter(settings.model_copy(update={"pseudonymise_logs": False}))
        caplog.clear()
        logging.getLogger("gldb.api").info("case for %s", NRIC)
        assert NRIC in caplog.text
    finally:
        install_log_filter(settings)  # leave the application state as the app would


def test_app_startup_installs_the_log_filter(settings: Settings, db: Database) -> None:
    uninstall_log_filter()
    make_env(settings, db)
    assert getattr(logging.getLogRecordFactory(), "_gldb_pseudonymise", False) is True
