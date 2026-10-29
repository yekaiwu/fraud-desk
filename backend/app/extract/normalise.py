"""Canonical values from printed text (docs/ingest-and-extraction.md 6.2, data-model.md 2 and 5).

The model (or the text layer) hands us text exactly as printed; this module turns it into
``Cell.value``. **No guessing, no repair**: a value that cannot be parsed becomes ``None`` with the
raw text kept and an ``unparsed`` warning code. Absolute date formats only (no relative dates, so
nothing here reads the clock).

``canonical`` returns ``(value, warning_code)``; ``warning_code`` is one of ``"unparsed"``,
``"negative_in_amount_column"`` or ``None``. ``format_warning`` builds the stable warning string.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.core.models import RAW_MAX, Cell, CellKind, CellSource, Region, truncate
from app.core.money import fmt_money, parse_money

__all__ = [
    "SCALE_WORDS",
    "canonical",
    "currency_code",
    "format_warning",
    "looks_like_date",
    "make_cell",
    "parse_date",
]

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_FULL = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "sept",
}

_DMY_NAME = re.compile(r"^(\d{1,2})[\s\-/.]+([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{4}|\d{2})$")
_MDY_NAME = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_DMY_NUM = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")

# Date-shaped text anywhere (used to find rows that start with a date and a period's two dates).
DATE_FRAGMENT = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}"
    r"|\d{1,2}[\s\-/.]+[A-Za-z]{3,9}\.?[\s\-/.,]+(?:\d{4}|\d{2})"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})\b"
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))

CURRENCY_CODES = frozenset(
    [
        "SGD",
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CNY",
        "RMB",
        "HKD",
        "MYR",
        "IDR",
        "THB",
        "INR",
        "AUD",
        "NZD",
        "CAD",
        "CHF",
        "KRW",
        "TWD",
        "PHP",
        "VND",
        "AED",
        "SAR",
        "QAR",
        "ZAR",
        "BRL",
        "MXN",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "CZK",
        "HUF",
        "TRY",
        "RUB",
        "ILS",
        "EGP",
        "PKR",
        "BDT",
        "LKR",
        "KWD",
        "BHD",
        "OMR",
    ]
)
_CURRENCY_WORDS = {
    "singapore dollar": "SGD",
    "singapore dollars": "SGD",
    "us dollar": "USD",
    "us dollars": "USD",
    "united states dollars": "USD",
    "s$": "SGD",
    "$": "SGD",
    "us$": "USD",
    "sgd": "SGD",
    "rmb": "CNY",
    "€": "EUR",
    "£": "GBP",
}

SCALE_WORDS = (
    (re.compile(r"in\s+thousands?|s?\$?\s*'000|\(?\s*000'?s\s*\)?|in\s+'000|\bk\$", re.I), "1000"),
    (re.compile(r"in\s+millions?|\$\s*m\b|\bs\$\s*m\b|'m\b|\bmillions?\b", re.I), "1000000"),
)

_ENUM_SYNONYMS: dict[str, str] = {
    "unaudited": "unaudited",
    "management accounts": "unaudited",
    "draft": "unaudited",
    "audited": "audited",
    "paid": "paid",
    "unpaid": "unpaid",
    "outstanding": "unpaid",
    "partially paid": "partial",
    "part paid": "partial",
    "part-paid": "partial",
    "partial": "partial",
    "in thousands": "1000",
    "'000": "1000",
    "s$'000": "1000",
    "in millions": "1000000",
    "'m": "1000000",
}

_SUFFIX = re.compile(r"\s+(CR|DR)$", re.I)


def _ws(text: str) -> str:
    return " ".join(text.translate(_ZERO_WIDTH).split())


def format_warning(code: str, where: str, raw: str | None = None) -> str:
    """Stable warning text: ``unparsed: <where> raw="<40 chars>"`` / ``negative_in_amount_column: <where>``."""
    if code == "negative_in_amount_column":
        return f"negative_in_amount_column: {where}"
    snippet = (raw or "")[:40]
    return f'{code}: {where} raw="{snippet}"'


# ---------------------------------------------------------------------------------- dates


def looks_like_date(text: str) -> bool:
    """True when the whole text has the *shape* of a printed date, valid or not (31 Feb 2026 is)."""
    t = _ws(text)
    m = DATE_FRAGMENT.fullmatch(t)
    return m is not None


def parse_date(raw: str) -> date | None:
    """Day-first parse of the accepted printed forms; ``None`` for anything else, including an
    impossible date (never rolled over)."""
    t = _ws(raw).strip(" ,;")
    year: int
    month: int
    day: int
    m = _ISO.match(t)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DMY_NUM.match(t)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = _DMY_NAME.match(t)
            if m:
                day, name, ytxt = int(m.group(1)), m.group(2), m.group(3)
                mm = _month(name)
                if mm is None:
                    return None
                month, year = mm, _year(ytxt)
            else:
                m = _MDY_NAME.match(t)
                if not m:
                    return None
                name, day, year = m.group(1), int(m.group(2)), int(m.group(3))
                mm = _month(name)
                if mm is None:
                    return None
                month = mm
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month(name: str) -> int | None:
    n = name.casefold()
    if len(n) == 3:
        return _MONTHS.get(n)
    if n in _MONTH_FULL and n[:3] in _MONTHS:
        return _MONTHS[n[:3]]
    return None


def _year(txt: str) -> int:
    if len(txt) == 4:
        return int(txt)
    y = int(txt)
    return 2000 + y if y <= 69 else 1900 + y


# ---------------------------------------------------------------------------------- currency


def currency_code(raw: str) -> str | None:
    t = _ws(raw).strip().strip("()").casefold()
    if t in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[t]
    up = t.upper()
    if len(up) == 3 and up in CURRENCY_CODES:
        return up
    # "SGD 1,234.00" or "S$" followed by something: look at the leading token only
    first = re.match(r"^(US\$|S\$|\$|€|£|[A-Za-z]{3})(?![A-Za-z])", _ws(raw).strip())
    if first:
        tok = first.group(1)
        if tok.casefold() in _CURRENCY_WORDS:
            return _CURRENCY_WORDS[tok.casefold()]
        if tok.upper() in CURRENCY_CODES:
            return tok.upper()
    return None


# ---------------------------------------------------------------------------------- canonical


def canonical(
    kind: CellKind,
    raw: str | None,
    *,
    values: list[str] | None = None,
    magnitude: bool = False,
) -> tuple[str | None, str | None]:
    """``(value, warning_code)`` for printed text of a given kind.

    ``magnitude=True`` is for debit/credit/amount columns: a minus sign or brackets are dropped
    (warning ``negative_in_amount_column``); a ``DR``/``CR`` suffix is dropped silently.
    """
    if raw is None:
        return None, None
    text = _ws(raw)
    if text == "" or text in ("-", "--", "–", "—", "N/A", "n/a", "NIL", "nil"):
        return None, None

    if kind == "text":
        return text, None

    if kind == "money":
        value = parse_money(text)
        if value is None:
            return None, "unparsed"
        if magnitude:
            inner = _SUFFIX.sub("", text).strip()
            printed_negative = inner[:1] in ("(", "-", "\u2212")
            warn = "negative_in_amount_column" if printed_negative else None
            return fmt_money(abs(value)), warn
        return fmt_money(value), None

    if kind == "decimal":
        cleaned = text.replace(",", "")
        try:
            d = Decimal(cleaned)
        except InvalidOperation:
            return None, "unparsed"
        if not d.is_finite():
            return None, "unparsed"
        return format(d, "f"), None

    if kind == "percent":
        cleaned = text.replace("%", "").replace(",", "").strip()
        try:
            d = Decimal(cleaned)
        except InvalidOperation:
            return None, "unparsed"
        if not d.is_finite():
            return None, "unparsed"
        out = format(d, "f")
        if "." not in out:
            out += ".00"
        elif len(out.split(".")[1]) < 2:
            out += "0"
        return out, None

    if kind == "int":
        cleaned = text.replace(",", "")
        return (cleaned, None) if re.fullmatch(r"\d+", cleaned) else (None, "unparsed")

    if kind == "date":
        d = parse_date(text)
        return (d.isoformat(), None) if d else (None, "unparsed")

    if kind == "id":
        return "".join(text.split()).upper(), None

    if kind == "bool":
        low = text.casefold()
        if low in ("true", "yes", "y"):
            return "true", None
        if low in ("false", "no", "n"):
            return "false", None
        return None, "unparsed"

    if kind == "enum":
        return _enum(text, values)

    return text, None


def _enum(text: str, values: list[str] | None) -> tuple[str | None, str | None]:
    low = text.casefold()
    if values is None:  # currency
        code = currency_code(text)
        return (code, None) if code else (None, "unparsed")
    for v in values:
        if low == v.casefold():
            return v, None
    syn = _ENUM_SYNONYMS.get(low)
    if syn is not None and syn in values:
        return syn, None
    # "in thousands" style scale phrases and "Audited"/"Management Accounts" inside a longer phrase
    if "1000" in values or "1000000" in values:
        for pattern, scale in SCALE_WORDS:
            if pattern.search(text) and scale in values:
                return scale, None
    if "audited" in values:
        if re.search(r"\bun-?audited\b|management accounts", low):
            return "unaudited", None
        if re.search(r"\baudited\b", low):
            return "audited", None
    if "partial" in values and re.search(r"\bpart(?:ially|ial)?\b.*\bpaid\b", low):
        return "partial", None
    return None, "unparsed"


# ---------------------------------------------------------------------------------- cells


def make_cell(
    raw: str | None,
    kind: CellKind,
    *,
    source: CellSource,
    box: Region | None,
    verified: bool | None,
    alt_raw: str | None = None,
    values: list[str] | None = None,
    magnitude: bool = False,
) -> tuple[Cell, str | None]:
    """A ``Cell`` from printed text; the second element is a warning *code* (see ``canonical``).

    ``raw`` is kept as printed (capped at 500 characters by the model). A blank/None raw gives an
    empty cell: ``value=None``, ``verified=None`` and no box.
    """
    text = None if raw is None else _ws(raw)
    if text is None or text == "":
        return Cell(value=None, raw=None, kind=kind, source=source, verified=None), None
    value, warn = canonical(kind, text, values=values, magnitude=magnitude)
    if value is None and warn is None:  # a printed dash: an empty cell
        return Cell(value=None, raw=None, kind=kind, source=source, verified=None), None
    cell = Cell(
        value=value,
        raw=truncate(text, RAW_MAX),
        kind=kind,
        box=box,
        source=source,
        verified=verified,
        alt_raw=alt_raw,
    )
    return cell, warn
