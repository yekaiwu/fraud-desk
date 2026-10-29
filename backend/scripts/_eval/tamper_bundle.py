"""Bundle-channel tampers: files are added, duplicated or re-submitted (T16a, T16b, T16c).

T03, T06 and T17 (a statement removed, a page dropped, invoices removed from a sequence) are built
through the generator's recorded ``drop_document`` / ``drop_page`` mutations in ``tamper_world``.
"""

from __future__ import annotations

import copy
import importlib
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from scripts._eval.tamper_core import (
    Ctx,
    Plan,
    TamperNotApplicable,
    TmpBundle,
    field_value,
)
from scripts._gen.schema import Target

D = Decimal


def _invoice_key(ctx: Ctx) -> str:
    inv = ctx.pick(ctx.docs("invoice"), "invoice")
    return inv["key"]


def _digital_path(ctx: Ctx, key: str) -> str:
    return next(d.digital_path for d in ctx.base.documents if d.key == key)


def _locate_file(key: str, note: str):  # noqa: ANN202
    def locate(b: dict[str, Any], n: dict[str, Any]):  # noqa: ANN202
        return Target(doc_key=key), {"copies": "1"}, {"copies": note}

    return locate


def t16a(ctx: Ctx) -> Plan:
    key = _invoice_key(ctx)
    src = _digital_path(ctx, key)
    stem = src.rsplit("/", 1)[-1].removesuffix(".pdf")

    def edit(tb: TmpBundle) -> None:
        tb.add_file(f"digital/{stem}_resubmitted.pdf", (tb.root / src).read_bytes(), src)

    return Plan(
        edit=f"{key}: the same file submitted twice in one bundle",
        file_edit=edit,
        locate=_locate_file(key, "2 (identical bytes)"),
    )


def _world(ctx: Ctx) -> Any:
    spec = ctx.base.spec
    mod = importlib.import_module(f"scripts._gen.bundle_{spec.scenario}")
    spec = mod.resolve_spec(spec)
    if spec.scenario == "loan":
        from scripts._gen.world import world_from_spec

        return world_from_spec(spec)
    story, _facts = mod.story_world(spec)
    return story


def t16b(ctx: Ctx) -> Plan:
    key = _invoice_key(ctx)
    src = _digital_path(ctx, key)
    stem = src.rsplit("/", 1)[-1].removesuffix(".pdf")
    number = field_value(ctx.gt, key, "invoice_number")

    def edit(tb: TmpBundle) -> None:
        from scripts._gen.render_invoice import render_invoice

        world = _world(ctx)
        inv = copy.deepcopy(world.invoice(number))
        if inv.layout == "A":
            inv.layout = "B"
            for ln in inv.lines:
                ln.gst = (ln.amount * D("0.09")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        else:
            inv.layout = "A"
            for ln in inv.lines:
                ln.gst = None
        rd = render_invoice(inv)
        tb.add_file(f"digital/{stem}_retypeset.pdf", rd.pdf, src)

    return Plan(
        edit=f"{key}: the same invoice re-typeset in the other layout (new bytes, same supplier UEN, number, date, total) and submitted in the same bundle",
        file_edit=edit,
        locate=_locate_file(key, "2 (new bytes)"),
    )


def t16c(ctx: Ctx) -> Plan:
    """A second case holding one invoice that an earlier case already stored (needs two cases in
    one database: the eval runs the base bundle first, then this case)."""
    key = _invoice_key(ctx)
    src = _digital_path(ctx, key)

    def edit(tb: TmpBundle) -> None:
        keep = [f for f in tb.manifest.files if f.path == src]
        tb.manifest.files = keep
        tb.manifest.upload_order = {"digital": [src]}  # type: ignore[assignment]
        tb.manifest.documents = [d for d in tb.manifest.documents if d.key == key]

    return Plan(
        edit=f"{key}: resubmitted in a second case after the base bundle was stored",
        file_edit=edit,
        locate=_locate_file(key, "stored by an earlier case"),
    )


BUNDLE_BUILDERS = {"T16a": t16a, "T16b": t16b, "T16c": t16c}
_ = TamperNotApplicable
