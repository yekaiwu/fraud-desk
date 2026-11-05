"""Money parsing and comparison (docs/data-model.md section 2).

Amounts are ``Decimal`` in Python, plain decimal strings in JSON. Floats are rejected on purpose.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, getcontext, localcontext

MONEY = Decimal("0.01")
DEFAULT_TOLERANCE = Decimal("1.00")

# Recognised currency prefixes. A letter touching a digit is only allowed inside one of these
# (so "S8222222A" or "T08LL1234D" or "12A" are never amounts, plan 8.5).
_CURRENCY_PREFIX = re.compile(
    r"^(?:S\$|US\$|SGD|USD|EUR|GBP|MYR|IDR|HKD|CNY|RMB|JPY|AUD|THB|INR|NZD|CHF|\$|€|£)\s*",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"\s+(CR|DR)$", re.IGNORECASE)
# Number shapes. A COMMA followed by exactly three digits is a thousands separator ("1,234"), a comma
# followed by one or two digits at the very end is the decimal mark ("978,12"). A POINT is always the
# decimal mark, whatever follows it: "12.500" is twelve and a half, and points are never read as
# thousands separators, so "1.000.000" is not an amount (it can be read two ways, and an id or a
# version number looks the same). Anything else is not a number.
_US = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?$")  # 1,234.56  1234.5  .5
_EU = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,2}$")  # 1.234,56  978,12  9,5
_SPACED = re.compile(r"^\d{1,3}(?: \d{3})+(?:[.,]\d{1,2})$")  # 2 053,73  1 319.97 (decimals needed)
_EMPTY = {"", "-", "--", "N/A", "NA", "NIL", "N.A."}
# the other minus signs a PDF prints (U+2212 minus, U+2013 en dash, U+2012 figure dash) are a minus
_MINUS = str.maketrans({"\u2212": "-", "\u2013": "-", "\u2012": "-"})
# More integer digits than this is not an amount (a trillion-trillion is a misread, not a balance), and
# a document that prints one is unreadable, not a crash: rounding it would exceed the decimal context.
MAX_INTEGER_DIGITS = 18


def quantize(d: Decimal, places: int = 2) -> Decimal:
    """Round half up to ``places`` decimals; never returns negative zero."""
    q = Decimal(1).scaleb(-places)
    need = d.adjusted() + places + 2 if d.is_finite() else 0
    if (
        need > getcontext().prec
    ):  # a very large figure: round it in a context wide enough to hold it
        with localcontext() as wide:
            wide.prec = need
            out = d.quantize(q, rounding=ROUND_HALF_UP)
    else:
        out = d.quantize(q, rounding=ROUND_HALF_UP)
    return abs(out) if out == 0 else out


def parse_money(text: str | None) -> Decimal | None:
    """Parse a printed amount; ``None`` when it is not an amount.

    Accepts ``1,234.56``, ``S$1,234.56``, ``SGD 1,234.56``, ``$ 1234.5``, ``(1,234.56)`` (negative),
    trailing `` DR`` (negative) and `` CR`` (positive), a trailing minus (``2.05-``), and the
    decimal-comma forms ``978,12``, ``$ 2 053,73`` and ``1.234,56`` (see ``parse_number``). Returns ``None`` for ``""``, ``"-"``,
    ``"N/A"`` and whenever a letter touches a digit outside a currency prefix or a space-separated
    CR/DR suffix.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        raise TypeError(f"parse_money expects str or None, got {type(text).__name__}")
    s = " ".join(text.translate(_MINUS).split())
    if s.upper() in _EMPTY:
        return None

    negative = False
    suffix = _SUFFIX.search(s)
    if suffix:
        negative = suffix.group(1).upper() == "DR"
        s = s[: suffix.start()].strip()

    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    for _ in range(2):  # a sign may come before or after the currency prefix
        if s[:1] in ("+", "-"):
            negative = negative or s[0] == "-"
            s = s[1:].lstrip()
        prefix = _CURRENCY_PREFIX.match(s)
        if prefix:
            s = s[prefix.end() :].lstrip()

    if re.search(r"[.,]\d{1,2}-$", s):  # a printed trailing minus after the decimals: "2.05-"
        negative = True
        s = s[:-1].rstrip()
    value = parse_number(s)
    if value is None:
        return None
    exponent = value.as_tuple().exponent
    # whole numbers and one decimal get cents; so do zeros after the cents ("12.500" is 12.50)
    if exponent > -2 or value == value.quantize(MONEY):  # type: ignore[operator]
        value = value.quantize(MONEY)
    if negative:
        value = -value
    return value if value != 0 else abs(value)


def parse_number(text: str) -> Decimal | None:
    """Digits with separators to a ``Decimal``, no sign and no currency; ``None`` when the shape is
    not a number. Both conventions are read: ``1,234.56`` and ``1.234,56``, ``978,12`` (a comma
    before one or two final digits is a decimal mark) against ``1,234`` (three digits: thousands).
    ``2 053,73`` (a space between groups, decimals required) is read too. Ids, dates, phone
    numbers and OCR debris (``21053;73)``, ``1,234,56``, ``12,3456``) are not numbers."""
    s = text.strip()
    if not s or s == ".":
        return None
    if _US.match(s):
        digits = s.replace(",", "")
    elif _EU.match(s):
        digits = s.replace(".", "").replace(",", ".")
    elif _SPACED.match(s):
        digits = s.replace(" ", "").replace(",", ".")
    else:
        return None
    try:
        out = Decimal(digits)
    except InvalidOperation:
        return None
    if out.adjusted() >= MAX_INTEGER_DIGITS:
        return None
    return out


def to_decimal(value: str | None) -> Decimal | None:
    """Canonical decimal string (``Cell.value``) to ``Decimal``; ``None`` for None/empty/invalid."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"to_decimal expects str or None, got {type(value).__name__}")
    s = value.strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d.adjusted() < MAX_INTEGER_DIGITS else None


def fmt_money(d: Decimal) -> str:
    """Quantize to 0.01 and format as a plain string (``format(d, "f")``)."""
    return format(quantize(d, 2), "f")


def approx_eq(a: Decimal, b: Decimal, tol: Decimal = DEFAULT_TOLERANCE) -> bool:
    """Inclusive comparison: a difference of exactly ``tol`` passes."""
    for v in (a, b, tol):
        if isinstance(v, float):
            raise TypeError("approx_eq does not accept float; use Decimal")
    return abs(a - b) <= tol


def total(values: Iterable[Decimal | None]) -> Decimal:
    """Sum ignoring None."""
    out = Decimal("0")
    for v in values:
        if v is not None:
            out += v
    return out


# A printed figure as a reader would call it an amount: two decimals, or thousands separators.
_AMOUNT_TOKEN = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})\)?$")


def count_amount_tokens(texts: Iterable[str]) -> int:
    """How many of ``texts`` look like a printed money figure (``1,234.56``, ``(12,000)``, ``0.15``).
    Years, IDs, quantities without separators and page numbers do not."""
    return sum(1 for t in texts if _AMOUNT_TOKEN.match(t.strip()))
