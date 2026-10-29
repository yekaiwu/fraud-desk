"""Review controls follow "high now OR high under the code defaults" (scoring-and-queue.md 10a):
lowering a check's severity in the check catalogue reduces points only; it never relaxes a review
control. Raising a severity to high adds the controls."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.findings import is_high_flag
from tests.test_api_support import Env, make_env, mk_flag, review_body

BASE = mk_flag("bs_running_balance", "high", doc="d1", locator="transactions:3")
LOWERED = BASE.model_copy(update={"severity": "low", "code_severity": "high"})
PLAIN_LOW = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")
RAISED = PLAIN_LOW.model_copy(update={"severity": "high", "code_severity": "low"})


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def verdict(env: Env, case_id: str):  # noqa: ANN201
    return env.review(case_id, **review_body("verdict", "approve", reason="Looks fine to me."))


def test_is_high_flag() -> None:
    assert is_high_flag(BASE) and is_high_flag(LOWERED) and is_high_flag(RAISED)
    assert not is_high_flag(PLAIN_LOW)
    assert not is_high_flag(
        mk_flag("bs_dates", "medium").model_copy(update={"code_severity": "low"})
    )


def test_lowered_high_flag_still_blocks_approval_until_decided(env: Env) -> None:
    case = env.seed(flags=[LOWERED])
    r = verdict(env, case.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "high_flags_open"
    assert r.json()["error"]["details"]["flag_ids"] == [LOWERED.id]
    # confirming the flag (no second approver needed) lets the approval through
    assert (
        env.review(
            case.id, **review_body("flag_decision", "confirm", flag_ids=[LOWERED.id])
        ).status_code
        == 201
    )
    assert verdict(env, case.id).status_code == 201


def test_lowered_high_flag_dismissal_still_needs_a_second_approver(env: Env) -> None:
    case = env.seed(flags=[LOWERED])
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOWERED.id]))
    assert r.status_code == 201 and r.json()["entry"]["status"] == "pending_second_approval"
    assert r.json()["case"]["pending_second_approvals"] == 1
    assert verdict(env, case.id).status_code == 409  # a pending dismissal still counts as open
    r2 = env.review(
        case.id,
        **review_body("second_approval", "approve", target_entry_id="r1", reviewer="b.lim"),
    )
    assert r2.status_code == 201 and r2.json()["entry"]["status"] == "effective"
    assert verdict(env, case.id).status_code == 201


def test_a_plain_low_flag_has_no_such_controls(env: Env) -> None:
    case = env.seed(flags=[PLAIN_LOW])
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[PLAIN_LOW.id]))
    assert r.json()["entry"]["status"] == "effective"
    assert verdict(env, case.id).status_code == 201


def test_raising_a_low_rule_to_high_adds_both_controls(env: Env) -> None:
    blocked = env.seed(flags=[RAISED])
    r = verdict(env, blocked.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "high_flags_open"
    pending = env.seed(flags=[RAISED])
    r = env.review(pending.id, **review_body("flag_decision", "dismiss", flag_ids=[RAISED.id]))
    assert r.json()["entry"]["status"] == "pending_second_approval"


def test_scoring_still_follows_the_effective_severity(env: Env) -> None:
    from app.core.models import SCORE_WEIGHTS
    from app.findings import group_flags

    case = env.seed(flags=[]).model_copy()
    (lowered,) = group_flags(case, [LOWERED])
    assert lowered.weight == SCORE_WEIGHTS["low"] and lowered.hard_stop is True
