"""FIT checks: closed "does it fit" questions (docs/semantic-signals.md, section FIT).

Registered only when ``GLDB_SEMANTIC`` is on (the registry imports this module on discovery). The
logic is in ``app/semantic/fit.py``.
"""

from __future__ import annotations

from app.core.context import CaseContext
from app.core.models import Flag
from app.semantic import fit
from app.semantic.registry import semantic_check


@semantic_check(
    rule=fit.GOODS.rule,
    applies_to={"invoice"},
    requires={"company_profile"},
    description=fit.GOODS.description,
    severity="medium",
    stage="crossdoc",
    version=fit.FIT_VERSION,
)
def sem_goods_fit(ctx: CaseContext) -> list[Flag]:
    return fit.run_question(ctx, fit.GOODS)


@semantic_check(
    rule=fit.INCOME.rule,
    applies_to={"source_of_funds_declaration"},
    description=fit.INCOME.description,
    severity="medium",
    stage="crossdoc",
    version=fit.FIT_VERSION,
)
def sem_income_fit(ctx: CaseContext) -> list[Flag]:
    return fit.run_question(ctx, fit.INCOME)


@semantic_check(
    rule=fit.COHERENCE.rule,
    applies_to={"bank_statement"},
    requires={"company_profile"},
    description=fit.COHERENCE.description,
    severity="medium",
    stage="crossdoc",
    version=fit.FIT_VERSION,
)
def sem_coherence_fit(ctx: CaseContext) -> list[Flag]:
    return fit.run_question(ctx, fit.COHERENCE)
