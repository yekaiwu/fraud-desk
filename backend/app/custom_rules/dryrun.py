"""Dry run of a custom rule on stored cases (docs/custom-rules.md).

Before a rule is saved the officer sees how it would have behaved on the newest stored cases: how many it
could be evaluated on, how many it would have flagged, how many it skipped because the field was not
printed, and how many of those are scans or photographs. A rule that fires on none, or on more than 30%
of the cases it can read, needs an extra confirmation to save.

Read-only: each case is loaded (decrypted) through the ordinary case loader and run through the same
``engine.evaluate`` an analysis uses. Nothing is written to any case, and only counts and a few case ids
leave this module, never document content.
"""

from __future__ import annotations

import logging

from app.core.db import Database
from app.custom_rules import labels
from app.custom_rules.engine import SKIP_FIELD_MISSING, LoadedCustomRule, evaluate
from app.custom_rules.readback import readback
from app.custom_rules.spec import (
    DRY_RUN_MAX_FIRE_RATE,
    CustomRuleSpec,
    DryRunResult,
    DryRunWarning,
)

log = logging.getLogger("gldb.custom_rules")

DRY_RUN_RULE_ID = "cus_0000000000"
EXAMPLES = 5


def dry_run(db: Database, spec: CustomRuleSpec, *, limit: int) -> DryRunResult:
    """Run ``spec`` over the newest ``limit`` stored cases that are not being analysed."""
    rule = LoadedCustomRule(
        rule_id=DRY_RUN_RULE_ID,
        version=0,
        spec=spec,
        spec_hash=spec.spec_hash(),
        name="dry run",
        author="",
        origin="own",
        readback=readback(spec),
    )
    with db.conn() as c:
        ids = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM cases WHERE status != 'processing' "
                "ORDER BY created_at DESC, id LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    considered = evaluated = fired = skipped_missing = scanned_evaluated = scanned_fired = 0
    examples: list[str] = []
    for case_id in ids:
        try:
            case = db.load_case(case_id)
        except Exception:  # noqa: BLE001 - a case that cannot be read is left out of every count
            log.warning("dry run could not load a case")
            continue
        considered += 1
        result = evaluate(rule, case, today=case.today)
        if not result.outcomes:
            continue
        evaluated += 1
        scanned_evaluated += any(o.scanned for o in result.outcomes)
        if result.skip_code == SKIP_FIELD_MISSING:
            skipped_missing += 1
        if result.flags:
            fired += 1
            scanned_fired += any(o.scanned and o.status == "fired" for o in result.outcomes)
            if len(examples) < EXAMPLES:
                examples.append(case_id)
    rate = fired / evaluated if evaluated else 0.0
    warnings = _warnings(spec, evaluated, fired, rate)
    return DryRunResult(
        spec_hash=spec.spec_hash(),
        cases_considered=considered,
        evaluated=evaluated,
        fired=fired,
        skipped_missing=skipped_missing,
        scanned_evaluated=scanned_evaluated,
        scanned_fired=scanned_fired,
        fire_rate=round(rate, 4),
        warnings=warnings,
        examples=examples,
        needs_confirmation=bool(warnings),
    )


def _warnings(spec: CustomRuleSpec, evaluated: int, fired: int, rate: float) -> list[DryRunWarning]:
    if evaluated == 0:
        label = labels.doc_label(spec.doc_types()[0])
        message = f"No stored case has a {label} to test this on."
        return [DryRunWarning(code="no_cases", message=message)]
    if fired == 0:
        message = (
            f"It fired on none of the {evaluated} cases it could be tested on. "
            "Check that the limit is not too strict."
        )
        return [DryRunWarning(code="fires_on_none", message=message)]
    if rate > DRY_RUN_MAX_FIRE_RATE:
        message = (
            f"It fired on {fired} of {evaluated} cases ({round(rate * 100)}%); a rule that "
            "fires that often is noise."
        )
        return [DryRunWarning(code="fires_too_often", message=message)]
    return []
