"""The case officer: read the whole trail and brief the human reviewer.

It writes words only. Its text never changes a flag, the score or the lane; the screen labels it as
generated. Step citations name the rules of flags that exist on the case; an unknown citation is dropped.
"""

from __future__ import annotations

from typing import Any

from app.agents.call import AgentFailed, ask
from app.core.context import CaseContext
from app.core.llm import wrap_untrusted
from app.core.models import AgentReport, Flag, clean_text

NAME = "case_officer"
MAX_STEPS = 3

TASK = """You are the case officer leading a fraud-review team at a bank. Specialist reviewers and a \
challenger have examined a customer's documents. You receive what they found. Write for the human \
reviewer who will decide the case.

summary: two or three plain sentences. Say what stands out and what the reviewer should look at \
first. Use only the flags and notes you are given; do not add facts. Never say a document is forged; \
say what does not match.
next_steps: up to three concrete actions for the reviewer (for example: ask the applicant for the \
original March statement), each with the flag ids it relates to. If nothing needs action, return no \
steps."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "next_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "string"},
                    "flag_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["step", "flag_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "next_steps"],
    "additionalProperties": False,
}


def open_flags(ctx: CaseContext) -> list[Flag]:
    return [f for f in ctx.case.flags if f.severity in ("medium", "high")]


def _digest(
    ctx: CaseContext, team: list[AgentReport], flags: list[Flag], alias: dict[str, str]
) -> str:
    lines = [f"use case: {ctx.case.use_case}"]
    for r in team:
        if r.agent in ("documents", "finance", "identity", "relationships"):
            lines.append(f"{r.agent}: {r.summary}")
    lines.append("flags still standing:")
    for f in flags:
        lines.append(f"- {alias[f.id]} [{f.severity}] {f.rule}: {clean_text(f.explanation, 260)}")
    challenger = next((r for r in team if r.agent == "challenger"), None)
    rule_of = {f.id: f.rule for f in ctx.case.flags}
    lowered = [v for v in (challenger.verdicts if challenger else []) if v.action == "lowered"]
    if lowered:
        lines.append("flags the challenger lowered (with its reason):")
        lines.extend(f"- {rule_of.get(v.flag_id, '?')}: {v.reason}" for v in lowered)
    return "\n".join(lines)


def brief(ctx: CaseContext, team: list[AgentReport]) -> tuple[str, list[str]]:
    """(summary, next steps). Raises ``AgentFailed``."""
    flags = open_flags(ctx)
    alias = {f.id: f"F{n}" for n, f in enumerate(flags, start=1)}
    try:
        body = wrap_untrusted(_digest(ctx, team, flags, alias), label="trail")
    except ValueError:
        raise AgentFailed("trail text could not be wrapped safely") from None
    reply = ask(
        ctx,
        name=NAME,
        task=TASK,
        user=f"The review team's trail:\n{body}",
        schema_name="Briefing",
        json_schema=SCHEMA,
    )
    summary = clean_text(str(reply.get("summary", "")), 600)
    if not summary:
        raise AgentFailed("empty summary")
    rule_of = {a: f.rule for f in flags for a in [alias[f.id]]}
    steps: list[str] = []
    for item in reply.get("next_steps", [])[:MAX_STEPS]:
        step = clean_text(str(item.get("step", "")), 240)
        rules = sorted({rule_of[i] for i in item.get("flag_ids", []) if i in rule_of})
        if step:
            steps.append(f"{step} ({', '.join(rules)})" if rules else step)
    return summary, steps
