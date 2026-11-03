"""Reading unreliable: lower a flag that rests on a figure the readers of a photo do not agree on.

A phone photo is read badly by any one engine (docs/limitations.md section 12), and a misread amount
makes a clean statement look edited. Two independent OCR readers misread *different* characters, so a
figure they print differently is a figure nobody has read yet. A figure an edit put on the page is
printed the same way by both, so agreement is never a reason to lower anything.

The rule, applied once after the rules and the cross-document checks (``GLDB_MULTI_READER``):

1. Every money, date and identifier cell of a photographed or scanned page was compared with the second
   reader while the page was extracted (``extract/agreement.py``). A cell the readers print differently
   has ``agreement == "differ"`` and carries the second reader's figure in ``other_raw``. A value the
   model returned that neither OCR reader prints (the model reading the page image against two OCRs
   that both disagree with it) is ``unsupported``.
2. ``differ``: a medium or high flag on a document with such a cell is tried again on a copy of the case
   in which every ``differ`` cell holds the *second* reader's figure (the checks are pure functions of
   the documents, so this is cheap and uses no model). A flag that does not come back fails only
   because of a figure the readers disagree on. A flag that comes back fails whichever reader is
   right and keeps its severity.
3. ``unsupported``: no second figure exists to try, so the flag is judged by what it cites. A flag that
   cites an unsupported figure (the figure is in its row or the row above, is the field it names, or
   sits inside a region it names) rests on a value no OCR reader prints.
4. Such a flag is lowered to ``low``, its explanation starts with "Reading unreliable, check the page
   image by eye", the evidence names both readings, and ``Flag.reading`` records the severity it
   would have had. It is counted in coverage and in the case's partial reasons (``score.py``) and is
   never cleared.
5. A flag that exists only on the copy is added to the case, at its own severity, when it is anchored
   to a row and cites no disputed figure: it is a real failure that was merely masked (a check that
   reports the first break on a statement hides the next one). Any other flag that exists only on the
   copy depends on the disputed figures and is not reported on its own; the lowered flag of the same
   page shows that the page needs a look.

Nothing is ever raised by this module and nothing is removed: the worst it does to a flag is
``low`` with the reason in front.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.checks.registry import run_stage_checks
from app.core.context import CaseContext
from app.core.models import Case, Cell, Document, Flag, ReadingNote, Region
from app.extract.normalise import canonical
from app.semantic.policy import is_semantic_rule

log = logging.getLogger("gldb.reading")

CHECK_STAGES = ("rules", "crossdoc")
NOTE = "Reading unreliable, check the page image by eye: "
MAX_TRIALS = 10  # single-figure retries per flag, to say which figure the flag rests on
MAX_NEW_FLAGS = 3
_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
_Locator = tuple[str, str | None, int | None, str]  # (document id, table, row, key)


def _cells(doc: Document):  # noqa: ANN202
    """(locator, cell) for every cell of the document that holds a value."""
    for key, cell in doc.fields.items():
        yield (doc.id, None, None, key), cell
    for tname, table in doc.tables.items():
        for i, row in enumerate(table.rows):
            for key, cell in row.cells.items():
                yield (doc.id, tname, i, key), cell


def _cells_where(case: Case, state: str) -> dict[_Locator, Cell]:
    return {
        loc: cell
        for doc in case.documents
        for loc, cell in _cells(doc)
        if cell.value is not None
        and cell.agreement == state
        and (state != "differ" or cell.other_raw)
    }


def disagreeing_cells(case: Case) -> dict[_Locator, Cell]:
    """Every cell whose second reader prints other, readable digits."""
    return _cells_where(case, "differ")


def unsupported_cells(case: Case) -> dict[_Locator, Cell]:
    """Every cell the model returned that neither OCR reader prints."""
    return _cells_where(case, "unsupported")


def _cites(flag: Flag, loc: _Locator, cell: Cell) -> bool:
    """Whether the flag rests on this figure: it sits in the flag's row or the row above in the same
    table, is the field the flag names, or lies inside a region the flag names."""
    doc_id, table, row, key = loc
    if doc_id not in flag.document_ids:
        return False
    same_table = flag.table is not None and table == flag.table
    if same_table and flag.row is not None and row is not None and flag.row - 1 <= row <= flag.row:
        return True
    if table is None and flag.field == key:
        return True
    return any(_touches(r, cell) for r in (flag.region, *flag.extra_regions))


def _touches(region: Region | None, cell: Cell) -> bool:
    if region is None or cell.box is None:
        return False
    b = cell.box
    return (
        (region.file_id, region.page) == (b.file_id, b.page)
        and region.x < b.x + b.w
        and b.x < region.x + region.w
        and region.y < b.y + b.h
        and b.y < region.y + region.h
    )


def _independent(flag: Flag, disputed: dict[_Locator, Cell]) -> bool:
    """A flag seen only after the readers' disagreements were settled, anchored to a row, that cites
    figures none of which the readers disagree on: it was merely hidden behind a misread (a check that
    reports the first break on a statement hides the next one). A disputed figure counts as cited when
    it sits in the flag's row or the row above in the same table, or when its box touches a region the
    flag names; a flag without a row or a region is never independent."""
    if flag.row is None or flag.region is None or flag.table is None:
        return False
    return not any(_cites(flag, loc, cell) for loc, cell in disputed.items())


def describe(loc: _Locator) -> str:
    doc, table, row, key = loc
    where = f"{key.replace('_', ' ')}" if table is None else f"{table} row {(row or 0) + 1} {key}"
    return f"{doc} {where}"


def _swapped(case: Case, locs: dict[_Locator, Cell]) -> Case:
    """A copy of the case in which each located cell holds the second reader's figure."""
    copy = case.model_copy(deep=True)
    docs = {d.id: d for d in copy.documents}
    for (doc_id, table, row, key), cell in locs.items():
        doc = docs.get(doc_id)
        if doc is None:
            continue
        target = (
            doc.fields.get(key)
            if table is None
            else (
                doc.tables[table].rows[row].cells.get(key)
                if table in doc.tables and row is not None and row < len(doc.tables[table].rows)
                else None
            )
        )
        if target is None:
            continue
        value, _warn = canonical(cell.kind, cell.other_raw, values=None)
        if value is None:
            continue
        target.value, target.raw = value, cell.other_raw
    return copy


def _rerun(ctx: CaseContext, case: Case) -> tuple[dict[str, Flag], set[str]]:
    """The flags of the rules and cross-document stages on ``case``, by flag id, and the rules that
    ran (a check that was skipped on the changed figures has not passed). The model-based checks, the
    second reading and the review team are off: only the pure checks are repeated."""
    settings = ctx.settings.model_copy(
        update={"semantic": "off", "verify_scan_flags": False, "agents": False}
    )
    vctx = replace(ctx, case=case, settings=settings, semantic_tally={}, semantic_state={})
    flags: dict[str, Flag] = {}
    for stage in CHECK_STAGES:
        run_stage_checks(vctx, stage)  # type: ignore[arg-type]
    for f in case.flags:
        if f.stage in CHECK_STAGES:
            flags[f.id] = f
    return flags, {r.rule for r in case.check_runs if r.status == "ran"}


def _passes(flag: Flag, again: tuple[dict[str, Flag], set[str]]) -> bool:
    """The check ran on the retried figures and no longer raised this flag at its severity."""
    flags, ran = again
    back = flags.get(flag.id)
    return flag.rule in ran and (back is None or _RANK[back.severity] < _RANK[flag.severity])


def _depends_on(
    ctx: CaseContext, flag: Flag, culprits: dict[_Locator, Cell]
) -> dict[_Locator, Cell]:
    """The disagreeing figures this flag rests on: those that make it go away when taken alone. If
    none does alone (two figures together), all of them in the flag's documents."""
    near = {loc: c for loc, c in culprits.items() if loc[0] in flag.document_ids}
    singles: dict[_Locator, Cell] = {}
    for loc in list(near)[:MAX_TRIALS]:
        if _passes(flag, _rerun(ctx, _swapped(ctx.case, {loc: near[loc]}))):
            singles[loc] = near[loc]
    return singles or near


def _readings(ctx: CaseContext, cells: dict[_Locator, Cell]) -> list[str]:
    out = []
    for loc, cell in list(cells.items())[:6]:
        mine = cell.raw or cell.value or "?"
        other = cell.other_raw if cell.agreement == "differ" else "printed by neither OCR reader"
        out.append(f"{describe(loc)}: {mine} / {other}")
    return out


def _lowered(flag: Flag, cells: list[str]) -> Flag:
    detail = "; ".join(cells)
    return flag.model_copy(
        update={
            "severity": "low",
            "reading": ReadingNote(severity=flag.severity, cells=cells),
            "explanation": NOTE + flag.explanation,
            "evidence": (
                f"{flag.evidence}; the readers disagree ({detail}); "
                "this check rests on a figure nobody has read reliably"
            ),
        }
    )


def apply_reading_agreement(ctx: CaseContext) -> dict[str, object]:
    """Apply the rule to ``ctx.case``. Returns a small summary for the stage record."""
    if not ctx.settings.multi_reader:
        return {}
    case = ctx.case
    differ, unsupported = disagreeing_cells(case), unsupported_cells(case)
    disputed = {**differ, **unsupported}
    if not disputed:
        return {}
    docs_with = {loc[0] for loc in disputed}
    candidates = {
        f.id
        for f in case.flags
        if f.stage in CHECK_STAGES
        and f.severity in ("medium", "high")
        and not is_semantic_rule(f.rule)
        and docs_with & set(f.document_ids)
    }
    variant: tuple[dict[str, Flag], set[str]] = ({}, set())
    if differ:
        try:
            variant = _rerun(ctx, _swapped(case, differ))
        except Exception:  # noqa: BLE001 - never lose a first-reading flag to a failed retry
            log.warning("reading agreement retry failed for %s", case.id, exc_info=True)
            return {"reading_agreement": "unavailable"}
    lowered = 0
    for i, flag in enumerate(case.flags):
        if flag.id not in candidates:
            continue
        culprits: dict[_Locator, Cell] = {}
        if differ and _passes(flag, variant):  # fails only on a disputed figure
            culprits.update(_depends_on(ctx, flag, differ))
        culprits.update({loc: c for loc, c in unsupported.items() if _cites(flag, loc, c)})
        if culprits:
            case.flags[i] = _lowered(flag, _readings(ctx, culprits))
            lowered += 1
    known = {f.id for f in case.flags}
    added = 0
    for fid, flag in variant[0].items():
        if fid in known or added >= MAX_NEW_FLAGS or _RANK[flag.severity] < _RANK["medium"]:
            continue
        if is_semantic_rule(flag.rule) or not (docs_with & set(flag.document_ids)):
            continue
        if not _independent(flag, disputed):
            continue  # depends on the disputed figures: the lowered flag on the same page shows it
        seen = "; found once the figures the readers differ on were read as the second reader did"
        case.flags.append(flag.model_copy(update={"evidence": flag.evidence + seen}))
        added += 1
    out: dict[str, object] = {"reading_unreliable_flags": lowered}
    if added:
        out["reading_new_flags"] = added
    out["reading_disagreeing_cells"] = len(differ)
    out["reading_unsupported_cells"] = len(unsupported)
    return out
