"""Custom rules inside an analysis: loading, running, coverage, scoring, provenance, visibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.api.explain import build_method
from app.checks.registry import run_stage_checks
from app.core.cases import new_case
from app.core.config import Settings
from app.core.db import Database
from app.core.models import CheckRun, Finding, Score
from app.core.provenance import build_provenance
from app.core.testing import FIXED_TODAY, make_ctx, make_doc
from app.custom_rules import store
from app.custom_rules.engine import SKIP_FAILED, SKIP_FIELD_MISSING, SKIP_NO_DOCUMENT
from app.custom_rules.readback import readback
from app.effectiveness import compute_effectiveness
from app.monitoring import _rule_rates
from app.pack import build_pack, render_pack_html
from app.pipeline import JobRunner, run_pipeline
from app.queue import fast_lane_eligible, lane_for
from app.reading_agreement import apply_reading_agreement
from app.score import compute_coverage, run_stage
from app.score_model import CUSTOM_FAMILY, family_of
from app.semantic.policy import counts_in_coverage
from tests.custom_rules_helpers import (
    DEBT,
    EQUITY,
    ctx_with,
    invoice,
    loaded,
    spec_of,
    statement_doc,
    t1,
    t3,
    t4,
    with_cell,
)
from tests.test_pipeline import with_documents

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
BIG = t1("invoice.total", "gt", "100000")


@pytest.fixture
def on(settings: Settings) -> Settings:
    return settings.model_copy(update={"custom_rules": "on"})


def seed(
    db: Database, owner: str, condition: dict, severity: str = "low", name: str = "Big invoices"
):  # noqa: ANN201
    spec = spec_of(condition, severity)
    return store.create_rule(
        db,
        owner=owner,
        name=name,
        description="",
        spec=spec,
        readback=readback(spec),
        reason="because",
        at=NOW,
        draft_id=None,
    )


def analyse(
    db: Database,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    *docs,
    account: str | None = "alice",
):  # noqa: ANN001, ANN201
    case = new_case(settings, "loan", reference="T-1", today=FIXED_TODAY)
    db.create_case(case)
    with_documents(monkeypatch, *docs)
    return run_pipeline(case.id, db=db, settings=settings, account=account)


def shape(case):  # noqa: ANN001, ANN201
    """Everything of a result that must not move when no custom rule runs (times and ids left out)."""
    prov = case.provenance.model_dump(mode="json") if case.provenance else {}
    for k in ("created_at",):
        prov.pop(k, None)
    return {
        "runs": [
            (r.rule, r.stage, r.status, r.reason, r.code, r.flags, r.note) for r in case.check_runs
        ],
        "flags": [f.model_dump(mode="json") for f in case.flags],
        "findings": [f.model_dump(mode="json") for f in case.findings],
        "score": case.score.model_dump(mode="json") if case.score else None,
        "coverage": case.coverage.model_dump(mode="json") if case.coverage else None,
        "lane": case.lane,
        "prov": prov,
        "details": [(s.stage, s.status, sorted(s.detail), s.warnings) for s in case.stages],
    }


# ------------------------------------------------------------------ in a real analysis


def test_a_rule_that_found_nothing_is_still_a_check_run_on_the_case(db, on, monkeypatch) -> None:  # noqa: ANN001
    seed(db, "alice", t1("invoice.total", "gt", "9999999"))
    case = analyse(db, on, monkeypatch, invoice())
    runs = [r for r in case.check_runs if r.custom is not None]
    assert len(runs) == 1 and runs[0].status == "ran" and runs[0].flags == 0
    assert runs[0].stage == "rules" and runs[0].note == "Checked 1 invoice: none matched."
    assert runs[0].custom.name == "Big invoices" and runs[0].custom.author == "alice"  # type: ignore[union-attr]
    assert not [f for f in case.flags if f.rule.startswith("cus_")]
    rules_stage = next(s for s in case.stages if s.stage == "rules")
    assert rules_stage.detail["custom_checks"] == 1 and rules_stage.detail["custom_flags"] == 0
    stored = db.load_case(case.id)
    assert [r.custom for r in stored.check_runs if r.custom] == [
        runs[0].custom
    ]  # survives the round trip


def test_a_firing_rule_produces_a_finding_and_a_stamp(db, on, monkeypatch) -> None:  # noqa: ANN001
    rule = seed(db, "alice", BIG, "medium")
    case = analyse(db, on, monkeypatch, invoice())
    flag = next(f for f in case.flags if f.rule == rule.id)
    assert flag.stage == "rules" and flag.severity == "medium"
    finding = next(f for f in case.findings if rule.id in f.rules)
    assert finding.severity == "medium" and finding.hard_stop is False and finding.weight == 15
    rules_stage = next(s for s in case.stages if s.stage == "rules")
    assert rules_stage.detail["custom_flags"] == 1
    stamp = case.provenance.custom_rules  # type: ignore[union-attr]
    assert stamp is not None and stamp["rules"] == [
        {
            "rule": rule.id,
            "version": 1,
            "spec_hash": spec_of(BIG, "medium").spec_hash(),
            "origin": "own",
        }
    ]
    assert len(stamp["hash"]) == 64


def test_the_rule_set_is_the_one_of_the_account_that_started_the_run(db, on, monkeypatch) -> None:  # noqa: ANN001
    mine = seed(db, "alice", BIG)
    seed(db, "bob", t1("invoice.total", "gt", "5"), name="Bobs")
    case = analyse(db, on, monkeypatch, invoice(), account="alice")
    assert [r.rule for r in case.check_runs if r.custom] == [mine.id]
    bobs = analyse(db, on, monkeypatch, invoice(), account="bob")
    assert [r.custom.author for r in bobs.check_runs if r.custom] == ["bob"]  # type: ignore[union-attr]
    carol = analyse(db, on, monkeypatch, invoice(), account="carol")
    assert not [r for r in carol.check_runs if r.custom]


def test_adopted_public_rules_run_for_the_adopter_only(db, on, monkeypatch) -> None:  # noqa: ANN001
    rule = seed(db, "alice", BIG)
    store.publish(db, rule.id, actor="alice", reason="share it", at=NOW)
    before = analyse(db, on, monkeypatch, invoice(), account="bob")
    assert not [r for r in before.check_runs if r.custom] and not before.provenance.custom_rules  # type: ignore[union-attr]
    store.adopt(db, rule.id, "bob", at=NOW)
    after = analyse(db, on, monkeypatch, invoice(), account="bob")
    run = next(r for r in after.check_runs if r.custom)
    assert (run.rule, run.custom.origin, run.custom.author) == (rule.id, "adopted", "alice")  # type: ignore[union-attr]
    assert after.provenance.custom_rules["rules"][0]["origin"] == "adopted"  # type: ignore[union-attr,index]


def test_a_disabled_or_retired_rule_does_not_run(db, on, monkeypatch) -> None:  # noqa: ANN001
    a, b = seed(db, "alice", BIG), seed(db, "alice", t3("receipt.receipt_number"), name="No number")
    store.set_enabled(db, a.id, False, actor="alice", reason="pause it", at=NOW)
    store.retire(db, b.id, actor="alice", reason="obsolete now", at=NOW)
    case = analyse(db, on, monkeypatch, invoice())
    assert not [r for r in case.check_runs if r.custom]
    assert case.provenance.custom_rules is None  # type: ignore[union-attr]


def test_a_rule_for_an_absent_document_type_is_skipped_but_listed(db, on, monkeypatch) -> None:  # noqa: ANN001
    seed(db, "alice", t3("receipt.receipt_number"))
    case = analyse(db, on, monkeypatch, invoice())
    run = next(r for r in case.check_runs if r.custom)
    assert (run.status, run.code, run.flags) == ("skipped", SKIP_NO_DOCUMENT, 0)
    assert not case.partial and "check_error" not in " ".join(
        w for s in case.stages for w in s.warnings
    )


def test_job_runner_passes_the_account_to_the_pipeline(db, on, monkeypatch) -> None:  # noqa: ANN001
    rule = seed(db, "alice", BIG)
    case = new_case(on, "loan", reference="T-2", today=FIXED_TODAY)
    db.create_case(case)
    with_documents(monkeypatch, invoice())
    runner = JobRunner(db, on)
    try:
        done = runner.wait(runner.submit(case.id, account="alice").id, timeout=30)
        assert done.state == "succeeded"
        stored = db.load_case(case.id)
        assert [r.rule for r in stored.check_runs if r.custom] == [rule.id]
        anon = new_case(on, "loan", reference="T-3", today=FIXED_TODAY)
        db.create_case(anon)
        runner.wait(runner.submit(anon.id).id, timeout=30)
        assert not [r for r in db.load_case(anon.id).check_runs if r.custom]
    finally:
        runner.shutdown()


# ------------------------------------------------------------------ switched off: nothing changes


def test_with_the_feature_off_nothing_loads_and_the_result_is_unchanged(
    db, settings, monkeypatch
) -> None:  # noqa: ANN001
    seed(db, "alice", BIG, "medium")
    calls: list[str] = []
    real = store.load_rule_set
    monkeypatch.setattr(store, "load_rule_set", lambda *a, **k: calls.append("x") or real(*a, **k))
    off = analyse(db, settings, monkeypatch, invoice(), account="alice")
    assert calls == []
    plain_db = Database(":memory:")
    plain_db.init_schema()
    plain = analyse(plain_db, settings, monkeypatch, invoice(), account="alice")
    assert shape(off) == shape(plain)
    dumped = off.model_dump_json()
    assert "custom" not in dumped and "cus_" not in dumped
    assert "custom_rules" not in json.dumps(off.provenance.model_dump(mode="json"))  # type: ignore[union-attr]


def test_no_signed_in_account_means_no_custom_rules(db, on, monkeypatch) -> None:  # noqa: ANN001
    seed(db, "alice", BIG, "medium")
    case = analyse(db, on, monkeypatch, invoice(), account=None)
    assert not [r for r in case.check_runs if r.custom] and case.provenance.custom_rules is None  # type: ignore[union-attr]


def test_a_rule_set_that_cannot_be_read_gives_a_warning_and_the_analysis_goes_on(
    db, on, monkeypatch
) -> None:  # noqa: ANN001
    seed(db, "alice", BIG)

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("database is locked /secret")

    monkeypatch.setattr(store, "load_rule_set", boom)
    case = analyse(db, on, monkeypatch, invoice())
    assert case.status == "open"
    warnings = [w for s in case.stages for w in s.warnings]
    assert any(w.startswith("custom_rules_unavailable:") for w in warnings)
    assert "secret" not in " ".join(warnings)
    assert not [r for r in case.check_runs if r.custom]
    assert all(s.status == "ok" for s in case.stages)


# ------------------------------------------------------------------ coverage, lanes, score


def _case_with(runs: list[tuple[str, str, str | None]], findings: list[Finding] | None = None):  # noqa: ANN202
    ctx = make_ctx([invoice()])
    case = ctx.case
    case.check_runs = [CheckRun(rule=r, stage="rules", status=s, code=c) for r, s, c in runs]  # type: ignore[arg-type]
    case.findings = findings or []
    case.coverage = compute_coverage(case)  # what the score stage stores
    case.score = Score(value=sum(f.weight for f in case.findings), band="low")
    return case


BASE = [
    ("a", "ran", None),
    ("b", "ran", None),
    ("c", "ran", None),
    ("d", "ran", None),
    ("e", "skipped", None),
]
CUSTOM_RUNS = [
    ("cus_aaaaaaaaaa", "ran", None),
    ("cus_bbbbbbbbbb", "skipped", SKIP_NO_DOCUMENT),
    ("cus_cccccccccc", "skipped", SKIP_FIELD_MISSING),
    ("cus_dddddddddd", "skipped", SKIP_FAILED),
    ("cus_eeeeeeeeee", "error", None),  # cannot happen from the engine; still never counted
]


def test_a_custom_run_never_counts_in_coverage() -> None:
    for status, code in (
        ("ran", None),
        ("skipped", SKIP_NO_DOCUMENT),
        ("skipped", SKIP_FAILED),
        ("error", None),
    ):
        assert counts_in_coverage("cus_aaaaaaaaaa", status, code) is False
    assert counts_in_coverage("inv_total", "ran", None) is True


def test_coverage_is_identical_with_and_without_custom_runs() -> None:
    plain = compute_coverage(_case_with(BASE))
    with_custom = compute_coverage(_case_with(BASE + CUSTOM_RUNS))
    assert (with_custom.applicable, with_custom.ran, with_custom.ratio) == (
        plain.applicable,
        plain.ran,
        plain.ratio,
    )
    assert with_custom.skipped == plain.skipped
    for one in CUSTOM_RUNS:
        alone = compute_coverage(_case_with([*BASE, one]))
        assert (alone.applicable, alone.ran, alone.ratio, alone.skipped) == (
            plain.applicable,
            plain.ran,
            plain.ratio,
            plain.skipped,
        )


def test_custom_runs_cannot_lift_a_weak_case_over_the_fast_lane_bar() -> None:
    weak = [
        ("a", "ran", None),
        ("b", "ran", None),
        ("c", "ran", None),
        ("d", "skipped", None),
        ("e", "skipped", None),
    ]
    many = [(f"cus_{i:010d}", "ran", None) for i in range(8)]
    case = _case_with(weak + many)
    assert not fast_lane_eligible(case) and lane_for(case) != "fast"
    assert fast_lane_eligible(_case_with(BASE + many))


def finding(rule: str, weight: int, severity: str) -> Finding:
    return Finding(
        id=f"F-{rule}",
        group_key=f"rule:{rule}",
        title="t",
        severity=severity,  # type: ignore[arg-type]
        flag_ids=[],
        document_ids=[],
        rules=[rule],
        weight=weight,
        summary="s",
    )


def test_a_low_custom_finding_does_not_close_the_fast_lane_and_is_never_a_hard_stop() -> None:
    case = _case_with(BASE + [CUSTOM_RUNS[0]], [finding("cus_aaaaaaaaaa", 5, "low")])
    assert fast_lane_eligible(case) and lane_for(case) == "fast"
    from app.findings import default_hard_stop

    for severity in ("info", "low", "medium"):
        assert default_hard_stop("cus_aaaaaaaaaa", severity) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("severity", "n", "points"), [("medium", 1, 15), ("medium", 2, 30), ("low", 5, 25)]
)
@pytest.mark.parametrize("model", ["legacy", "calibrated"])
def test_custom_flags_alone_never_take_a_case_out_of_the_fast_lane(
    severity: str, n: int, points: int, model: str
) -> None:
    settings = Settings(_env_file=None, llm_backend="fake", db_path=":memory:", score_model=model)

    def analysed(*rules):  # noqa: ANN002, ANN202
        ctx = ctx_with([invoice()], *rules, settings=settings)
        run_stage_checks(ctx, "rules")
        for run in ctx.case.check_runs:  # the built-in checks that did not apply here all ran
            if not run.custom:
                run.status, run.reason, run.code = "ran", None, None
        run_stage(ctx)
        return ctx

    plain = analysed()
    assert plain.case.lane == "fast"
    rules = [
        loaded(t1("invoice.total", "gt", str(i)), severity, rule_id=f"cus_{i:010d}")
        for i in range(1, n + 1)
    ]
    case = analysed(*rules).case
    assert len([f for f in case.findings if f.rules[0].startswith("cus_")]) == n
    assert case.coverage is not None and case.coverage.ratio == plain.case.coverage.ratio  # type: ignore[union-attr]
    assert case.lane == "fast" and fast_lane_eligible(case)
    # the displayed score still carries the custom points
    assert case.score is not None and case.score.value > plain.case.score.value  # type: ignore[union-attr]
    if model == "legacy":
        assert case.score.value == plain.case.score.value + points  # type: ignore[union-attr]


def test_a_builtin_medium_flag_still_blocks_the_fast_lane_with_or_without_custom_flags() -> None:
    builtin = finding("inv_total", 15, "medium")
    custom = finding("cus_aaaaaaaaaa", 15, "medium")
    only_custom = _case_with(BASE, [custom])
    assert fast_lane_eligible(only_custom) and lane_for(only_custom) == "fast"
    # 15 built-in + 15 custom = 30 shown, but the built-in 15 alone is under the bar: still fast
    assert fast_lane_eligible(_case_with(BASE, [builtin, custom]))
    heavy = _case_with(BASE, [finding("inv_total", 30, "high"), custom])
    assert not fast_lane_eligible(heavy) and lane_for(heavy) != "fast"
    two = _case_with(BASE, [builtin, finding("inv_date", 15, "medium"), custom])
    assert not fast_lane_eligible(two)


def test_a_real_run_with_a_custom_flag_keeps_the_builtin_coverage(monkeypatch) -> None:  # noqa: ANN001
    docs = [invoice()]
    plain = make_ctx(docs)
    run_stage_checks(plain, "rules")
    run_stage(plain)
    fired = ctx_with(docs, loaded(BIG, "medium"))
    run_stage_checks(fired, "rules")
    run_stage(fired)
    assert fired.case.coverage is not None and plain.case.coverage is not None
    assert (fired.case.coverage.applicable, fired.case.coverage.ran, fired.case.coverage.ratio) == (
        plain.case.coverage.applicable,
        plain.case.coverage.ran,
        plain.case.coverage.ratio,
    )
    assert [f for f in fired.case.findings if f.hard_stop] == []
    assert fired.case.score.value == plain.case.score.value + 15  # type: ignore[union-attr]


def test_custom_findings_are_one_score_family_and_capped() -> None:
    assert (
        family_of(["cus_aaaaaaaaaa"]) == family_of(["cus_bbbbbbbbbb"]) == CUSTOM_FAMILY == "custom"
    )
    assert family_of(["inv_total"]) != CUSTOM_FAMILY
    settings = Settings(
        _env_file=None, llm_backend="fake", db_path=":memory:", score_model="calibrated"
    )
    rules = [
        loaded(t1("invoice.total", "gt", str(i)), "medium", rule_id=f"cus_{i:010d}")
        for i in range(1, 9)
    ]
    ctx = ctx_with([invoice()], *rules, settings=settings)
    run_stage_checks(ctx, "rules")
    run_stage(ctx)
    assert len([f for f in ctx.case.findings if f.rules[0].startswith("cus_")]) == 8
    assert (
        ctx.case.score is not None and ctx.case.score.value < 60
    )  # custom rules alone never reach High
    assert ctx.case.score.band != "high"


def test_the_weights_file_cannot_carry_a_custom_rule() -> None:
    from pathlib import Path

    import app.score_model as sm

    raw = json.loads((Path(sm.__file__).parent / "score_weights.default.json").read_text())
    sm.ScoreWeights.model_validate(raw)  # the shipped file is valid as it is
    first = next(iter(raw["rules"].values()))
    raw["rules"]["cus_aaaaaaaaaa:medium"] = first
    with pytest.raises(ValueError, match="not a registered check"):
        sm.ScoreWeights.model_validate(raw)


# ------------------------------------------------------------------ doubtful readings and the second reader


def test_reading_agreement_leaves_custom_flags_alone() -> None:
    doc = with_cell(invoice(), "total", agreement="differ", other_raw="12000.00", verified=False)
    doc = with_cell(doc, "subtotal", agreement="differ", other_raw="1.00", verified=False)
    settings = Settings(_env_file=None, llm_backend="fake", db_path=":memory:", multi_reader=True)
    ctx = ctx_with([doc], loaded(BIG, "medium"), settings=settings)
    run_stage_checks(ctx, "rules")
    before = [f.model_copy(deep=True) for f in ctx.case.flags if f.rule.startswith("cus_")]
    assert (
        before and before[0].severity == "low" and "unverified reading: total" in before[0].evidence
    )
    apply_reading_agreement(ctx)
    after = [f for f in ctx.case.flags if f.rule.startswith("cus_")]
    assert after == before and all(f.reading is None for f in after)


def test_a_custom_flag_on_a_confident_cell_is_not_touched_by_other_disagreements() -> None:
    doc = with_cell(invoice(), "subtotal", agreement="differ", other_raw="1.00", verified=False)
    settings = Settings(_env_file=None, llm_backend="fake", db_path=":memory:", multi_reader=True)
    ctx = ctx_with([doc], loaded(BIG, "medium"), settings=settings)
    run_stage_checks(ctx, "rules")
    apply_reading_agreement(ctx)
    flag = next(f for f in ctx.case.flags if f.rule.startswith("cus_"))
    assert flag.severity == "medium" and flag.reading is None


# ------------------------------------------------------------------ provenance and the ruleset


def test_provenance_carries_the_stamp_only_when_rules_were_loaded() -> None:
    plain = build_provenance(make_ctx([invoice()]))
    assert plain.custom_rules is None and "custom_rules" not in plain.model_dump(mode="json")
    rule = loaded(BIG)
    with_rules = build_provenance(ctx_with([invoice()], rule))
    assert with_rules.custom_rules is not None
    assert with_rules.custom_rules["rules"][0]["rule"] == rule.rule_id
    assert (
        with_rules.ruleset_version == plain.ruleset_version
    )  # custom rules are not in the ruleset hash
    assert with_rules.settings_hash == plain.settings_hash


def test_custom_rules_never_enter_the_registered_checks() -> None:
    from app.checks.registry import all_checks, checks_by_rule, get_check

    ctx = ctx_with([invoice()], loaded(BIG))
    run_stage_checks(ctx, "rules")
    assert not [m for m in all_checks() if m.rule.startswith("cus_")]
    assert not [r for r in checks_by_rule() if r.startswith("cus_")]
    assert get_check("cus_0123456789") is None


def test_setting_is_listed_in_result_settings_only_when_on(settings: Settings) -> None:
    assert "custom_rules" not in settings.result_settings()
    assert (
        settings.model_copy(update={"custom_rules": "on"}).result_settings()["custom_rules"] == "on"
    )


# ------------------------------------------------------------------ pack, explain, trails


def test_the_pack_labels_custom_runs_and_flags(db, on, monkeypatch) -> None:  # noqa: ANN001
    rule = seed(db, "alice", BIG, "medium")
    quiet = seed(db, "alice", t1("invoice.total", "gt", "9999999"), name="Quiet one")
    case = analyse(db, on, monkeypatch, invoice())
    pack = build_pack(db, case.id)
    runs = {r["rule"]: r for r in pack["check_runs"]}
    assert runs[rule.id]["custom"]["author"] == "alice" and runs[rule.id]["custom"]["version"] == 1
    assert runs[quiet.id]["custom"]["name"] == "Quiet one" and runs[quiet.id]["flags"] == 0
    assert all("custom" not in r for k, r in runs.items() if not k.startswith("cus_"))
    flag = next(f for f in pack["flags"] if f["rule"] == rule.id)
    assert flag["custom"] == {"name": "Big invoices", "author": "alice", "version": 1}
    assert all("custom" not in f for f in pack["flags"] if not f["rule"].startswith("cus_"))
    assert pack["provenance"]["custom_rules"]["rules"][0]["rule"] in (rule.id, quiet.id)
    html = render_pack_html(pack)
    assert "Custom rule by alice (unreviewed)" in html


def test_the_pack_of_a_case_without_custom_rules_is_unchanged(db, settings, monkeypatch) -> None:  # noqa: ANN001
    case = analyse(db, settings, monkeypatch, invoice())
    pack = build_pack(db, case.id)
    text = json.dumps(pack, default=str)
    assert '"custom"' not in text and "custom_rules" not in text
    assert "Custom rule by" not in render_pack_html(pack)


def test_explain_method_describes_custom_rules_only_when_on(
    settings: Settings, on: Settings
) -> None:
    assert "custom_rules" not in build_method(settings) and "custom_rules" not in build_method()
    section = build_method(on)["custom_rules"]
    assert section["max_severity"] == "medium" and section["hard_stop"] is False
    assert section["in_coverage"] is False and section["score_family"] == "custom"
    assert "owner or by an account that adopted it" in section["how"]


def test_effectiveness_and_drift_never_show_private_rule_ids(db, on, monkeypatch) -> None:  # noqa: ANN001
    rule = seed(db, "alice", BIG, "medium")
    analyse(db, on, monkeypatch, invoice())
    report = compute_effectiveness(db)
    assert rule.id not in json.dumps(report)
    assert not [r for r in report["rules"] if r["rule"].startswith("cus_")]
    bodies = [
        {
            "flags": [
                {"rule": rule.id, "severity": "medium"},
                {"rule": "inv_total", "severity": "medium"},
            ]
        }
    ]
    assert _rule_rates(bodies) == {"inv_total": 1.0}


def test_analysis_finished_audit_row_carries_the_stamp_hash(db, on, monkeypatch) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    from app.api import cases as cases_api
    from app.audit import read_audit

    seed(db, "alice", BIG)
    case = new_case(on, "loan", reference="T-4", today=FIXED_TODAY)
    db.create_case(case)
    with_documents(monkeypatch, invoice())
    runner = JobRunner(db, on)
    try:
        job = runner.wait(runner.submit(case.id, account="alice").id, timeout=30)
    finally:
        runner.shutdown()
    app = SimpleNamespace(state=SimpleNamespace(db=db, settings=on))
    cases_api._audit_job_outcome(app, job.id, case.id)
    row = next(r for r in read_audit(db, case.id) if r["action"] == "case.analysis_finished")
    stored = db.load_case(case.id)
    assert row["detail"]["custom_rules"] == stored.provenance.custom_rules["hash"]  # type: ignore[union-attr,index]
    # and not at all when none ran
    plain = new_case(on, "loan", reference="T-5", today=FIXED_TODAY)
    db.create_case(plain)
    runner = JobRunner(db, on)
    try:
        job2 = runner.wait(runner.submit(plain.id).id, timeout=30)
    finally:
        runner.shutdown()
    cases_api._audit_job_outcome(app, job2.id, plain.id)
    row2 = next(r for r in read_audit(db, plain.id) if r["action"] == "case.analysis_finished")
    assert "custom_rules" not in row2["detail"]


# ------------------------------------------------------------------ the statement template end to end


def test_the_worked_debt_example_runs_through_an_analysis(db, on, monkeypatch) -> None:  # noqa: ANN001
    rule = seed(db, "alice", t4(DEBT, EQUITY), "medium", name="Heavy borrowing")
    doc = statement_doc(
        borrowings_current="500000.00",
        borrowings_non_current="1200000.00",
        total_equity_sub="600000.00",
    )
    case = analyse(db, on, monkeypatch, doc, make_doc("receipt", id="d9"))
    flag = next(f for f in case.flags if f.rule == rule.id)
    assert (
        flag.severity == "medium"
        and "= 1700000.00 is greater than 2 x total equity" in flag.evidence
    )
    assert case.score is not None and case.lane != "fast"
