"""Review workflow over HTTP (review-workflow-and-audit.md section 6, tests 1 to 14 and 16 to 19)."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.review import (
    VERDICT_LABELS,
    VERDICTS_BY_USE_CASE,
    allowed_verdicts,
    flag_dispositions,
    materialise,
    parse_review_request,
)
from tests.test_api_support import (
    Env,
    make_env,
    mk_flag,
    review_body,
)

HIGH = mk_flag("bs_running_balance", "high", doc="d1", locator="transactions:3")
MEDIUM = mk_flag("inv_dates", "medium", doc="d2", locator="invoice_date")
LOW = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")
INFO = mk_flag("doc_unclassified", "info", doc="d3", locator="-")
FLAGS = [HIGH, MEDIUM, LOW, INFO]


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def error(resp) -> dict:  # noqa: ANN001
    assert set(resp.json()) == {"error"}, resp.text
    assert resp.headers["x-request-id"]
    return resp.json()["error"]


# 1. clean case ------------------------------------------------------------------------------


def test_clean_case_can_be_approved_with_a_five_character_reason(env: Env) -> None:
    case = env.seed(flags=[])
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["score"]["value"] == 0 and detail["score"]["band"] == "low"
    assert detail["findings"] == []
    assert "approve" in detail["allowed_verdicts"]
    r = env.review(case.id, **review_body("verdict", "approve", reason="abcde"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["case"]["status"] == "closed" and body["case"]["decision"] == "approve"
    assert body["entry"]["id"] == "r1" and body["entry"]["status"] == "effective"
    after = env.client.get(f"/cases/{case.id}").json()
    assert after["status"] == "closed" and after["decision"] == "approve"
    assert after["allowed_verdicts"] == []
    assert [e["action"] for e in env.audit(case.id)] == ["review.verdict"]


# 2. reasons -----------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "abcd", "   ok   ", "      "])
def test_reason_below_five_characters_is_rejected_and_nothing_is_written(
    env: Env, reason: str | None
) -> None:
    case = env.seed(flags=FLAGS)
    body = review_body("verdict", "request_documents")
    if reason is None:
        del body["reason"]
    else:
        body["reason"] = reason
    r = env.review(case.id, **body)
    assert r.status_code == 422 and error(r)["code"] == "reason_required"
    assert error(r)["details"] == {"field": "reason"}
    assert env.client.get(f"/cases/{case.id}/review").json() == {"entries": []}
    assert env.client.get(f"/cases/{case.id}").json()["status"] == "open"
    rejected = [e for e in env.audit(case.id) if e["action"] == "review.rejected"]
    assert len(rejected) == 1 and rejected[0]["detail"]["code"] == "reason_required"


def test_reason_of_exactly_five_characters_after_trimming_is_accepted(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(
        case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id], reason="  12345  ")
    )
    assert r.status_code == 201 and r.json()["entry"]["reason"] == "12345"


def test_reason_longer_than_one_thousand_characters_is_a_validation_error(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(
        case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id], reason="x" * 1001)
    )
    assert r.status_code == 422 and error(r)["code"] == "validation_error"
    ok = env.review(
        case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id], reason="x" * 1000)
    )
    assert ok.status_code == 201


# 3. reviewer ------------------------------------------------------------------------------------


@pytest.mark.parametrize("reviewer", ["a", "  b ", "x" * 65, "", None])
def test_reviewer_must_be_two_to_sixty_four_characters(env: Env, reviewer: str | None) -> None:
    case = env.seed(flags=FLAGS)
    body = review_body("verdict", "request_documents")
    if reviewer is None:
        del body["reviewer"]
    else:
        body["reviewer"] = reviewer
    r = env.review(case.id, **body)
    assert r.status_code == 422 and error(r)["code"] == "reviewer_required"
    assert env.client.get(f"/cases/{case.id}/review").json() == {"entries": []}


def test_reviewer_of_sixty_four_characters_is_accepted(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("verdict", "request_documents", reviewer="r" * 64))
    assert r.status_code == 201


# 4 to 9. dismissals and the second approver ------------------------------------------------------


def dispositions(env: Env, case_id: str) -> dict[str, str]:
    return {f["id"]: f["disposition"] for f in env.client.get(f"/cases/{case_id}").json()["flags"]}


def test_dismissing_a_high_flag_waits_for_a_second_approver(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    assert r.status_code == 201
    assert r.json()["entry"]["status"] == "pending_second_approval"
    assert r.json()["case"]["pending_second_approvals"] == 1
    assert dispositions(env, case.id)[HIGH.id] == "pending_second_approval"
    detail = env.client.get(f"/cases/{case.id}").json()
    assert [p["entry_id"] for p in detail["pending_second_approvals"]] == ["r1"]
    assert detail["pending_second_approvals"][0]["flag_ids"] == [HIGH.id]
    assert detail["pending_second_approvals"][0]["reviewer"] == "a.tan"
    assert detail["status"] == "open" and detail["decision"] is None


def test_same_reviewer_cannot_countersign_even_with_other_case_and_spaces(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    r = env.review(
        case.id,
        **review_body("second_approval", "approve", target_entry_id="r1", reviewer="A.Tan "),
    )
    assert r.status_code == 409 and error(r)["code"] == "second_approver_must_differ"
    assert dispositions(env, case.id)[HIGH.id] == "pending_second_approval"


def test_a_different_reviewer_countersigns_and_the_flag_is_dismissed(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    r = env.review(
        case.id,
        **review_body(
            "second_approval",
            "approve",
            target_entry_id="r1",
            reviewer="b.lim",
            reason="Agree: rounding.",
        ),
    )
    assert r.status_code == 201 and r.json()["entry"]["status"] == "effective"
    assert r.json()["case"]["status"] == "open" and r.json()["case"]["decision"] is None
    assert r.json()["case"]["pending_second_approvals"] == 0
    assert dispositions(env, case.id)[HIGH.id] == "dismissed"
    entries = env.client.get(f"/cases/{case.id}/review").json()["entries"]
    assert [(e["id"], e["status"]) for e in entries] == [("r1", "effective"), ("r2", "effective")]
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["pending_second_approvals"] == []
    assert detail["score"]["value"] == 50  # review never changes the score


def test_decline_returns_the_flag_to_open(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[HIGH.id]))
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    r = env.review(
        case.id,
        **review_body("second_approval", "decline", target_entry_id="r2", reviewer="b.lim"),
    )
    assert r.status_code == 201
    entries = env.client.get(f"/cases/{case.id}/review").json()["entries"]
    assert entries[1]["status"] == "declined"
    assert dispositions(env, case.id)[HIGH.id] == "open"  # not back to "confirmed"


def test_medium_low_and_info_dismissals_are_effective_immediately(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    for flag in (MEDIUM, LOW, INFO):
        r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[flag.id]))
        assert r.status_code == 201 and r.json()["entry"]["status"] == "effective", flag.id
    d = dispositions(env, case.id)
    assert d[MEDIUM.id] == d[LOW.id] == d[INFO.id] == "dismissed"
    assert d[HIGH.id] == "open"


def test_one_entry_with_a_high_and_a_low_flag_is_pending_as_a_whole(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id, LOW.id]))
    assert r.json()["entry"]["status"] == "pending_second_approval"
    d = dispositions(env, case.id)
    assert d[HIGH.id] == d[LOW.id] == "pending_second_approval"


def test_confirming_is_always_effective(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[HIGH.id]))
    assert r.json()["entry"]["status"] == "effective"
    assert dispositions(env, case.id)[HIGH.id] == "confirmed"


# 10. flag_decision_pending ------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["confirm", "dismiss"])
def test_a_flag_with_a_pending_dismissal_takes_no_other_decision(env: Env, decision: str) -> None:
    case = env.seed(flags=FLAGS)
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    r = env.review(
        case.id,
        **review_body("flag_decision", decision, flag_ids=[HIGH.id, LOW.id], reviewer="c.ng"),
    )
    assert r.status_code == 409 and error(r)["code"] == "flag_decision_pending"
    assert error(r)["details"]["flag_ids"] == [HIGH.id]
    assert len(env.client.get(f"/cases/{case.id}/review").json()["entries"]) == 1


# 11. approve gate ----------------------------------------------------------------------------------


def test_approve_is_gated_by_open_high_flags(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    approve = review_body("verdict", "approve", reviewer="z.ho")
    r = env.review(case.id, **approve)
    assert r.status_code == 409 and error(r)["code"] == "high_flags_open"
    assert error(r)["details"]["flag_ids"] == [HIGH.id]
    # a pending dismissal still counts as open
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    r = env.review(case.id, **approve)
    assert r.status_code == 409 and error(r)["code"] == "high_flags_open"
    # after the countersignature it is effectively dismissed
    env.review(
        case.id, **review_body("second_approval", "approve", target_entry_id="r1", reviewer="b.lim")
    )
    r = env.review(case.id, **approve)
    assert r.status_code == 201 and r.json()["case"]["status"] == "closed"


def test_approve_after_confirming_the_high_flag(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[HIGH.id]))
    r = env.review(case.id, **review_body("verdict", "approve"))
    assert r.status_code == 201 and r.json()["case"]["decision"] == "approve"


def test_medium_and_low_flags_do_not_block_approval(env: Env) -> None:
    case = env.seed(flags=[MEDIUM, LOW, INFO])
    r = env.review(case.id, **review_body("verdict", "approve"))
    assert r.status_code == 201


# 12. verdicts per use case -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("use_case", "decision", "status"),
    [
        ("loan", "refer_edd", 422),
        ("kyc", "reject_or_escalate", 422),
        ("aml", "reject_or_escalate", 422),
        ("aml", "refer_edd", 422),
        ("kyc", "refer_edd", 201),
        ("loan", "reject_or_escalate", 201),
        ("aml", "escalate_aml", 201),
        ("kyc", "escalate_aml", 201),
        ("loan", "escalate_aml", 201),
    ],
)
def test_verdicts_are_limited_per_use_case(
    env: Env, use_case: str, decision: str, status: int
) -> None:
    case = env.seed(use_case=use_case, flags=[])
    r = env.review(case.id, **review_body("verdict", decision))
    assert r.status_code == status, r.text
    if status == 422:
        assert error(r)["code"] == "verdict_not_allowed"
        assert error(r)["details"]["allowed"] == [
            v
            for v in (
                "approve",
                "request_documents",
                "refer_edd",
                "escalate_aml",
                "reject_or_escalate",
            )
            if v in VERDICTS_BY_USE_CASE[use_case]  # type: ignore[index]
        ]
        assert env.client.get(f"/cases/{case.id}").json()["status"] == "open"
    else:
        assert r.json()["case"]["status"] == "closed"


def test_allowed_verdicts_in_the_case_view_follow_the_use_case(env: Env) -> None:
    expected = {
        "kyc": ["approve", "request_documents", "refer_edd", "escalate_aml"],
        "aml": ["approve", "request_documents", "escalate_aml"],
        "loan": ["approve", "request_documents", "escalate_aml", "reject_or_escalate"],
    }
    for use_case, verdicts in expected.items():
        case = env.seed(use_case=use_case)
        assert env.client.get(f"/cases/{case.id}").json()["allowed_verdicts"] == verdicts
    assert set(VERDICT_LABELS) == {
        "approve",
        "request_documents",
        "refer_edd",
        "escalate_aml",
        "reject_or_escalate",
    }


def test_unknown_verdict_is_a_validation_error(env: Env) -> None:
    case = env.seed(flags=[])
    r = env.review(case.id, **review_body("verdict", "auto_approve"))
    assert r.status_code == 422 and error(r)["code"] == "validation_error"


# 13. escalation clock ----------------------------------------------------------------------------


def test_escalate_aml_stamps_the_suspicion_clock_once(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(
        case.id, **review_body("verdict", "escalate_aml", reason="Funds routed via three companies")
    )
    assert r.status_code == 201, r.text
    entry, summary = r.json()["entry"], r.json()["case"]
    assert summary["status"] == "closed" and summary["decision"] == "escalate_aml"
    assert summary["escalated_at"] == entry["created_at"]  # the entry time from the injected clock
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["escalated_at"] == entry["created_at"]
    # escalation is not blocked by open high flags (paperwork must not stop it)
    assert [f["disposition"] for f in detail["flags"]].count("open") == 4
    again = env.review(case.id, **review_body("verdict", "escalate_aml"))
    assert again.status_code == 409 and error(again)["code"] == "case_not_open"
    assert env.client.get(f"/cases/{case.id}").json()["escalated_at"] == entry["created_at"]


def test_reject_or_escalate_and_other_verdicts_do_not_stamp_the_clock(env: Env) -> None:
    for decision in ("reject_or_escalate", "approve"):
        case = env.seed(flags=[])
        r = env.review(case.id, **review_body("verdict", decision))
        assert r.status_code == 201
        assert r.json()["case"]["escalated_at"] is None


# 14. non-gated verdicts and pending dismissals ---------------------------------------------------


def test_refer_edd_is_not_gated_and_leaves_pending_dismissals_unresolved(env: Env) -> None:
    case = env.seed(use_case="kyc", flags=FLAGS)
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    r = env.review(case.id, **review_body("verdict", "refer_edd", reviewer="b.lim"))
    assert r.status_code == 201 and r.json()["case"]["decision"] == "refer_edd"
    assert r.json()["case"]["pending_second_approvals"] == 1
    pack = env.client.get(f"/cases/{case.id}/pack").json()
    assert pack["unresolved_at_close"] == ["r1"]


def test_request_documents_awaits_documents_and_keeps_the_analysis(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("verdict", "request_documents"))
    assert r.status_code == 201
    assert (
        r.json()["case"]["status"] == "awaiting_documents" and r.json()["case"]["decision"] is None
    )
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["status"] == "awaiting_documents" and detail["allowed_verdicts"] == []
    assert len(detail["flags"]) == 4 and detail["score"]["value"] == 30 + 15 + 5
    again = env.review(case.id, **review_body("verdict", "approve"))
    assert again.status_code == 409 and error(again)["code"] == "case_not_open"


# 16. stale version -----------------------------------------------------------------------------------


def test_stale_case_version_is_rejected(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("verdict", "request_documents", case_version=2))
    assert r.status_code == 409 and error(r)["code"] == "stale_version"
    assert error(r)["details"]["current_version"] == 1


# 17. state guards -------------------------------------------------------------------------------------


def test_review_needs_an_open_case(env: Env) -> None:
    closed = env.seed(status="closed", decision="approve")
    r = env.review(closed.id, **review_body("verdict", "approve"))
    assert r.status_code == 409 and error(r)["code"] == "case_not_open"
    processing = env.seed(status="processing")
    r = env.review(processing.id, **review_body("verdict", "approve"))
    assert r.status_code == 409 and error(r)["code"] == "case_processing"
    err = env.seed(status="error")
    r = env.review(err.id, **review_body("verdict", "approve"))
    assert r.status_code == 409 and error(r)["code"] == "case_not_open"
    missing = env.review("c_doesnotexist", **review_body("verdict", "approve"))
    assert missing.status_code == 404 and error(missing)["details"]["kind"] == "case"


# 18. flag targets ---------------------------------------------------------------------------------------


def test_unknown_flag_id_is_a_404_and_writes_nothing(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=["nope.d1.-"]))
    assert r.status_code == 404 and error(r)["details"]["kind"] == "flag"
    assert env.client.get(f"/cases/{case.id}/review").json() == {"entries": []}
    rejected = [e for e in env.audit(case.id) if e["action"] == "review.rejected"]
    assert rejected and rejected[0]["detail"]["http_status"] == 404


@pytest.mark.parametrize(
    "extra",
    [
        {"flag_ids": [LOW.id], "finding_id": "fnd_1"},
        {},
        {"flag_ids": []},
        {"flag_ids": [f"x.d1.{i}" for i in range(51)]},
    ],
    ids=["both", "neither", "empty", "fifty-one"],
)
def test_flag_decision_needs_exactly_one_target_of_one_to_fifty(env: Env, extra: dict) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("flag_decision", "dismiss", **extra))
    assert r.status_code == 422 and error(r)["code"] == "validation_error"


def test_fifty_flags_in_one_entry_are_allowed(env: Env) -> None:
    flags = [mk_flag("inv_dates", "low", doc="d2", locator=f"lines:{i}") for i in range(50)]
    case = env.seed(flags=flags)
    r = env.review(
        case.id, **review_body("flag_decision", "dismiss", flag_ids=[f.id for f in flags])
    )
    assert r.status_code == 201 and len(r.json()["entry"]["flag_ids"]) == 50


def test_unknown_finding_id_is_a_404(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    r = env.review(case.id, **review_body("flag_decision", "confirm", finding_id="fnd_99"))
    assert r.status_code == 404 and error(r)["details"]["kind"] == "finding"


def test_unknown_target_entry_is_a_404_and_a_non_pending_target_is_a_409(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    missing = env.review(
        case.id, **review_body("second_approval", "approve", target_entry_id="r9", reviewer="b.lim")
    )
    assert missing.status_code == 404 and error(missing)["details"]["kind"] == "entry"
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[HIGH.id]))
    not_pending = env.review(
        case.id, **review_body("second_approval", "approve", target_entry_id="r1", reviewer="b.lim")
    )
    assert not_pending.status_code == 409 and error(not_pending)["code"] == "entry_not_pending"


# 19. finding_id --------------------------------------------------------------------------------------------


def test_finding_decision_expands_to_its_flags(env: Env) -> None:
    case = env.seed(flags=[MEDIUM, LOW, INFO], group=True)
    detail = env.client.get(f"/cases/{case.id}").json()
    finding = detail["findings"][0]
    assert sorted(finding["flag_ids"]) == sorted(f.id for f in (MEDIUM, LOW, INFO))
    r = env.review(case.id, **review_body("flag_decision", "confirm", finding_id=finding["id"]))
    assert r.status_code == 201
    entry = r.json()["entry"]
    assert entry["finding_id"] == finding["id"] and entry["flag_ids"] == finding["flag_ids"]
    assert {f["disposition"] for f in env.client.get(f"/cases/{case.id}").json()["flags"]} == {
        "confirmed"
    }


def test_finding_with_a_high_flag_dismissal_is_pending(env: Env) -> None:
    case = env.seed(flags=[HIGH, LOW], group=True)
    r = env.review(case.id, **review_body("flag_decision", "dismiss", finding_id="fnd_1"))
    assert r.json()["entry"]["status"] == "pending_second_approval"


# pure helpers -------------------------------------------------------------------------------------------------


def test_pure_helpers_do_not_touch_the_database(env: Env) -> None:
    from app.core.models import ReviewEntry

    case = env.seed(flags=FLAGS)
    loaded = env.db.load_case(case.id)
    assert allowed_verdicts(loaded) == [
        "approve",
        "request_documents",
        "escalate_aml",
        "reject_or_escalate",
    ]
    assert allowed_verdicts(loaded, current=False) == []
    entries = [
        ReviewEntry(
            id="r1",
            kind="flag_decision",
            reviewer="a.tan",
            decision="dismiss",
            reason="because",
            flag_ids=[HIGH.id],
            status="pending_second_approval",
            case_version=1,
            created_at=env.clock.now,
        ),
        ReviewEntry(
            id="r2",
            kind="flag_decision",
            reviewer="a.tan",
            decision="confirm",
            reason="because",
            flag_ids=[MEDIUM.id],
            status="effective",
            case_version=1,
            created_at=env.clock.now,
        ),
        ReviewEntry(  # another version never carries over
            id="r3",
            kind="flag_decision",
            reviewer="a.tan",
            decision="confirm",
            reason="because",
            flag_ids=[LOW.id],
            status="effective",
            case_version=7,
            created_at=env.clock.now,
        ),
    ]
    d = flag_dispositions(loaded, entries)
    assert d[HIGH.id] == "pending_second_approval" and d[MEDIUM.id] == "confirmed"
    assert d[LOW.id] == "open" and d[INFO.id] == "open"
    view = materialise(loaded, entries)
    assert {f.id: f.disposition for f in view.flags} == d
    assert [e.id for e in view.review] == ["r1", "r2"]
    assert all(f.disposition == "open" for f in loaded.flags)  # the original is untouched


def test_request_models_reject_unknown_fields_and_kinds() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_review_request({**review_body("verdict", "approve"), "extra": 1})
    with pytest.raises(ValidationError):
        parse_review_request({**review_body("verdict", "approve"), "kind": "auto"})
    ok = parse_review_request(review_body("flag_decision", "dismiss", flag_ids=["a", "a", "b"]))
    assert ok.flag_ids == ["a", "b"]  # duplicates collapse


def test_review_never_changes_flags_findings_or_score(env: Env) -> None:
    case = env.seed(flags=FLAGS)
    before = env.client.get(f"/cases/{case.id}").json()
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id]))
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[HIGH.id]))
    env.review(case.id, **review_body("verdict", "approve"))
    after = env.client.get(f"/cases/{case.id}").json()
    for key in ("findings", "score", "coverage", "lane", "top_finding"):
        assert after[key] == before[key], key
    assert [{k: v for k, v in f.items() if k != "disposition"} for f in after["flags"]] == [
        {k: v for k, v in f.items() if k != "disposition"} for f in before["flags"]
    ]


def test_tipping_off_there_is_no_applicant_facing_route(env: Env) -> None:
    paths = {getattr(r, "path", "") for r in env.app.routes}
    assert not any("applicant" in p for p in paths)
    openapi = env.client.get("/openapi.json").json()["paths"]
    assert not any("applicant" in p for p in openapi)
