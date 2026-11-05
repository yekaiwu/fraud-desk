"""The review team (app/agents): who owns what, the challenger's asymmetry, failure handling."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents import challenger, officer, run_team
from app.agents.roster import ROLES, is_arguable, owner
from app.core.config import Settings
from app.core.errors import LLMError
from app.core.llm import LLMRequest, LLMResult
from app.core.models import Cost, Flag
from app.core.testing import make_ctx, make_doc


class StubLLM:
    """Answers by purpose; a value that is an Exception is raised."""

    def __init__(self, answers: dict[str, Any]):
        self.answers = answers
        self.calls: list[LLMRequest] = []

    def complete_json(self, req: LLMRequest, *, ctx_costs: Any = None) -> LLMResult:
        self.calls.append(req)
        ans = self.answers[req.purpose]
        if isinstance(ans, Exception):
            raise ans
        return LLMResult(
            data=ans,
            model="stub",
            cost=Cost(stage="crossdoc", purpose=req.purpose, model="stub"),
        )


def flag(
    fid: str, rule: str, docs: list[str], severity: str = "high", stage: str = "crossdoc"
) -> Flag:
    return Flag(
        id=fid,
        rule=rule,
        stage=stage,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        document_ids=docs,
        evidence="director Tan Wei Mina",
        explanation="Director name differs.",
    )


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, llm_backend="fake", db_path=":memory:", agents=True, **kw)


def ctx_with(flags: list[Flag], llm: StubLLM, **kw: Any):  # noqa: ANN202
    docs = [
        make_doc(
            "bank_statement", id="d1", file_id="f1", fields={"account_holder": "Tan Wei Ming"}
        ),
        make_doc("invoice", id="d2", file_id="f2", fields={"buyer_name": "Tan Wei Mina"}),
    ]
    return make_ctx(docs, flags=flags, llm=llm, settings=kw.pop("settings", settings()), **kw)


GOOD_QUOTE = {
    "verdicts": [
        {
            "flag_id": "F1",
            "verdict": "doubtful",
            "reason": "The statement prints the name correctly.",
            "document_id": "d1",
            "quote": "Tan Wei Ming",
        }
    ]
}
BRIEFING = {
    "summary": "One thing stands out.",
    "next_steps": ["Ask for the ID [F1, zz]"],
}


def test_every_rule_has_one_owner_and_every_agent_a_role() -> None:
    assert owner("pdf_modified", "forensics") == "documents"
    assert owner("bs_totals", "rules") == "finance"
    assert owner("passport_mrz", "rules") == "identity"
    assert owner("sem_entity_link", "crossdoc") == "relationships"
    assert set(ROLES) == {
        "documents",
        "finance",
        "identity",
        "relationships",
        "challenger",
        "case_officer",
    }


def test_only_judgement_flags_are_arguable() -> None:
    assert is_arguable("x_directors") and is_arguable("sem_coherence_fit")
    assert not is_arguable("bs_running_balance") and not is_arguable("inv_total")
    assert not is_arguable("pdf_text_vs_render")


def test_a_verified_quote_lowers_the_flag_and_keeps_it_visible() -> None:
    llm = StubLLM({"agent:challenger": GOOD_QUOTE, "agent:case_officer": BRIEFING})
    ctx = ctx_with([flag("a", "x_directors", ["d1"])], llm)
    out = run_team(ctx)
    f = ctx.case.flags[0]
    assert f.severity == "info" and f.explanation.startswith(challenger.NOTE)
    assert f.code_severity is None
    assert out["lowered"] == 1
    team = {r.agent: r for r in ctx.case.agents}
    assert team["challenger"].verdicts[0].action == "lowered"
    assert team["challenger"].verdicts[0].quote == "Tan Wei Ming"
    assert team["identity"].flag_ids == ["a"] and "1 flag" in team["identity"].handoff


def test_a_quote_that_is_not_in_the_document_changes_nothing() -> None:
    bad = {"verdicts": [{**GOOD_QUOTE["verdicts"][0], "quote": "Tan Wei Ming Junior"}]}
    llm = StubLLM({"agent:challenger": bad, "agent:case_officer": BRIEFING})
    ctx = ctx_with([flag("a", "x_directors", ["d1"])], llm)
    run_team(ctx)
    assert ctx.case.flags[0].severity == "high"
    assert ctx.case.agents[4].verdicts[0].action == "kept"


def test_a_quote_from_another_document_than_named_is_not_accepted() -> None:
    wrong_doc = {"verdicts": [{**GOOD_QUOTE["verdicts"][0], "document_id": "d2"}]}
    llm = StubLLM({"agent:challenger": wrong_doc, "agent:case_officer": BRIEFING})
    ctx = ctx_with([flag("a", "x_directors", ["d1"])], llm)
    run_team(ctx)
    assert ctx.case.flags[0].severity == "high"


def test_arithmetic_and_forensic_flags_never_reach_the_challenger() -> None:
    llm = StubLLM({"agent:case_officer": BRIEFING})
    ctx = ctx_with(
        [
            flag("a", "bs_totals", ["d1"], stage="rules"),
            flag("b", "pdf_text_vs_render", ["d1"], stage="forensics"),
        ],
        llm,
    )
    run_team(ctx)
    assert [c.purpose for c in llm.calls] == ["agent:case_officer"]
    assert {f.severity for f in ctx.case.flags} == {"high"}
    assert next(r for r in ctx.case.agents if r.agent == "challenger").status == "skipped"


def test_a_failed_challenger_leaves_every_flag_and_says_so() -> None:
    llm = StubLLM({"agent:challenger": LLMError("boom"), "agent:case_officer": BRIEFING})
    ctx = ctx_with([flag("a", "x_directors", ["d1"])], llm)
    out = run_team(ctx)
    assert ctx.case.flags[0].severity == "high"
    assert "agents_unavailable: challenger" in str(out["warning"])
    assert next(r for r in ctx.case.agents if r.agent == "challenger").status == "failed"


def test_a_clean_case_makes_no_model_call() -> None:
    llm = StubLLM({})
    ctx = ctx_with([flag("a", "bs_totals", ["d1"], severity="low", stage="rules")], llm)
    run_team(ctx)
    assert llm.calls == []
    assert next(r for r in ctx.case.agents if r.agent == "case_officer").status == "skipped"


def test_officer_citations_are_checked_against_real_flag_ids() -> None:
    llm = StubLLM({"agent:challenger": GOOD_QUOTE, "agent:case_officer": BRIEFING})
    ctx = ctx_with(
        [flag("a", "x_directors", ["d1"]), flag("b", "bs_totals", ["d1"], stage="rules")], llm
    )
    run_team(ctx)
    co = next(r for r in ctx.case.agents if r.agent == "case_officer")
    assert co.next_steps == ["Ask for the ID (bs_totals)"]
    assert co.flag_ids == ["b"]  # a was lowered, b stands
    assert officer.open_flags(ctx)[0].id == "b"


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        (
            "Ask for the statement [March 2024] [F1]",
            "Ask for the statement [March 2024] (bs_totals)",
        ),
        ("Ask for the ID [F1, zz]", "Ask for the ID (bs_totals)"),
        ("Ask for the ID [F1, F2]", "Ask for the ID (bs_totals, x_directors)"),
        ("Ask for the ID [F2, zz].", "Ask for the ID. (x_directors)"),
        ("Ask for the [Q1 2024] statement", "Ask for the [Q1 2024] statement"),
        ("Check the [March 2024] statement [F1]", "Check the [March 2024] statement (bs_totals)"),
        ("Compare [F1] with the invoice", "Compare with the invoice (bs_totals)"),
        ("Ask for [the original] statement", "Ask for [the original] statement"),
        ("Ask for the ID [F1] [F2]", "Ask for the ID (bs_totals, x_directors)"),
    ],
)
def test_only_flag_alias_brackets_are_removed_from_a_next_step(step: str, expected: str) -> None:
    flags = [flag("a", "bs_totals", ["d1"], stage="rules"), flag("b", "x_directors", ["d1"])]
    llm = StubLLM(
        {"agent:case_officer": {"summary": "One thing stands out.", "next_steps": [step]}}
    )
    ctx = ctx_with(flags, llm)
    _, steps = officer.brief(ctx, [])
    assert steps == [expected]


def test_off_means_nothing_runs_and_the_json_is_unchanged() -> None:
    llm = StubLLM({})
    ctx = ctx_with(
        [flag("a", "x_directors", ["d1"])],
        llm,
        settings=settings().model_copy(update={"agents": False}),
    )
    assert run_team(ctx) == {}
    assert ctx.case.agents == [] and "agents" not in ctx.case.model_dump(mode="json")


def test_default_is_on_only_with_a_real_model() -> None:
    assert Settings(_env_file=None, llm_backend="fake", db_path=":memory:").agents is False
    s = Settings(_env_file=None, llm_backend="anthropic", anthropic_api_key="k", db_path=":memory:")
    assert s.agents is True


@pytest.mark.parametrize("n", [0])
def test_no_flags_no_calls(n: int) -> None:
    llm = StubLLM({})
    ctx = ctx_with([], llm)
    run_team(ctx)
    assert llm.calls == [] and len(ctx.case.agents) == 6


def test_schemas_accept_what_the_model_actually_sends() -> None:
    from app.core.llm import validate_json_schema

    upheld = {
        "verdicts": [
            {
                "flag_id": "F1",
                "verdict": "upheld",
                "reason": "r",
                "document_id": None,
                "quote": None,
            }
        ]
    }
    assert validate_json_schema(upheld, challenger.SCHEMA) is None
    assert validate_json_schema({"summary": "s"}, officer.SCHEMA) is None  # no steps is fine
    assert (
        validate_json_schema({"next_steps": []}, officer.SCHEMA) is not None
    )  # a summary is not optional
