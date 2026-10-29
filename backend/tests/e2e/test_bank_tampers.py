"""T-integration-loan-06: the Phase-1 gate. Bank statement tampers built through
``BundleSpec.mutations`` (world channel: clean typography, no forensic trace) are caught end to end
with the right rule, and the flag points at the edited cell.

T01 one deposit changed, balances left alone; T02a the same edit with balances and printed totals
recomputed to the end of that statement; T03 a month removed from the bundle (plan 9.2).
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from scripts import make_bundle
from scripts._gen.schema import BundleSpec, Mutation
from tests.e2e._support import Run, build_bundles, run_bundle, temp_root, text_under

EDITED = "bank_statement:2025-09"  # month index 2 of 6: neither first nor last


@pytest.fixture(scope="module")
def tamper_env():
    root = temp_root("gldb-e2e-tamper-")
    base = build_bundles(root, variants=("clean",))["clean"]
    truth = json.loads((base / "ground_truth.json").read_text(encoding="utf-8"))
    stmt = next(d for d in truth["documents"] if d["key"] == EDITED)
    credits = [
        (r["index"], Decimal(r["cells"]["credit"]["value"]))
        for r in stmt["tables"]["transactions"]["rows"]
        if r["cells"]["credit"]["value"]
    ]
    index, amount = max(credits, key=lambda c: c[1])
    edited = (amount * Decimal("1.25")).quantize(Decimal("0.01"))
    edit = Mutation(
        op="override_cell",
        target=EDITED,
        args={"table": "transactions", "row": index, "column": "credit", "value": str(edited)},
    )
    yield {"root": root, "index": index, "amount": amount, "edited": edited, "edit": edit}


def _run(env: dict, name: str, mutations: list[Mutation]) -> Run:
    spec = BundleSpec(scenario="loan", variant="clean", mutations=mutations)
    bdir = env["root"] / "bundles" / name
    make_bundle.build_bundle(spec, bdir)
    return run_bundle(bdir, tmp=env["root"] / name)


def _doc_name(run: Run, doc_id: str) -> str:
    doc = next(d for d in run.case.documents if d.id == doc_id)
    return next(f.name for f in run.case.files if f.id == doc.file_id)


def _printed(value: Decimal) -> str:
    return f"{value:,.2f}"


def test_t01_changed_deposit_is_caught_on_the_edited_row(tamper_env: dict) -> None:
    run = _run(tamper_env, "t01", [tamper_env["edit"]])
    flags = run.flags("bs_running_balance")
    assert flags, run.rules()
    flag = flags[0]
    assert flag.severity == "high" and flag.table == "transactions"
    assert flag.row == tamper_env["index"]  # localisation: the edited row
    assert _doc_name(run, flag.document_ids[0]) == "bank_statement_2025-09.pdf"
    assert flag.region is not None
    # the highlight lands on the changed line of the page (its date or its figures)
    row_doc = next(d for d in run.case.documents if d.id == flag.document_ids[0])
    row = row_doc.tables["transactions"].rows[flag.row]
    assert text_under(run, flag.region) in {c.raw for c in row.cells.values() if c.raw}
    assert _printed(tamper_env["edited"]) in {c.raw for c in row.cells.values() if c.raw}
    assert run.rules(min_severity="medium") <= {
        "bs_running_balance",
        "bs_totals",
    }  # may_also: totals


def test_t02a_recomputed_to_the_end_of_the_statement_breaks_continuity(tamper_env: dict) -> None:
    run = _run(
        tamper_env,
        "t02a",
        [
            tamper_env["edit"],
            Mutation(op="recompute_chain", target=EDITED, args={"from_row": 0, "propagate": False}),
        ],
    )
    # the edited statement is now internally consistent: its own chain and totals are silent
    assert not run.flags("bs_running_balance") and not run.flags("bs_totals")
    flags = run.flags("bs_period_continuity")
    assert flags, run.rules()
    flag = flags[0]
    names = {_doc_name(run, d) for d in flag.document_ids}
    assert names == {"bank_statement_2025-09.pdf", "bank_statement_2025-10.pdf"}
    # localisation: one of the flag's regions sits on the opening balance of the next statement
    # or the closing balance of the edited one (printed amounts of the broken join)
    regions = [r for r in [flag.region, *flag.extra_regions] if r is not None]
    assert regions
    texts = " | ".join(text_under(run, r) for r in regions)
    assert "," in texts and any(ch.isdigit() for ch in texts)
    assert run.rules(min_severity="medium") == {"bs_period_continuity"}


def test_t03_a_missing_month_is_a_gap_between_the_neighbours(tamper_env: dict) -> None:
    run = _run(tamper_env, "t03", [Mutation(op="drop_document", target=EDITED, args={})])
    flags = run.flags("bs_period_continuity")
    assert flags, run.rules()
    flag = flags[0]
    assert {_doc_name(run, d) for d in flag.document_ids} == {
        "bank_statement_2025-08.pdf",
        "bank_statement_2025-10.pdf",
    }
    assert "not covered" in flag.evidence
    assert flag.row is None  # neighbours, no single row (test-data-and-eval 4.3 T03)
    assert len([d for d in run.case.documents if d.doc_type == "bank_statement"]) == 5


def test_untampered_base_is_silent_so_every_catch_above_is_new(
    loan_runs: dict[str, Run],
) -> None:
    clean = loan_runs["clean"]
    assert not [f for f in clean.case.flags if f.severity != "info"]


def test_baseline_timings_and_cost_are_recorded_per_stage(loan_runs: dict[str, Run]) -> None:
    """First baseline (cost-efficiency 3.1, plan 6): every stage has seconds, cost is exactly 0."""
    case = loan_runs["fraud"].case
    assert [s.stage for s in case.stages] == [
        "ingest",
        "classify",
        "extract",
        "forensics",
        "rules",
        "crossdoc",
        "score",
    ]
    assert all(s.seconds > 0 for s in case.stages)
    assert all(s.cost_usd == 0 for s in case.stages) and case.costs == []
    assert sum(s.seconds for s in case.stages) < 60
