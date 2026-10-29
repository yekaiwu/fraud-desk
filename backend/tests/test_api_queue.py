"""Ranked queue (api.md section 4, scoring-and-queue.md 4.9 and test 21/22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.queue import build_queue_item
from app.core.config import Settings
from app.core.db import Database
from app.core.models import CheckRun, QueueItem
from tests.test_api_support import (
    FIXED_NOW,
    Env,
    FakeClock,
    make_env,
    mk_flag,
    review_body,
    write_fixture,
)

H = mk_flag("bs_running_balance", "high")
H2 = mk_flag("x_directors", "high", doc="d2", locator="-")
M = mk_flag("inv_dates", "medium", doc="d3", locator="invoice_date")
L = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def ids(resp) -> list[str]:  # noqa: ANN001
    assert resp.status_code == 200, resp.text
    return [i["case_id"] for i in resp.json()["items"]]


def runs(ran: int, total: int) -> list[CheckRun]:
    return [
        CheckRun(
            rule=f"r{i}", stage="rules", status="ran" if i < ran else "skipped", reason="no doc"
        )
        for i in range(total)
    ]


def test_priority_fixture_open_by_score_then_less_coverage_then_processing_awaiting_closed(
    env: Env,
) -> None:
    t = FIXED_NOW
    closed99 = env.seed(flags=[H, H2, M, L], status="closed", decision="approve", created_at=t)
    awaiting90 = env.seed(flags=[H, H2, M], status="awaiting_documents", created_at=t)
    processing = env.seed(flags=[], status="processing", created_at=t)
    open40 = env.seed(flags=[H, M][:1] + [L] * 2, created_at=t)  # 30 + 5 + 5 = 40
    open80_lowcov = env.seed(
        flags=[H, H2, M], check_runs=runs(2, 4), created_at=t + timedelta(seconds=5)
    )
    open80 = env.seed(flags=[H, H2, M], check_runs=runs(4, 4), created_at=t)
    got = ids(
        env.client.get("/cases", params={"status": "processing,open,awaiting_documents,closed"})
    )
    assert got == [
        open80_lowcov.id,
        open80.id,
        open40.id,
        processing.id,
        awaiting90.id,
        closed99.id,
    ]
    assert env.db.load_case(open80_lowcov.id).score.value == 75  # type: ignore[union-attr]


def test_default_status_filter_hides_closed_and_error_unless_asked(env: Env) -> None:
    open_ = env.seed(flags=[H])
    closed = env.seed(flags=[], status="closed", decision="approve")
    err = env.seed(flags=[], status="error")
    processing = env.seed(flags=[], status="processing")
    awaiting = env.seed(flags=[], status="awaiting_documents")
    default = set(ids(env.client.get("/cases")))
    assert default == {open_.id, processing.id, awaiting.id}
    assert set(ids(env.client.get("/cases", params={"status": "closed"}))) == {closed.id}
    assert set(ids(env.client.get("/cases", params={"status": "closed,error"}))) == {
        closed.id,
        err.id,
    }


def test_filters_use_case_lane_band_score_partial_and_text(env: Env) -> None:
    loan = env.seed(
        use_case="loan", flags=[H, H2], reference="ACME-001", applicant_name="Example Trading"
    )
    kyc = env.seed(use_case="kyc", flags=[L], reference="BETA-002", applicant_name="Beta Holdings")
    aml = env.seed(use_case="aml", flags=[], partial=True, reference="GAMMA_003")
    get = lambda **p: set(ids(env.client.get("/cases", params=p)))  # noqa: E731
    assert get(use_case="kyc") == {kyc.id}
    assert get(use_case="kyc,aml") == {kyc.id, aml.id}
    assert (
        get(lane="loan") == {loan.id}
        and get(lane="edd") == {kyc.id}
        and get(lane="aml") == {aml.id}
    )
    assert get(band="high") == {loan.id} and get(band="low") == {kyc.id, aml.id}
    assert get(min_score=5) == {loan.id, kyc.id} and get(min_score=60) == {loan.id}
    assert get(partial="true") == {aml.id} and get(partial="false") == {loan.id, kyc.id}
    assert get(q="acme") == {loan.id} and get(q="holdings") == {kyc.id}
    assert get(q="gamma_") == {aml.id}
    assert get(q="%") == set()  # LIKE wildcards are escaped
    assert get(use_case="loan", band="low") == set()


@pytest.mark.parametrize(
    "params",
    [
        {"use_case": "mortgage"},
        {"status": "open,done"},
        {"lane": "slow"},
        {"band": "extreme"},
        {"min_score": 101},
        {"min_score": -1},
        {"sort": "alphabet"},
        {"order": "sideways"},
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"partial": "maybe"},
    ],
)
def test_bad_query_values_are_validation_errors(env: Env, params: dict) -> None:
    r = env.client.get("/cases", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"]["errors"]


def test_pagination_has_next_and_page_size_cap(env: Env) -> None:
    seeded = [env.seed(flags=[], created_at=FIXED_NOW + timedelta(seconds=i)) for i in range(5)]
    p1 = env.client.get("/cases", params={"page_size": 2}).json()
    p2 = env.client.get("/cases", params={"page_size": 2, "page": 2}).json()
    p3 = env.client.get("/cases", params={"page_size": 2, "page": 3}).json()
    assert (p1["total"], p1["page"], p1["page_size"], p1["has_next"]) == (5, 1, 2, True)
    assert p2["has_next"] is True and p3["has_next"] is False
    assert len(p1["items"]) == len(p2["items"]) == 2 and len(p3["items"]) == 1
    assert [i["case_id"] for i in p1["items"] + p2["items"] + p3["items"]] == [s.id for s in seeded]
    exact = env.client.get("/cases", params={"page_size": 5}).json()
    assert exact["has_next"] is False
    assert env.client.get("/cases", params={"page": 9}).json()["items"] == []
    assert env.client.get("/cases").json()["page_size"] == 25


def test_sorting_options(env: Env) -> None:
    a = env.seed(flags=[H, H2], check_runs=runs(1, 4), created_at=FIXED_NOW + timedelta(seconds=3))
    b = env.seed(flags=[L], check_runs=runs(4, 4), created_at=FIXED_NOW + timedelta(seconds=1))
    c = env.seed(flags=[M], check_runs=runs(2, 4), created_at=FIXED_NOW + timedelta(seconds=2))
    get = lambda **p: ids(env.client.get("/cases", params=p))  # noqa: E731
    assert get(sort="score") == [a.id, c.id, b.id]
    assert get(sort="score", order="asc") == [b.id, c.id, a.id]
    assert get(sort="created") == [a.id, c.id, b.id]
    assert get(sort="created", order="asc") == [b.id, c.id, a.id]
    assert get(sort="age") == [b.id, c.id, a.id]  # oldest first
    assert get(sort="coverage") == [a.id, c.id, b.id]  # least coverage first
    assert get(sort="priority", order="asc") == get(sort="priority")  # priority order is frozen


def test_queue_item_values_age_and_nulls(settings: Settings, db: Database) -> None:
    env = make_env(settings, db, FakeClock(FIXED_NOW + timedelta(hours=1)))
    clean = env.seed(flags=[], reference="CLEAN", created_at=FIXED_NOW)
    partial = env.seed(flags=[L], partial=True, check_runs=runs(1, 3), created_at=FIXED_NOW)
    processing = env.seed(flags=[H], status="processing", created_at=FIXED_NOW)
    items = {
        i["case_id"]: i for i in env.client.get("/cases", params={"page_size": 100}).json()["items"]
    }
    c = items[clean.id]
    assert c["age_seconds"] == 3600 and c["score"] == 0 and c["band"] == "low"
    assert (
        c["top_finding"] == "No flags raised" and c["findings_count"] == 0 and c["high_count"] == 0
    )
    assert c["coverage"] == {
        "applicable": 1,
        "ran": 1,
        "ratio": 1.0,
        "skipped": [],
        "pages_total": 0,
        "pages_unchecked": 0,
        "unchecked_pages": [],  # the queue carries the count only, never the page list
    }
    assert c["file_count"] == 0 and c["partial"] is False and c["decision"] is None
    p = items[partial.id]
    assert (
        p["partial"] is True and p["coverage"]["skipped"] == [] and p["coverage"]["ratio"] == 0.3333
    )
    q = items[processing.id]
    assert q["status"] == "processing"
    for key in ("score", "band", "coverage", "lane", "top_finding"):
        assert q[key] is None, key
    assert set(c) == set(QueueItem.model_fields)
    write_fixture("queue_page", env.client.get("/cases").json())


def test_age_seconds_is_never_negative_and_build_queue_item_is_pure(env: Env) -> None:
    case = env.seed(flags=[H], created_at=FIXED_NOW)
    rows, _ = env.db.list_cases()
    earlier = FIXED_NOW - timedelta(days=1)  # a clock behind the case: clamp to zero
    item = build_queue_item(rows[0], now=earlier)
    assert item.age_seconds == 0 and item.pending_second_approvals == 0
    later = build_queue_item(
        rows[0], now=FIXED_NOW + timedelta(seconds=90), pending_second_approvals=2
    )
    assert later.age_seconds == 90 and later.pending_second_approvals == 2
    assert later.case_id == case.id and later.high_count == 1 and later.findings_count == 1
    naive = datetime(2026, 10, 27, 10, 0, 30, tzinfo=UTC)
    assert build_queue_item(rows[0], now=naive).age_seconds == 30


def test_pending_second_approvals_count_only_the_current_version(env: Env) -> None:
    a, b = env.seed(flags=[H, H2]), env.seed(flags=[H])
    env.review(a.id, **review_body("flag_decision", "dismiss", flag_ids=[H.id]))
    env.review(a.id, **review_body("flag_decision", "dismiss", flag_ids=[H2.id]))
    env.review(
        a.id, **review_body("second_approval", "approve", target_entry_id="r2", reviewer="b.lim")
    )
    items = {i["case_id"]: i for i in env.client.get("/cases").json()["items"]}
    assert items[a.id]["pending_second_approvals"] == 1
    assert items[b.id]["pending_second_approvals"] == 0
    # a pending entry of an older version no longer counts after the version moves on
    case = env.db.load_case(a.id)
    case.version = 2
    env.db.save_case(case)
    items = {i["case_id"]: i for i in env.client.get("/cases").json()["items"]}
    assert items[a.id]["pending_second_approvals"] == 0


def test_decision_and_status_show_in_the_queue_after_a_verdict(env: Env) -> None:
    case = env.seed(flags=[L])
    env.review(case.id, **review_body("verdict", "approve"))
    item = env.client.get("/cases", params={"status": "closed"}).json()["items"][0]
    assert item["decision"] == "approve" and item["status"] == "closed"
