"""AML demo bundle (plan 0.5 AML row, docs 4.2): aml-clean, aml-fraud, aml-forger.

Tanjong Ridge Trading Pte. Ltd. declares S$1,000,000.00 of sales proceeds from Orchid Bay Holdings
under INV-2026-0117. The statements show three credits from three other companies, each forwarded
within 1 to 3 days at 98 to 99.5% to a different entity. The fraud variant prints GST at 8% on the
invoice; the forger prints 9% (917,431.19 + 82,568.81 = 1,000,000.00) so only the cross-document
and pass-through signals remain.

Regular invoices use their own series (``SI-2026-0107`` ...) in every variant so the planted
``INV-2026-0117`` cannot look back-dated within a series (see ``kyc_plots.renumber_regular_invoices``).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from . import kyc_plots as plots
from .bundle_common import write_bundle
from .kyc_common import (
    INV_RULES,
    PDF_RULES,
    quirk_records,
    resolve_spec,
    statement_planted,
    truth_box,
)
from .kyc_sof import render_sof
from .kyc_view import apply_mutations, view_of
from .render_common import RenderedDoc
from .render_contract import render_contract
from .render_id import render_id
from .render_invoice import render_invoice, render_po
from .render_profile import render_profile
from .render_statement import render_statement
from .schema import (
    BundleSpec,
    Expected,
    Manifest,
    Planted,
    RuleExpectation,
    Target,
)
from .world import World, world_from_spec

NAME = "aml"
D = Decimal
__all__ = ["NAME", "build", "render_docs", "resolve_spec", "story_world"]


def story_world(spec: BundleSpec) -> tuple[World, dict[str, str]]:
    """The world of the spec's company with the variant's plot applied (no tampers)."""
    w = plots.renumber_regular_invoices(world_from_spec(spec))
    view = view_of(w)
    facts: dict[str, str] = {}
    invoice = None
    if spec.variant != "clean":
        w = plots.plant_routing(w)
        invoice = plots.planted_invoice(w, spec.variant)
        w.invoices.append(invoice)
        facts = {
            "invoice_number": invoice.number,
            "sof_amount": f"{plots.SOF_AMOUNT:.2f}",
            "buyer": plots.BUYER_NAME,
        }
    view = view_of(w)
    view.sof = plots.make_sof(w, spec.variant, invoice)
    return w, facts


def render_docs(world: World) -> list[RenderedDoc]:
    ov = world.producer_overrides
    docs: list[RenderedDoc] = []

    def keep(key: str) -> bool:
        return key not in world.dropped_docs

    docs.append(render_profile(world, ov.get("company_profile:-")))
    for n, p in enumerate(
        (p for p in world.people if p.id_kind == "nric" and p.is_director and p.on_profile), start=1
    ):
        if keep(f"national_id:{n}"):
            docs.append(render_id(p, n, ov.get(f"national_id:{n}")))
    if keep("source_of_funds_declaration:-"):
        docs.append(render_sof(world, ov.get("source_of_funds_declaration:-")))
    for st in world.statements:
        if keep(st.key):
            docs.append(render_statement(st, world.quirks, ov.get(st.key)))
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


def _expected(variant: str) -> Expected:
    if variant == "clean":
        return Expected(
            lane_any_of=["fast", "aml"],
            band_max="low",
            verdict="approve",
            must_fire=[],
            max_flags_ge_medium=0,
        )
    fire = [
        RuleExpectation(rule="x_sof_support", min_severity="high"),
        RuleExpectation(rule="x_remitter_vs_buyer", min_severity="high"),
        RuleExpectation(rule="bs_pass_through", min_severity="medium"),
    ]
    if variant == "fraud":
        fire.append(RuleExpectation(rule="inv_gst"))
        return Expected(
            lane="aml", band_min="high", verdict="escalate_aml", must_fire=fire, must_not_fire=[]
        )
    return Expected(
        lane="aml",
        band_min="medium",
        verdict="escalate_aml",
        must_fire=fire,
        must_not_fire=[
            *INV_RULES,
            "bs_running_balance",
            "bs_totals",
            "bs_period_continuity",
            *PDF_RULES,
        ],
    )


def _planted(
    world: World, docs: dict[str, RenderedDoc], facts: dict[str, str], variant: str
) -> list[Planted]:
    if variant == "clean":
        return []
    out: list[Planted] = []
    sof = view_of(world).sof
    assert sof is not None
    out.append(
        Planted(
            id="P1",
            kind="sof_unsupported",
            description=(
                f"Source of funds declares {sof.declared_amount:.2f} of sales proceeds from "
                f"{sof.counterparty} under {sof.invoice_reference}; no credit names that buyer "
                f"or invoice, so {sof.declared_amount:.2f} is unsupported"
            ),
            deck_slide=15,
            target=Target(
                doc_key="source_of_funds_declaration:-",
                field="declared_amount",
                box=truth_box(docs, "source_of_funds_declaration:-", field="declared_amount"),
            ),
            facts={
                "declared_amount": f"{sof.declared_amount:.2f}",
                "buyer": plots.BUYER_NAME,
                "invoice_number": str(sof.invoice_reference),
                "unsupported": f"{plots.SOF_AMOUNT:.2f}",
            },
        )
    )
    inv = world.invoices[-1]
    out.append(
        Planted(
            id="P2",
            kind="invoice_unpaid_for_sof",
            description=(
                f"{inv.number} to {inv.buyer.name} for {inv.total:.2f} dated {inv.date}, "
                f"due {inv.due}, with no paid stamp; the money never arrives from the buyer"
            ),
            deck_slide=15,
            target=Target(
                doc_key=inv.key,
                field="total",
                box=truth_box(docs, inv.key, field="total"),
            ),
            facts={
                "invoice_number": inv.number,
                "total": f"{inv.total:.2f}",
                "invoice_date": str(inv.date),
                "due_date": str(inv.due),
            },
        )
    )
    if variant == "fraud":
        out.append(
            Planted(
                id="P2b",
                kind="invoice_gst_8pct",
                description=f"{inv.number} prints GST at {inv.gst_rate:.0f}% ({inv.gst_amount:.2f})",
                deck_slide=15,
                target=Target(
                    doc_key=inv.key,
                    field="gst_amount",
                    box=truth_box(docs, inv.key, field="gst_amount"),
                ),
                facts={"gst_rate": f"{inv.gst_rate:.2f}", "gst_amount": f"{inv.gst_amount:.2f}"},
            )
        )
    out.extend(statement_planted(world, docs))
    return out


def build(spec: BundleSpec, out_dir: Path) -> Manifest:
    spec = resolve_spec(spec)
    story, facts = story_world(spec)
    muts = list(spec.mutations)
    world = apply_mutations(story, muts)
    world.variant = spec.variant
    docs = render_docs(world)
    by_key = {d.key: d for d in docs}
    return write_bundle(
        spec,
        out_dir,
        docs,
        applicant={"name": world.company.party.name, "uen": world.company.party.uen},
        expected=_expected(spec.variant),
        planted=_planted(world, by_key, facts, spec.variant),
        quirks=quirk_records(spec),
        doc_date=spec.today,
    )
