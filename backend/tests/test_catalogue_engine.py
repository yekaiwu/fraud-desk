"""Check catalogue overrides in the engine (docs/scoring-and-queue.md "Catalogue overrides").

Severity override, hard stops that never loosen, switched-off checks in coverage, provenance and
the pipeline. The unit tests use an isolated registry of stub checks so coverage is exact.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import app.checks.registry as reg
from app.checks.registry import SWITCHED_OFF, CheckMeta, make_flag, run_stage_checks
from app.core.cache import canonical_json
from app.core.cases import new_case
from app.core.config import Settings
from app.core.db import Database
from app.core.models import SCORE_WEIGHTS, Case, RuleOverride
from app.core.provenance import build_provenance, ruleset_version
from app.core.testing import FIXED_NOW, FIXED_TODAY, make_ctx, make_doc
from app.findings import HARD_STOP_RULES, default_hard_stop
from app.pipeline import run_pipeline
from app.queue import fast_lane_eligible
from app.score import run_stage
from tests.test_pipeline import CLEAN, EDITED, statement, with_documents

HARD = "x_directors"  # a hard-stop rule at medium severity
SOFT = "zz_soft"  # not a hard-stop rule
LOUD = "zz_loud"  # not a hard-stop rule, high by default
FILLERS = [f"zz_fill_{n}" for n in range(9)]
assert HARD in HARD_STOP_RULES and SOFT not in HARD_STOP_RULES and LOUD not in HARD_STOP_RULES


def override(
    *,
    enabled: bool = True,
    severity: str | None = None,
    revision: int = 1,
    by: str = "Ann Officer",
    reason: str = "calibration for this quarter",
    at: str = "2026-10-20T08:30:00.000000Z",
) -> RuleOverride:
    return RuleOverride(
        enabled=enabled,
        severity=severity,  # type: ignore[arg-type]
        updated_by=by,
        updated_at=at,
        reason=reason,
        revision=revision,
    )


class Registry:
    """Stub registry: three flagging checks and nine silent ones, so coverage is 12 of 12."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg.discover()  # import the real checks first, so discovery cannot run inside the stub
        self.calls: list[str] = []
        metas: dict[str, CheckMeta] = {}

        def add(rule: str, severity: str, flag: bool) -> None:
            def func(ctx):  # noqa: ANN001, ANN202
                self.calls.append(rule)
                if not flag:
                    return []
                return [
                    make_flag(
                        rule,
                        docs=["d1"],
                        evidence="e",
                        explanation="Something is off here.",
                        discriminator=rule,
                    )
                ]

            metas[rule] = CheckMeta(
                rule=rule,
                severity=severity,  # type: ignore[arg-type]
                applies_to=frozenset({"*"}),
                description="A stub check.",
                func=func,
            )

        add(HARD, "medium", True)
        add(SOFT, "medium", True)
        add(LOUD, "high", True)
        for rule in FILLERS:
            add(rule, "low", False)
        monkeypatch.setattr(reg, "_REGISTRY", metas)
        monkeypatch.setattr(reg, "_discovered", True)

    def analyse(self, overrides: dict[str, RuleOverride] | None = None):  # noqa: ANN201
        ctx = make_ctx(documents=[make_doc("invoice", id="d1")])
        ctx.overrides = overrides or {}
        run_stage_checks(ctx, "rules")
        run_stage(ctx)
        return ctx

    @staticmethod
    def finding(ctx, rule: str):  # noqa: ANN001, ANN205
        return next(f for f in ctx.case.findings if rule in f.rules)


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> Registry:
    return Registry(monkeypatch)


# ------------------------------------------------------------------------------- severity


def test_baseline_has_no_override_traces(stubs: Registry) -> None:
    ctx = stubs.analyse()
    assert ctx.case.coverage is not None and ctx.case.coverage.ratio == 1.0
    assert all(f.code_severity is None for f in ctx.case.flags)
    body = ctx.case.model_dump_json()
    assert "code_severity" not in body and '"code"' not in body and "catalogue" not in body


def test_severity_override_changes_flags_findings_weight_and_score(stubs: Registry) -> None:
    base = stubs.analyse()
    assert stubs.finding(base, LOUD).severity == "high"
    ctx = stubs.analyse({LOUD: override(severity="low")})
    flag = next(f for f in ctx.case.flags if f.rule == LOUD)
    assert flag.severity == "low" and flag.code_severity == "high"
    f = stubs.finding(ctx, LOUD)
    assert f.severity == "low" and f.weight == SCORE_WEIGHTS["low"]
    assert f.weight < stubs.finding(base, LOUD).weight
    assert base.case.score is not None and ctx.case.score is not None
    assert ctx.case.score.value == base.case.score.value - (
        SCORE_WEIGHTS["high"] - SCORE_WEIGHTS["low"]
    )
    # untouched rules are exactly as before
    assert stubs.finding(ctx, SOFT).severity == stubs.finding(base, SOFT).severity
    assert [(x.severity, x.code_severity) for x in ctx.case.flags if x.rule == SOFT] == [
        ("medium", None)
    ]
    # the override severity equal to the produced one records nothing
    same = stubs.analyse({LOUD: override(severity="high")})
    assert all(x.code_severity is None for x in same.case.flags)
    # name/note-only overrides never touch severity
    plain = stubs.analyse({LOUD: override()})
    assert plain.case.score == base.case.score and plain.case.flags == base.case.flags


def test_raising_a_severity_raises_weight_and_score(stubs: Registry) -> None:
    base = stubs.analyse()
    ctx = stubs.analyse({SOFT: override(severity="high")})
    assert stubs.finding(ctx, SOFT).weight == SCORE_WEIGHTS["high"]
    assert ctx.case.score is not None and base.case.score is not None
    assert ctx.case.score.value > base.case.score.value


def test_severity_counts_and_queue_columns_follow_the_effective_severity(
    stubs: Registry, db: Database, settings: Settings
) -> None:
    base = stubs.analyse()
    low = stubs.analyse({LOUD: override(severity="info")})
    db.create_case(low.case.model_copy(update={"id": "c_lowered00000"}))
    with db.conn() as c:
        row = c.execute("SELECT high_count, findings_count FROM cases").fetchone()
    assert sum(1 for f in base.case.findings if f.severity == "high") == 1
    assert row["high_count"] == 0 and row["findings_count"] == 2  # info findings are not counted


# ------------------------------------------------------------------------------ hard stops


def test_default_hard_stop_function() -> None:
    assert default_hard_stop("high", ["zz"]) is True
    assert default_hard_stop("medium", [HARD]) is True
    assert default_hard_stop("medium", ["zz"]) is False
    assert default_hard_stop("low", [HARD]) is False and default_hard_stop("info", [HARD]) is False


def test_lowering_a_hard_stop_rule_keeps_the_hard_stop_and_the_fast_lane_shut(
    stubs: Registry,
) -> None:
    base = stubs.analyse()
    assert stubs.finding(base, HARD).hard_stop is True and base.case.lane != "fast"
    for severity in ("low", "info"):
        ctx = stubs.analyse({HARD: override(severity=severity)})
        f = stubs.finding(ctx, HARD)
        assert f.severity == severity and f.hard_stop is True  # fewer points, same block
        assert f.weight == SCORE_WEIGHTS[severity]
        assert ctx.case.lane != "fast" and not fast_lane_eligible(ctx.case)
        assert ctx.case.score is not None and base.case.score is not None
        assert ctx.case.score.value <= base.case.score.value


def test_lowering_a_high_rule_keeps_its_hard_stop_whatever_its_id(stubs: Registry) -> None:
    base = stubs.analyse()
    assert stubs.finding(base, LOUD).hard_stop is True  # high is always a hard stop
    ctx = stubs.analyse({LOUD: override(severity="info")})
    assert stubs.finding(ctx, LOUD).hard_stop is True and ctx.case.lane != "fast"


def test_lowering_a_plain_rule_never_creates_a_hard_stop_and_can_reach_the_fast_lane(
    stubs: Registry,
) -> None:
    base = stubs.analyse({LOUD: override(severity="low"), HARD: override(severity="low")})
    # LOUD was high (hard stop); HARD was medium on a hard-stop rule: both still block
    assert base.case.lane != "fast"
    # SOFT alone: medium, not a hard-stop rule, so lowering it blocks nothing
    only_soft = stubs.analyse({SOFT: override(severity="low")})
    assert stubs.finding(only_soft, SOFT).hard_stop is False
    assert default_hard_stop("medium", [SOFT]) is False


def test_raising_adds_a_hard_stop_by_the_default_logic_on_the_effective_severity(
    stubs: Registry,
) -> None:
    base = stubs.analyse()
    assert stubs.finding(base, SOFT).hard_stop is False
    up = stubs.analyse({SOFT: override(severity="high")})
    assert stubs.finding(up, SOFT).hard_stop is True  # high is a hard stop
    still = stubs.analyse({SOFT: override(severity="medium")})
    assert stubs.finding(still, SOFT).hard_stop is False  # medium on a plain rule is not


def test_hard_stop_uses_the_original_severity_of_mixed_findings(stubs: Registry) -> None:
    """A group with an overridden and a non-overridden member keeps the original block."""
    from app.core.models import Flag
    from app.findings import group_flags

    case = make_ctx().case

    def flag(rule: str, sev: str, original: str | None) -> Flag:
        return Flag(
            id=f"{rule}.d1.-~acme",
            rule=rule,
            stage="rules",
            severity=sev,  # type: ignore[arg-type]
            document_ids=["d1"],
            evidence="e",
            explanation="It is off.",
            code_severity=original,  # type: ignore[arg-type]
        )

    # one rule family ("holder"): x_ownership_vs_registry (hard stop) lowered, kyc_shareholdings not
    flags = [
        flag("x_ownership_vs_registry", "low", "medium"),
        flag("kyc_shareholdings", "low", None),
    ]
    (finding,) = group_flags(case, flags)
    assert finding.severity == "low" and finding.hard_stop is True
    assert finding.rules == ["kyc_shareholdings", "x_ownership_vs_registry"]


# ------------------------------------------------------------------------- switched off


def test_switched_off_check_is_not_executed_and_is_recorded_as_skipped(stubs: Registry) -> None:
    ctx = stubs.analyse({SOFT: override(enabled=False, reason="too noisy on scanned statements")})
    assert SOFT not in stubs.calls and HARD in stubs.calls
    run = next(r for r in ctx.case.check_runs if r.rule == SOFT)
    assert run.status == "skipped" and run.code == SWITCHED_OFF == "switched_off"
    assert run.flags == 0 and run.seconds == 0
    assert run.reason == (
        "Switched off in the check catalogue by Ann Officer on 2026-10-20: "
        "too noisy on scanned statements"
    )
    assert not any(f.rule == SOFT for f in ctx.case.flags)


def test_switched_off_check_lowers_coverage_and_is_listed_as_not_run(stubs: Registry) -> None:
    base = stubs.analyse()
    ctx = stubs.analyse({SOFT: override(enabled=False)})
    assert base.case.coverage is not None and ctx.case.coverage is not None
    assert (base.case.coverage.applicable, base.case.coverage.ran) == (12, 12)
    cov = ctx.case.coverage
    assert cov.applicable == 12 and cov.ran == 11 and cov.ratio == round(11 / 12, 4)
    assert cov.ratio < base.case.coverage.ratio
    assert cov.skipped == [
        {
            "rule": SOFT,
            "status": "skipped",
            "code": "switched_off",
            "reason": (
                "Switched off in the check catalogue by Ann Officer on 2026-10-20: "
                "calibration for this quarter"
            ),
        }
    ]
    # other skips keep their old three-key shape
    assert all(set(s) == {"rule", "status", "reason"} for s in base.case.coverage.skipped)
    assert ctx.case.top_finding is not None


def test_a_check_that_could_not_run_anyway_keeps_its_own_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = Registry(monkeypatch)
    ctx = make_ctx()  # no files or documents: every "*" check has a requirement gap
    ctx.overrides = {SOFT: override(enabled=False)}
    run_stage_checks(ctx, "rules")
    run = next(r for r in ctx.case.check_runs if r.rule == SOFT)
    assert run.reason == "no files or documents in the case" and run.code is None
    assert registry.calls == []


def test_fast_lane_safeguard_for_switched_off_hard_stop_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the hard-stop check could ever block; switching it off must still keep the lane shut."""
    reg.discover()
    metas: dict[str, CheckMeta] = {}
    for rule in (HARD, *FILLERS, "zz_extra"):
        metas[rule] = CheckMeta(
            rule=rule,
            severity="medium",
            applies_to=frozenset({"*"}),
            description="A stub check.",
            func=lambda ctx: [],
        )
    monkeypatch.setattr(reg, "_REGISTRY", metas)
    monkeypatch.setattr(reg, "_discovered", True)

    def analyse(overrides: dict[str, RuleOverride]):  # noqa: ANN202
        ctx = make_ctx(documents=[make_doc("invoice", id="d1")])
        ctx.overrides = overrides
        run_stage_checks(ctx, "rules")
        run_stage(ctx)
        return ctx

    assert analyse({}).case.lane == "fast"
    soft_off = analyse({FILLERS[0]: override(enabled=False)})
    assert soft_off.case.coverage is not None and soft_off.case.coverage.ratio == round(10 / 11, 4)
    assert soft_off.case.lane == "fast"  # a plain check switched off, coverage still >= 80%
    hard_off = analyse({HARD: override(enabled=False)})
    assert hard_off.case.coverage is not None and hard_off.case.coverage.ratio >= 0.8
    assert hard_off.case.lane != "fast" and not fast_lane_eligible(hard_off.case)


def test_explain_method_mentions_the_switched_off_condition() -> None:
    from app.api.explain import build_method

    requires = build_method()["fast_lane"]["requires"]
    assert any("switched off" in line for line in requires)


# --------------------------------------------------------------------------- provenance


def test_provenance_has_no_catalogue_key_without_overrides(stubs: Registry) -> None:
    ctx = make_ctx(documents=[make_doc("invoice", id="d1")])
    prov = build_provenance(ctx)
    assert prov.catalogue is None
    assert "catalogue" not in json.loads(prov.model_dump_json())
    assert "catalogue" not in prov.model_dump(mode="json", exclude_none=False)


def test_provenance_catalogue_lists_overrides_sorted_with_a_hash(stubs: Registry) -> None:
    ctx = make_ctx(documents=[make_doc("invoice", id="d1")])
    plain = build_provenance(ctx)
    ctx.overrides = {
        SOFT: override(enabled=False, revision=2, by="Someone", reason="a different reason"),
        HARD: override(severity="low", revision=1),
        LOUD: override(),  # a note/name-only override is still listed
    }
    prov = build_provenance(ctx)
    entries = [
        {"rule": HARD, "enabled": True, "severity": "low", "revision": 1},
        {"rule": LOUD, "enabled": True, "severity": None, "revision": 1},
        {"rule": SOFT, "enabled": False, "severity": None, "revision": 2},
    ]
    assert prov.catalogue == {
        "overrides": entries,
        "hash": hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest(),
    }
    # code identity is unchanged and every other stamped hash is identical
    assert prov.ruleset_version == plain.ruleset_version == ruleset_version(reg.all_checks())
    for key in ("settings_hash", "prompt_hash", "flags_digest", "input_hashes", "models"):
        assert getattr(prov, key) == getattr(plain, key)
    dumped = json.loads(prov.model_dump_json())
    assert dumped["catalogue"]["hash"] == prov.catalogue["hash"]
    # the hash changes with the settings, not with the free text
    ctx.overrides[LOUD] = override(revision=1, by="Other", reason="new words")
    assert build_provenance(ctx).catalogue == prov.catalogue
    ctx.overrides[LOUD] = override(severity="info")
    assert build_provenance(ctx).catalogue["hash"] != prov.catalogue["hash"]


def test_ruleset_version_is_independent_of_overrides(stubs: Registry) -> None:
    before = ruleset_version(reg.all_checks())
    stubs.analyse({SOFT: override(enabled=False, severity="info")})
    assert ruleset_version(reg.all_checks()) == before


# -------------------------------------------------------------------- pipeline end to end


@pytest.fixture
def env(db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    case = new_case(settings, "loan", reference="T-1", today=FIXED_TODAY)
    db.create_case(case)
    with_documents(monkeypatch, statement(EDITED))
    return db, settings, case


def snapshot(case: Case) -> dict:
    """Everything the engine decided, minus wall-clock noise (timings, stamps)."""
    return {
        "flags": [f.model_dump(mode="json") for f in case.flags],
        "findings": [f.model_dump(mode="json") for f in case.findings],
        "score": case.score.model_dump(mode="json") if case.score else None,
        "coverage": case.coverage.model_dump(mode="json") if case.coverage else None,
        "lane": case.lane,
        "top_finding": case.top_finding,
        "runs": [(r.rule, r.status, r.reason, r.flags) for r in case.check_runs],
        "digest": case.provenance.flags_digest if case.provenance else None,
        "provenance": {
            k: v
            for k, v in (case.provenance.model_dump(mode="json") if case.provenance else {}).items()
            if k != "created_at"
        },
    }


def write(db: Database, rule: str, **fields) -> None:  # noqa: ANN003
    state = {"enabled": True, "severity": None, "name": None, "description": None, "note": None}
    db.write_rule_override(
        rule,
        {**state, **fields},
        reviewer="Ann Officer",
        reason="calibration for this quarter",
        at=FIXED_NOW,
    )


def test_pipeline_override_reset_and_timing(env) -> None:  # noqa: ANN001
    db, settings, case = env
    base = run_pipeline(case.id, db=db, settings=settings)
    flag = next(f for f in base.flags if f.rule == "bs_running_balance")
    assert flag.severity == "high" and base.provenance is not None
    assert base.provenance.catalogue is None
    base_snap = snapshot(base)
    assert '"catalogue"' not in db.load_case(case.id).model_dump_json()
    assert next(f for f in base.findings if "bs_running_balance" in f.rules).hard_stop is True

    # 1. a change applies to analyses run after it; the stored result is not rewritten
    write(db, "bs_running_balance", severity="low")
    assert snapshot(db.load_case(case.id)) == base_snap

    # 2. a re-run uses the current settings
    lowered = run_pipeline(case.id, db=db, settings=settings)
    assert lowered.version == 2
    f = next(f for f in lowered.findings if "bs_running_balance" in f.rules)
    assert f.severity == "low" and f.weight == SCORE_WEIGHTS["low"] and f.hard_stop is True
    flag = next(x for x in lowered.flags if x.rule == "bs_running_balance")
    assert (flag.severity, flag.code_severity) == ("low", "high")
    assert lowered.score is not None and base.score is not None
    assert lowered.score.value == base.score.value - (SCORE_WEIGHTS["high"] - SCORE_WEIGHTS["low"])
    assert lowered.lane != "fast"
    prov = lowered.provenance
    assert prov is not None and prov.catalogue is not None
    assert prov.catalogue["overrides"] == [
        {"rule": "bs_running_balance", "enabled": True, "severity": "low", "revision": 1}
    ]
    assert prov.ruleset_version == base.provenance.ruleset_version
    stored = json.loads(db.load_case(case.id).model_dump_json())
    assert stored["provenance"]["catalogue"]["hash"] == prov.catalogue["hash"]

    # 3. a reset restores a result identical to the baseline
    db.write_rule_override(
        "bs_running_balance", None, reviewer="Bob", reason="back to the defaults", at=FIXED_NOW
    )
    restored = run_pipeline(case.id, db=db, settings=settings)
    assert restored.version == 3
    assert snapshot(restored) == base_snap
    assert restored.provenance is not None and restored.provenance.catalogue is None


def test_pipeline_switched_off_check_and_effectiveness(env) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    import app.main as main

    db, settings, case = env
    base = run_pipeline(case.id, db=db, settings=settings)
    assert base.coverage is not None and base.coverage.ran >= 1
    write(db, "bs_running_balance", enabled=False)
    off = run_pipeline(case.id, db=db, settings=settings)
    assert off.coverage is not None
    assert off.coverage.applicable == base.coverage.applicable
    assert off.coverage.ran == base.coverage.ran - 1
    assert off.coverage.ratio < base.coverage.ratio
    entry = next(s for s in off.coverage.skipped if s["rule"] == "bs_running_balance")
    assert entry["code"] == "switched_off" and entry["status"] == "skipped"
    assert entry["reason"].startswith("Switched off in the check catalogue by Ann Officer on 2026-")
    assert not any(f.rule == "bs_running_balance" for f in off.flags)
    assert off.score is not None and base.score is not None and off.score.value < base.score.value
    # silence is visible: the explanation line says how many checks ran
    assert off.top_finding is not None
    # the stats keep keying by rule id and show no run after the switch
    client = TestClient(main.create_app(settings, db=db))
    rows = client.get("/rules/effectiveness").json()["rules"]
    row = next(r for r in rows if r["rule"] == "bs_running_balance")
    case_rows = db.load_case(case.id)
    assert case_rows.version == 2
    assert row["cases_ran"] == 0 and row["cases_skipped"] == 1 and row["times_fired"] == 0
    assert row["default_severity"] == "high"
    # the case view shows the run
    body = client.get(f"/cases/{case.id}").json()
    run = next(r for r in body["check_runs"] if r["rule"] == "bs_running_balance")
    assert run["status"] == "skipped" and run["code"] == "switched_off"
    assert body["provenance"]["catalogue"]["overrides"][0]["enabled"] is False
    # the pack lists it under not-run checks
    assert (
        "Switched off in the check catalogue"
        in client.get(f"/cases/{case.id}/pack", params={"format": "html"}).text
    )


def test_overrides_are_read_once_per_analysis_not_cached_across_runs(env) -> None:  # noqa: ANN001
    db, settings, case = env
    run_pipeline(case.id, db=db, settings=settings)
    for severity, expected in (("info", "info"), ("medium", "medium")):
        write(db, "bs_running_balance", severity=severity)
        out = run_pipeline(case.id, db=db, settings=settings)
        assert next(f for f in out.flags if f.rule == "bs_running_balance").severity == expected


def test_clean_statement_with_overrides_still_raises_nothing(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = new_case(settings, "loan", today=FIXED_TODAY)
    db.create_case(case)
    with_documents(monkeypatch, statement(CLEAN))
    write(db, "bs_running_balance", severity="high")
    out = run_pipeline(case.id, db=db, settings=settings)
    assert not any(f.rule == "bs_running_balance" for f in out.flags)
    assert out.provenance is not None and out.provenance.catalogue is not None
