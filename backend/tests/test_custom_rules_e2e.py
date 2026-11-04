"""Custom rules end to end through the real app: two signed-in accounts, the fake model, the real job
runner and pipeline. Only the extract stage is replaced (no OCR) so the case holds a known invoice."""

from __future__ import annotations

from typing import Any

import pytest

import app.pipeline as pipeline
from app.core.config import Settings
from app.core.db import Database
from app.core.models import StageOutcome
from tests.custom_rules_author_helpers import (
    SECRET,
    TOTAL_REQUEST,
    Api,
    As,
    fake,
    finish,
    make_api,
    start,
    total_answer,
)
from tests.custom_rules_helpers import invoice
from tests.test_api_support import tiny_pdf


@pytest.fixture(autouse=True)
def _proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)


@pytest.fixture
def api(settings: Settings, db: Database) -> Api:
    return make_api(settings, db, fake(total_answer()))


def set_total(monkeypatch: pytest.MonkeyPatch, total: str) -> None:
    def extract(ctx):  # noqa: ANN001, ANN202
        ctx.case.documents = [invoice(total=total)]
        return StageOutcome(detail={"documents": 1})

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "extract", extract)


def analyse(api: Api, who: As) -> dict[str, Any]:
    r = who.request(
        "POST",
        "/cases",
        data={"use_case": "loan"},
        files=[("files", ("invoice.pdf", tiny_pdf(), "application/pdf"))],
    )
    assert r.status_code == 202, r.text
    created = r.json()
    job = api.app.state.runner.wait(created["job_id"], timeout=120)
    assert job.state == "succeeded", job.error
    got = who.get(f"/cases/{created['case_id']}")
    assert got.status_code == 200, got.text
    return got.json()


def custom_runs(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in case["check_runs"] if r.get("custom")]


def custom_flags(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in case["flags"] if f["rule"].startswith("cus_")]


def make_rule(who: As) -> dict[str, Any]:
    draft = start(who, TOTAL_REQUEST)
    assert draft["status"] == "ready"
    saved = finish(who, draft["draft_id"], "Big invoices")
    assert saved.status_code == 201, saved.text
    return saved.json()["rule"]


def test_a_private_rule_runs_for_its_owner_only_until_it_is_adopted(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, bob = api.as_("alice"), api.as_("bob")
    assert alice.get("/me").json()["custom_rules"] is True
    rule = make_rule(alice)
    rid = rule["rule_id"]
    assert rid.startswith("cus_") and rule["visibility"] == "private"
    assert bob.get("/custom-rules").json() == {"mine": [], "adopted": []}
    assert bob.get(f"/custom-rules/{rid}").status_code == 404

    # The owner's analysis: the rule fires, and is a check run, a flag and a finding of its own.
    set_total(monkeypatch, "120000.00")
    fired = analyse(api, alice)
    (run,) = custom_runs(fired)
    assert run["rule"] == rid and run["stage"] == "rules" and run["flags"] == 1
    assert run["status"] == "ran" and run["custom"]["origin"] == "own"
    assert run["custom"]["author"] == "alice" and run["custom"]["version"] == 1
    assert run["custom"]["spec_hash"] == rule["spec_hash"]
    (flag,) = custom_flags(fired)
    assert flag["severity"] == "low" and flag["stage"] == "rules"
    assert flag["id"].startswith(rid) and "120000" in flag["evidence"]
    assert any(flag["id"] in f["flag_ids"] for f in fired["findings"])
    assert fired["provenance"]["custom_rules"]  # which rule and version ran is stamped on the run

    # Not triggered: still a check run on the case, with no flag. This is what the timeline lists.
    set_total(monkeypatch, "500.00")
    quiet = analyse(api, alice)
    (run,) = custom_runs(quiet)
    assert run["rule"] == rid and run["flags"] == 0 and run["status"] == "ran"
    assert not custom_flags(quiet)
    rules_stage = next(s for s in quiet["stages"] if s["stage"] == "rules")
    assert rules_stage["detail"]["custom_checks"] == 1

    # Coverage ignores custom runs: both cases have the same built-in coverage.
    assert quiet["coverage"]["ratio"] == fired["coverage"]["ratio"]
    assert quiet["coverage"]["applicable"] == fired["coverage"]["applicable"]

    # The other account has nothing of it: not in the case, not in the check list, not in the pack.
    set_total(monkeypatch, "120000.00")
    bobs = analyse(api, bob)
    assert not custom_runs(bobs) and not custom_flags(bobs)
    assert rid not in str(bobs["check_runs"]) and "custom_rules" not in (bobs["provenance"] or {})

    # Publishing alone changes nothing for bob; adopting does, from his next analysis on.
    assert (
        alice.post(f"/custom-rules/{rid}/publish", {"reason": "share with the desk"}).status_code
        == 200
    )
    still = analyse(api, bob)
    assert not custom_runs(still)
    adopted = bob.post(f"/custom-rules/{rid}/adopt")
    assert adopted.status_code == 200, adopted.text
    assert not custom_runs(bobs)  # the earlier case is not rewritten
    after = analyse(api, bob)
    (run,) = custom_runs(after)
    assert run["rule"] == rid and run["flags"] == 1 and run["custom"]["origin"] == "adopted"
    assert run["custom"]["author"] == "alice"
    assert custom_flags(after)

    # Taking the rule back: bob's next analysis no longer runs it, his past case stays explainable.
    assert bob.post(f"/custom-rules/{rid}/unadopt").status_code == 200
    assert not custom_runs(analyse(api, bob))
    assert custom_runs(bob.get(f"/cases/{after['id']}").json())


def test_the_pack_and_the_case_list_the_rule_whether_or_not_it_fired(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = api.as_("alice")
    rid = make_rule(alice)["rule_id"]
    set_total(monkeypatch, "500.00")
    quiet = analyse(api, alice)
    pack = alice.get(f"/cases/{quiet['id']}/pack", params={"format": "json"})
    assert pack.status_code == 200, pack.text
    assert rid in pack.text and "custom" in pack.text.lower()


def test_a_retired_rule_stops_running_but_the_past_case_still_names_it(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = api.as_("alice")
    rid = make_rule(alice)["rule_id"]
    set_total(monkeypatch, "120000.00")
    before = analyse(api, alice)
    assert (
        alice.post(f"/custom-rules/{rid}/retire", {"reason": "not needed any more"}).status_code
        == 200
    )
    assert not custom_runs(analyse(api, alice))
    again = alice.get(f"/cases/{before['id']}").json()
    assert custom_runs(again)[0]["rule"] == rid and custom_flags(again)


def test_with_the_feature_off_the_routes_refuse_and_cases_carry_no_trace(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = make_api(settings, db, fake(total_answer()), custom_rules="off")
    alice = off.as_("alice")
    assert alice.get("/me").json()["custom_rules"] is False
    r = alice.get("/custom-rules")
    assert r.status_code == 404 and r.json()["error"]["code"] == "feature_off"
    set_total(monkeypatch, "120000.00")
    case = analyse(off, alice)
    assert not custom_runs(case) and not custom_flags(case)
    assert not any(r["rule"].startswith("cus_") for r in case["check_runs"])
