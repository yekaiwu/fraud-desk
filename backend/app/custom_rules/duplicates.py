"""Duplicate checks before a custom rule is saved (docs/custom-rules.md).

* Against the built-in checks: a rule that does what a built-in already does (same document type, same
  fields, same relationship) is not saved; the officer is pointed at the severity setting of the
  existing check in the check catalogue. A built-in that looks at the same fields in another way is
  shown as a warning.
* Against custom rules: an identical condition (canonicalised and hashed, severity left out) among the
  officer's own rules, the public rules or the ones they adopted is refused with a pointer to it;
  the same template on the same fields with another condition is a warning.

Another account's PRIVATE rule is ignored entirely: naming it, even as a warning, would reveal that it
exists, and it only ever runs for its owner.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.checks.registry import CheckMeta, all_checks
from app.core.db import Database
from app.core.models import RuleOverride, clean_text
from app.custom_rules import store
from app.custom_rules.spec import (
    CustomRuleSpec,
    DateWindow,
    DuplicateNote,
    FieldMissing,
    FieldVsConstant,
    FsRatio,
    Idea,
    addressable_fields,
    field_info,
)

NAME_SHOWN = 80
WHAT_SHOWN = 300


@dataclass(frozen=True)
class Footprint:
    """What a built-in check reads, in the terms of a template."""

    rule: str  # a registered built-in rule id
    template: str  # the template that would express what it checks
    paths: frozenset[str]  # spec paths it reads
    relation: str | None  # see relation_of(); None = any relation of that template
    exact: bool  # True: an identical spec is a duplicate (the save is refused)


def _fp(
    rule: str, template: str, paths: Iterable[str], relation: str | None, exact: bool
) -> Footprint:
    return Footprint(rule, template, frozenset(paths), relation, exact)


def _entity_footprints() -> tuple[Footprint, ...]:
    """``x_entity`` compares company names and UENs across the bundle: any rule that asks whether
    one of those is missing overlaps with it."""
    return tuple(
        _fp("x_entity", "field_missing", [path], "missing", False)
        for path in addressable_fields()
        if path.endswith((".company_name", ".uen"))
    )


BUILTIN_FOOTPRINTS: tuple[Footprint, ...] = (
    _fp("rcpt_merchant", "field_missing", ["receipt.merchant_name"], "missing", True),
    _fp("rcpt_dates", "date_window", ["receipt.receipt_date"], "in_future", True),
    _fp("rcpt_dates", "date_window", ["receipt.receipt_date"], "older_than", False),
    _fp("inv_dates", "date_window", ["invoice.invoice_date"], "in_future", True),
    _fp("date_valid", "date_window", ["passport.date_of_issue"], "in_future", True),
    _fp("date_valid", "date_window", ["national_id.date_of_issue"], "in_future", True),
    _fp("bs_dates", "date_window", ["bank_statement.period_end"], None, False),
    _fp(
        "fs_balance",
        "fs_ratio",
        [
            "balance_sheet.total_assets",
            "balance_sheet.total_liabilities",
            "balance_sheet.total_equity",
        ],
        None,
        False,
    ),
    _fp("inv_ids", "field_missing", ["invoice.supplier_uen"], "missing", False),
    _fp("inv_ids", "field_missing", ["invoice.buyer_uen"], "missing", False),
    _fp("inv_ids", "field_missing", ["invoice.supplier_gst_no"], "missing", False),
    _fp("fs_entity", "field_missing", ["financial_statement.company_name"], "missing", False),
    _fp("fs_entity", "field_missing", ["financial_statement.uen"], "missing", False),
    *_entity_footprints(),
)


def relation_of(spec: CustomRuleSpec) -> str:
    """How a rule relates its field to its limit: ``upper`` (greater), ``lower`` (smaller), ``equal``,
    ``not_equal``, the date direction (``older_than``, ``newer_than``, ``in_future``) or ``missing``."""
    c = spec.condition
    if isinstance(c, FieldVsConstant):
        if c.operator in ("gt", "gte"):
            return "upper"
        if c.operator in ("lt", "lte"):
            return "lower"
        return "equal" if c.operator in ("eq", "in") else "not_equal"
    if isinstance(c, DateWindow):
        if (
            c.direction == "newer_than" and c.days == 0
        ):  # later than the case date: the same as in_future
            return "in_future"
        return c.direction
    if isinstance(c, FieldMissing):
        return "missing"
    assert isinstance(c, FsRatio)
    return "upper" if c.operator in ("gt", "gte") else "lower"


def _effective_severity(meta: CheckMeta, overrides: Mapping[str, RuleOverride]) -> str:
    override = overrides.get(meta.rule)
    return (override.severity if override and override.severity else None) or meta.severity


def builtin_duplicates(
    spec: CustomRuleSpec, overrides: Mapping[str, RuleOverride]
) -> list[DuplicateNote]:
    """Built-in checks that already cover this rule (``identical``) or read the same fields
    (``similar``). ``overrides`` are the check catalogue's, so the severity named is the effective one."""
    metas = {m.rule: m for m in all_checks()}
    paths = frozenset(spec.paths())
    relation = relation_of(spec)
    notes: dict[str, DuplicateNote] = {}
    for fp in BUILTIN_FOOTPRINTS:
        meta = metas.get(fp.rule)
        if meta is None or fp.template != spec.template or fp.paths != paths:
            continue
        same_relation = fp.relation is None or fp.relation == relation
        identical = fp.exact and same_relation
        if fp.rule in notes and (notes[fp.rule].level == "identical" or not identical):
            continue
        if identical:
            severity = _effective_severity(meta, overrides)
            notes[fp.rule] = DuplicateNote(
                kind="builtin",
                level="identical",
                rule=fp.rule,
                name=meta.description,
                what=meta.description,
                action=(
                    f"This is already checked by {fp.rule}, which flags at {severity} now. To change "
                    f"how serious it is, change its severity in the check catalogue "
                    f"(Check catalogue > {fp.rule})."
                ),
            )
        else:
            notes[fp.rule] = DuplicateNote(
                kind="builtin",
                level="similar",
                rule=fp.rule,
                name=meta.description,
                what=meta.description,
                action=(
                    f"{fp.rule} already looks at the same fields in its own way. Check that this "
                    "rule adds something, or change that check's severity in the check catalogue."
                ),
            )
    c = spec.condition
    if isinstance(c, FieldMissing):
        info = field_info(c.field)
        if info is not None and info.required:
            notes["required_field"] = DuplicateNote(
                kind="builtin",
                level="similar",
                rule="required_field",
                name=None,
                what="A missing required field already marks the document as read only in part.",
                action=(
                    "This rule will still run, but a document read only in part is flagged at "
                    "low, whatever severity you choose."
                ),
            )
    return list(notes.values())


def _shown(text: str) -> str:
    return clean_text(text, WHAT_SHOWN)


def custom_duplicates(
    db: Database, spec: CustomRuleSpec, *, viewer: str, exclude_rule: str | None = None
) -> list[DuplicateNote]:
    """Custom rules the ``viewer`` can see that check the same thing. ``exclude_rule`` is the rule being
    edited (a new version of it is not a duplicate of itself)."""
    adopted = {a.rule_id: a.version for a in store.list_adoptions(db, viewer)}
    wanted_hash = spec.condition_hash()
    template, paths = spec.template, frozenset(spec.paths())
    notes: dict[str, DuplicateNote] = {}
    rules = {r.id: r for r in store.list_owned(db, viewer, include_retired=False)}
    rules.update({r.id: r for r in store.list_public(db)})
    for rule_id in adopted:
        rule = store.get_rule(db, rule_id)
        if rule is not None and rule.public:
            rules[rule_id] = rule
    for rule in rules.values():
        if rule.id == exclude_rule or rule.status != "active":
            continue
        own = rule.owner == viewer
        versions = {rule.current_version} if own else set()
        if rule.visibility == "public" and rule.published_version is not None:
            versions.add(rule.published_version)
        if rule.id in adopted:
            versions.add(adopted[rule.id])
        found: tuple[str, store.VersionRow] | None = None
        for number in sorted(versions, reverse=True):
            row = store.get_version(db, rule.id, number)
            if row is None or row.spec is None:
                continue
            if row.condition_hash == wanted_hash:
                found = ("identical", row)
                break
            if (
                found is None
                and row.spec.template == template
                and frozenset(row.spec.paths()) == paths
            ):
                found = ("similar", row)
        if found is None:
            continue
        level, row = found
        if level == "identical":
            if own:
                action = "Change the severity of that rule instead."
            elif adopted.get(rule.id) == row.version:
                action = "You already adopted it."
            else:
                action = "Adopt it from the public rules."
        else:
            action = "Check that this is not the same idea; you can change that rule instead."
        notes[rule.id] = DuplicateNote(
            kind="custom",
            level=level,  # type: ignore[arg-type]
            rule=rule.id,
            name=clean_text(store.shown_name(rule, row), NAME_SHOWN),
            what=_shown(row.readback),
            action=action,
        )
    return sorted(notes.values(), key=lambda n: (n.level != "identical", n.rule))


def find_duplicates(
    db: Database, spec: CustomRuleSpec, *, viewer: str, exclude_rule: str | None = None
) -> list[DuplicateNote]:
    """Built-in duplicates first, then the viewer's custom ones."""
    return [
        *builtin_duplicates(spec, db.rule_overrides()),
        *custom_duplicates(db, spec, viewer=viewer, exclude_rule=exclude_rule),
    ]


def blocking(notes: Sequence[DuplicateNote]) -> DuplicateNote | None:
    """The first note that stops the save (an identical rule), or ``None``."""
    return next((n for n in notes if n.level == "identical"), None)


def related_builtins(doc_types: Iterable[str], limit: int = 5) -> list[Idea]:
    """Built-in checks that look at the same kinds of document, closest first: the ones with a
    footprint on those documents, then the ones that apply only to them."""
    wanted = set(doc_types)
    if not wanted:
        return []
    footprint_hits: dict[str, int] = {}
    for fp in BUILTIN_FOOTPRINTS:
        hits = sum(1 for p in fp.paths if p.partition(".")[0] in wanted)
        if hits:
            footprint_hits[fp.rule] = footprint_hits.get(fp.rule, 0) + hits
    ranked: list[tuple[tuple[int, int, str], CheckMeta]] = []
    for meta in all_checks():
        if "*" in meta.applies_to or not meta.applies_to & wanted:
            continue
        only_these = 0 if meta.applies_to <= wanted else 1
        ranked.append(((-footprint_hits.get(meta.rule, 0), only_these, meta.rule), meta))
    ranked.sort(key=lambda t: t[0])
    return [
        Idea(kind="related_builtin", rule=m.rule, text=m.description) for _, m in ranked[:limit]
    ]
