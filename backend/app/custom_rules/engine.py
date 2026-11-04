"""The fixed engine that runs custom rules (docs/custom-rules.md).

A custom rule is data: one of four templates with one condition (``spec.py``). This module runs it. No
generated code ever runs; the four templates are the four ``_eval_*`` functions below.

``evaluate`` is pure and total: no wall clock (``today`` is the case's), no network, no mutation of the
case, and it never raises (any unexpected failure becomes a skip with code ``custom_failed``). That is
what keeps a broken or surprising rule from marking a case partial or blocking the fast lane: a custom
run is ``ran`` or ``skipped``, never ``error``, and none of them counts in coverage
(``semantic/policy.py``). Flags are capped at medium, are never hard stops and never use a built-in
rule id (they are ``cus_<10 hex>``).

Evidence quotes document text, which is untrusted: every document string goes through ``clean_text``.
The explanation on a flag is code text built from the spec; it never contains the rule's name or
description (other accounts' cases and the review team's model calls read flag explanations).
"""

from __future__ import annotations

import logging
import operator as op
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal

from app.assemble import AssembledStatement, merged_statement
from app.checks.registry import make_flag, unique_flag_id
from app.core.cache import canonical_json
from app.core.context import CaseContext, natural_key
from app.core.models import (
    Case,
    Cell,
    CheckRun,
    CustomRunInfo,
    Document,
    Flag,
    Region,
    Row,
    Severity,
    clean_text,
)
from app.core.money import fmt_money
from app.custom_rules import labels, readback
from app.custom_rules.spec import (
    DATE_OPERATOR_TEXT,
    OPERATOR_TEXT,
    CustomRuleSpec,
    DateWindow,
    FieldMissing,
    FieldVsConstant,
    FsRatio,
    cap_custom_severity,
    field_info,
    fs_lines,
    normal_number,
    parse_number,
)

log = logging.getLogger("gldb.custom_rules")

SKIP_NO_DOCUMENT = "custom_no_document"  # no usable document of the rule's type
SKIP_FIELD_MISSING = (
    "custom_field_missing"  # every document of the type lacks the field / the right FS line
)
SKIP_SPEC_INVALID = "custom_spec_invalid"  # a stored spec no longer validates (schema drift)
SKIP_FAILED = "custom_failed"  # anything unexpected; never an "error" run
CUSTOM_FLAG_CAP = 5  # flags per rule per case; the last says "+N more"
USABLE_DOC_STATUSES = ("ok", "partial")
EVIDENCE_VALUE_CHARS = 80
DECIMAL_PRECISION = 120
EXTRA_REGIONS = 4

_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "gt": op.gt,
    "gte": op.ge,
    "lt": op.lt,
    "lte": op.le,
    "eq": op.eq,
    "ne": op.ne,
}
ZERO = Decimal("0")


@dataclass(frozen=True)
class LoadedCustomRule:
    """A custom rule as an analysis runs it: one version of one rule, already validated."""

    rule_id: str
    version: int
    spec: CustomRuleSpec
    spec_hash: str
    name: str  # the owner's display name, plain text; NEVER put in a flag or a prompt
    author: str  # the owner account
    origin: Literal["own", "adopted"]
    readback: str  # readback.readback(spec), stored with the version

    def run_info(self) -> CustomRunInfo:
        return CustomRunInfo(
            rule_id=self.rule_id,
            version=self.version,
            spec_hash=self.spec_hash,
            template=self.spec.template,
            name=self.name,
            author=self.author,
            origin=self.origin,
            readback=self.readback,
        )


@dataclass(frozen=True)
class DocOutcome:
    document_id: str
    status: Literal["fired", "passed", "missing"]
    scanned: bool  # any page of the document is a scan or a photo


@dataclass(frozen=True)
class Evaluation:
    flags: list[Flag] = field(default_factory=list)  # at most CUSTOM_FLAG_CAP
    outcomes: list[DocOutcome] = field(default_factory=list)
    skip_code: str | None = None  # one of the SKIP_* codes, or None when the rule ran
    skip_reason: str | None = None
    note: str = ""  # "Checked 2 invoices: 1 matched." (code-built, shown even with 0 flags)


# ---------------------------------------------------------------- shared helpers


def _doubtful(cell: Cell) -> bool:
    """A cell the page text could not confirm, or that two readers read differently."""
    return (
        cell.verified is False
        or cell.alt_raw is not None
        or cell.agreement
        in (
            "differ",
            "unsupported",
        )
    )


def _lowered(severity: str, doubtful: bool) -> Severity:
    """The final severity: the rule's own (never above medium), lowered to low when it rests on a
    doubtful reading (an info rule stays info)."""
    base = cap_custom_severity(severity)
    if doubtful and _RANK[base] > _RANK["low"]:
        return "low"
    return base


def _box(cell: Cell | None, label: str) -> Region | None:
    if cell is None or cell.box is None:
        return None
    return cell.box.model_copy(update={"label": label})


def _text(value: str | None) -> str:
    return clean_text(value or "", EVIDENCE_VALUE_CHARS)


def _scanned(case: Case, doc: Document) -> bool:
    """Any page of the document (any member page of an assembled statement) is a scan or a photo."""
    parts = [doc]
    if isinstance(doc, AssembledStatement):
        by_id = {d.id: d for d in case.documents}
        parts = [by_id[i] for i in doc.member_ids if i in by_id]
    for part in parts:
        for ref in case.files:
            if ref.id == part.file_id and any(
                p.is_scan for p in ref.pages if part.page_start <= p.page <= part.page_end
            ):
                return True
    return False


def _readable_documents(case: Case, doc_types: Sequence[str]) -> list[Document]:
    """Documents of the rule's type that are usable, in natural id order. A bank statement that came
    in pieces is read once, only when the pieces were put back together (the pages of an incomplete
    or ambiguous statement count as not reviewed), and as the merged statement the built-in rules
    read (``assemble._merge``), so a figure printed on a later page is not 'missing'."""
    by_id = {d.id: d for d in case.documents}
    found: list[Document] = []
    for doc in case.documents:
        if doc.doc_type not in doc_types or doc.status not in USABLE_DOC_STATUSES:
            continue
        link = doc.statement
        if link is not None:
            if not (link.state == "assembled" and link.members and link.members[0] == doc.id):
                continue
            members = [by_id[i] for i in link.members if i in by_id]
            if len(members) == len(link.members) and all(
                m.status in USABLE_DOC_STATUSES for m in members
            ):
                found.append(merged_statement(members))
                continue
        found.append(doc)
    return sorted(found, key=lambda d: natural_key(d.id))


def _day_text(n: int) -> str:
    return readback.days_text(n)


@dataclass
class _Hit:
    """What one template found on one document."""

    status: Literal["fired", "passed", "missing"]
    evidence: str = ""
    field_key: str | None = None
    table: str | None = None
    row: int | None = None
    region: Region | None = None
    extra_regions: list[Region] = field(default_factory=list)
    doubtful: bool = False
    names: list[str] = field(default_factory=list)  # what the unverified suffix names
    note: str = ""  # appended to the evidence (a document read only in part)


# ---------------------------------------------------------------- T1 field vs constant


def _numeric(cell: Cell) -> Decimal | None:
    if cell.value is None:
        return None
    try:
        value = Decimal(cell.value.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _eval_field_vs_constant(spec: CustomRuleSpec, doc: Document) -> _Hit:
    c = spec.condition
    assert isinstance(c, FieldVsConstant)
    info = field_info(c.field)
    assert info is not None
    key = info.field
    cell = doc.fields.get(key)
    if cell is None or cell.value is None:
        return _Hit("missing")
    label = labels.field_label(c.field)
    if info.kind == "enum":
        got = cell.value.strip()
        if not got:
            return _Hit("missing")
        wanted = [v.casefold() for v in (c.values if c.values is not None else [c.value or ""])]
        inside = got.casefold() in wanted
        fired = inside if c.operator in ("eq", "in") else not inside  # ne, not_in: the opposite
        if c.values is not None:
            shown = ", ".join(c.values)
            phrase = f"{OPERATOR_TEXT[c.operator]}: {shown}"
        else:
            phrase = f"{OPERATOR_TEXT[c.operator]} '{c.value}'"
        evidence = f"{label} '{_text(got)}' {phrase}"
    elif info.kind == "date":
        got_date = cell.date_()
        wanted_date = _parse_date(c.value)
        if got_date is None or wanted_date is None:
            return _Hit("missing")
        fired = _COMPARE[c.operator](got_date, wanted_date)
        evidence = (
            f"{label} {got_date.isoformat()} {DATE_OPERATOR_TEXT[c.operator]} "
            f"{wanted_date.isoformat()}"
        )
    else:
        got_num = _numeric(cell)
        wanted_num = parse_number(c.value) if c.value is not None else None
        if got_num is None or wanted_num is None:
            return _Hit("missing")
        fired = _COMPARE[c.operator](got_num, wanted_num)
        evidence = f"{label} {_text(cell.value)} {OPERATOR_TEXT[c.operator]} {c.value}"
    if not fired:
        return _Hit("passed")
    return _Hit(
        "fired",
        evidence=evidence,
        field_key=key,
        region=_box(cell, label),
        doubtful=_doubtful(cell),
        names=[key],
    )


def _parse_date(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------- T2 date window


def _eval_date_window(spec: CustomRuleSpec, doc: Document, today: date) -> _Hit:
    c = spec.condition
    assert isinstance(c, DateWindow)
    info = field_info(c.field)
    assert info is not None
    key = info.field
    cell = doc.fields.get(key)
    got = cell.date_() if cell is not None else None
    if cell is None or got is None:
        return _Hit("missing")
    if c.direction == "in_future":
        fired = got > today
    elif c.direction == "older_than":
        assert c.days is not None
        fired = got < today - timedelta(days=c.days)
    else:
        assert c.days is not None
        fired = got > today - timedelta(days=c.days)
    if not fired:
        return _Hit("passed")
    label = labels.field_label(c.field)
    delta = (today - got).days
    if delta > 0:
        where = f"{_day_text(delta)} before the case date {today.isoformat()}"
    elif delta < 0:
        where = f"{_day_text(-delta)} after the case date {today.isoformat()}"
    else:
        where = f"on the case date {today.isoformat()}"
    tail = ""
    if c.direction == "older_than":
        tail = f" (more than {c.days})"
    elif c.direction == "newer_than":
        tail = f" (less than {c.days})"
    return _Hit(
        "fired",
        evidence=f"{label} {got.isoformat()} is {where}{tail}",
        field_key=key,
        region=_box(cell, label),
        doubtful=_doubtful(cell),
        names=[key],
    )


# ---------------------------------------------------------------- T3 field missing


def _blank(cell: Cell | None) -> bool:
    if cell is None:
        return True
    if cell.value is None:
        return not (cell.raw or "").strip()  # printed but not understood is not missing
    return not cell.value.strip()


def _eval_field_missing(spec: CustomRuleSpec, doc: Document) -> _Hit:
    c = spec.condition
    assert isinstance(c, FieldMissing)
    info = field_info(c.field)
    assert info is not None
    cell = doc.fields.get(info.field)
    if not _blank(cell):
        return _Hit("passed")
    evidence = f"{labels.field_label(c.field)} is missing on this {labels.doc_label(doc.doc_type)}"
    return _Hit(
        "fired",
        evidence=evidence,
        field_key=info.field,
        region=_box(cell, labels.field_label(c.field)),
        doubtful=doc.status == "partial",
        note="; the document was read only in part" if doc.status == "partial" else "",
    )


# ---------------------------------------------------------------- T4 financial statement line vs line


@dataclass
class _LineValue:
    path: str
    value: Decimal
    rows: list[Row]


def _statement_line(doc: Document, path: str, column: str) -> _LineValue | None:
    """The value of a line on one column: the sum of its rows, or the one printed subtotal row (the same
    rule as ``checks/financial_statement._L``). ``None`` when the line is not printed."""
    line = fs_lines()[path]
    table = doc.tables.get(line.table)
    if table is None:
        return None
    rows = []
    for r in table.rows:
        key = r.cells.get("line")
        cell = r.cells.get(column)
        amount = cell.money() if cell is not None else None
        # a figure that is not a finite number (NaN, Infinity) is a line that is not printed
        if key is not None and key.value == line.line and amount is not None and amount.is_finite():
            rows.append(r)
    if not rows:
        return None
    subtotal = next((r for r in rows if _is_subtotal(r)), None)
    if subtotal is not None:
        value = subtotal.cells[column].money()
        assert value is not None
        return _LineValue(path, value, [subtotal])
    total = ZERO
    for r in rows:
        total += r.cells[column].money() or ZERO
    return _LineValue(path, total, rows)


def _is_subtotal(row: Row) -> bool:
    cell = row.cells.get("is_subtotal")
    return cell is not None and cell.value == "true"


def _eval_fs_ratio(spec: CustomRuleSpec, doc: Document) -> _Hit:
    with localcontext() as context:
        context.prec = (
            DECIMAL_PRECISION  # a printed figure of 30 digits must not overflow the default 28
        )
        return _fs_ratio_hit(spec, doc)


def _fs_ratio_hit(spec: CustomRuleSpec, doc: Document) -> _Hit:
    c = spec.condition
    assert isinstance(c, FsRatio)
    col = c.column
    right = _statement_line(doc, c.right, col)
    lefts = [(p, _statement_line(doc, p, col)) for p in c.left]
    printed = [(p, v) for p, v in lefts if v is not None]
    if right is None or not printed:
        return _Hit("missing")
    multiplier = parse_number(c.multiplier)
    assert multiplier is not None
    lhs = sum((v.value for _, v in printed), ZERO)
    rhs = multiplier * right.value
    if not _COMPARE[c.operator](lhs, rhs):
        return _Hit("passed")
    terms = " + ".join(f"{labels.line_label(p)} {fmt_money(v.value)}" for p, v in printed)
    total = f" = {fmt_money(lhs)}" if len(printed) > 1 else ""
    mult = normal_number(c.multiplier)
    right_text = f"{labels.line_label(c.right)} {fmt_money(right.value)}"
    scaled = f"{mult} x {right_text} = {fmt_money(rhs)}"
    year = "current year" if col == "current" else "prior year"
    evidence = f"{terms}{total} {OPERATOR_TEXT[c.operator]} {scaled} ({year})"
    absent = [labels.line_label(p) for p, v in lefts if v is None]
    if absent:
        evidence += "; not printed, counted as 0: " + ", ".join(absent)
    used = [v for _, v in printed] + [right]
    doubtful_names = [
        labels.line_label(v.path)
        for v in used
        if any(_doubtful(r.cells[col]) for r in v.rows if col in r.cells)
    ]
    right_row = right.rows[0]
    left_boxes = [
        _box(r.cells[col], labels.line_label(v.path))
        for _, v in printed
        for r in v.rows[:1]
        if col in r.cells
    ]
    return _Hit(
        "fired",
        evidence=evidence,
        table=fs_lines()[c.right].table,
        row=right_row.index,
        region=_box(right_row.cells.get(col), labels.line_label(c.right)),
        extra_regions=[b for b in left_boxes if b is not None][:EXTRA_REGIONS],
        doubtful=bool(doubtful_names),
        names=doubtful_names,
    )


# ---------------------------------------------------------------- evaluate


def _count_text(n: int, doc_type: str) -> str:
    return f"{n} {labels.doc_label(doc_type) if n == 1 else labels.doc_plural(doc_type)}"


def _note(spec: CustomRuleSpec, outcomes: list[DocOutcome]) -> str:
    doc_type = spec.doc_types()[0]
    fired = sum(1 for o in outcomes if o.status == "fired")
    missing = sum(1 for o in outcomes if o.status == "missing")
    text = f"Checked {_count_text(len(outcomes), doc_type)}: " + (
        f"{fired} matched" if fired else "none matched"
    )
    if missing:
        text += (
            f"; {missing} skipped because the figure or date was not printed or could not be read"
        )
    return text + "."


def _flag_from(rule: LoadedCustomRule, doc: Document, hit: _Hit, explanation: str) -> Flag:
    spec = rule.spec
    severity = _lowered(spec.severity, hit.doubtful)
    evidence = hit.evidence + hit.note
    if hit.doubtful and hit.names and not hit.note:
        evidence += "; unverified reading: " + ", ".join(hit.names)
    return make_flag(
        rule.rule_id,
        docs=[doc],
        field=hit.field_key,
        table=hit.table,
        row=hit.row,
        region=hit.region,
        extra_regions=hit.extra_regions,
        severity=severity,
        evidence=evidence,
        explanation=explanation,
    )


def _hit_for(spec: CustomRuleSpec, doc: Document, today: date) -> _Hit:
    c = spec.condition
    if isinstance(c, FieldVsConstant):
        return _eval_field_vs_constant(spec, doc)
    if isinstance(c, DateWindow):
        return _eval_date_window(spec, doc, today)
    if isinstance(c, FieldMissing):
        return _eval_field_missing(spec, doc)
    return _eval_fs_ratio(spec, doc)


def _evaluate(rule: LoadedCustomRule, case: Case, today: date) -> Evaluation:
    spec = rule.spec
    docs = _readable_documents(case, spec.doc_types())
    doc_type = spec.doc_types()[0]
    if not docs:
        reason = f"no usable {labels.doc_label(doc_type)} in the case"
        return Evaluation(
            skip_code=SKIP_NO_DOCUMENT,
            skip_reason=reason,
            note=reason[0].upper() + reason[1:] + ".",
        )
    explanation = readback.flag_explanation(spec)
    flags: list[Flag] = []
    outcomes: list[DocOutcome] = []
    failed = 0
    for doc in docs:
        try:
            hit = _hit_for(spec, doc, today)
        except Exception as exc:  # noqa: BLE001 - one unreadable document must not hide the others
            log.warning(
                "custom rule %s could not read %s: %s", rule.rule_id, doc.id, type(exc).__name__
            )
            failed += 1
            hit = _Hit("missing")
        outcomes.append(DocOutcome(doc.id, hit.status, _scanned(case, doc)))
        if hit.status == "fired":
            flags.append(_flag_from(rule, doc, hit, explanation))
    note = _note(spec, outcomes)
    if failed == len(docs):
        raise RuntimeError("every document failed")  # evaluate() turns this into SKIP_FAILED
    if all(o.status == "missing" for o in outcomes):
        reason = (
            f"the field or line this rule reads is not printed, or could not be read, on any "
            f"{labels.doc_label(doc_type)} in the case"
        )
        return Evaluation(
            outcomes=outcomes, skip_code=SKIP_FIELD_MISSING, skip_reason=reason, note=note
        )
    if len(flags) > CUSTOM_FLAG_CAP:
        extra = len(flags) - CUSTOM_FLAG_CAP
        flags = flags[:CUSTOM_FLAG_CAP]
        flags[-1] = flags[-1].model_copy(
            update={"evidence": f"{flags[-1].evidence}; +{extra} more"}
        )
    return Evaluation(flags=flags, outcomes=outcomes, note=note)


def evaluate(rule: LoadedCustomRule, case: Case, *, today: date) -> Evaluation:
    """Run one rule on one case. Pure and total: it never raises and writes nothing to ``case``."""
    try:
        return _evaluate(rule, case, today)
    except Exception as exc:  # noqa: BLE001 - a custom rule must never break an analysis
        return Evaluation(
            skip_code=SKIP_FAILED,
            skip_reason=f"the custom rule could not be evaluated: {type(exc).__name__}",
        )


# ---------------------------------------------------------------- registry hook


def run_custom_rules(ctx: CaseContext, taken: set[str]) -> list[CheckRun]:
    """Run the custom rules loaded for this analysis and append their flags to ``ctx.case.flags``.
    Returns one ``CheckRun`` per rule, whether it fired, passed or was skipped, so a rule that found
    nothing is still visible on the case. Never raises."""
    runs: list[CheckRun] = []
    for rule in sorted(ctx.custom_rules, key=lambda r: r.rule_id):
        started = time.perf_counter()
        try:
            result = evaluate(rule, ctx.case, today=ctx.today)
            flags = []
            for flag in result.flags:
                fixed = flag.model_copy(update={"id": unique_flag_id(flag.id, taken)})
                taken.add(fixed.id)
                flags.append(fixed)
            ctx.case.flags.extend(flags)
            skipped = result.skip_code is not None
            runs.append(
                CheckRun(
                    rule=rule.rule_id,
                    stage="rules",
                    status="skipped" if skipped else "ran",
                    flags=len(flags),
                    seconds=time.perf_counter() - started,
                    reason=result.skip_reason,
                    code=result.skip_code,
                    note=result.note or None,
                    custom=rule.run_info(),
                )
            )
        except Exception as exc:  # noqa: BLE001 - belt and braces on top of evaluate's own
            log.warning("custom rule %s could not run: %s", rule.rule_id, type(exc).__name__)
            runs.append(
                CheckRun(
                    rule=rule.rule_id,
                    stage="rules",
                    status="skipped",
                    reason=f"the custom rule could not be run: {type(exc).__name__}",
                    code=SKIP_FAILED,
                    seconds=time.perf_counter() - started,
                    custom=rule.run_info(),
                )
            )
    return runs


def custom_stamp(rules: Sequence[LoadedCustomRule]) -> dict[str, Any] | None:
    """``{"rules": [{rule, version, spec_hash, origin}...sorted by rule], "hash": sha256}``; ``None``
    when no custom rule was loaded, so a case analysed without any keeps a provenance identical to
    before custom rules existed."""
    if not rules:
        return None
    import hashlib

    entries = [
        {"rule": r.rule_id, "version": r.version, "spec_hash": r.spec_hash, "origin": r.origin}
        for r in sorted(rules, key=lambda r: r.rule_id)
    ]
    return {
        "rules": entries,
        "hash": hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest(),
    }
