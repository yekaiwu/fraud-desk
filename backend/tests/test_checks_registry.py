"""Check framework (registry, make_flag, runner) and the walking-skeleton check bs_running_balance."""

from __future__ import annotations

from decimal import Decimal

import pytest

import app.checks.registry as reg
from app.checks.registry import (
    CheckSkipped,
    all_checks,
    applicable,
    check,
    discover,
    make_flag,
    run_stage_checks,
)
from app.core.models import Flag, Region
from app.core.testing import make_ctx, make_doc


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """An isolated registry so test checks never leak into the real one."""
    monkeypatch.setattr(reg, "_REGISTRY", dict(reg._REGISTRY))
    discover()
    return reg._REGISTRY


# ---------------------------------------------------------------- make_flag


def test_make_flag_id_format() -> None:
    d3 = make_doc("bank_statement", id="d3")
    flag = make_flag(
        "bs_running_balance", docs=[d3], table="transactions", row=17, evidence="e", explanation="x"
    )
    assert flag.id == "bs_running_balance.d3.transactions:17"
    assert flag.rule == "bs_running_balance" and flag.stage == "rules" and flag.severity == "high"
    assert flag.document_ids == ["d3"] and flag.table == "transactions" and flag.row == 17


def test_make_flag_locators_docs_and_discriminator() -> None:
    f = make_flag(
        "x_rule",
        docs=["d12", "d9"],
        field="closing_balance",
        evidence="e",
        explanation="x",
        severity="medium",
    )
    assert f.id == "x_rule.d9+d12.closing_balance"  # natural order, not lexicographic
    assert f.severity == "medium" and f.document_ids == ["d9", "d12"]
    assert (
        make_flag("x_rule", docs=["d1"], evidence="e", explanation="x", severity="low").id
        == "x_rule.d1.-"
    )
    # field wins over table/row; row 0 is a valid locator
    assert (
        make_flag(
            "x_rule", docs=["d1"], table="t", row=0, evidence="e", explanation="x", severity="low"
        ).id
        == "x_rule.d1.t:0"
    )
    assert (
        make_flag("x_rule", docs=[], evidence="e", explanation="x", severity="info").id
        == "x_rule..-"
    )
    d = make_flag(
        "x_rule",
        docs=["d1"],
        evidence="e",
        explanation="x",
        severity="low",
        discriminator="Tan Wei.Ming~#2/é",
    )
    assert d.id == "x_rule.d1.-~tan_wei_ming__2__"
    assert d.id.count(".") == 2 and d.id.count("~") == 1 and "#" not in d.id
    ok = make_flag(
        "x_rule",
        docs=["d1"],
        evidence="e",
        explanation="x",
        severity="low",
        discriminator="store:c_abc123",
    )
    assert ok.id.endswith("~store:c_abc123")
    assert (
        make_flag(
            "x_rule", docs=["d1"], field="a.b", evidence="e", explanation="x", severity="low"
        ).id
        == "x_rule.d1.a_b"
    )


def test_make_flag_requires_severity_for_unregistered_rule() -> None:
    with pytest.raises(ValueError):
        make_flag("not_registered_anywhere", docs=["d1"], evidence="e", explanation="x")


def test_make_flag_truncates_and_keeps_regions() -> None:
    r1 = Region(file_id="f1", page=1, x=0.1, y=0.1, w=0.1, h=0.1)
    f = make_flag(
        "x_rule",
        docs=["d1"],
        evidence="e" * 2000,
        explanation="x" * 400,
        severity="low",
        region=r1,
        extra_regions=[r1],
    )
    assert len(f.evidence) == 1000 and len(f.explanation) == 300
    assert f.region == r1 and f.extra_regions == [r1]


# ---------------------------------------------------------------- discovery and runner


def test_discovery_finds_walking_skeleton_and_builtins() -> None:
    discover()
    discover()  # idempotent
    rules = {m.rule: m for m in all_checks()}
    assert {"bs_running_balance", "doc_unreadable", "doc_unclassified"} <= set(rules)
    meta = rules["bs_running_balance"]
    assert meta.stage == "rules" and meta.severity == "high"
    assert meta.applies_to == frozenset({"bank_statement"}) and meta.use_cases == {
        "kyc",
        "aml",
        "loan",
    }
    assert meta.description and meta.tamper
    assert "func" not in meta.model_dump()  # excluded from serialisation
    ordered = [(m.stage, m.rule) for m in all_checks()]
    stage_rank = {"forensics": 0, "rules": 1, "crossdoc": 2}
    assert ordered == sorted(ordered, key=lambda t: (stage_rank[t[0]], t[1]))


def test_duplicate_rule_from_another_function_is_rejected(scratch_registry) -> None:  # noqa: ANN001
    def a(ctx):  # noqa: ANN001, ANN202
        return []

    def b(ctx):  # noqa: ANN001, ANN202
        return []

    check(rule="dup_rule", applies_to={"invoice"}, severity="low", description="d")(a)
    with pytest.raises(ValueError):
        check(rule="dup_rule", applies_to={"invoice"}, severity="low", description="d")(b)


def test_broken_check_module_is_reraised_with_its_name(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import app.checks as package

    (tmp_path / "zz_broken.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.setattr(package, "__path__", [*package.__path__, str(tmp_path)])
    with pytest.raises(ImportError, match="app.checks.zz_broken"):
        discover()


def test_applicable_filters_by_use_case(scratch_registry) -> None:  # noqa: ANN001
    check(
        rule="loan_only_t",
        applies_to={"invoice"},
        severity="low",
        description="d",
        use_cases={"loan"},
    )(lambda ctx: [])
    assert "loan_only_t" in {m.rule for m in applicable(make_ctx(use_case="loan"))}
    assert "loan_only_t" not in {m.rule for m in applicable(make_ctx(use_case="kyc"))}


def test_applicable_includes_out_of_scope_check_when_its_document_is_present(
    scratch_registry,  # noqa: ANN001
) -> None:
    """Silence is not safety: a check whose document was uploaded runs whatever the use case."""
    check(
        rule="loan_only_t2",
        applies_to={"invoice"},
        severity="low",
        description="d",
        use_cases={"loan"},
    )(lambda ctx: [])
    ctx = make_ctx([make_doc("invoice", id="d1")], use_case="kyc")
    assert "loan_only_t2" in {m.rule for m in applicable(ctx)}
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    assert runs["loan_only_t2"].status == "ran"
    # an unusable document does not pull the check in
    failed = make_ctx([make_doc("invoice", id="d1", status="failed")], use_case="kyc")
    assert "loan_only_t2" not in {m.rule for m in applicable(failed)}


def test_runner_semantics(scratch_registry) -> None:  # noqa: ANN001
    invoice = make_doc("invoice", id="d1")
    stmt = make_doc("bank_statement", id="d2")
    failed = make_doc("bank_statement", id="d3", status="failed")

    def flagging(ctx):  # noqa: ANN001, ANN202
        return [
            make_flag("t_flags", docs=["d1"], evidence="e", explanation="x"),
            make_flag("t_flags", docs=["d1"], evidence="e2", explanation="x"),
        ]  # same id twice

    def quiet(ctx):  # noqa: ANN001, ANN202
        return []

    def skips(ctx):  # noqa: ANN001, ANN202
        raise CheckSkipped("currency mismatch")

    def crashes(ctx):  # noqa: ANN001, ANN202
        raise KeyError("secret internal detail")

    ok = dict(applies_to={"invoice"}, severity="medium", description="d")
    check(rule="t_flags", **ok)(flagging)
    check(rule="t_quiet", **ok)(quiet)
    check(rule="t_skips", **ok)(skips)
    check(rule="t_crashes", **ok)(crashes)
    check(rule="t_anyof", applies_to={"invoice", "contract"}, severity="low", description="d")(
        quiet
    )
    check(
        rule="t_allof",
        applies_to={"invoice"},
        requires={"bank_statement", "contract"},
        severity="low",
        description="d",
    )(quiet)
    check(rule="t_nodoc", applies_to={"purchase_order"}, severity="low", description="d")(quiet)
    check(rule="t_star", applies_to={"*"}, severity="info", description="d")(quiet)
    check(
        rule="t_cross", applies_to={"invoice"}, stage="crossdoc", severity="low", description="d"
    )(quiet)

    ctx = make_ctx([invoice, stmt, failed])
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    assert runs["t_flags"].status == "ran" and runs["t_flags"].flags == 2
    assert runs["t_quiet"].status == "ran" and runs["t_quiet"].flags == 0  # [] still counts as ran
    assert runs["t_skips"].status == "skipped" and runs["t_skips"].reason == "currency mismatch"
    assert runs["t_crashes"].status == "error"
    assert "KeyError" in (runs["t_crashes"].reason or "") and "Traceback" not in (
        runs["t_crashes"].reason or ""
    )
    assert runs["t_anyof"].status == "ran"  # any-of: invoice present, contract absent
    assert runs["t_allof"].status == "skipped" and "contract" in (runs["t_allof"].reason or "")
    assert runs["t_nodoc"].status == "skipped"
    assert runs["t_star"].status == "ran"
    assert "t_cross" not in runs  # other stage
    assert [f.id for f in ctx.case.flags if f.rule == "t_flags"] == [
        "t_flags.d1.-",
        "t_flags.d1.-#2",
    ]
    assert all(f.stage == "rules" for f in ctx.case.flags)
    assert len(ctx.case.check_runs) == len([r for r in runs.values()])

    # idempotent: a second run replaces, never appends
    n_flags, n_runs = len(ctx.case.flags), len(ctx.case.check_runs)
    run_stage_checks(ctx, "rules")
    assert (len(ctx.case.flags), len(ctx.case.check_runs)) == (n_flags, n_runs)
    run_stage_checks(ctx, "crossdoc")
    crossdoc = {r.rule for r in ctx.case.check_runs if r.stage == "crossdoc"}
    assert "t_cross" in crossdoc  # the scratch crossdoc check ran (real crossdoc rules may too)
    assert len(ctx.case.check_runs) == n_runs + len(crossdoc)  # rules runs kept


def test_failed_documents_do_not_satisfy_requirements(scratch_registry) -> None:  # noqa: ANN001
    check(rule="t_needs_stmt", applies_to={"bank_statement"}, severity="low", description="d")(
        lambda c: []
    )
    ctx = make_ctx(
        [
            make_doc("bank_statement", id="d1", status="failed"),
            make_doc("bank_statement", id="d2", status="received_not_checked"),
        ]
    )
    run = {r.rule: r for r in run_stage_checks(ctx, "rules")}["t_needs_stmt"]
    assert run.status == "skipped" and "usable" in (run.reason or "")


def test_star_check_needs_some_file_or_document(scratch_registry) -> None:  # noqa: ANN001
    check(rule="t_star2", applies_to={"*"}, severity="info", description="d")(lambda c: [])
    runs = {r.rule: r for r in run_stage_checks(make_ctx(), "rules")}
    assert runs["t_star2"].status == "skipped"


def test_coverage_is_ran_over_applicable_for_the_case(scratch_registry) -> None:  # noqa: ANN001
    ctx = make_ctx([make_doc("invoice", id="d1")], use_case="loan")
    run_stage_checks(ctx, "rules")
    runs = ctx.case.check_runs
    assert len(runs) == len([m for m in applicable(ctx) if m.stage == "rules"])
    assert any(r.rule == "bs_running_balance" and r.status == "skipped" for r in runs)


# ---------------------------------------------------------------- bs_running_balance


def tx(date: str, debit=None, credit=None, balance=None) -> dict:  # noqa: ANN001
    return {"date": date, "description": "x", "debit": debit, "credit": credit, "balance": balance}


def statement(rows: list[dict], *, opening: str | None = "10000.00", id: str = "d3", boxes=False):  # noqa: ANN001, ANN201, A002
    doc = make_doc(
        "bank_statement", id=id, fields={"opening_balance": opening}, tables={"transactions": rows}
    )
    if boxes:
        for r in doc.tables["transactions"].rows:
            for name, cell in r.cells.items():
                cell.box = Region(
                    file_id="f1", page=1, x=0.1, y=0.3 + 0.02 * r.index, w=0.1, h=0.015, label=name
                )
    return doc


F_BS_1 = [
    tx("2026-03-01", credit="8000.00", balance="18000.00"),
    tx("2026-03-02", debit="12.50", balance="17987.50"),
    tx("2026-03-03", credit="500.00", balance="18487.50"),
    tx("2026-03-04", debit="87.50", balance="18400.00"),
]


def run(doc, **kw):  # noqa: ANN001, ANN003, ANN201
    from app.checks.bank_statement import bs_running_balance

    return bs_running_balance(make_ctx([doc], **kw))


def test_running_balance_clean() -> None:
    assert run(statement(F_BS_1)) == []


def test_running_balance_one_edited_amount_gives_one_flag() -> None:
    rows = [dict(r) for r in F_BS_1]
    rows[2]["credit"] = "1500.00"  # balances left alone
    (flag,) = run(statement(rows, boxes=True))
    assert flag.id == "bs_running_balance.d3.transactions:2"
    assert flag.row == 2 and flag.table == "transactions" and flag.severity == "high"
    assert flag.evidence.startswith(
        "previous 17987.50 + credit 1500.00 = 19487.50, printed 18487.50; diff 1000.00"
    )
    assert (
        flag.explanation
        == "The balance on row 3 is 1000.00 lower than the previous balance plus the transaction amounts."
    )
    assert flag.region is not None and flag.region.label == "balance"
    assert {r.label for r in flag.extra_regions} == {"credit", "previous balance"}
    assert "later row" not in flag.evidence  # later balances are anchored on the printed one


def test_running_balance_debit_form_and_later_breaks() -> None:
    rows = [dict(r) for r in F_BS_1]
    rows[1]["balance"] = "19987.50"  # edited balance: row 2 breaks
    rows[2]["balance"] = "18487.50"  # row 3 now breaks too (19987.50 + 500 != 18487.50)
    (flag,) = run(statement(rows))
    assert flag.row == 1
    assert flag.evidence == (
        "previous 18000.00 - debit 12.50 = 17987.50, printed 19987.50; diff 2000.00; "
        "1 later row(s) also break"
    )
    assert "higher" in flag.explanation


def test_running_balance_reports_first_break_per_document() -> None:
    rows = [dict(r) for r in F_BS_1]
    rows[0]["balance"] = "99999.00"
    rows[3]["balance"] = "1.00"
    flags = run(statement(rows))
    assert len(flags) == 1 and flags[0].row == 0
    assert (
        "2 later row(s) also break" in flags[0].evidence
    )  # rows 2 and 4 (row 3 follows the print)
    one_each = run_two(rows)
    assert [f.document_ids for f in one_each] == [["d3"], ["d4"]]


def run_two(rows):  # noqa: ANN001, ANN201
    from app.checks.bank_statement import bs_running_balance

    return bs_running_balance(make_ctx([statement(rows), statement(rows, id="d4")]))


def test_running_balance_tolerance_boundary() -> None:
    rows = [dict(r) for r in F_BS_1]
    rows[0]["balance"] = "18001.00"  # off by exactly 1.00: passes
    assert run(statement(rows)) == []
    rows[0]["balance"] = "18001.01"
    assert len(run(statement(rows))) == 1
    ctx_tol = make_ctx(
        [statement(rows)],
        settings=__import__("app.core.config", fromlist=["Settings"]).Settings(
            _env_file=None, money_tolerance=Decimal("5.00"), llm_backend="fake"
        ),
    )
    from app.checks.bank_statement import bs_running_balance

    assert bs_running_balance(ctx_tol) == []  # tolerance is a setting


def test_running_balance_balance_only_day_end_quirk() -> None:
    rows = [
        tx("2026-03-01", credit="8000.00", balance="18000.00"),
        tx("2026-03-02", debit="12.50", balance=None),
        tx("2026-03-02", credit="3800.00", balance="21787.50"),
    ]
    assert run(statement(rows)) == []  # honest quirk: balance only on the last row of the day
    rows[2]["balance"] = "22787.50"
    (flag,) = run(statement(rows))
    assert flag.row == 2
    assert flag.evidence.startswith(
        "anchor row 1 balance 18000.00 + net 3787.50 since row 2 = 21787.50, printed 22787.50"
    )


def test_running_balance_brought_forward_row_and_overdraft_quirk() -> None:
    rows = [
        tx("2026-03-01", balance="10000.00"),  # BALANCE B/F: no debit or credit
        tx("2026-03-02", debit="10120.50", balance="-120.50"),
        tx("2026-03-03", credit="20.50", balance="-100.00"),
    ]
    assert run(statement(rows)) == []
    rows[0]["balance"] = "10500.00"
    (flag,) = run(statement(rows))
    assert flag.row == 0 and flag.evidence.startswith(
        "previous 10000.00 + 0.00 = 10000.00, printed 10500.00"
    )


def test_running_balance_rows_with_both_amounts_are_skipped_but_anchor() -> None:
    rows = [
        tx("2026-03-01", debit="5.00", credit="5.00", balance="777.00"),  # invalid row: new anchor
        tx("2026-03-02", credit="3.00", balance="780.00"),
    ]
    assert run(statement(rows)) == []
    rows[1]["balance"] = "900.00"
    (flag,) = run(statement(rows))
    assert flag.row == 1 and flag.evidence.startswith("previous 777.00 + credit 3.00 = 780.00")


def test_running_balance_forger_who_recomputes_everything_is_a_known_miss() -> None:
    rows = [
        tx("2026-03-01", credit="18000.00", balance="28000.00"),  # amount edited ...
        tx(
            "2026-03-02", debit="12.50", balance="27987.50"
        ),  # ... and all later balances recomputed
    ]
    assert run(statement(rows)) == []


def test_running_balance_skips_when_unevaluable() -> None:
    from app.checks.bank_statement import bs_running_balance

    for doc in (
        statement([]),
        statement(F_BS_1, opening=None),
        statement([tx("2026-03-01", credit="1.00")]),
    ):  # no balance column values
        with pytest.raises(CheckSkipped):
            bs_running_balance(make_ctx([doc]))


def test_running_balance_unverified_reading_is_appended() -> None:
    rows = [dict(r) for r in F_BS_1]
    rows[2]["credit"] = "1500.00"
    doc = statement(rows)
    doc.tables["transactions"].rows[2].cells["credit"].verified = False
    (flag,) = run(doc)
    assert flag.evidence.endswith("; unverified reading: credit")


def test_running_balance_through_the_runner_sets_stage_and_rule() -> None:
    rows = [dict(r) for r in F_BS_1]
    rows[2]["credit"] = "1500.00"
    ctx = make_ctx([statement(rows)])
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    assert runs["bs_running_balance"].status == "ran" and runs["bs_running_balance"].flags == 1
    (flag,) = [f for f in ctx.case.flags if f.rule == "bs_running_balance"]
    assert isinstance(flag, Flag) and flag.stage == "rules"
