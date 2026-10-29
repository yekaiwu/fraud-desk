"""NRIC/FIN card and passport extractors (T-extract-kyc-04; test 27: MRZ 44 and 43 characters)."""

from __future__ import annotations

import pytest

from app.extract.base import LayoutNotRecognised
from app.extract.registry import get_extractors
from tests.extract_helpers import draw_pages, make_case
from tests.extract_kyc_helpers import (
    MRZ1,
    MRZ2,
    assert_all_boxed_and_verified,
    parse,
    render_nric,
    render_passport,
    values,
)

NRIC = "national_id"
PASSPORT = "passport"


# ------------------------------------------------------------------------------------ national id


def test_nric_card_fields_with_labels_printed_above_their_values() -> None:
    result, _ctx, _doc = parse(render_nric(), NRIC)
    assert values(result.fields) == {
        "holder_name": "TAN WEI MING",  # as printed (upper case), not re-cased
        "id_number": "S1234567D",
        "date_of_birth": "1985-03-12",
        "sex": "M",
        "nationality": "SINGAPORE CITIZEN",
        "date_of_issue": "2015-08-13",
        "expiry_date": None,  # a card with no expiry prints none
        "address": "10 Anson Road #05-01 Singapore 079903",
    }
    assert_all_boxed_and_verified(result)
    assert result.warnings == []


def test_nric_with_a_printed_expiry() -> None:
    result, _ctx, _doc = parse(render_nric(expiry="12 Mar 2035"), NRIC)
    assert result.fields["expiry_date"].value == "2035-03-12"


def test_a_malformed_id_number_is_kept_exactly_as_printed() -> None:
    """Tamper: a digit deleted. No repair, no check-letter logic here (id_format/id_checksum flag it)."""
    result, _ctx, _doc = parse(render_nric(id_number="S123457D"), NRIC)
    cell = result.fields["id_number"]
    assert (cell.value, cell.raw, cell.verified) == ("S123457D", "S123457D", True)


def test_nothing_is_masked_in_the_cell_value() -> None:
    result, _ctx, _doc = parse(render_nric(), NRIC)
    assert "*" not in (result.fields["id_number"].value or "")


def test_an_invalid_printed_date_gives_none_with_raw_kept() -> None:
    result, _ctx, _doc = parse(render_nric(dob="31 Feb 1985"), NRIC)
    dob = result.fields["date_of_birth"]
    assert dob.value is None and dob.raw == "31 Feb 1985"
    assert any(w.startswith("unparsed: fields.date_of_birth") for w in result.warnings)


def test_id_number_found_by_its_shape_when_the_label_is_missing() -> None:
    pdf = draw_pages(
        [
            [
                (60, 100, "Name", "l", 7.5),
                (60, 110, "TAN WEI MING", "l", 10, True),
                (60, 150, "Reg no S1234567D issued", "l", 9),
            ]
        ]
    )
    result, _ctx, _doc = parse(pdf, NRIC)
    assert result.fields["id_number"].value == "S1234567D"
    assert result.fields["id_number"].box is not None


def test_national_id_without_an_id_number_is_not_understood() -> None:
    ctx, doc, _ = make_case(draw_pages([[(60, 100, "Some unrelated page", "l", 10)]]), NRIC)
    with pytest.raises(LayoutNotRecognised):
        get_extractors(NRIC)["text_layer"].extract(doc, ctx)


# ------------------------------------------------------------------------------------ passport


def test_passport_visual_fields_and_44_character_mrz_preserved_with_filler() -> None:
    result, _ctx, _doc = parse(render_passport(), PASSPORT)
    f = values(result.fields)
    assert len(MRZ1) == len(MRZ2) == 44
    assert f["mrz_line1"] == MRZ1 and f["mrz_line2"] == MRZ2  # '<' preserved byte for byte
    assert result.fields["mrz_line1"].raw == MRZ1
    assert f["passport_number"] == "K1234567"
    assert (f["surname"], f["given_names"]) == ("TAN", "WEI MING")
    assert (f["nationality"], f["issuing_country"], f["sex"]) == ("SGP", "SGP", "M")
    assert f["date_of_birth"] == "1985-03-12"
    assert f["date_of_issue"] == "2021-09-16" and f["date_of_expiry"] == "2031-09-15"
    assert result.warnings == []
    assert_all_boxed_and_verified(result)


def test_holder_name_is_composed_from_the_printed_parts_and_marked_derived() -> None:
    result, _ctx, _doc = parse(render_passport(), PASSPORT)
    name = result.fields["holder_name"]
    assert name.value == "WEI MING TAN"
    assert name.source == "derived" and name.verified is None and name.box is None


def test_a_43_character_mrz_line_is_kept_as_read_with_an_unparsed_warning() -> None:
    short = MRZ2[:-1]
    assert len(short) == 43
    result, _ctx, _doc = parse(render_passport(mrz2=short), PASSPORT)
    assert result.fields["mrz_line2"].value == short  # never padded
    assert result.fields["mrz_line1"].value == MRZ1
    assert any(w.startswith("unparsed: fields.mrz_line2") for w in result.warnings)
    assert not any("mrz_line1" in w for w in result.warnings)


def test_a_long_mrz_line_is_not_trimmed() -> None:
    long = MRZ1 + "<"
    result, _ctx, _doc = parse(render_passport(mrz1=long), PASSPORT)
    assert result.fields["mrz_line1"].value == long
    assert any(w.startswith("unparsed: fields.mrz_line1") for w in result.warnings)


def test_the_mrz_is_found_without_any_visual_labels() -> None:
    result, _ctx, _doc = parse(render_passport(with_labels=False), PASSPORT)
    assert result.fields["mrz_line1"].value == MRZ1
    assert result.fields["mrz_line2"].value == MRZ2
    assert result.fields["passport_number"].value is None  # not read from the MRZ here


def test_a_digit_changed_in_the_mrz_is_extracted_as_printed() -> None:
    forged = MRZ2[:13] + "9" + MRZ2[14:]
    result, _ctx, _doc = parse(render_passport(mrz2=forged), PASSPORT)
    assert result.fields["mrz_line2"].value == forged  # check digits are passport_mrz's job


def test_a_passport_page_with_nothing_recognisable_is_not_understood() -> None:
    ctx, doc, _ = make_case(draw_pages([[(60, 100, "Nothing to see here", "l", 10)]]), PASSPORT)
    with pytest.raises(LayoutNotRecognised):
        get_extractors(PASSPORT)["text_layer"].extract(doc, ctx)
