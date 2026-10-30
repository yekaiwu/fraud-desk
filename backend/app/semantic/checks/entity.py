"""sem_entity_link: a counterparty the string matchers could not place, adjudicated against the
declared parties (docs/semantic-signals.md, "Entity adjudication"). Logic is in ``app.semantic.entity``."""

from __future__ import annotations

from app.checks.registry import CheckSkipped
from app.core.context import CaseContext
from app.core.models import Flag
from app.semantic.entity import (
    RULE,
    adjudicate,
    build_candidates,
    flags_from,
)
from app.semantic.registry import semantic_check


@semantic_check(
    rule=RULE,
    applies_to={"bank_statement", "invoice"},
    stage="crossdoc",
    severity="low",
    description=(
        "A statement or invoice counterparty that the name matchers do not link to a declared "
        "supplier, customer, director or shareholder is read by a model: same, different or "
        "cannot tell (needs ID). Never overrides a code match; quotes verified."
    ),
)
def sem_entity_link(ctx: CaseContext) -> list[Flag]:
    plan = build_candidates(ctx)
    if not plan.observed or not plan.declared:
        raise CheckSkipped("no counterparty to place, or no declared party to place it against")
    if not plan.candidates:
        return []  # every counterparty is matched by code or shares nothing with a declared party
    return flags_from(adjudicate(ctx, plan.candidates), ctx.docs())
