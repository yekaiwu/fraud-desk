"""Loan demo bundle (plan 0.5 Loan row, docs 4.2): loan-clean, loan-fraud, loan-forger."""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from . import quirks as qk
from .bundle_common import write_bundle
from .render_common import RenderedDoc
from .render_contract import render_contract
from .render_fs import render_fs
from .render_id import render_id
from .render_invoice import render_invoice, render_po
from .render_profile import render_profile
from .render_statement import render_statement
from .schema import (
    DEFAULT_QUIRKS,
    Box,
    BundleSpec,
    Expected,
    Manifest,
    Mutation,
    Planted,
    RuleExpectation,
    Target,
)
from .world import (
    World,
    apply_plot,
    month_bounds,
    stream,
    weekday_on_or_before,
    world_from_spec,
)

NAME = "loan"
D = Decimal

PDF_RULES = [
    "pdf_text_vs_render",
    "pdf_modified",
    "pdf_editor_software",
    "pdf_producer_unexpected",
    "pdf_bytes_after_signature",
    "font_span_outlier",
    "font_mix",
]
BS_RULES = [
    "bs_totals",
    "bs_running_balance",
    "bs_period_continuity",
    "bs_dates",
    "bs_holder",
    "bs_page_chain",
    "bs_duplicates",
    "bs_round_numbers",
    "bs_benford",
]
FS_ANY_OF = ["fs_balance", "fs_subtotals", "fs_cash_tie", "fs_comparatives"]


def resolve_spec(spec: BundleSpec) -> BundleSpec:
    """Resolve default quirks (Q14 on odd company index) so the manifest regenerates itself."""
    s = spec.model_copy(deep=True)
    if s.quirks == DEFAULT_QUIRKS:
        s.quirks = qk.default_quirks(s.company_index)
    return s


def plot_mutations(spec: BundleSpec, world: World) -> list[Mutation]:
    """The loan fraud story as explicit, recorded mutations (docs 4.2). Fraud and forger share
    the same picks (stream purpose ``plot`` is variant independent)."""
    if spec.variant == "clean":
        return []
    rng = stream(spec.seed, "loan", spec.company_index, "plot")
    out: list[Mutation] = []
    revenue = world.notes["revenue"]
    fake = D(int(math.floor(float(revenue) * 8 / 3 / 10000 + 0.5) * 10000))
    out.append(
        Mutation(
            op="override_cell",
            target="financial_statement:FY2025",
            args={
                "plot": "variant",
                "table": "income_statement",
                "line": "revenue",
                "column": "current",
                "value": format(fake, "f"),
                "recompute": "forger" if spec.variant == "forger" else "none",
            },
        )
    )
    over = [world.invoices[i] for i in world.notes.get("over_idx", [])]
    # 4 invoices marked paid with no matching credit
    for inv in over:
        lag = rng.randint(10, 30)
        paid = weekday_on_or_before(inv.date + timedelta(days=lag))
        paid = min(paid, weekday_on_or_before(month_bounds(2025, 12)[1] - timedelta(days=8)))
        out.append(
            Mutation(
                op="set_field",
                target=inv.key,
                args={"plot": "variant", "field": "payment_status", "value": "paid"},
            )
        )
        out.append(
            Mutation(
                op="set_field",
                target=inv.key,
                args={"plot": "variant", "field": "paid_date", "value": paid.isoformat()},
            )
        )
    # 3 invoices exceed their contract by >= 10%
    cites = [inv for inv in over if inv.contract_ref]
    contract_by_ref = {c.number: c for c in world.contracts}
    for inv in cites[:3]:
        c = contract_by_ref[inv.contract_ref]  # type: ignore[index]
        target = (c.value * D(str(round(rng.uniform(1.12, 1.25), 3)))).quantize(D("0.01"))
        out.append(
            Mutation(
                op="set_field",
                target=inv.key,
                args={"plot": "variant", "field": "subtotal_target", "value": format(target, "f")},
            )
        )
    return out


def render_docs(world: World, spec: BundleSpec) -> list[RenderedDoc]:
    docs: list[RenderedDoc] = []
    ov = world.producer_overrides

    def keep(key: str) -> bool:
        return key not in world.dropped_docs

    docs.append(render_profile(world, ov.get("company_profile:-")))
    for n, p in enumerate(
        (p for p in world.people if p.id_kind == "nric" and p.is_director and p.on_profile), start=1
    ):
        if keep(f"national_id:{n}"):
            docs.append(render_id(p, n, ov.get(f"national_id:{n}")))
    for st in world.statements:
        if keep(st.key):
            docs.append(render_statement(st, world.quirks, ov.get(st.key)))
    for fy in sorted(world.fs):
        if keep(world.fs[fy].key):
            docs.append(render_fs(world.fs[fy], ov.get(world.fs[fy].key)))
    for inv in world.invoices:
        if keep(inv.key):
            docs.append(render_invoice(inv, ov.get(inv.key)))
    lookup = {c.key: c for c in world.contracts}
    for c in world.contracts:
        if keep(c.key):
            docs.append(render_contract(c, lookup, ov.get(c.key)))
    for po in world.pos:
        if keep(po.key):
            docs.append(render_po(po, ov.get(po.key)))
    return docs


def _truth_box(
    docs: dict[str, RenderedDoc],
    key: str,
    *,
    table: str | None = None,
    row: int | None = None,
    field: str | None = None,
    cell: str | None = None,
) -> Box | None:
    d = docs.get(key)
    if d is None:
        return None
    path = f"digital/{d.stem}.pdf"
    b = None
    if table is not None and row is not None:
        r = d.truth.tables[table]["rows"][row]
        b = r["cells"][cell]["box"] if cell else r["box"]
    elif field is not None:
        b = d.truth.fields.get(field, {}).get("box")
    if not b:
        return None
    return Box(file=path, page=b["page"], x=b["x"], y=b["y"], w=b["w"], h=b["h"])


def _expected(variant: str) -> Expected:
    if variant == "clean":
        return Expected(
            lane_any_of=["fast", "loan"],
            band_max="low",
            verdict="approve",
            must_fire=[],
            max_flags_ge_medium=0,
        )
    fire = [
        RuleExpectation(rule="x_revenue_vs_credits", min_severity="medium"),
        RuleExpectation(rule="x_invoice_vs_receipts"),
        RuleExpectation(rule="x_invoice_vs_contract"),
    ]
    if variant == "fraud":
        fire.append(RuleExpectation(rule=list(FS_ANY_OF), min_severity="high"))
        return Expected(
            lane="loan",
            band_min="medium",
            verdict="reject_or_escalate",
            must_fire=fire,
            must_not_fire=["x_cash_at_year_end"],
        )
    return Expected(
        lane="loan",
        band_min="medium",
        verdict="reject_or_escalate",
        must_fire=fire,
        must_not_fire=[*FS_ANY_OF, "x_cash_at_year_end", *BS_RULES, *PDF_RULES],
    )


def build(spec: BundleSpec, out_dir: Path) -> Manifest:
    spec = resolve_spec(spec)
    base = world_from_spec(spec)
    muts = list(spec.mutations)
    if spec.variant != "clean" and not any(m.args.get("plot") == "variant" for m in muts):
        muts = plot_mutations(spec, base) + muts
    spec = spec.model_copy(update={"mutations": muts})
    world = apply_plot(base, muts)
    world.variant = spec.variant
    docs = render_docs(world, spec)
    by_key = {d.key: d for d in docs}
    planted: list[Planted] = []
    if spec.variant != "clean":
        rev = world.fs["FY2025"].tables["income_statement"][0]
        true_rev = base.fs["FY2025"].tables["income_statement"][0].current
        planted.append(
            Planted(
                id="P1",
                kind="revenue_inflation",
                description=f"FY2025 revenue {rev.current:.2f} on the income statement; trade credits support about {true_rev:.2f}",
                deck_slide=16,
                target=Target(
                    doc_key="financial_statement:FY2025",
                    table="income_statement",
                    row=0,
                    box=_truth_box(
                        by_key,
                        "financial_statement:FY2025",
                        table="income_statement",
                        row=0,
                        cell="current",
                    ),
                ),
                facts={"revenue_printed": f"{rev.current:.2f}", "revenue_true": f"{true_rev:.2f}"},
            )
        )
        over = [world.invoices[i] for i in world.notes.get("over_idx", [])]
        for i, inv in enumerate(over):
            planted.append(
                Planted(
                    id=f"P2{'abcd'[i]}",
                    kind="paid_without_receipt",
                    description=f"{inv.number} marked paid on {inv.paid_date} with no matching credit in the statements",
                    deck_slide=16,
                    target=Target(
                        doc_key=inv.key,
                        field="payment_status",
                        box=_truth_box(by_key, inv.key, field="payment_status"),
                    ),
                    facts={
                        "invoice_number": inv.number,
                        "total": f"{inv.total:.2f}",
                        "paid_date": str(inv.paid_date),
                    },
                )
            )
        cref = {c.number: c for c in world.contracts}
        k = 0
        for inv in over:
            c = cref.get(inv.contract_ref or "")
            if c is not None and inv.subtotal > c.value * D("1.10"):
                planted.append(
                    Planted(
                        id=f"P3{'abc'[k]}",
                        kind="invoice_over_contract",
                        description=f"{inv.number} subtotal {inv.subtotal:.2f} exceeds contract {c.number} value {c.value:.2f} by at least 10%",
                        deck_slide=16,
                        target=Target(
                            doc_key=inv.key,
                            field="subtotal",
                            box=_truth_box(by_key, inv.key, field="subtotal"),
                        ),
                        facts={
                            "invoice_number": inv.number,
                            "contract_number": c.number,
                            "subtotal": f"{inv.subtotal:.2f}",
                            "contract_value": f"{c.value:.2f}",
                        },
                    )
                )
                k += 1
    quirk_ids = [q for q in spec.quirks if not (q == "Q09" and world.layout != "ocbc_style")]
    return write_bundle(
        spec,
        out_dir,
        docs,
        applicant={"name": world.company.party.name, "uen": world.company.party.uen},
        expected=_expected(spec.variant),
        planted=planted,
        quirks=qk.records(quirk_ids),
        doc_date=spec.today,
    )
