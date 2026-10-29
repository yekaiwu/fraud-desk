"""Metadata-channel tampers: only the Info dictionary (Producer, Creator, ModDate) of one statement
changes (T32a editor named and modified date, T32b modified date only, T32c an unexpected producer,
T33 an AI image generator named as the creator). The page content is byte-identical.

Implemented through the generator's recorded ``set_producer`` mutation, so the manifest regenerates
itself; the rewrite happens at render time, which is equivalent to rewriting the Info dictionary of
the finished file.
"""

from __future__ import annotations

from typing import Any

from scripts._eval.tamper_core import Ctx, Plan
from scripts._eval.tamper_world import mut
from scripts._gen.schema import Target


def _statements(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.docs("bank_statement")


def t32_producer(
    ctx: Ctx, *, producer: str | None, creator: str | None, mod_days: int | None, what: str
) -> Plan:
    st = ctx.pick(_statements(ctx), "statement")
    key = st["key"]
    args: dict[str, Any] = {}
    if producer:
        args["producer"] = producer
    if creator:
        args["creator"] = creator
    if mod_days is not None:
        args["mod_days"] = mod_days
    return Plan(
        edit=f"{key}: {what}",
        mutations=[mut("set_producer", key, **args)],
        locate=lambda g0, g1: (Target(doc_key=key), {"info": "honest"}, {"info": json_args(args)}),
    )


def json_args(a: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(a.items()))


EDITORS = ["Adobe Photoshop 25.1", "iLovePDF", "Smallpdf", "Canva", "Sejda PDF Editor"]
AI_TOOLS = ["Midjourney v6", "DALL-E 3", "Stable Diffusion XL"]


def t32a(ctx: Ctx) -> Plan:
    ed = ctx.pick(EDITORS, "editor")
    return t32_producer(
        ctx,
        producer=ed,
        creator=ed,
        mod_days=3,
        what=f"Producer/Creator set to {ed}, ModDate = creation + 3 days",
    )


def t32b(ctx: Ctx) -> Plan:
    return t32_producer(
        ctx, producer=None, creator=None, mod_days=3, what="ModDate = creation + 3 days only"
    )


def t32c(ctx: Ctx) -> Plan:
    return t32_producer(
        ctx,
        producer="Mock Word Processor 3.1",
        creator="Mock Word Processor 3.1",
        mod_days=None,
        what="Producer set to Mock Word Processor 3.1 (not the issuer's)",
    )


def t33(ctx: Ctx) -> Plan:
    tool = ctx.pick(AI_TOOLS, "generator")
    return t32_producer(
        ctx, producer=None, creator=tool, mod_days=None, what=f"Creator set to {tool}"
    )


METADATA_BUILDERS = {"T32a": t32a, "T32b": t32b, "T32c": t32c, "T33": t33}
