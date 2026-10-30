"""``sem_txn_labels``: runs the transaction description labeller (``app.semantic.txn``) for the case.

It creates no flag. Its job is to put the verified labels where ``x_declared_vs_activity`` (crossdoc
stage, later) can read them, and to make a model failure visible: when the labeller could not run,
this check is recorded as errored, so coverage drops and the case is partial. It mirrors the rule it
feeds: it is skipped (no model call) when that rule would be skipped or could not use a label.
"""

from __future__ import annotations

from app.assemble import logical_docs
from app.checks.kyc import declared_categories
from app.checks.registry import CheckSkipped
from app.core.context import CaseContext
from app.core.models import Flag
from app.semantic.registry import semantic_check
from app.semantic.txn import (
    C5_OF_LABEL,
    RULE,
    label_statements,
    statement_rows,
)

USABLE = ("ok", "partial")


@semantic_check(
    rule=RULE,
    applies_to={"company_profile"},
    requires={"company_profile", "bank_statement"},
    stage="rules",
    severity="low",
    use_cases={"kyc"},
    description=(
        "Reads the statement descriptions with a model, into a closed set of labels, for the "
        "declared-business check. Creates no flag."
    ),
)
def sem_txn_labels(ctx: CaseContext) -> list[Flag]:
    activity = ""
    for doc in ctx.docs("company_profile"):
        cell = doc.fields.get("principal_activity")
        text = (cell.text() if cell is not None else "").strip()
        if doc.status in USABLE and text:
            activity = text
            break
    declared = declared_categories(activity)
    if not declared:
        raise CheckSkipped("declared activity not mappable")
    if set(C5_OF_LABEL.values()) <= declared:
        raise CheckSkipped("the declared business already covers every label a rule can use")
    statements = [
        d for d in logical_docs(ctx, "bank_statement", whole_only=True) if d.status in USABLE
    ]
    currencies = {
        ((d.fields["currency"].text() if "currency" in d.fields else "").upper() or "SGD")
        for d in statements
    }
    if len(currencies) > 1:
        raise CheckSkipped("currency mismatch")
    if not any(statement_rows(d) for d in statements):
        raise CheckSkipped("no transactions")
    label_statements(ctx, statements)  # raises SemanticNotRun when a call failed
    return []
