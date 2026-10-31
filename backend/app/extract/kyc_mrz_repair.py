"""Repair of a passport MRZ that was read from a scan (ICAO 9303 TD3).

An OCR or vision reading of the machine-readable zone confuses the look-alike characters of its font
(``O`` and ``0``, ``I`` and ``1``, ``S`` and ``5``, ``B`` and ``8``, ``Z`` and ``2``, ``G`` and ``6``). The
MRZ states its own checksums, so a reading can be put right with evidence: each position has a known
class (digit or letter), and where the checksums still fail, a single look-alike substitution is
accepted only if it makes every check digit hold. If no such substitution exists the reading is left
as it was, so a genuinely altered MRZ still fails the ``passport_mrz`` check.

Only cells read by a model from a scan are touched (``source`` is not ``text_layer``): a text layer is
the document's own text and is never rewritten. The unrepaired reading is kept in ``Cell.alt_raw``.
"""

from __future__ import annotations

from app.checks.kyc import mrz_check_digit
from app.core.context import CaseContext
from app.core.models import Cell, Document
from app.extract.base import ExtractionResult
from app.extract.registry import register_postprocessor

LINE = 44
_TO_DIGIT = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
    "G": "6",
}
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A", "3": "E"}
_LOOKALIKES = ("O0DQ", "I1L", "Z2", "S5", "B8", "G6")
_ANGLES = {"«": "<", "‹": "<", "(": "<", "[": "<", "{": "<"}
_DIGIT_POSITIONS = (
    *range(13, 20),
    *range(21, 28),
    9,
    42,
    43,
)  # birth, expiry, and the check digits
_LETTER_POSITIONS = (*range(10, 13),)  # nationality
_SEX_POSITION = 20


def _clean(text: str) -> str:
    out = "".join(_ANGLES.get(ch, ch) for ch in "".join(text.split()).upper())
    return out


def _valid(line2: str) -> bool:
    try:
        return all(
            str(mrz_check_digit(data)) == printed or (set(data) == {"<"} and printed in "<0")
            for data, printed in (
                (line2[0:9], line2[9]),
                (line2[13:19], line2[19]),
                (line2[21:27], line2[27]),
                (line2[28:42], line2[42]),
                (line2[0:10] + line2[13:20] + line2[21:43], line2[43]),
            )
        )
    except ValueError:
        return False


def repair_line2(text: str) -> str:
    """The second MRZ line with look-alike slips put right, or ``text`` unchanged when it cannot be
    repaired with evidence (wrong length, or no single substitution satisfies the check digits)."""
    line = _clean(text)
    if len(line) != LINE:
        return text
    chars = list(line)
    for i in _DIGIT_POSITIONS:
        chars[i] = _TO_DIGIT.get(chars[i], chars[i])
    for i in _LETTER_POSITIONS:
        chars[i] = _TO_LETTER.get(chars[i], chars[i])
    if chars[_SEX_POSITION] not in "MF<":
        chars[_SEX_POSITION] = "<"
    classed = "".join(chars)
    if _valid(classed):
        return classed
    found: list[str] = []
    for i, ch in enumerate(classed):
        group = next((g for g in _LOOKALIKES if ch in g), None)
        if group is None:
            continue
        for alt in group:
            if alt != ch:
                trial = classed[:i] + alt + classed[i + 1 :]
                if _valid(trial):
                    found.append(trial)
    # one candidate is evidence; several are a coin toss (a check digit alone holds by chance 1 in 10)
    return found[0] if len(found) == 1 else text


def repair_line1(text: str) -> str:
    """The first MRZ line: after ``P<`` and the issuing state only letters and ``<`` occur."""
    line = _clean(text)
    if len(line) != LINE or not line.startswith("P"):
        return text
    return line[:2] + "".join(_TO_LETTER.get(ch, ch) for ch in line[2:])


@register_postprocessor(doc_types={"passport"})
def repair_scanned_mrz(result: ExtractionResult, ctx: CaseContext, doc: Document) -> None:
    for name, fix in (("mrz_line1", repair_line1), ("mrz_line2", repair_line2)):
        cell = result.fields.get(name)
        if cell is None or not cell.value or cell.source == "text_layer":
            continue
        fixed = fix(cell.value)
        if fixed != "".join(cell.value.split()) and fixed != cell.value:
            result.fields[name] = Cell(
                **{**cell.model_dump(), "value": fixed, "alt_raw": cell.alt_raw or cell.value}
            )
            result.warnings.append(f"mrz_repaired: {name}")
