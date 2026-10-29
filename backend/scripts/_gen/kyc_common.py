"""Helpers shared by ``bundle_kyc`` and ``bundle_aml``: spec resolution, truth boxes, quirk records."""

from __future__ import annotations

from typing import Any

from . import quirks as qk
from .render_common import RenderedDoc, union_box
from .schema import DEFAULT_QUIRKS, Box, BundleSpec, Planted, Quirk, Target
from .world import World

# rules the careful forger must not trip (docs 4.2 'must_not_fire' column)
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
    "bs_pass_through",
]
PDF_RULES = [
    "pdf_text_vs_render",
    "pdf_modified",
    "pdf_editor_software",
    "pdf_producer_unexpected",
    "pdf_bytes_after_signature",
    "font_span_outlier",
    "font_mix",
]
INV_RULES = [
    "inv_lines",
    "inv_gst",
    "inv_total",
    "inv_ids",
    "inv_dates",
    "inv_parties",
    "inv_duplicate",
    "inv_sequence",
]
# quirks that cannot appear in a bundle of the scenario (no invoices / FS / that layout)
NOT_APPLICABLE = {
    "kyc": {"Q02", "Q03", "Q04", "Q06", "Q14", "H1", "H3"},
    "aml": {"Q06", "Q09", "Q14"},
}


def resolve_spec(spec: BundleSpec) -> BundleSpec:
    """Default quirk list -> the company's quirks (Q14 on odd indexes), so a manifest regenerates."""
    s = spec.model_copy(deep=True)
    if s.quirks == DEFAULT_QUIRKS:
        s.quirks = qk.default_quirks(s.company_index)
    return s


def quirk_records(spec: BundleSpec) -> list[Quirk]:
    drop = NOT_APPLICABLE.get(spec.scenario, set())
    return qk.records([q for q in spec.quirks if q not in drop])


def file_of(d: RenderedDoc) -> str:
    return f"digital/{d.stem}.pdf"


def _box(d: RenderedDoc, b: dict[str, Any] | None) -> Box | None:
    if not b:
        return None
    return Box(file=file_of(d), page=b["page"], x=b["x"], y=b["y"], w=b["w"], h=b["h"])


def truth_box(
    docs: dict[str, RenderedDoc],
    key: str,
    *,
    table: str | None = None,
    row: int | None = None,
    field: str | None = None,
    cell: str | None = None,
) -> Box | None:
    """Digital-render box of a field, a table row, or one cell of a row."""
    d = docs.get(key)
    if d is None:
        return None
    if table is not None and row is not None:
        rows = d.truth.tables[table]["rows"]
        if row >= len(rows):
            return None
        r = rows[row]
        return _box(d, r["cells"][cell]["box"] if cell else r["box"])
    if field is not None:
        return _box(d, d.truth.fields.get(field, {}).get("box"))
    return None


def table_box(docs: dict[str, RenderedDoc], key: str, table: str) -> Box | None:
    d = docs.get(key)
    if d is None:
        return None
    return _box(d, union_box([r["box"] for r in d.truth.tables[table]["rows"] if r["box"]]))


def statement_planted(world: World, docs: dict[str, RenderedDoc]) -> list[Planted]:
    """AML routing: one record per hop (credit row target) and one per statement whose balances
    changed only because the chain was recomputed."""
    out: list[Planted] = []
    cr = [
        (st, i, r)
        for st in world.statements
        for i, r in enumerate(st.rows)
        if r.kind == "routed_in"
    ]
    db = [
        (st, i, r)
        for st in world.statements
        for i, r in enumerate(st.rows)
        if r.kind == "routed_out"
    ]
    for k, ((st, i, c), (dst, _j, d)) in enumerate(zip(cr, db, strict=True)):
        out.append(
            Planted(
                id=f"P3{'abcdef'[k]}",
                kind="pass_through",
                description=(
                    f"{c.credit:.2f} from {c.counterparty} on {c.date} is forwarded as "
                    f"{d.debit:.2f} to {d.counterparty} on {d.date}"
                ),
                deck_slide=15,
                target=Target(
                    doc_key=st.key,
                    table="transactions",
                    row=i,
                    box=truth_box(docs, st.key, table="transactions", row=i),
                ),
                facts={
                    "remitter": str(c.counterparty),
                    "credit": f"{c.credit:.2f}",
                    "credit_date": str(c.date),
                    "forwarded_to": str(d.counterparty),
                    "debit": f"{d.debit:.2f}",
                    "debit_date": str(d.date),
                    "debit_statement": dst.month,
                },
            )
        )
    hit = {st.key for st, _i, _r in cr + db}
    for st in world.statements:
        if st.key in hit or st.month not in world.notes.get("plot_chain_months", []):
            continue
        out.append(
            Planted(
                id=f"P4-{st.month}",
                kind="balances_recomputed",
                description=f"{st.month} opening balance changed because earlier statements were edited",
                deck_slide=15,
                target=Target(
                    doc_key=st.key,
                    field="opening_balance",
                    box=truth_box(docs, st.key, field="opening_balance"),
                ),
                facts={"statement": st.month},
            )
        )
    return out
