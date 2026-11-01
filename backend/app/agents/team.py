"""Run the review team on a case, after the cross-document stage and before scoring.

Order: the four specialists report what their checks found (no model call), the challenger rules on
the flags that are a matter of judgement, the case officer briefs the reviewer. A member that fails
is recorded as failed and the flags stay as they were; the rest of the pipeline is unaffected.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from app.agents import challenger, officer
from app.agents.call import AgentFailed
from app.agents.roster import ROLES, owner
from app.core.context import CaseContext
from app.core.models import AgentName, AgentReport, Flag

log = logging.getLogger("gldb.agents")

SPECIALISTS: tuple[AgentName, ...] = ("documents", "finance", "identity", "relationships")


def _specialists(ctx: CaseContext) -> list[AgentReport]:
    runs: dict[str, int] = {n: 0 for n in SPECIALISTS}
    for r in ctx.case.check_runs:
        if r.status == "ran":
            runs[owner(r.rule, r.stage)] += 1
    mine: dict[str, list[Flag]] = {n: [] for n in SPECIALISTS}
    for f in ctx.case.flags:
        if f.severity in ("medium", "high"):
            mine[owner(f.rule, f.stage)].append(f)
    out = []
    for name in SPECIALISTS:
        flags = mine[name]
        arguable = len(challenger.candidates_among(flags))
        if flags:
            rules = ", ".join(sorted({f.rule for f in flags}))
            summary = f"Ran {runs[name]} checks. {len(flags)} flag(s) at medium or above: {rules}."
        else:
            summary = f"Ran {runs[name]} checks. No flags at medium or above."
        handoff = (
            f"{arguable} flag(s) passed to the challenger."
            if arguable
            else ("Nothing for the challenger." if flags else "")
        )
        out.append(
            AgentReport(
                agent=name,
                role=ROLES[name],
                checks_run=runs[name],
                flag_ids=[f.id for f in flags],
                summary=summary,
                handoff=handoff,
            )
        )
    return out


def _spent(ctx: CaseContext, before: int) -> Decimal:
    return sum((c.usd for c in ctx.costs.costs()[before:]), Decimal("0"))


def run_team(ctx: CaseContext) -> dict[str, object]:
    """Returns a small summary for the stage record, plus a ``warning`` key when a member failed."""
    if not ctx.settings.agents:
        return {}
    team = _specialists(ctx)
    summary: dict[str, object] = {"agents": "done"}
    warnings: list[str] = []

    flags = challenger.candidates(ctx)
    ch = AgentReport(
        agent="challenger",
        role=ROLES["challenger"],
        status="skipped",
        summary="No judgement flags to argue.",
    )
    if flags:
        start, before = time.perf_counter(), len(ctx.costs.costs())
        try:
            verdicts = challenger.challenge(ctx, flags)
            lowered = challenger.apply(ctx, verdicts)
            ch = AgentReport(
                agent="challenger",
                role=ROLES["challenger"],
                flag_ids=[f.id for f in flags],
                verdicts=verdicts,
                summary=f"Ruled on {len(flags)} flag(s); lowered {lowered}, kept {len(flags) - lowered}.",
                handoff="Lowered flags go to the case officer with the reason." if lowered else "",
            )
            summary["challenged"] = len(flags)
            summary["lowered"] = lowered
        except AgentFailed as exc:
            ch = AgentReport(
                agent="challenger",
                role=ROLES["challenger"],
                status="failed",
                flag_ids=[f.id for f in flags],
                summary="The challenger could not run; every flag stands.",
            )
            warnings.append(f"agents_unavailable: challenger: {exc.reason}")
        except Exception as exc:  # noqa: BLE001 - never lose the flags to a bug in the team
            log.warning("challenger failed for %s", ctx.case.id, exc_info=True)
            ch = AgentReport(
                agent="challenger",
                role=ROLES["challenger"],
                status="failed",
                flag_ids=[f.id for f in flags],
                summary="The challenger could not run; every flag stands.",
            )
            warnings.append(f"agents_unavailable: challenger: {type(exc).__name__}")
        ch = ch.model_copy(
            update={
                "cost_usd": _spent(ctx, before),
                "seconds": round(time.perf_counter() - start, 3),
            }
        )
    team.append(ch)

    standing = officer.open_flags(ctx)
    lowered_any = any(v.action == "lowered" for v in ch.verdicts)
    co = AgentReport(
        agent="case_officer",
        role=ROLES["case_officer"],
        status="skipped",
        summary="No flags at medium or above. Nothing to escalate.",
    )
    if standing or lowered_any:
        start, before = time.perf_counter(), len(ctx.costs.costs())
        try:
            text, steps = officer.brief(ctx, team)
            co = AgentReport(
                agent="case_officer",
                role=ROLES["case_officer"],
                flag_ids=[f.id for f in standing],
                summary=text,
                next_steps=steps,
            )
        except AgentFailed as exc:
            co = AgentReport(
                agent="case_officer",
                role=ROLES["case_officer"],
                status="failed",
                summary="The case officer could not write a summary.",
            )
            warnings.append(f"agents_unavailable: case_officer: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            log.warning("case officer failed for %s", ctx.case.id, exc_info=True)
            co = AgentReport(
                agent="case_officer",
                role=ROLES["case_officer"],
                status="failed",
                summary="The case officer could not write a summary.",
            )
            warnings.append(f"agents_unavailable: case_officer: {type(exc).__name__}")
        co = co.model_copy(
            update={
                "cost_usd": _spent(ctx, before),
                "seconds": round(time.perf_counter() - start, 3),
            }
        )
    team.append(co)

    ctx.case.agents = team
    if warnings:
        summary["warning"] = "; ".join(warnings)
    return summary
