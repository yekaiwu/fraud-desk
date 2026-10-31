"""MRZ repair for scan readings (ICAO 9303 sample passport)."""

from __future__ import annotations

from app.core.context import CaseContext
from app.core.models import Cell
from app.core.testing import make_ctx
from app.extract.base import ExtractionResult
from app.extract.kyc_mrz_repair import _valid, repair_line1, repair_line2, repair_scanned_mrz

L1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
L2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


def swap(s: str, i: int, c: str) -> str:
    return s[:i] + c + s[i + 1 :]


def test_the_sample_is_valid() -> None:
    assert _valid(L2)
    assert repair_line2(L2) == L2


def test_lookalike_slips_are_repaired_when_the_checksums_say_so() -> None:
    assert repair_line2(swap(L2, 5, "O")) == L2  # 0 read as O inside the passport number
    assert repair_line2(swap(L2, 22, "Z")) == L2  # a letter in the expiry date
    assert repair_line2(swap(L2, 26, "S")) == L2
    assert repair_line2(L2.replace("<", "«", 3)) == L2  # angle brackets read as guillemets


def test_a_changed_digit_is_not_healed() -> None:
    tampered = swap(L2, 23, "9")  # a different digit: no look-alike substitution can hold
    assert repair_line2(tampered) == tampered
    assert not _valid(tampered)


def test_two_errors_and_wrong_lengths_are_left_alone() -> None:
    two = swap(swap(L2, 5, "O"), 3, "B")
    assert repair_line2(two) == two
    assert repair_line2(L2[:-1]) == L2[:-1]


def test_first_line_holds_letters_only_after_the_state() -> None:
    assert repair_line1(L1.replace("A", "4", 1)) == L1
    assert repair_line1(L1) == L1


def test_only_scan_readings_are_rewritten() -> None:
    bad = swap(L2, 5, "O")
    ctx: CaseContext = make_ctx([])
    scan = ExtractionResult(
        fields={"mrz_line2": Cell(value=bad, raw=bad, source="llm_cheap")}, tables={}
    )
    repair_scanned_mrz(scan, ctx, None)  # type: ignore[arg-type]
    assert scan.fields["mrz_line2"].value == L2
    assert scan.fields["mrz_line2"].alt_raw == bad
    layer = ExtractionResult(
        fields={"mrz_line2": Cell(value=bad, raw=bad, source="text_layer")}, tables={}
    )
    repair_scanned_mrz(layer, ctx, None)  # type: ignore[arg-type]
    assert layer.fields["mrz_line2"].value == bad
