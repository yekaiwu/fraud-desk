"""Semantic signals base (docs/semantic-signals.md): switch, verifier, call wrapper, registry,
severity cap and fast-lane rules. Fake client only: no network, no key."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest

from app.checks.registry import CheckSkipped, all_checks, applicable, get_check, run_stage_checks
from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import LLMTransportError
from app.core.llm import FakeLLMClient, LLMRequest, prompt_registry
from app.core.models import Coverage, Document, Finding, Flag, Score
from app.core.testing import make_ctx, make_doc
from app.queue import fast_lane_eligible, lane_for
from app.score import run_stage
from app.semantic.call import (
    PURPOSE_PREFIX,
    SemanticNotRun,
    call_model,
    run_claims,
    system_prompt,
)
from app.semantic.claims import claims_json_schema, norm, parse_note, verify_claims
from app.semantic.flags import make_semantic_flag
from app.semantic.policy import cap_severity, is_semantic_rule, semantic_enabled
from app.semantic.registry import (
    discover_semantic,
    semantic_check,
    semantic_checks,
    unregister,
)

RULE = "sem_unit_probe"
KINDS = ("odd_description", "mismatch")


def settings_on(**kw: Any) -> Settings:
    return Settings(_env_file=None, llm_backend="fake", semantic="on", **kw)


def profile(**fields: str | None) -> Document:
    base = {
        "company_name": "Acme Trading Pte Ltd",
        "principal_activity": "Wholesale of electronics",
    }
    base.update(fields)
    return make_doc("company_profile", id="d1", fields=base)


def statement() -> Document:
    return make_doc(
        "bank_statement",
        id="d2",
        fields={"account_holder": "ACME TRADING PTE LTD"},
        tables={
            "transactions": [
                {"date": "2026-01-03", "description": "TT FROM K LOGISTICS", "credit": "1500.50"},
                {"date": "2026-01-04", "description": "CASH DEPOSIT OVERSEAS", "credit": "9000.00"},
            ]
        },
    )


def claim(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "odd_description",
        "severity": "medium",
        "statement": "The description looks unrelated to the declared activity.",
        "citations": [
            {
                "document_id": "d2",
                "table": "transactions",
                "row": 1,
                "quote": "CASH DEPOSIT OVERSEAS",
            }
        ],
    }
    base.update(kw)
    return base


def verify(claims: list[dict[str, Any]], docs: list[Document] | None = None, **kw: Any):  # noqa: ANN202
    return verify_claims({"claims": claims}, docs or [profile(), statement()], kinds=KINDS, **kw)


# ------------------------------------------------------------------------------- the switch


def test_default_is_off_and_env_spellings_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).semantic == "off"
    assert not semantic_enabled(Settings(_env_file=None))
    for raw, want in (("on", "on"), ("TRUE", "on"), ("1", "on"), ("off", "off"), ("", "off")):
        monkeypatch.setenv("GLDB_SEMANTIC", raw)
        assert Settings(_env_file=None).semantic == want
    monkeypatch.setenv("GLDB_SEMANTIC", "maybe")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_result_settings_unchanged_when_off_and_listed_when_on() -> None:
    off = Settings(_env_file=None).result_settings()
    assert not any(k.startswith("semantic") for k in off)
    on = settings_on().result_settings()
    assert on["semantic"] == "on" and "semantic_max_claims" in on
    assert {k: v for k, v in on.items() if not k.startswith("semantic")} == off


def test_nothing_is_registered_while_off() -> None:
    @semantic_check(rule=RULE, applies_to={"company_profile"}, description="probe")
    def probe(ctx):  # noqa: ANN001, ANN202
        return []

    try:
        off = Settings(_env_file=None)
        assert semantic_checks(off) == []
        assert [m.rule for m in all_checks(off)] == [m.rule for m in all_checks()]
        assert all(not is_semantic_rule(m.rule) for m in all_checks())
        ctx = make_ctx([profile()])
        assert RULE not in {m.rule for m in applicable(ctx)}
        runs = run_stage_checks(ctx, "rules")
        assert RULE not in {r.rule for r in runs}
        assert ctx.semantic_tally == {}
        on = settings_on()
        assert RULE in {m.rule for m in all_checks(on)}
        assert RULE in {m.rule for m in applicable(make_ctx([profile()], settings=on))}
    finally:
        unregister(RULE)


def test_semantic_rule_must_use_prefix_stage_and_severity() -> None:
    with pytest.raises(ValueError, match="must start with"):
        semantic_check(rule="not_semantic", applies_to={"*"}, description="x")
    with pytest.raises(ValueError, match="stage"):
        semantic_check(rule="sem_x", applies_to={"*"}, description="x", stage="forensics")
    with pytest.raises(ValueError, match="severity"):
        semantic_check(rule="sem_x", applies_to={"*"}, description="x", severity="high")


def test_discovery_imports_the_checks_package_and_get_check_finds_semantic_rules() -> None:
    discover_semantic()  # an empty package is fine; it must not raise
    assert get_check("sem_does_not_exist") is None


def test_cap_severity() -> None:
    assert [cap_severity(s) for s in ("info", "low", "medium", "high")] == [
        "info",
        "low",
        "medium",
        "medium",
    ]


# ------------------------------------------------------------------------------- the verifier


def test_verifier_keeps_a_claim_whose_quote_is_in_the_cell() -> None:
    v = verify([claim()])
    assert v.proposed == 1 and len(v.claims) == 1 and v.n_dropped == 0
    c = v.claims[0]
    assert c.citations[0].locator() == "d2 transactions[1]"
    assert c.evidence() == 'd2 transactions[1]: "CASH DEPOSIT OVERSEAS"'


def test_verifier_rejects_a_quote_that_is_not_in_the_data() -> None:
    bad = claim(
        citations=[
            {"document_id": "d2", "table": "transactions", "row": 1, "quote": "WIRE FROM SHELL CO"}
        ]
    )
    v = verify([bad])
    assert v.claims == [] and v.dropped["quote_not_found"] == 1


def test_verifier_rejects_a_quote_taken_from_another_row_or_field() -> None:
    other_row = claim(
        citations=[
            {
                "document_id": "d2",
                "table": "transactions",
                "row": 0,
                "quote": "CASH DEPOSIT OVERSEAS",
            }
        ]
    )
    other_field = claim(
        citations=[
            {"document_id": "d1", "field": "company_name", "quote": "Wholesale of electronics"}
        ]
    )
    v = verify([other_row, other_field])
    assert v.claims == [] and v.dropped["quote_not_found"] == 2


def test_verifier_rejects_unknown_document_field_table_row_column() -> None:
    cases = {
        "document_not_provided": {"document_id": "d9", "field": "x", "quote": "abc"},
        "unknown_field": {"document_id": "d1", "field": "nope", "quote": "abc"},
        "unknown_table": {"document_id": "d2", "table": "nope", "row": 0, "quote": "abc"},
        "unknown_row": {"document_id": "d2", "table": "transactions", "row": 7, "quote": "abc"},
        "unknown_column": {
            "document_id": "d2",
            "table": "transactions",
            "row": 0,
            "column": "nope",
            "quote": "abc",
        },
        "empty_quote": {"document_id": "d1", "field": "company_name", "quote": "   "},
        "bad_shape": {"document_id": "d1", "quote": "Acme"},  # neither a field nor a row
    }
    for reason, citation in cases.items():
        v = verify([claim(citations=[citation])])
        assert v.claims == [] and v.dropped[reason] == 1, reason


def test_a_document_the_model_was_not_shown_cannot_be_cited() -> None:
    v = verify([claim()], docs=[profile()])  # d2 was not shown
    assert v.claims == [] and v.dropped["document_not_provided"] == 1


def test_one_bad_citation_drops_the_whole_claim() -> None:
    good = {"document_id": "d2", "table": "transactions", "row": 1, "quote": "CASH DEPOSIT"}
    bad = {"document_id": "d1", "field": "company_name", "quote": "not there at all"}
    v = verify([claim(citations=[good, bad])])
    assert v.claims == [] and v.dropped["quote_not_found"] == 1


def test_claim_without_citation_wrong_kind_or_empty_statement_is_dropped() -> None:
    v = verify(
        [
            claim(citations=[]),
            claim(kind="invented_kind"),
            claim(statement="  "),
            "not a dict",
        ]  # type: ignore[list-item]
    )
    assert v.claims == []
    assert v.dropped["no_citation"] == 1
    assert v.dropped["bad_kind"] == 1
    assert v.dropped["bad_shape"] == 2


def test_quote_matching_is_case_space_and_width_tolerant_but_not_a_paraphrase() -> None:
    assert norm("  ＡＣＭＥ​  trading ") == "acme trading"
    ok = claim(
        citations=[{"document_id": "d1", "field": "company_name", "quote": "acme   TRADING"}]
    )
    para = claim(
        citations=[{"document_id": "d1", "field": "company_name", "quote": "Acme Trade Pte"}]
    )
    v = verify([ok, para])
    assert len(v.claims) == 1 and v.dropped["quote_not_found"] == 1


def test_short_quote_counts_only_when_it_is_the_whole_cell() -> None:
    doc = make_doc(
        "company_profile", id="d1", fields={"company_name": "Zed Pte Ltd", "status": "Live"}
    )
    whole = claim(citations=[{"document_id": "d1", "field": "status", "quote": "Live"}])
    inside = claim(citations=[{"document_id": "d1", "field": "company_name", "quote": "Z"}])
    v = verify([whole, inside], docs=[doc])
    assert len(v.claims) == 1 and v.dropped["quote_not_found"] == 1


def test_printed_form_of_a_number_is_accepted_as_well_as_the_value() -> None:
    doc = make_doc("bank_statement", id="d2", tables={"transactions": [{"description": "X"}]})
    cell = doc.tables["transactions"].rows[0].cells["credit"]
    doc.tables["transactions"].rows[0].cells["credit"] = cell.model_copy(
        update={"value": "1500.50", "raw": "1,500.50"}
    )
    both = [
        claim(
            citations=[
                {
                    "document_id": "d2",
                    "table": "transactions",
                    "row": 0,
                    "column": "credit",
                    "quote": q,
                }
            ]
        )
        for q in ("1500.50", "1,500.50")
    ]
    v = verify(both, docs=[doc])
    assert len(v.claims) == 2 and v.n_dropped == 0  # both the value and the printed form verify


def test_high_severity_from_the_model_is_capped_and_counted() -> None:
    v = verify([claim(severity="high")])
    assert v.claims[0].severity == "medium" and v.severity_capped == 1
    assert parse_note(v.note()) == {"proposed": 1, "kept": 1, "dropped": 0, "capped": 1}


def test_max_claims_and_duplicates() -> None:
    claims = [
        claim(citations=[{"document_id": "d2", "table": "transactions", "row": row, "quote": q}])
        for row, q in (
            (0, "TT FROM K LOGISTICS"),
            (1, "CASH DEPOSIT OVERSEAS"),
            (1, "CASH DEPOSIT"),
        )
    ]
    v = verify(claims + [copy.deepcopy(claims[1])], max_claims=2)
    assert len(v.claims) == 2
    assert v.dropped["duplicate"] == 1 and v.dropped["over_limit"] >= 1


def test_malformed_reply_is_a_counted_drop_not_an_exception() -> None:
    v = verify_claims({"claims": "oops"}, [profile()], kinds=KINDS)
    assert v.claims == [] and v.dropped["bad_shape"] == 1
    assert verify_claims({}, [profile()], kinds=KINDS).dropped["bad_shape"] == 1


def test_extra_attributes_come_back_but_are_not_evidence() -> None:
    schema = claims_json_schema(
        KINDS, extra_properties={"category": {"type": "string"}}, extra_required=["category"]
    )
    assert "category" in schema["properties"]["claims"]["items"]["properties"]
    v = verify_claims(
        {"claims": [claim(category="trade")]}, [statement()], kinds=KINDS, extra_keys=("category",)
    )
    assert v.claims[0].attrs == {"category": "trade"}


# ----------------------------------------------------------------------- the call wrapper


def fake_with(reply: dict[str, Any] | Exception, **kw: Any) -> FakeLLMClient:
    def answer(_req: LLMRequest) -> dict:
        if isinstance(reply, Exception):
            raise reply
        return reply

    return FakeLLMClient({f"{PURPOSE_PREFIX}unit": answer}, **kw)


def run(ctx, docs: list[Document] | None = None) -> Any:  # noqa: ANN001, ANN401
    return run_claims(
        ctx,
        rule=RULE,
        name="unit",
        task="Find descriptions that do not fit the declared activity.",
        docs=docs or [profile(), statement()],
        kinds=KINDS,
    )


def test_run_claims_calls_registers_verifies_and_tallies() -> None:
    ctx = make_ctx(
        [profile(), statement()], llm=fake_with({"claims": [claim(), claim(citations=[])]})
    )
    v = run(ctx)
    assert len(v.claims) == 1 and v.dropped["no_citation"] == 1
    assert PURPOSE_PREFIX + "unit" in prompt_registry()
    (req,) = ctx.llm.calls  # type: ignore[attr-defined]
    assert req.purpose == "semantic:unit" and req.model_role == "cheap"
    assert req.cache_extra.startswith("sem1:")
    assert "[[[DOC-" in req.user and "Find descriptions" in req.system
    assert "Find descriptions" not in req.user  # instructions never ride with document text
    assert "CASH DEPOSIT OVERSEAS" not in req.system
    assert ctx.semantic_tally[RULE].proposed == 2
    # cost recorded without the caller opening a scope
    assert ctx.costs.costs()[0].stage == "rules"


def test_prompt_hash_and_model_are_in_the_cache_key() -> None:
    from app.core.cache import cache_key

    def req_for(system: str) -> LLMRequest:
        return LLMRequest(
            purpose="semantic:keytest",
            system=system,
            user="u",
            schema_name="Claims",
            json_schema=claims_json_schema(KINDS),
        )

    a, b = req_for(system_prompt("task A")), req_for(system_prompt("task B"))
    assert cache_key(a, "m1", "1") != cache_key(b, "m1", "1")  # prompt text
    assert cache_key(a, "m1", "1") != cache_key(a, "m2", "1")  # model name


def test_second_identical_call_is_a_cache_hit() -> None:
    db = Database(":memory:")
    db.init_schema()
    cache = ResponseCache(db, enabled=True)
    llm = fake_with({"claims": []}, cache=cache)
    ctx = make_ctx([profile(), statement()], llm=llm)
    run(ctx)
    run(ctx)
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        {"claims": [claim()], "score": 0},  # extra key: the schema forbids it
        {"claims": "not a list"},
        {"nope": []},
        LLMTransportError("status 503"),
        TimeoutError("timed out"),
        RuntimeError("boom"),
    ],
)
def test_any_failed_or_malformed_call_is_not_run(failure: dict[str, Any] | Exception) -> None:
    ctx = make_ctx([profile(), statement()], llm=fake_with(failure))
    with pytest.raises(SemanticNotRun) as err:
        run(ctx)
    assert "boom" not in err.value.reason  # only the class name of a non-LLM error
    assert ctx.semantic_tally == {}


def test_no_fixture_and_exhausted_budget_are_not_run() -> None:
    with pytest.raises(SemanticNotRun, match="LLMNoFixture"):
        run(make_ctx([profile(), statement()], llm=FakeLLMClient()))
    ctx = make_ctx([profile(), statement()], llm=fake_with({"claims": []}))
    ctx.costs.limits = ctx.costs.limits.model_copy(update={"max_calls": 1})
    ctx.costs.reserve_call()
    with pytest.raises(SemanticNotRun, match="budget_exceeded"):
        run(ctx)


def test_call_model_returns_the_reply_for_non_claim_uses() -> None:
    ctx = make_ctx([profile()], llm=FakeLLMClient({"semantic:other": {"ok": True}}))
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    out = call_model(
        ctx,
        name="other",
        system="S",
        user="U",
        schema_name="Ok",
        json_schema=schema,
        stage="crossdoc",
    )
    assert out == {"ok": True}
    assert ctx.costs.costs()[0].stage == "crossdoc"


# ---------------------------------------------------------------- flags and the check runner


@pytest.fixture
def probe() -> Iterator[None]:
    @semantic_check(rule=RULE, applies_to={"bank_statement"}, description="probe", severity="low")
    def sem_unit_probe(ctx):  # noqa: ANN001, ANN202
        docs = ctx.docs("bank_statement")
        v = run(ctx, docs)
        return [make_semantic_flag(RULE, c, docs) for c in v.claims]

    yield
    unregister(RULE)


def test_flag_from_a_verified_claim_is_medium_at_most_with_rebuilt_evidence(probe: None) -> None:
    reply = {"claims": [claim(severity="high", statement="Looks odd.")]}
    ctx = make_ctx([statement()], settings=settings_on(), llm=fake_with(reply))
    (run_,) = [r for r in run_stage_checks(ctx, "rules") if r.rule == RULE]
    assert run_.status == "ran" and run_.flags == 1
    assert parse_note(run_.reason) == {"proposed": 1, "kept": 1, "dropped": 0, "capped": 1}
    (flag,) = [f for f in ctx.case.flags if f.rule == RULE]
    assert flag.severity == "medium" and flag.stage == "rules"
    assert flag.evidence == 'd2 transactions[1]: "CASH DEPOSIT OVERSEAS"'
    assert flag.explanation.startswith("Model-derived signal")
    assert flag.table == "transactions" and flag.row == 1


def test_catalogue_override_cannot_lift_a_semantic_flag_above_medium(probe: None) -> None:
    from app.core.models import RuleOverride

    ctx = make_ctx([statement()], settings=settings_on(), llm=fake_with({"claims": [claim()]}))
    ctx.overrides = {
        RULE: RuleOverride(
            enabled=True,
            severity="high",
            reason="make it loud",
            updated_by="tester",
            updated_at="2026-10-27T09:00:00Z",
            revision=1,
        )
    }
    run_stage_checks(ctx, "rules")
    (flag,) = [f for f in ctx.case.flags if f.rule == RULE]
    assert flag.severity == "medium"


def test_a_failed_call_is_an_errored_check_so_the_case_is_partial(probe: None) -> None:
    ctx = make_ctx([statement()], settings=settings_on(), llm=fake_with(LLMTransportError("x")))
    run_stage_checks(ctx, "rules")
    (r,) = [r for r in ctx.case.check_runs if r.rule == RULE]
    assert r.status == "error" and "SemanticNotRun" in (r.reason or "")
    assert not [f for f in ctx.case.flags if f.rule == RULE]
    run_stage(ctx)
    assert ctx.case.score is not None and ctx.case.score.partial
    assert f"check_error:{RULE}" in ctx.case.score.partial_reasons
    assert ctx.case.coverage is not None and ctx.case.coverage.ratio < 1.0
    assert ctx.case.lane != "fast"


def test_a_check_may_skip_when_there_is_nothing_to_analyse(probe: None) -> None:
    @semantic_check(rule="sem_unit_skip", applies_to={"bank_statement"}, description="skip")
    def sem_unit_skip(ctx):  # noqa: ANN001, ANN202
        raise CheckSkipped("no transactions")

    try:
        ctx = make_ctx([statement()], settings=settings_on(), llm=fake_with({"claims": []}))
        run_stage_checks(ctx, "rules")
        r = next(r for r in ctx.case.check_runs if r.rule == "sem_unit_skip")
        assert r.status == "skipped"
    finally:
        unregister("sem_unit_skip")


# ------------------------------------------------------------------- lane and score rules


def finding(rule: str, weight: int, severity: str = "medium") -> Finding:
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


def fast_case(ratio_runs: list[tuple[str, str]], findings: list[Finding] | None = None):  # noqa: ANN202
    from app.core.models import CheckRun

    ctx = make_ctx([statement()])
    case = ctx.case
    case.check_runs = [CheckRun(rule=r, stage="rules", status=s) for r, s in ratio_runs]  # type: ignore[arg-type]
    case.findings = findings or []
    ran = sum(1 for _, s in ratio_runs if s == "ran")
    case.coverage = Coverage(applicable=len(ratio_runs), ran=ran, ratio=ran / len(ratio_runs))
    case.score = Score(value=sum(f.weight for f in case.findings), band="low")
    return case


def test_a_semantic_check_that_ran_cannot_lift_coverage_over_the_fast_lane_bar() -> None:
    base = [("a", "ran"), ("b", "ran"), ("c", "ran"), ("d", "ran"), ("e", "skipped")]  # 0.8 -> ok
    assert fast_lane_eligible(fast_case(base))
    weak = [("a", "ran"), ("b", "ran"), ("c", "ran"), ("d", "skipped"), ("e", "skipped")]  # 0.6
    assert not fast_lane_eligible(fast_case(weak))
    with_sem = weak + [(f"sem_x{i}", "ran") for i in range(6)]  # overall 9/11 = 0.82
    case = fast_case(with_sem)
    assert case.coverage is not None and case.coverage.ratio > 0.8
    assert not fast_lane_eligible(case) and lane_for(case) != "fast"


def _coverage_of(runs: list[tuple[str, str]]) -> Coverage:
    from app.score import compute_coverage

    case = fast_case(runs)
    return compute_coverage(case)


def test_a_semantic_run_or_skip_leaves_the_displayed_coverage_as_it_is_with_the_feature_off() -> (
    None
):
    base = [("a", "ran"), ("b", "ran"), ("c", "skipped")]
    off = _coverage_of(base)
    on = _coverage_of(base + [("sem_x", "ran"), ("sem_y", "skipped"), ("sem_z", "ran")])
    assert (on.applicable, on.ran, on.ratio, on.skipped) == (
        off.applicable,
        off.ran,
        off.ratio,
        off.skipped,
    )


def test_a_failed_semantic_check_still_lowers_the_displayed_coverage() -> None:
    base = [("a", "ran"), ("b", "ran")]
    failed = _coverage_of(base + [("sem_x", "error")])
    assert failed.ratio < _coverage_of(base).ratio
    assert [d["rule"] for d in failed.skipped] == ["sem_x"]


def test_semantic_runs_do_not_hold_back_a_case_that_would_be_fast_anyway() -> None:
    runs = [("a", "ran"), ("b", "ran"), ("c", "ran"), ("d", "ran"), ("sem_x", "skipped")]
    assert fast_lane_eligible(fast_case(runs))


def test_a_semantic_finding_keeps_a_low_score_case_out_of_the_fast_lane() -> None:
    runs = [("a", "ran"), ("b", "ran"), ("sem_x", "ran")]
    assert fast_lane_eligible(fast_case(runs))
    assert not fast_lane_eligible(fast_case(runs, [finding("sem_x", 15)]))
    # a non-semantic finding of the same weight does not close the fast lane on its own
    assert fast_lane_eligible(fast_case(runs, [finding("bs_totals", 15)]))


def test_semantic_flags_only_add_to_the_score(probe: None) -> None:
    def scored(llm: FakeLLMClient, settings: Settings | None) -> Score:
        ctx = make_ctx([statement()], llm=llm, **({"settings": settings} if settings else {}))
        run_stage_checks(ctx, "rules")
        run_stage(ctx)
        assert ctx.case.score is not None
        return ctx.case.score

    off = scored(fake_with({"claims": [claim()]}), None)
    on = scored(fake_with({"claims": [claim()]}), settings_on())
    on_empty = scored(fake_with({"claims": []}), settings_on())
    assert on.value >= off.value and on_empty.value == off.value
    assert on.value - off.value == 15  # one medium finding, nothing else changed


def test_flag_type_is_a_plain_flag(probe: None) -> None:
    ctx = make_ctx([statement()], settings=settings_on(), llm=fake_with({"claims": [claim()]}))
    run_stage_checks(ctx, "rules")
    assert all(isinstance(f, Flag) for f in ctx.case.flags)
