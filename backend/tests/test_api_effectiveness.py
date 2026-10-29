"""Rule effectiveness (review-workflow test 32, section 4.14, api.md section 9)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.checks.registry import all_checks
from app.core.config import Settings
from app.core.db import Database
from app.core.models import CheckRun
from app.effectiveness import NOT_REGISTERED, compute_effectiveness
from tests.test_api_support import (
    FIXED_NOW,
    Env,
    FakeClock,
    make_env,
    mk_flag,
    review_body,
    write_fixture,
)


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db, FakeClock())


def at(env: Env, seconds: int) -> None:
    env.clock.now = FIXED_NOW + timedelta(seconds=seconds)


def rule_row(body: dict[str, Any], rule: str) -> dict[str, Any]:
    return next(r for r in body["rules"] if r["rule"] == rule)


def build_scenario(env: Env) -> dict[str, Any]:
    """Three cases with known counts; the analysis stamp is FIXED_NOW for all of them."""
    a1 = mk_flag("bs_running_balance", "high", doc="d1", locator="transactions:1")
    a2 = mk_flag("bs_dates", "low", doc="d1", locator="transactions:2")
    b1 = mk_flag("bs_running_balance", "high", doc="d2", locator="transactions:1")
    b2 = mk_flag("bs_running_balance", "high", doc="d2", locator="transactions:2")
    b3 = mk_flag("bs_running_balance", "high", doc="d2", locator="transactions:3")
    c1 = mk_flag("bs_running_balance", "high", doc="d3", locator="transactions:1")
    c2 = mk_flag("inv_dates", "medium", doc="d4", locator="invoice_date")
    gone = mk_flag("retired_rule", "low", doc="d1", locator="-")
    runs = [
        CheckRun(rule="bs_running_balance", stage="rules", status="ran"),
        CheckRun(rule="bs_dates", stage="rules", status="ran"),
        CheckRun(rule="inv_dates", stage="rules", status="skipped", reason="no invoice"),
    ]
    a = env.seed(flags=[a1, a2, gone], check_runs=runs)
    b = env.seed(flags=[b1, b2, b3], check_runs=runs[:1])
    c = env.seed(
        use_case="kyc",
        flags=[c1, c2],
        created_at=FIXED_NOW + timedelta(days=3),
        check_runs=[
            CheckRun(rule="bs_running_balance", stage="rules", status="error", reason="boom")
        ],
    )
    at(env, 100)
    env.review(a.id, **review_body("flag_decision", "confirm", flag_ids=[a1.id]))
    at(env, 150)
    env.review(a.id, **review_body("flag_decision", "dismiss", flag_ids=[a2.id]))
    at(env, 160)
    env.review(a.id, **review_body("flag_decision", "dismiss", flag_ids=[gone.id]))
    at(env, 600)
    assert env.review(a.id, **review_body("verdict", "approve")).status_code == 201
    at(env, 200)
    env.review(b.id, **review_body("flag_decision", "confirm", flag_ids=[b1.id]))
    at(env, 300)
    env.review(b.id, **review_body("flag_decision", "confirm", flag_ids=[b2.id]))
    at(env, 400)
    env.review(
        b.id, **review_body("flag_decision", "dismiss", flag_ids=[b3.id])
    )  # needs a second approver
    at(env, 700)
    r = env.review(
        b.id, **review_body("second_approval", "approve", target_entry_id="r3", reviewer="b.lim")
    )
    assert r.status_code == 201
    at(env, 1000)
    assert env.review(b.id, **review_body("verdict", "approve")).status_code == 201
    return {"a": a, "b": b, "c": c}


def test_known_counts_rates_and_medians(env: Env) -> None:
    build_scenario(env)
    r = env.client.get("/rules/effectiveness")
    assert r.status_code == 200
    body = r.json()
    assert body["generated_for"] == {"use_case": None, "since": None}
    assert body["cases_total"] == 3 and body["cases_decided"] == 2
    assert body["median_seconds_to_verdict"] == 800  # (600 + 1000) / 2

    bs = rule_row(body, "bs_running_balance")
    assert bs["stage"] == "rules" and bs["default_severity"] == "high" and bs["description"]
    assert (bs["cases_fired"], bs["times_fired"]) == (3, 5)
    assert (bs["confirmed"], bs["dismissed"], bs["pending"], bs["open"]) == (3, 1, 0, 1)
    assert bs["dismissal_rate"] == 0.25  # 1 of 4 decided
    assert (
        bs["median_seconds_to_decide"] == 250
    )  # 100, 200, 300 and 700 (when the countersign made it effective)
    assert (bs["cases_ran"], bs["cases_skipped"]) == (2, 1)

    dates = rule_row(body, "bs_dates")
    assert (dates["times_fired"], dates["confirmed"], dates["dismissed"]) == (1, 0, 1)
    assert dates["dismissal_rate"] == 1.0 and dates["median_seconds_to_decide"] == 150
    inv = rule_row(body, "inv_dates")
    assert (
        inv["times_fired"],
        inv["open"],
        inv["dismissal_rate"],
        inv["median_seconds_to_decide"],
    ) == (
        1,
        1,
        None,
        None,
    )
    assert (inv["cases_ran"], inv["cases_skipped"]) == (0, 1)
    write_fixture("effectiveness", body)


def test_unfired_registered_rules_appear_with_zeros_and_order_is_stable(env: Env) -> None:
    build_scenario(env)
    body = env.client.get("/rules/effectiveness").json()
    registered = {c.rule for c in all_checks()}
    rules = {r["rule"] for r in body["rules"]}
    assert registered <= rules
    quiet = rule_row(body, "pdf_modified")
    assert (
        quiet["times_fired"] == 0 and quiet["cases_fired"] == 0 and quiet["dismissal_rate"] is None
    )
    assert quiet["median_seconds_to_decide"] is None and quiet["cases_ran"] == 0
    keys = [(-r["times_fired"], r["rule"]) for r in body["rules"]]
    assert keys == sorted(keys)
    assert body["rules"][0]["rule"] == "bs_running_balance"
    assert set(body["rules"][0]) == {
        "rule",
        "stage",
        "default_severity",
        "description",
        "cases_fired",
        "times_fired",
        "confirmed",
        "dismissed",
        "pending",
        "open",
        "dismissal_rate",
        "median_seconds_to_decide",
        "cases_ran",
        "cases_skipped",
    }


def test_a_fired_rule_that_is_no_longer_registered_is_still_listed(env: Env) -> None:
    build_scenario(env)
    gone = rule_row(env.client.get("/rules/effectiveness").json(), "retired_rule")
    assert gone["description"] == NOT_REGISTERED and gone["times_fired"] == 1
    assert gone["dismissed"] == 1 and gone["default_severity"] == "low"


def test_pending_dismissals_count_as_pending_not_dismissed(env: Env) -> None:
    flag = mk_flag("bs_running_balance", "high")
    case = env.seed(flags=[flag])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[flag.id]))
    body = env.client.get("/rules/effectiveness").json()
    row = rule_row(body, "bs_running_balance")
    assert (row["pending"], row["dismissed"], row["confirmed"], row["open"]) == (1, 0, 0, 0)
    assert row["dismissal_rate"] is None
    assert body["cases_decided"] == 0 and body["median_seconds_to_verdict"] is None


def test_declined_dismissal_counts_as_open_again(env: Env) -> None:
    flag = mk_flag("bs_running_balance", "high")
    case = env.seed(flags=[flag])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[flag.id]))
    env.review(
        case.id, **review_body("second_approval", "decline", target_entry_id="r1", reviewer="b.lim")
    )
    row = rule_row(env.client.get("/rules/effectiveness").json(), "bs_running_balance")
    assert (row["open"], row["pending"], row["dismissed"]) == (1, 0, 0)


def test_dispositions_come_from_the_latest_version_only(env: Env) -> None:
    flag = mk_flag("bs_running_balance", "high")
    case = env.seed(flags=[flag])
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[flag.id]))
    loaded = env.db.load_case(case.id)
    loaded.version = 2  # as if a rerun produced version 2 with the same flag, not yet reviewed
    env.db.save_case(loaded)
    row = rule_row(env.client.get("/rules/effectiveness").json(), "bs_running_balance")
    assert (row["confirmed"], row["open"]) == (0, 1)


def test_filters_by_use_case_and_since(env: Env) -> None:
    build_scenario(env)
    kyc = env.client.get("/rules/effectiveness", params={"use_case": "kyc"}).json()
    assert kyc["generated_for"]["use_case"] == "kyc"
    assert kyc["cases_total"] == 1 and kyc["cases_decided"] == 0
    assert rule_row(kyc, "bs_running_balance")["times_fired"] == 1
    loan = env.client.get("/rules/effectiveness", params={"use_case": "loan"}).json()
    assert loan["cases_total"] == 2 and rule_row(loan, "bs_running_balance")["times_fired"] == 4
    since = env.client.get("/rules/effectiveness", params={"since": "2026-10-29"}).json()
    assert since["generated_for"]["since"] == "2026-10-29" and since["cases_total"] == 1
    exact = env.client.get("/rules/effectiveness", params={"since": "2026-10-27"}).json()
    assert exact["cases_total"] == 3  # created_at is compared by date, inclusive
    assert env.client.get("/rules/effectiveness", params={"since": "yesterday"}).status_code == 422
    assert (
        env.client.get("/rules/effectiveness", params={"use_case": "mortgage"}).status_code == 422
    )


def test_empty_database_gives_zero_rows_for_every_rule(env: Env) -> None:
    body = env.client.get("/rules/effectiveness").json()
    assert body["cases_total"] == 0 and body["median_seconds_to_verdict"] is None
    assert len(body["rules"]) == len(all_checks())
    assert all(r["times_fired"] == 0 for r in body["rules"])


def test_compute_function_matches_the_endpoint(env: Env) -> None:
    build_scenario(env)
    assert compute_effectiveness(env.db) == env.client.get("/rules/effectiveness").json()
