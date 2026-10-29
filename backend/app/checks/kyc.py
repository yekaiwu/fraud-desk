"""KYC rules (docs/checks-kyc-aml.md sections 4.2 to 4.6).

ID format and checksum, passport MRZ, date sanity, shareholdings arithmetic and UBO threshold,
ownership and directors against the mock registry, declared business against bank activity.

Documents are written by the person being checked: every extracted string is data. Evidence masks
ID numbers (``mask_id``) and passes names through ``safe_text``. No wall clock: ``ctx.today`` only.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.models import Cell, Document, Flag, Region, Row
from app.core.money import fmt_money
from app.graph import counterparty_of
from app.names import (
    cell_region,
    entity_key,
    ids_conflict_or_equal,
    labelled,
    looks_like_nric,
    mask_id,
    match_person,
    normalise_id,
    pct_tol,
    safe_text,
    same_identity,
    sorted_rows,
    union_region,
    unverified_note,
)

# --------------------------------------------------------------------------------------
# Constants (starting guesses, recalibrated from the eval; plan 4 intro, 9)
# --------------------------------------------------------------------------------------

MIN_AGE = 16
MAX_AGE = 100
NAME_PLACEHOLDERS = frozenset(
    {"", "-", "n/a", "na", "nominee", "nominees", "various", "others", "other", "tbc", "unknown",
     "undisclosed", "bearer", "confidential"}
)  # fmt: skip
DVA_MIN_ROWS = 2
DVA_MIN_SHARE = Decimal("0.05")
DVA_DOMINANT_SHARE = Decimal("0.50")
RISKY_LABELS = ("crypto_virtual_assets", "gambling", "money_services")
USABLE = ("ok", "partial")
MAX_EXTRA_REGIONS = 6
MAX_MRZ_FLAGS = 8

NRIC_RE = re.compile(r"^[STFGM]\d{7}[A-Z]$")
_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_LETTERS_ST = "JZIHGFEDCBA"
_LETTERS_FG = "XWUTRQPNMLK"

DOC_LABELS = {
    "ownership_chart": "ownership chart",
    "shareholder_register": "shareholder register",
    "company_profile": "company profile",
}


SHORT_LABELS = {
    "ownership chart": "chart",
    "shareholder register": "register",
    "company profile": "profile",
}


def _docs(ctx: CaseContext, *types: str) -> list[Document]:
    return [d for d in ctx.docs(*types) if d.status in USABLE]


def _cell(doc: Document, name: str) -> Cell:
    return doc.fields.get(name) or Cell(value=None)


def _cap(regions: list[Region | None], limit: int = MAX_EXTRA_REGIONS) -> list[Region]:
    return [r for r in regions if r is not None][:limit]


def _row_region(row: Row) -> Region | None:
    return row.box or union_region([c.box for c in row.cells.values()])


def _pct(d: Decimal) -> str:
    return fmt_money(d)


# --------------------------------------------------------------------------------------
# NRIC / FIN
# --------------------------------------------------------------------------------------


def nric_weighted_sum(prefix: str, digits: str) -> tuple[int, int]:
    """(weighted digit sum including the +4 offset for T/G, that sum mod 11)."""
    if prefix not in "STFG" or len(prefix) != 1 or not re.fullmatch(r"\d{7}", digits):
        raise ValueError("expected a prefix in S/T/F/G and exactly 7 digits")
    total = sum(int(d) * w for d, w in zip(digits, _WEIGHTS, strict=True))
    if prefix in "TG":
        total += 4
    return total, total % 11


def nric_check_letter(prefix: str, digits: str) -> str:
    """Check letter of an NRIC/FIN (plan 1.4). The M prefix is not supported (table unconfirmed)."""
    _, r = nric_weighted_sum(prefix, digits)
    return (_LETTERS_ST if prefix in "ST" else _LETTERS_FG)[r]


@check(
    rule="id_format",
    applies_to={"national_id"},
    severity="high",
    description="NRIC/FIN has the form S/T/F/G/M + 7 digits + letter",
    tamper="modified IDs",
)
def id_format(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    read = 0
    for doc in _docs(ctx, "national_id"):
        cell = _cell(doc, "id_number")
        if cell.value is None:
            continue
        read += 1
        number = normalise_id(cell.value)
        if NRIC_RE.match(number):
            continue
        shown = safe_text(mask_id(number))
        flags.append(
            make_flag(
                "id_format",
                docs=[doc],
                field="id_number",
                region=labelled(cell_region(cell), "id_number"),
                evidence=f"ID '{shown}' does not have the form S/T/F/G/M + 7 digits + letter"
                + unverified_note(cell),
                explanation=(
                    f"The ID number {shown} is not a well-formed NRIC/FIN "
                    "(S/T/F/G/M, 7 digits, 1 letter)."
                ),
            )
        )
    if read == 0:
        raise CheckSkipped("no readable id number")
    return flags


@check(
    rule="id_checksum",
    applies_to={"national_id"},
    severity="high",
    description="Check letter matches the 7 digits",
    tamper="modified IDs",
)
def id_checksum(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    verified = 0
    for doc in _docs(ctx, "national_id"):
        cell = _cell(doc, "id_number")
        number = normalise_id(cell.value)
        if not NRIC_RE.match(number) or number[0] not in "STFG":
            continue
        verified += 1
        prefix, digits, printed = number[0], number[1:8], number[8]
        total, r = nric_weighted_sum(prefix, digits)
        expected = nric_check_letter(prefix, digits)
        if printed == expected:
            continue
        offset = " + 4" if prefix in "TG" else ""
        base = total - 4 if prefix in "TG" else total
        shown = safe_text(mask_id(number))
        flags.append(
            make_flag(
                "id_checksum",
                docs=[doc],
                field="id_number",
                region=labelled(cell_region(cell), "id_number"),
                evidence=(
                    f"ID {shown}: printed check letter {printed}, computed {expected} "
                    f"(prefix {prefix}, weighted digit sum {base}{offset}"
                    f"{' = ' + str(total) if offset else ''}, {total} mod 11 = {r})"
                )
                + unverified_note(cell),
                explanation=(
                    f"The check letter on this NRIC does not match its digits "
                    f"({printed} printed, {expected} expected). The ID number is not well-formed."
                ),
            )
        )
    if verified == 0:
        raise CheckSkipped("no S/T/F/G id to verify")
    return flags


# --------------------------------------------------------------------------------------
# date_valid
# --------------------------------------------------------------------------------------


def _age(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


@check(
    rule="date_valid",
    applies_to={"national_id", "passport"},
    severity="high",
    description="Dates are real, not future, plausible age",
    tamper="modified IDs",
)
def date_valid(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "national_id", "passport"):
        expiry_field = "expiry_date" if doc.doc_type == "national_id" else "date_of_expiry"
        names = ("date_of_birth", "date_of_issue", expiry_field)
        cells = {n: _cell(doc, n) for n in names}
        if all(c.value is None and not c.raw for c in cells.values()):
            continue
        evaluated += 1

        def flag(
            field: str,
            disc: str,
            evidence: str,
            explanation: str,
            *extra: Cell,
            doc: Document = doc,
            cells: dict[str, Cell] = cells,
        ) -> None:
            cell = cells.get(field) or _cell(doc, field)
            flags.append(
                make_flag(
                    "date_valid",
                    docs=[doc],
                    field=field,
                    region=labelled(cell_region(cell), field),
                    extra_regions=_cap([cell_region(c) for c in extra]),
                    evidence=evidence + unverified_note(cell, *extra),
                    explanation=explanation,
                    discriminator=disc,
                )
            )

        # real calendar date
        for name, cell in cells.items():
            if cell.value is None and cell.raw:
                printed = safe_text(cell.raw, 40)
                flag(
                    name,
                    "calendar",
                    f"printed '{printed}' is not a real calendar date",
                    f"The {name.replace('_', ' ')} '{printed}' is not a real calendar date.",
                )
        dob = cells["date_of_birth"].date_()
        issue = cells["date_of_issue"].date_()
        expiry = cells[expiry_field].date_()
        today = ctx.today
        if dob is not None:
            if dob > today:
                flag(
                    "date_of_birth",
                    "future",
                    f"date of birth {dob.isoformat()} is after {today.isoformat()}",
                    f"The date of birth {dob.isoformat()} is in the future.",
                )
            else:
                age = _age(dob, today)
                if not MIN_AGE <= age <= MAX_AGE:
                    flag(
                        "date_of_birth",
                        "age",
                        f"date of birth {dob.isoformat()} gives age {age} on {today.isoformat()}, "
                        f"outside {MIN_AGE} to {MAX_AGE}",
                        f"The date of birth {dob.isoformat()} gives an implausible age of {age}.",
                    )
        if issue is not None and issue > today:
            flag(
                "date_of_issue",
                "future",
                f"date of issue {issue.isoformat()} is after {today.isoformat()}",
                f"The date of issue {issue.isoformat()} is in the future.",
            )
        if issue is not None and dob is not None and issue <= dob:
            flag(
                "date_of_issue",
                "order",
                f"date of issue {issue.isoformat()} is not after date of birth {dob.isoformat()}",
                "The document was issued on or before the holder's date of birth.",
                cells["date_of_birth"],
            )
        if expiry is not None and issue is not None and expiry < issue:
            flag(
                expiry_field,
                "order",
                f"expiry {expiry.isoformat()} is before date of issue {issue.isoformat()}",
                "The document expires before it was issued.",
                cells["date_of_issue"],
            )
        if doc.doc_type == "national_id" and dob is not None:
            number = normalise_id(_cell(doc, "id_number").value)
            if number.startswith("S") and dob.year >= 2000:
                id_cell = _cell(doc, "id_number")
                flag(
                    "date_of_birth",
                    "prefix",
                    f"ID {safe_text(mask_id(number))} has prefix S but date of birth is "
                    f"{dob.isoformat()} (S numbers are issued to people born before 2000)",
                    "An S-prefix NRIC cannot belong to someone born in 2000 or later.",
                    id_cell,
                )
    if evaluated == 0:
        raise CheckSkipped("no dates extracted from the ID documents")
    return flags


# --------------------------------------------------------------------------------------
# passport_mrz (ICAO 9303 TD3)
# --------------------------------------------------------------------------------------

_MRZ_CHARS = re.compile(r"^[A-Z0-9<]*$")
_MRZ_WEIGHTS = (7, 3, 1)


def mrz_check_digit(text: str) -> int:
    """ICAO 9303 check digit: values 0-9, A-Z = 10-35, '<' = 0; weights 7,3,1; sum mod 10."""
    total = 0
    for i, ch in enumerate(text):
        if ch.isdigit():
            value = int(ch)
        elif "A" <= ch <= "Z":
            value = ord(ch) - 55
        elif ch == "<":
            value = 0
        else:
            raise ValueError(f"character {ch!r} is not valid in an MRZ")
        total += value * _MRZ_WEIGHTS[i % 3]
    return total % 10


def _mrz_date(
    yymmdd: str, today: date | None, *, century_rule: Literal["birth", "expiry"]
) -> date | None:
    if not re.fullmatch(r"\d{6}", yymmdd):
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    try:
        if century_rule == "expiry" or today is None:
            return date(2000 + yy, mm, dd)
        candidate = date(2000 + yy, mm, dd)
        return candidate if candidate <= today else date(1900 + yy, mm, dd)
    except ValueError:
        return None


def _mrz_names(line1: str) -> tuple[str, str]:
    body = line1[5:]
    surname, _, given = body.partition("<<")
    return (
        " ".join(surname.replace("<", " ").split()),
        " ".join(given.replace("<", " ").split()),
    )


@check(
    rule="passport_mrz",
    applies_to={"passport"},
    severity="high",
    description="MRZ check digits and visual fields agree",
    tamper="modified passports",
)
def passport_mrz(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    seen_mrz = False
    for doc in _docs(ctx, "passport"):
        c1, c2 = _cell(doc, "mrz_line1"), _cell(doc, "mrz_line2")
        if c1.value is None and c2.value is None:
            continue
        seen_mrz = True
        flags.extend(_check_passport(ctx, doc, c1, c2)[:MAX_MRZ_FLAGS])
    if not seen_mrz:
        raise CheckSkipped("no mrz extracted")
    return flags


def _check_passport(ctx: CaseContext, doc: Document, c1: Cell, c2: Cell) -> list[Flag]:
    out: list[Flag] = []
    l1 = "".join((c1.value or "").split())
    l2 = "".join((c2.value or "").split())

    def mrz_flag(
        field: str,
        disc: str,
        evidence: str,
        explanation: str,
        *,
        region_cell: Cell | None = None,
        extra: list[Region | None] | None = None,
    ) -> None:
        base = c1 if field == "mrz_line1" else c2
        cell = region_cell or base
        out.append(
            make_flag(
                "passport_mrz",
                docs=[doc],
                field=field,
                region=labelled(cell_region(cell), field if region_cell is None else disc),
                extra_regions=_cap(extra or []),
                evidence=evidence + unverified_note(base, region_cell),
                explanation=explanation,
                discriminator=disc,
            )
        )

    # 1. length and characters
    bad_format = False
    for field, text, present in (
        ("mrz_line1", l1, c1.value is not None),
        ("mrz_line2", l2, c2.value is not None),
    ):
        if not present:
            continue
        number = field[-1]
        if len(text) != 44:
            bad_format = True
            mrz_flag(
                field,
                f"length_line{number}",
                f"MRZ line {number} has {len(text)} characters, expected 44",
                f"MRZ line {number} has {len(text)} characters instead of 44.",
            )
        elif not _MRZ_CHARS.match(text):
            bad_format = True
            mrz_flag(
                field,
                f"charset_line{number}",
                f"MRZ line {number} contains characters outside A-Z 0-9 <",
                f"MRZ line {number} contains characters that are not allowed in a passport MRZ.",
            )
    if bad_format:
        return out

    # 2. check digits (line 2)
    if c2.value is not None:
        elements = (
            ("passport number", "passport_number_digit", l2[0:9], l2[9]),
            ("date of birth", "date_of_birth_digit", l2[13:19], l2[19]),
            ("expiry date", "expiry_date_digit", l2[21:27], l2[27]),
            ("personal number", "personal_number_digit", l2[28:42], l2[42]),
            (
                "composite",
                "composite_digit",
                l2[0:10] + l2[13:20] + l2[21:43],
                l2[43],
            ),
        )
        for label, disc, data, printed in elements:
            computed = str(mrz_check_digit(data))
            ok = printed == computed
            if label == "personal number" and set(data) == {"<"}:
                ok = printed in ("<", "0")
            if not ok:
                mrz_flag(
                    "mrz_line2",
                    disc,
                    f"{label} check digit: printed {printed}, computed {computed}",
                    f"The {label} check digit in the MRZ is {printed} but its characters give "
                    f"{computed}.",
                )
    # 3. visual zone against the MRZ
    _visual_vs_mrz(ctx, doc, c1, c2, l1, l2, emit=mrz_flag)
    return out


def _visual_vs_mrz(  # noqa: PLR0912, PLR0915
    ctx: CaseContext,
    doc: Document,
    c1: Cell,
    c2: Cell,
    l1: str,
    l2: str,
    *,
    emit: Callable[..., None],
) -> None:
    """Visual-zone comparisons; each mismatch is reported through ``emit`` (the caller's flag
    builder, which owns ordering and the per-passport cap)."""

    def report(
        field_cell: Cell, mrz_field: str, disc: str, evidence: str, explanation: str
    ) -> None:
        mrz_cell = c1 if mrz_field == "mrz_line1" else c2
        emit(
            mrz_field,
            disc,
            evidence,
            explanation,
            region_cell=field_cell if cell_region(field_cell) is not None else None,
            extra=[cell_region(mrz_cell)],
        )

    if l2:
        visual = _cell(doc, "passport_number")
        if visual.value is not None:
            mrz_no = l2[0:9].rstrip("<")
            if normalise_id(visual.value) != mrz_no:
                report(
                    visual,
                    "mrz_line2",
                    "visual_passport_number",
                    f"visual passport number {safe_text(mask_id(normalise_id(visual.value)))}, "
                    f"MRZ {safe_text(mask_id(mrz_no))}",
                    "The passport number printed on the page differs from the one in the MRZ.",
                )
        dob_cell = _cell(doc, "date_of_birth")
        dob = dob_cell.date_()
        if dob is not None:
            mrz_dob = _mrz_date(l2[13:19], ctx.today, century_rule="birth")
            if mrz_dob != dob:
                report(
                    dob_cell,
                    "mrz_line2",
                    "visual_date_of_birth",
                    f"visual date of birth {dob.isoformat()}, MRZ {l2[13:19]}",
                    "The date of birth printed on the page differs from the one in the MRZ.",
                )
        exp_cell = _cell(doc, "date_of_expiry")
        exp = exp_cell.date_()
        if exp is not None:
            mrz_exp = _mrz_date(l2[21:27], None, century_rule="expiry")
            if mrz_exp != exp:
                report(
                    exp_cell,
                    "mrz_line2",
                    "visual_date_of_expiry",
                    f"visual expiry {exp.isoformat()}, MRZ {l2[21:27]}",
                    "The expiry date printed on the page differs from the one in the MRZ.",
                )
        sex_cell = _cell(doc, "sex")
        sex = sex_cell.text().strip().upper()
        if sex:
            mrz_sex = "X" if l2[20] == "<" else l2[20]
            if sex != mrz_sex:
                report(
                    sex_cell,
                    "mrz_line2",
                    "visual_sex",
                    f"visual sex {safe_text(sex, 5)}, MRZ {mrz_sex}",
                    "The sex printed on the page differs from the one in the MRZ.",
                )
        nat_cell = _cell(doc, "nationality")
        nat = nat_cell.text().strip().upper()
        if re.fullmatch(r"[A-Z]{3}", nat) and nat != l2[10:13]:
            report(
                nat_cell,
                "mrz_line2",
                "visual_nationality",
                f"visual nationality {nat}, MRZ {l2[10:13]}",
                "The nationality printed on the page differs from the one in the MRZ.",
            )
    if l1:
        issuing_cell = _cell(doc, "issuing_country")
        issuing = issuing_cell.text().strip().upper()
        if re.fullmatch(r"[A-Z]{3}", issuing) and issuing != l1[2:5]:
            report(
                issuing_cell,
                "mrz_line1",
                "visual_issuing_country",
                f"visual issuing country {issuing}, MRZ {l1[2:5]}",
                "The issuing country printed on the page differs from the one in the MRZ.",
            )
        mrz_surname, mrz_given = _mrz_names(l1)
        for field, mrz_value, disc in (
            ("surname", mrz_surname, "visual_surname"),
            ("given_names", mrz_given, "visual_given_names"),
        ):
            cell = _cell(doc, field)
            if cell.value is None or not mrz_value:
                continue
            verdict = match_person(cell.value, mrz_value).verdict
            if verdict == "different":
                report(
                    cell,
                    "mrz_line1",
                    disc,
                    f"visual {field.replace('_', ' ')} {safe_text(cell.value)}, "
                    f"MRZ {safe_text(mrz_value)}",
                    f"The {field.replace('_', ' ')} printed on the page differ from the MRZ."
                    if field == "given_names"
                    else "The surname printed on the page differs from the one in the MRZ.",
                )


# --------------------------------------------------------------------------------------
# kyc_shareholdings
# --------------------------------------------------------------------------------------


def _is_named(name: str) -> bool:
    cleaned = " ".join(name.casefold().replace(".", " ").split()).strip(" ,;:")
    return cleaned not in NAME_PLACEHOLDERS and bool(cleaned.strip("-"))


def _threshold(ctx: CaseContext, printed: Cell | None) -> Decimal:
    base = ctx.settings.ubo_threshold_percent
    value = printed.money() if printed is not None else None
    if value is not None and value > 0:
        return min(value, base)
    return base


@check(
    rule="kyc_shareholdings",
    applies_to={"shareholder_register", "ownership_chart"},
    severity="high",
    use_cases={"kyc"},
    description="Shareholdings sum to 100% and holders above the UBO threshold are named",
    tamper="altered shareholder records",
)
def kyc_shareholdings(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "shareholder_register"):
        out = _register_flags(ctx, doc)
        if out is not None:
            evaluated += 1
            flags.extend(out)
    for doc in _docs(ctx, "ownership_chart"):
        out = _chart_flags(ctx, doc)
        if out is not None:
            evaluated += 1
            flags.extend(out)
    if evaluated == 0:
        raise CheckSkipped("no readable percentages")
    return flags


def _holder_flag(
    doc: Document,
    table: str,
    row: Row,
    *,
    evidence: str,
    explanation: str,
    name: str,
    severity: Literal["high", "medium"] = "high",
    kind: Literal["person", "company", "auto"] = "auto",
    pct_cell: Cell,
) -> Flag:
    return make_flag(
        "kyc_shareholdings",
        docs=[doc],
        table=table,
        row=row.index,
        region=cell_region(pct_cell, row),
        evidence=evidence + unverified_note(pct_cell),
        explanation=explanation,
        severity=severity,
        discriminator=entity_key(name, kind) or None,
    )


def _missing_holder_key(
    ctx: CaseContext, held: list[_Held], total: Decimal, tol: Decimal
) -> str | None:
    """Discriminator of the one registry holder that is absent from ``held`` when its stake is what
    the printed percentages fall short by. The sum flag and ``x_ownership_vs_registry`` then name
    the same holder, so one missing shareholder is one finding (plan 8.4)."""
    uen = normalise_id(ctx.applicant.uen)
    entity = ctx.registry.lookup(uen) if ctx.registry is not None and uen else None
    if entity is None:
        return None
    reg = _reg_holders(entity.shareholders)
    pairs, ambiguous, _ = _pair_holders(held, reg)
    paired = {p[0] for p in pairs}
    missing = [r for i, r in enumerate(reg) if i not in paired and i not in ambiguous]
    if len(missing) != 1 or missing[0].pct is None:
        return None
    if abs((Decimal(100) - total) - missing[0].pct) > tol + Decimal("0.005"):
        return None
    return entity_key(missing[0].name, missing[0].kind) or None


def _register_flags(ctx: CaseContext, doc: Document) -> list[Flag] | None:
    rows = sorted_rows(doc.tables.get("shareholders"))
    with_pct = [(r, r.cells["percent"]) for r in rows if r.cells["percent"].money() is not None]
    if not with_pct:
        return None
    flags: list[Flag] = []
    cells = [c for _, c in with_pct]
    total = sum((c.money() or Decimal(0) for c in cells), Decimal(0))
    tol = pct_tol(*cells)
    if abs(total - 100) > tol:
        flags.append(
            make_flag(
                "kyc_shareholdings",
                docs=[doc],
                table="shareholders",
                region=union_region([cell_region(c, r) for r, c in with_pct]),
                evidence=f"percentages sum to {_pct(total)}, expected 100.00 (tolerance {format(tol, 'f')})"
                + unverified_note(*cells),
                explanation=(f"The holdings on this register sum to {_pct(total)}%, not 100%."),
                discriminator=_missing_holder_key(
                    ctx, _doc_holders_table(doc, "shareholders"), total, tol
                ),
            )
        )
    total_cell = _cell(doc, "total_shares")
    total_shares = total_cell.int_()
    share_rows = [(r, r.cells["shares"].int_()) for r in rows]
    if total_shares is not None and share_rows and all(s is not None for _, s in share_rows):
        row_sum = sum(s for _, s in share_rows if s is not None)
        if row_sum != total_shares:
            flags.append(
                make_flag(
                    "kyc_shareholdings",
                    docs=[doc],
                    field="total_shares",
                    region=cell_region(total_cell),
                    extra_regions=_cap([_row_region(r) for r, _ in share_rows], 3),
                    evidence=f"rows total {row_sum} shares, register states {total_shares}"
                    + unverified_note(total_cell),
                    explanation=(
                        f"The rows add up to {row_sum} shares but the register states {total_shares}."
                    ),
                )
            )
    if total_shares:
        for row, pct_cell in with_pct:
            shares = row.cells["shares"].int_()
            pct = pct_cell.money()
            if shares is None or pct is None:
                continue
            expected = Decimal(shares) / Decimal(total_shares) * 100
            if abs(pct - expected) > pct_tol(pct_cell) + Decimal("0.005"):
                name = safe_text(row.cells["holder_name"].text())
                flags.append(
                    _holder_flag(
                        doc,
                        "shareholders",
                        row,
                        evidence=(
                            f"row {row.index + 1}: {shares} of {total_shares} shares = "
                            f"{_pct(expected)}%, printed {_pct(pct)}%"
                        ),
                        explanation=(
                            f"The percentage printed for {name or 'this holder'} ({_pct(pct)}%) "
                            f"does not match its shares ({_pct(expected)}%)."
                        ),
                        name="",
                        pct_cell=pct_cell,
                    )
                )
    limit = _threshold(ctx, None)
    for row, pct_cell in with_pct:
        pct = pct_cell.money()
        name_cell = row.cells["holder_name"]
        if pct is not None and pct >= limit and not _is_named(name_cell.text()):
            flags.append(_unnamed_flag(doc, "shareholders", row, pct, pct_cell, name_cell))
    return flags


def _unnamed_flag(
    doc: Document, table: str, row: Row, pct: Decimal, pct_cell: Cell, name_cell: Cell
) -> Flag:
    name = safe_text(name_cell.text())
    return _holder_flag(
        doc,
        table,
        row,
        evidence=f"holder of {_pct(pct)}% is not named ('{name}')",
        explanation=f"A holder of {_pct(pct)}% is not named, so the owner cannot be identified.",
        name=name_cell.text(),
        pct_cell=pct_cell,
    )


@dataclass
class _ChartRow:
    row: Row
    name: str
    key: str
    pct: Decimal | None
    parent_key: str
    parent_raw: str
    is_company: bool
    parent: _ChartRow | None = None
    eff: Decimal | None = None


def _chart_rows(doc: Document) -> list[_ChartRow]:
    out: list[_ChartRow] = []
    for row in sorted_rows(doc.tables.get("holders")):
        name = row.cells["holder_name"].text()
        parent_raw = row.cells["parent_name"].text().strip()
        out.append(
            _ChartRow(
                row=row,
                name=name,
                key=entity_key(name, "company"),
                pct=row.cells["percent"].money(),
                parent_key=entity_key(parent_raw, "company") if parent_raw else "",
                parent_raw=parent_raw,
                is_company=row.cells["holder_type"].text() == "company",
            )
        )
    return out


def _chart_flags(ctx: CaseContext, doc: Document) -> list[Flag] | None:  # noqa: PLR0912
    entries = _chart_rows(doc)
    if not any(e.pct is not None for e in entries):
        return None
    flags: list[Flag] = []
    by_key: dict[str, _ChartRow] = {}
    for e in entries:
        if e.key:
            by_key.setdefault(e.key, e)
    children: dict[str, list[_ChartRow]] = defaultdict(list)
    for e in entries:
        if e.parent_key:
            if e.parent_key in by_key and by_key[e.parent_key] is not e:
                e.parent = by_key[e.parent_key]
                children[e.parent_key].append(e)
            else:
                flags.append(
                    make_flag(
                        "kyc_shareholdings",
                        docs=[doc],
                        table="holders",
                        row=e.row.index,
                        region=cell_region(e.row.cells["parent_name"], e.row),
                        severity="low",
                        evidence=f"parent '{safe_text(e.parent_raw)}' is not on the chart",
                        explanation=(
                            f"{safe_text(e.name) or 'A holder'} sits under "
                            f"'{safe_text(e.parent_raw)}', which is not shown on the chart."
                        ),
                    )
                )

    # effective stakes
    def effective(e: _ChartRow, depth: int = 0) -> Decimal | None:
        if e.pct is None or depth > 10:
            return None
        if not e.parent_key:
            return e.pct
        if e.parent is None:
            return None
        parent_eff = effective(e.parent, depth + 1)
        return None if parent_eff is None else parent_eff * e.pct / 100

    for e in entries:
        e.eff = effective(e)
    # sums
    direct = [e for e in entries if not e.parent_key and e.pct is not None]
    if direct:
        cells = [e.row.cells["percent"] for e in direct]
        total = sum((e.pct or Decimal(0) for e in direct), Decimal(0))
        tol = pct_tol(*cells)
        if abs(total - 100) > tol:
            flags.append(
                make_flag(
                    "kyc_shareholdings",
                    docs=[doc],
                    table="holders",
                    region=union_region(
                        [cell_region(e.row.cells["percent"], e.row) for e in direct]
                    ),
                    evidence=f"direct holders sum to {_pct(total)}, expected 100.00 (tolerance {format(tol, 'f')})"
                    + unverified_note(*cells),
                    explanation=f"The direct holders on this chart sum to {_pct(total)}%, not 100%.",
                    discriminator=_missing_holder_key(
                        ctx, _doc_holders_chart(entries, ""), total, tol
                    ),
                )
            )
    for e in entries:
        kids = [k for k in children.get(e.key, []) if k.pct is not None] if e.key else []
        if not kids or (e.parent_key and e.parent is None):
            continue
        cells = [k.row.cells["percent"] for k in kids]
        total = sum((k.pct or Decimal(0) for k in kids), Decimal(0))
        if abs(total - 100) > pct_tol(*cells):
            flags.append(
                make_flag(
                    "kyc_shareholdings",
                    docs=[doc],
                    table="holders",
                    row=e.row.index,
                    region=union_region([cell_region(k.row.cells["percent"], k.row) for k in kids]),
                    evidence=(
                        f"holders under '{safe_text(e.name)}' sum to {_pct(total)}, expected 100.00"
                    )
                    + unverified_note(*cells),
                    explanation=(
                        f"The holders under {safe_text(e.name)} sum to {_pct(total)}%, not 100%."
                    ),
                )
            )
    # UBO threshold
    limit = _threshold(ctx, _cell(doc, "ubo_threshold_percent"))
    for e in entries:
        if e.eff is None or e.eff < limit:
            continue
        pct_cell = e.row.cells["percent"]
        name_cell = e.row.cells["holder_name"]
        kind: Literal["person", "company"] = "company" if e.is_company else "person"
        if not _is_named(e.name):
            flags.append(_unnamed_flag(doc, "holders", e.row, e.eff, pct_cell, name_cell))
            continue
        label = safe_text(e.name)
        if not e.is_company and e.row.cells["is_ubo"].text() == "false":
            flags.append(
                _holder_flag(
                    doc,
                    "holders",
                    e.row,
                    evidence=(
                        f"{label} holds {_pct(e.eff)}% (threshold {_pct(limit)}%) "
                        "but is not marked as UBO"
                    ),
                    explanation=f"{label} holds {_pct(e.eff)}% but is not marked as a beneficial owner.",
                    name=e.name,
                    kind=kind,
                    pct_cell=pct_cell,
                )
            )
        if e.is_company and not children.get(e.key):
            flags.append(
                _holder_flag(
                    doc,
                    "holders",
                    e.row,
                    evidence=f"no natural person shown behind '{label}' ({_pct(e.eff)}%)",
                    explanation=(
                        f"{label} holds {_pct(e.eff)}% but the chart shows no individual behind it."
                    ),
                    name=e.name,
                    kind=kind,
                    pct_cell=pct_cell,
                    severity="medium",
                )
            )
    return flags


# --------------------------------------------------------------------------------------
# Registry comparison helpers
# --------------------------------------------------------------------------------------


@dataclass
class _Held:
    name: str
    ident: str
    kind: Literal["person", "company"]
    pct: Decimal | None
    pct_cell: Cell
    row: Row | None
    reg_label: str = ""


def _kind_of(holder_type: str | None, ident: str | None) -> Literal["person", "company"]:
    if holder_type == "company":
        return "company"
    if holder_type == "individual":
        return "person"
    return "person" if looks_like_nric(ident) or not ident else "company"


def _pair_holders(
    doc_holders: list[_Held], reg_holders: list[_Held]
) -> tuple[list[tuple[int, int, str]], set[int], set[int]]:
    """Greedy pairing in registry order. Returns (pairs (reg, doc, identity), ambiguous registry
    indices, ambiguous document indices)."""
    used: set[int] = set()
    pairs: list[tuple[int, int, str]] = []
    amb_reg: set[int] = set()
    amb_doc: set[int] = set()
    for ri, reg in enumerate(reg_holders):
        best: tuple[int, int, str] | None = None
        for di, held in enumerate(doc_holders):
            if di in used:
                continue
            identity = same_identity(held.name, held.ident, reg.name, reg.ident, kind=reg.kind)
            if identity in ("match", "match_name_conflict"):
                rank = 0 if ids_conflict_or_equal(held.ident, reg.ident) == "equal" else 1
                if best is None or rank < best[0]:
                    best = (rank, di, identity)
            elif identity == "unknown":
                amb_reg.add(ri)
                amb_doc.add(di)
        if best is not None:
            used.add(best[1])
            pairs.append((ri, best[1], best[2]))
    matched_reg = {p[0] for p in pairs}
    return pairs, amb_reg - matched_reg, amb_doc - used


def _reg_holders(shareholders: list) -> list[_Held]:  # noqa: ANN001
    out: list[_Held] = []
    for sh in shareholders:
        cell = Cell(value=sh.percent, raw=sh.percent, kind="percent")
        try:
            pct = Decimal(sh.percent)
        except ArithmeticError:
            pct = None
        out.append(
            _Held(
                sh.name, sh.id_or_uen or "", _kind_of(sh.holder_type, sh.id_or_uen), pct, cell, None
            )
        )
    return out


def _doc_holders_chart(entries: list[_ChartRow], parent_key: str) -> list[_Held]:
    return [
        _Held(
            e.name,
            e.row.cells["id_or_uen"].text(),
            "company" if e.is_company else "person",
            e.pct,
            e.row.cells["percent"],
            e.row,
        )
        for e in entries
        if e.parent_key == parent_key
    ]


def _doc_holders_table(doc: Document, table: str) -> list[_Held]:
    out: list[_Held] = []
    for row in sorted_rows(doc.tables.get(table)):
        ident = row.cells["id_or_uen"].text()
        if not row.cells["holder_name"].text().strip() and not ident:
            continue
        out.append(
            _Held(
                row.cells["holder_name"].text(),
                ident,
                _kind_of(None, ident),
                row.cells["percent"].money(),
                row.cells["percent"],
                row,
            )
        )
    return out


def _fmt_held_id(ident: str) -> str:
    return f" ({safe_text(mask_id(ident))})" if ident else ""


# --------------------------------------------------------------------------------------
# x_ownership_vs_registry
# --------------------------------------------------------------------------------------


@check(
    rule="x_ownership_vs_registry",
    applies_to={"ownership_chart", "shareholder_register", "company_profile"},
    stage="crossdoc",
    severity="high",
    use_cases={"kyc"},
    description="Chart and register match the registry",
    tamper="ownership chart omits a shareholder",
)
def x_ownership_vs_registry(ctx: CaseContext) -> list[Flag]:
    if ctx.registry is None:
        raise CheckSkipped("registry unavailable")
    uen = normalise_id(ctx.applicant.uen)
    if not uen:
        raise CheckSkipped("applicant uen unknown")
    entity = ctx.registry.lookup(uen)
    if entity is None:
        docs = _docs(ctx, "ownership_chart", "shareholder_register", "company_profile")
        cells = [_cell(d, "uen") for d in docs]
        box = next((cell_region(c) for c in cells if cell_region(c) is not None), None)
        return [
            make_flag(
                "x_ownership_vs_registry",
                docs=docs,
                field="uen",
                region=labelled(box, "uen"),
                evidence=(
                    f"applicant UEN {safe_text(uen, 20)} not found in registry "
                    f"(as of {ctx.registry.as_of.isoformat()})"
                ),
                explanation=(
                    f"The applicant UEN {safe_text(uen, 20)} is not in the registry, so its owners "
                    "cannot be checked."
                ),
            )
        ]
    flags: list[Flag] = []
    reg_holders = _reg_holders(entity.shareholders)
    for doc in _docs(ctx, "ownership_chart"):
        entries = _chart_rows(doc)
        held = _doc_holders_chart(entries, "")
        flags.extend(_compare_holders(ctx, doc, "holders", held, reg_holders, "ownership chart"))
        flags.extend(_look_through(ctx, doc, entries, held, 1))
    for doc in _docs(ctx, "shareholder_register"):
        held = _doc_holders_table(doc, "shareholders")
        flags.extend(
            _compare_holders(ctx, doc, "shareholders", held, reg_holders, "shareholder register")
        )
    for doc in _docs(ctx, "company_profile"):
        held = _doc_holders_table(doc, "shareholders")
        flags.extend(
            _compare_holders(ctx, doc, "shareholders", held, reg_holders, "company profile")
        )
    return flags


def _compare_holders(  # noqa: PLR0912
    ctx: CaseContext,
    doc: Document,
    table: str,
    held: list[_Held],
    reg: list[_Held],
    label: str,
    *,
    scope: str = "",
) -> list[Flag]:
    if not held and not reg:
        return []
    flags: list[Flag] = []
    pairs, amb_reg, amb_doc = _pair_holders(held, reg)
    paired_reg = {p[0] for p in pairs}
    paired_doc = {p[1] for p in pairs}
    where = f"{label} {doc.id}{scope}"
    table_region = union_region([_row_or_none(h) for h in held])
    printed_total = sum((h.pct or Decimal(0) for h in held), Decimal(0))
    for ri, r in enumerate(reg):
        if ri in paired_reg or ri in amb_reg:
            continue
        pct_text = f" {_pct(r.pct)}%" if r.pct is not None else ""
        flags.append(
            make_flag(
                "x_ownership_vs_registry",
                docs=[doc],
                table=table,
                region=labelled(table_region, "holder missing here"),
                evidence=(
                    f"registry holds {safe_text(r.name)}{pct_text}{_fmt_held_id(r.ident)}; "
                    f"not on {where} ({len(held)} holders, {_pct(printed_total)}% printed)"
                ),
                explanation=(
                    f"{safe_text(r.name)} holds{pct_text} per the registry but is missing from the "
                    f"{label}."
                ),
                discriminator=entity_key(r.name, r.kind) or None,
            )
        )
    for di, h in enumerate(held):
        if di in paired_doc or di in amb_doc or not h.name.strip():
            continue
        pct_text = f" {_pct(h.pct)}%" if h.pct is not None else ""
        flags.append(
            make_flag(
                "x_ownership_vs_registry",
                docs=[doc],
                table=table,
                row=h.row.index if h.row else None,
                region=labelled(_row_or_none(h), "not in registry"),
                evidence=(
                    f"{safe_text(h.name)}{pct_text}{_fmt_held_id(h.ident)} is on {where} "
                    "but not among the registry shareholders"
                )
                + unverified_note(h.pct_cell),
                explanation=(
                    f"{safe_text(h.name)} is listed on the {label} but is not a shareholder in "
                    "the registry."
                ),
                discriminator=entity_key(h.name, h.kind) or None,
            )
        )
    for ri, di, identity in pairs:
        r, h = reg[ri], held[di]
        if (
            h.pct is not None
            and r.pct is not None
            and abs(h.pct - r.pct) > pct_tol(h.pct_cell, r.pct_cell)
        ):
            flags.append(
                make_flag(
                    "x_ownership_vs_registry",
                    docs=[doc],
                    table=table,
                    row=h.row.index if h.row else None,
                    region=labelled(cell_region(h.pct_cell, h.row), "percent differs"),
                    evidence=(
                        f"{SHORT_LABELS.get(label, label)} {_pct(h.pct)}%, "
                        f"registry {_pct(r.pct)}% for {safe_text(r.name)}"
                    )
                    + unverified_note(h.pct_cell),
                    explanation=(
                        f"{safe_text(r.name)} holds {_pct(h.pct)}% on the {label} but "
                        f"{_pct(r.pct)}% in the registry."
                    ),
                    discriminator=entity_key(r.name, r.kind) or None,
                )
            )
        if identity == "match_name_conflict":
            flags.append(
                make_flag(
                    "x_ownership_vs_registry",
                    docs=[doc],
                    table=table,
                    row=h.row.index if h.row else None,
                    region=labelled(_row_or_none(h), "name differs"),
                    evidence=(
                        f"{safe_text(mask_id(r.ident))} is '{safe_text(r.name)}' in the registry, "
                        f"'{safe_text(h.name)}' here"
                    )
                    + unverified_note(h.pct_cell),
                    explanation=(
                        f"The ID {safe_text(mask_id(r.ident))} belongs to {safe_text(r.name)} in the "
                        f"registry but is shown with a different name on the {label}."
                    ),
                    discriminator=entity_key(r.name, r.kind) or None,
                )
            )
    return flags


def _row_or_none(h: _Held) -> Region | None:
    if h.row is None:
        return None
    return h.row.box or union_region([c.box for c in h.row.cells.values()])


def _look_through(
    ctx: CaseContext, doc: Document, entries: list[_ChartRow], held: list[_Held], level: int
) -> list[Flag]:
    """Compare the children of a chart company holder with its own registry entry (depth cap 2)."""
    flags: list[Flag] = []
    assert ctx.registry is not None
    for h in held:
        if h.kind != "company" or not h.ident:
            continue
        sub = ctx.registry.lookup(h.ident)
        if sub is None:
            continue
        key = entity_key(h.name, "company")
        kids = _doc_holders_chart(entries, key)
        flags.extend(
            _compare_holders(
                ctx,
                doc,
                "holders",
                kids,
                _reg_holders(sub.shareholders),
                "ownership chart",
                scope=f" under '{safe_text(h.name)}'",
            )
        )
        if level < 2:
            flags.extend(_look_through(ctx, doc, entries, kids, level + 1))
    return flags


# --------------------------------------------------------------------------------------
# x_directors
# --------------------------------------------------------------------------------------


@dataclass
class _Person:
    name: str
    ident: str
    row: Row | None
    doc: Document | None

    @property
    def is_passport(self) -> bool:
        return self.doc is not None and self.doc.doc_type == "passport"


def _is_match(identity: str) -> bool:
    return identity in ("match", "match_name_conflict")


@check(
    rule="x_directors",
    applies_to={"company_profile"},
    requires={"company_profile"},
    stage="crossdoc",
    severity="high",
    use_cases={"kyc"},
    description="Directors match the registry and the ID documents",
    tamper="director not found in registry",
)
def x_directors(ctx: CaseContext) -> list[Flag]:  # noqa: PLR0912, PLR0915
    if ctx.registry is None:
        raise CheckSkipped("registry unavailable")
    uen = normalise_id(ctx.applicant.uen)
    if not uen:
        raise CheckSkipped("applicant uen unknown")
    entity = ctx.registry.lookup(uen)
    if entity is None:
        raise CheckSkipped("entity absent from registry")
    reg_dirs = [
        _Person(o.name, o.id_number or "", None, None)
        for o in entity.officers
        if "director" in o.position.lower()
        and (o.cessation_date is None or o.cessation_date > ctx.today)
    ]
    id_docs: list[_Person] = []
    for doc in _docs(ctx, "national_id"):
        id_docs.append(
            _Person(_cell(doc, "holder_name").text(), _cell(doc, "id_number").text(), None, doc)
        )
    for doc in _docs(ctx, "passport"):
        id_docs.append(
            _Person(
                _cell(doc, "holder_name").text(), _cell(doc, "passport_number").text(), None, doc
            )
        )
    flags: list[Flag] = []
    evaluated = 0
    profile_people: list[_Person] = []
    for doc in _docs(ctx, "company_profile"):
        table = doc.tables.get("directors")
        rows = [r for r in sorted_rows(table) if r.cells["director_name"].text().strip()]
        if not rows:
            continue
        evaluated += 1
        people = [
            _Person(r.cells["director_name"].text(), r.cells["id_number"].text(), r, doc)
            for r in rows
        ]
        profile_people.extend(people)
        # (a) profile director not in the registry
        for p in people:
            ids = [same_identity(p.name, p.ident, d.name, d.ident) for d in reg_dirs]
            if any(_is_match(i) for i in ids) or "unknown" in ids:
                continue
            assert p.row is not None
            flags.append(
                make_flag(
                    "x_directors",
                    docs=[doc],
                    table="directors",
                    row=p.row.index,
                    region=labelled(_row_region(p.row), "not in registry"),
                    evidence=(
                        f"director {safe_text(p.name)}{_fmt_held_id(p.ident)} is on the profile but "
                        f"not among the {len(reg_dirs)} current registry directors"
                    )
                    + unverified_note(p.row.cells["director_name"], p.row.cells["id_number"]),
                    explanation=(
                        f"{safe_text(p.name)} is listed as a director but is not a current director "
                        "in the registry."
                    ),
                    discriminator=entity_key(p.name, "person") or None,
                )
            )
        # (b) registry director missing from the profile
        for d in reg_dirs:
            ids = [same_identity(p.name, p.ident, d.name, d.ident) for p in people]
            if any(_is_match(i) for i in ids) or "unknown" in ids:
                continue
            flags.append(
                make_flag(
                    "x_directors",
                    docs=[doc],
                    table="directors",
                    region=labelled(
                        union_region([_row_region(p.row) for p in people if p.row]),
                        "director missing here",
                    ),
                    evidence=(
                        f"registry director {safe_text(d.name)}{_fmt_held_id(d.ident)} is missing "
                        "from the profile"
                    ),
                    explanation=(
                        f"{safe_text(d.name)} is a director in the registry but is not on the profile."
                    ),
                    discriminator=entity_key(d.name, "person") or None,
                )
            )
        # (c) id conflicts between the profile and the ID documents
        for p in people:
            assert p.row is not None
            for idp in id_docs:
                assert idp.doc is not None
                name_match = match_person(p.name, idp.name)
                verdict = name_match.verdict
                cmp = ids_conflict_or_equal(p.ident, idp.ident)
                # a passport number is not an NRIC: only NRIC cards can contradict the profile id
                if verdict in ("same", "probable") and cmp == "differ" and not idp.is_passport:
                    flags.append(
                        make_flag(
                            "x_directors",
                            docs=[doc, idp.doc],
                            table="directors",
                            row=p.row.index,
                            region=labelled(
                                cell_region(p.row.cells["id_number"], p.row), "id number"
                            ),
                            extra_regions=_cap([cell_region(_cell(idp.doc, "id_number"))]),
                            evidence=(
                                f"id number on profile ends {safe_text(normalise_id(p.ident)[-4:], 8)}, "
                                f"on ID document {idp.doc.id} ends "
                                f"{safe_text(normalise_id(idp.ident)[-4:], 8)}"
                            ),
                            explanation=(
                                f"The ID number shown for {safe_text(p.name)} on the profile differs "
                                "from the number on the ID document."
                            ),
                            discriminator=entity_key(p.name, "person") or None,
                        )
                    )
                elif (
                    (verdict == "different" or name_match.typo)  # a one-letter slip is no match
                    and cmp == "equal"
                    and p.ident
                    and idp.ident
                ):
                    flags.append(
                        make_flag(
                            "x_directors",
                            docs=[doc, idp.doc],
                            table="directors",
                            row=p.row.index,
                            region=labelled(
                                cell_region(p.row.cells["id_number"], p.row), "id number"
                            ),
                            extra_regions=_cap([cell_region(_cell(idp.doc, "holder_name"))]),
                            evidence=(
                                f"id number {safe_text(mask_id(p.ident))} belongs to a different name: "
                                f"profile '{safe_text(p.name)}', ID document {idp.doc.id} "
                                f"'{safe_text(idp.name)}'"
                            ),
                            explanation=(
                                f"The ID number given for {safe_text(p.name)} belongs to someone "
                                "with a different name."
                            ),
                            discriminator=entity_key(p.name, "person") or None,
                        )
                    )
        # (d) info: director without an ID document
        for p in people:
            assert p.row is not None
            linked = any(
                _is_match(same_identity(p.name, p.ident, i.name, i.ident))
                or match_person(p.name, i.name).verdict in ("same", "probable")
                for i in id_docs
            )
            if not linked:
                flags.append(
                    make_flag(
                        "x_directors",
                        docs=[doc],
                        table="directors",
                        row=p.row.index,
                        region=labelled(_row_region(p.row), "no ID document"),
                        severity="info",
                        evidence=f"no ID document supplied for director {safe_text(p.name)}",
                        explanation=f"No ID document was supplied for director {safe_text(p.name)}.",
                        discriminator=entity_key(p.name, "person") or None,
                    )
                )
    if evaluated == 0:
        raise CheckSkipped("no directors extracted from the company profile")
    # (d) info: an ID document linked to no director or owner
    others: list[_Person] = [*profile_people, *reg_dirs]
    for doc in _docs(ctx, "shareholder_register", "company_profile"):
        for row in sorted_rows(doc.tables.get("shareholders")):
            others.append(
                _Person(row.cells["holder_name"].text(), row.cells["id_or_uen"].text(), row, doc)
            )
    for doc in _docs(ctx, "ownership_chart"):
        for row in sorted_rows(doc.tables.get("holders")):
            if row.cells["holder_type"].text() != "company":
                others.append(
                    _Person(
                        row.cells["holder_name"].text(), row.cells["id_or_uen"].text(), row, doc
                    )
                )
    for idp in id_docs:
        assert idp.doc is not None
        linked = any(
            _is_match(same_identity(idp.name, idp.ident, o.name, o.ident))
            or match_person(idp.name, o.name).verdict in ("same", "probable")
            for o in others
        )
        if not linked and idp.name.strip():
            flags.append(
                make_flag(
                    "x_directors",
                    docs=[idp.doc],
                    field="holder_name",
                    region=labelled(cell_region(_cell(idp.doc, "holder_name")), "not linked"),
                    severity="info",
                    evidence=(
                        f"ID of {safe_text(idp.name)} is not linked to a director or owner (guarantor?)"
                    ),
                    explanation=(
                        f"The ID document of {safe_text(idp.name)} is not linked to any director or "
                        "owner in the bundle."
                    ),
                    discriminator=entity_key(idp.name, "person") or None,
                )
            )
    return flags


# --------------------------------------------------------------------------------------
# x_declared_vs_activity (C5)
# --------------------------------------------------------------------------------------

DECLARED_KEYWORDS: dict[str, tuple[str, ...]] = {
    "logistics": ("logistic", "freight", "forward", "shipping", "warehous", "transport", "courier",
                  "cargo", "supply chain"),
    "trading": ("trading", "wholesale", "import", "export", "distribut", "merchant"),
    "manufacturing": ("manufactur", "fabricat", "assembl", "production of"),
    "professional_services": ("consult", "accounting", "advisory", "legal", "engineering services",
                              "software", "it services"),
    "crypto_virtual_assets": ("crypto", "virtual asset", "digital token", "blockchain"),
    "gambling": ("gaming", "gambling", "casino", "betting"),
    "money_services": ("remittance", "money changer", "payment services"),
}  # fmt: skip
NON_RISKY = ("logistics", "trading", "manufacturing", "professional_services")


def declared_categories(text: str | None) -> set[str]:
    """C5 labels whose keywords occur in the declared principal activity (case-insensitive)."""
    low = (text or "").casefold()
    return {label for label, words in DECLARED_KEYWORDS.items() if any(w in low for w in words)}


@dataclass
class _Obs:
    doc: Document
    row: Row
    amount: Decimal
    is_credit: bool
    counterparty: str


@check(
    rule="x_declared_vs_activity",
    applies_to={"company_profile"},
    requires={"company_profile", "bank_statement"},
    stage="crossdoc",
    severity="medium",
    use_cases={"kyc"},
    description="Declared business against counterparty activity",
    tamper="declares logistics, statements show crypto",
)
def x_declared_vs_activity(ctx: CaseContext) -> list[Flag]:  # noqa: PLR0912
    activity = ""
    for doc in _docs(ctx, "company_profile"):
        text = _cell(doc, "principal_activity").text().strip()
        if text:
            activity = text
            break
    declared = declared_categories(activity)
    statements = _docs(ctx, "bank_statement")
    currencies = {(_cell(d, "currency").text().upper() or "SGD") for d in statements}
    if len(currencies) > 1:
        raise CheckSkipped("currency mismatch")
    min_prob = ctx.settings.classifier_min_prob
    by_label: dict[str, list[_Obs]] = defaultdict(list)
    turnover = Decimal(0)
    annotated = 0
    for doc in statements:
        for row in sorted_rows(doc.tables.get("transactions")):
            debit, credit = row.cells["debit"].money(), row.cells["credit"].money()
            amount = credit if credit is not None else debit
            if amount is None:
                continue
            turnover += amount
            ann = row.annotations.get("c5")
            if ann is None:
                continue
            annotated += 1
            if ann.probability < min_prob or ann.label == "other":
                continue
            by_label[ann.label].append(
                _Obs(doc, row, amount, credit is not None, counterparty_of(row) or "")
            )
    if annotated == 0:
        raise CheckSkipped("no c5 annotations")
    if not declared:
        raise CheckSkipped("declared activity not mappable")
    declared_text = ", ".join(sorted(declared))
    flags: list[Flag] = []

    def build(label: str, obs: list[_Obs], severity: str | None, evidence: str, expl: str) -> Flag:
        first = obs[0]
        docs = sorted({o.doc.id for o in obs}, key=natural_key)

        def amount_region(o: _Obs) -> Region | None:
            cell = o.row.cells["credit" if o.is_credit else "debit"]
            return cell_region(cell, o.row)

        return make_flag(
            "x_declared_vs_activity",
            docs=docs,
            table="transactions",
            row=first.row.index,
            region=labelled(amount_region(first), label),
            extra_regions=_cap([amount_region(o) for o in obs[1:]], 5),
            severity=severity,  # type: ignore[arg-type]
            evidence=evidence,
            explanation=expl,
            discriminator=label,
        )

    for label in RISKY_LABELS:
        if label in declared:
            continue
        obs = [o for o in by_label.get(label, []) if o.is_credit or label != "money_services"]
        if not obs:
            continue
        amount = sum((o.amount for o in obs), Decimal(0))
        share = amount / turnover if turnover > 0 else Decimal(0)
        if len(obs) < DVA_MIN_ROWS and share < DVA_MIN_SHARE:
            continue
        counts: dict[str, int] = defaultdict(int)
        for o in obs:
            counts[o.counterparty] += 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        top_text = ", ".join(f"{safe_text(n, 40)} x{c}" for n, c in top if n)
        pretty = label.replace("_", " ")
        flags.append(
            build(
                label,
                obs,
                None,
                f"declared: {declared_text}; {label}: {len(obs)} rows, {fmt_money(amount)} "
                f"({float(share) * 100:.1f}% of turnover)"
                + (f"; top counterparties: {top_text}" if top_text else ""),
                f"The statements show {pretty} activity ({float(share) * 100:.1f}% of turnover) "
                f"that the declared business ({declared_text}) does not mention.",
            )
        )
    sized = []
    for label in NON_RISKY:
        obs = by_label.get(label, [])
        if obs:
            sized.append((sum((o.amount for o in obs), Decimal(0)), label))
    if sized and turnover > 0:
        amount, label = max(sized, key=lambda t: (t[0], t[1]))
        share = amount / turnover
        if share >= DVA_DOMINANT_SHARE and label not in declared:
            flags.append(
                build(
                    label,
                    by_label[label],
                    "low",
                    f"{float(share) * 100:.1f}% of turnover goes to/comes from '{label}' "
                    f"counterparties; declared: {declared_text}",
                    f"{float(share) * 100:.1f}% of turnover relates to {label.replace('_', ' ')}, "
                    f"which differs from the declared business ({declared_text}).",
                )
            )
    return flags
