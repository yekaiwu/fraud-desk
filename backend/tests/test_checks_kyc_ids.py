"""ID rules: id_format, id_checksum, passport_mrz, date_valid (checks-kyc-aml.md 4.2, tests 2-7)."""

from __future__ import annotations

import pytest

from app.checks.kyc import (
    date_valid,
    id_checksum,
    id_format,
    mrz_check_digit,
    nric_check_letter,
    passport_mrz,
)
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Region
from app.names import mask_id
from tests.test_checks_kyc_fixtures import (
    ICAO_LINE1,
    ICAO_LINE2,
    SG_LINE1,
    SG_LINE2,
    ctx_for,
    mrz_line1,
    mrz_line2,
    national_id_doc,
    passport_doc,
    set_cell,
)

VECTORS = [
    ("S", "1234567", "D"),
    ("T", "0812345", "J"),
    ("F", "1234567", "N"),
    ("G", "1234567", "X"),
    ("S", "8222222", "G"),
    ("S", "0000001", "I"),
]


def nric_ctx(*docs):  # noqa: ANN001, ANN201
    return ctx_for(docs, registry=False)


# ------------------------------------------------------------------ test 2


@pytest.mark.parametrize(("prefix", "digits", "letter"), VECTORS)
def test_nric_check_letter_vectors(prefix: str, digits: str, letter: str) -> None:
    assert nric_check_letter(prefix, digits) == letter


def test_nric_check_letter_rejects_bad_input() -> None:
    for prefix, digits in (("M", "1234567"), ("S", "123456"), ("X", "1234567"), ("S", "12345a7")):
        with pytest.raises(ValueError):
            nric_check_letter(prefix, digits)


def test_pinned_vectors_pass_format_and_checksum() -> None:
    docs = [
        national_id_doc(f"d{i + 1}", "Test Person", f"{p}{d}{letter}", "1985-03-12")
        for i, (p, d, letter) in enumerate(VECTORS)
    ]
    ctx = nric_ctx(*docs)
    assert id_format(ctx) == []
    assert id_checksum(ctx) == []


def test_lower_case_and_spaced_ids_are_normalised() -> None:
    ctx = nric_ctx(national_id_doc("d1", "Tan Wei Ming", "s 1234567-d", "1985-03-12"))
    assert id_format(ctx) == [] and id_checksum(ctx) == []


# ------------------------------------------------------------------ test 3


def test_wrong_check_letter_is_flagged_with_masked_evidence() -> None:
    cell = Cell(
        value="S8222222A",
        kind="id",
        box=Region(file_id="f9", page=1, x=0.31, y=0.58, w=0.2, h=0.03),
    )
    doc = national_id_doc("d7", "Tan Wei Ming", "S8222222A", "1985-03-12")
    doc.fields["id_number"] = cell
    flags = id_checksum(nric_ctx(doc))
    assert len(flags) == 1
    flag = flags[0]
    assert flag.id == "id_checksum.d7.id_number" and flag.severity == "high"
    assert (
        flag.field == "id_number" and flag.region is not None and flag.region.label == "id_number"
    )
    assert flag.evidence == (
        "ID S****222A: printed check letter A, computed G (prefix S, weighted digit sum 70, 70 mod 11 = 4)"
    )
    assert "S8222222A" not in flag.evidence and "S8222222A" not in flag.explanation
    assert flag.explanation.startswith("The check letter on this NRIC does not match its digits")
    # format passes, so id_format stays silent
    assert id_format(nric_ctx(doc)) == []


def test_t_prefix_evidence_shows_the_offset() -> None:
    flags = id_checksum(nric_ctx(national_id_doc("d1", "A B", "T0812345K", "2008-01-01")))
    assert "weighted digit sum 106 + 4 = 110, 110 mod 11 = 0" in flags[0].evidence
    assert "computed J" in flags[0].evidence


def test_format_failures() -> None:
    docs = [
        national_id_doc("d1", "A", "S12345678", "1985-03-12"),
        national_id_doc("d2", "B", "X1234567D", "1985-03-12"),
        national_id_doc("d3", "C", "S12345", "1985-03-12"),
    ]
    ctx = nric_ctx(*docs)
    flags = id_format(ctx)
    assert [f.document_ids for f in flags] == [["d1"], ["d2"], ["d3"]]
    assert "S****5678" in flags[0].evidence and "S12345" in flags[2].evidence
    # ids failing the format are not re-flagged by the checksum rule
    with pytest.raises(CheckSkipped, match="no S/T/F/G id"):
        id_checksum(ctx)


def test_m_prefix_passes_format_and_is_unverified() -> None:
    m_only = nric_ctx(national_id_doc("d1", "A", "M1234567K", "1985-03-12"))
    assert id_format(m_only) == []
    with pytest.raises(CheckSkipped):
        id_checksum(m_only)
    mixed = nric_ctx(
        national_id_doc("d1", "A", "M1234567K", "1985-03-12"),
        national_id_doc("d2", "B", "S1234567D", "1985-03-12"),
    )
    assert id_checksum(mixed) == []  # ran on d2; M was silently unverified


def test_unreadable_ids_skip_not_flag() -> None:
    ctx = nric_ctx(national_id_doc("d1", "A", None, "1985-03-12"))
    with pytest.raises(CheckSkipped):
        id_format(ctx)
    with pytest.raises(CheckSkipped):
        id_checksum(ctx)


def test_unverified_value_is_noted_but_still_flagged() -> None:
    doc = national_id_doc("d1", "A", "S8222222A", "1985-03-12")
    doc.fields["id_number"] = Cell(value="S8222222A", kind="id", verified=False)
    flags = id_checksum(nric_ctx(doc))
    assert flags and flags[0].evidence.endswith("; value not confirmed in the text layer")


def test_mask_id() -> None:
    assert mask_id("S8222222A") == "S****222A"
    assert mask_id("s 8222222-a") == "S****222A"
    assert mask_id("S12345") == "S12345"
    assert mask_id("S****222A") == "S****222A"


# ------------------------------------------------------------------ test 4 and 5


def test_mrz_check_digit_vectors() -> None:
    assert mrz_check_digit("L898902C3") == 6
    assert mrz_check_digit("740812") == 2
    assert mrz_check_digit("120415") == 9
    assert mrz_check_digit("ZE184226B<<<<<") == 1
    assert mrz_check_digit("K1234567D") == 9
    assert mrz_check_digit("850312") == 7
    assert mrz_check_digit("310615") == 4


def test_icao_specimen_has_zero_flags() -> None:
    doc = passport_doc(
        "d1",
        line1=ICAO_LINE1,
        line2=ICAO_LINE2,
        holder_name="Anna Maria Eriksson",
        surname="Eriksson",
        given_names="Anna Maria",
        passport_number="L898902C3",
        nationality="UTO",
        issuing_country="UTO",
        date_of_birth="1974-08-12",
        sex="F",
        date_of_expiry="2012-04-15",
    )
    assert passport_mrz(nric_ctx(doc)) == []  # expired is a currency issue, not forgery


def test_singapore_vector_and_helper_agree() -> None:
    assert mrz_line2() == SG_LINE2
    assert mrz_line1("Tan", "Wei Ming") == SG_LINE1
    assert passport_mrz(nric_ctx(passport_doc("d1"))) == []


def test_tampered_dob_gives_two_flags() -> None:
    line2 = "K1234567D9SGP8503187M3106154<<<<<<<<<<<<<<<8"
    flags = passport_mrz(nric_ctx(passport_doc("d1", line2=line2, date_of_birth="1985-03-18")))
    assert len(flags) == 2
    assert flags[0].evidence == "date of birth check digit: printed 7, computed 3"
    assert flags[1].evidence == "composite check digit: printed 8, computed 0"
    assert all(f.field == "mrz_line2" and f.severity == "high" for f in flags)
    assert flags[0].id == "passport_mrz.d1.mrz_line2~date_of_birth_digit"


def test_tampered_number_gives_two_flags() -> None:
    line2 = "K1234568D9SGP8503127M3106154<<<<<<<<<<<<<<<8"
    flags = passport_mrz(nric_ctx(passport_doc("d1", line2=line2, passport_number="K1234568D")))
    assert [f.evidence for f in flags][0] == "passport number check digit: printed 9, computed 2"
    assert len(flags) == 2


def test_line_length_and_character_flags_are_alone() -> None:
    short = passport_mrz(nric_ctx(passport_doc("d1", line2=SG_LINE2[:43])))
    assert len(short) == 1 and short[0].evidence == "MRZ line 2 has 43 characters, expected 44"
    lower = SG_LINE2.replace("K1234567D", "k1234567D")
    chars = passport_mrz(nric_ctx(passport_doc("d1", line2=lower)))
    assert len(chars) == 1 and "outside A-Z 0-9 <" in chars[0].evidence


def test_whitespace_in_mrz_is_stripped_not_corrected() -> None:
    spaced = SG_LINE2[:10] + " " + SG_LINE2[10:]
    assert passport_mrz(nric_ctx(passport_doc("d1", line2=spaced))) == []


def test_personal_number_digit_may_be_zero_when_all_filler() -> None:
    zero = SG_LINE2[:42] + "0" + SG_LINE2[43:]  # composite covers l2[28:43]? no: only up to 42
    flags = passport_mrz(nric_ctx(passport_doc("d1", line2=zero)))
    # personal-number digit 0 is accepted; only the composite (which includes position 42) may differ
    assert all("personal number" not in f.evidence for f in flags)


def test_missing_mrz_skips_or_is_silent() -> None:
    with pytest.raises(CheckSkipped, match="no mrz extracted"):
        passport_mrz(nric_ctx(passport_doc("d1", line1=None, line2=None)))
    ctx = nric_ctx(passport_doc("d1", line1=None, line2=None), passport_doc("d2"))
    assert passport_mrz(ctx) == []  # one passport without an MRZ is not flagged


# ------------------------------------------------------------------ test 6


def test_visual_dob_vs_mrz() -> None:
    line2 = mrz_line2(dob="850318")
    flags = passport_mrz(nric_ctx(passport_doc("d1", line2=line2)))  # visual DOB stays 1985-03-12
    assert len(flags) == 1
    assert flags[0].evidence == "visual date of birth 1985-03-12, MRZ 850318"
    assert flags[0].id.endswith("~visual_date_of_birth")


def test_visual_surname_vs_mrz() -> None:
    line1 = mrz_line1("Lee", "Wei Ming")
    flags = passport_mrz(nric_ctx(passport_doc("d1", line1=line1)))
    assert [f.evidence for f in flags] == ["visual surname Tan, MRZ LEE"]
    assert flags[0].field == "mrz_line1"


def test_other_visual_fields() -> None:
    doc = passport_doc(
        "d1",
        passport_number="K1234568D",
        date_of_expiry="2031-06-16",
        sex="F",
        nationality="MYS",
        issuing_country="IDN",
        given_names="Jia Hao",
    )
    ev = sorted(f.id.split("~")[1] for f in passport_mrz(nric_ctx(doc)))
    assert ev == [
        "visual_date_of_expiry",
        "visual_given_names",
        "visual_issuing_country",
        "visual_nationality",
        "visual_passport_number",
        "visual_sex",
    ]


def test_mrz_truncation_of_long_names_is_probable_not_flagged() -> None:
    doc = passport_doc("d1", given_names="Wei Ming Jonathan Alexander")
    assert passport_mrz(nric_ctx(doc)) == []  # MRZ holds WEI MING only: probable, not different


def test_diacritics_fold_in_names() -> None:
    line1 = mrz_line1("Muller", "Zoe")
    doc = passport_doc("d1", line1=line1, surname="Müller", given_names="Zoë")
    assert passport_mrz(nric_ctx(doc)) == []


# ------------------------------------------------------------------ test 7


def flags_by_field(flags):  # noqa: ANN001, ANN201
    return sorted((f.field, f.id.split("~")[-1]) for f in flags)


def test_date_valid_each_condition() -> None:
    impossible = national_id_doc("d1", "A", "S1234567D", None, issue=None, expiry=None)
    impossible.fields["date_of_birth"] = Cell(value=None, raw="31 Feb 1990", kind="date")
    bare = {"issue": None, "expiry": None}
    future = national_id_doc("d2", "B", "T1234567J", "2027-01-01", **bare)
    child = national_id_doc("d3", "C", "T1234567J", "2015-06-01", **bare)
    ancient = national_id_doc("d4", "D", "S1234567D", "1900-01-01", **bare)
    s_prefix_2003 = national_id_doc("d5", "E", "S1234567D", "2003-05-01", **bare)
    t_prefix_1985 = national_id_doc("d6", "F", "T0812345J", "1985-03-12", **bare)
    bad_order = national_id_doc(
        "d7", "G", "S1234567D", "1985-03-12", issue="2015-01-01", expiry="2014-01-01"
    )
    expired = passport_doc("d8", date_of_expiry="2020-01-01", date_of_issue="2010-01-01")
    flags = date_valid(
        nric_ctx(
            impossible, future, child, ancient, s_prefix_2003, t_prefix_1985, bad_order, expired
        )
    )
    got = {(f.document_ids[0], f.id.split("~")[-1]) for f in flags}
    assert got == {
        ("d1", "calendar"),
        ("d2", "future"),
        ("d3", "age"),
        ("d4", "age"),
        ("d5", "prefix"),
        ("d7", "order"),
    }
    cal = next(f for f in flags if f.document_ids == ["d1"])
    assert cal.evidence == "printed '31 Feb 1990' is not a real calendar date"
    assert next(f for f in flags if f.document_ids == ["d3"]).evidence.startswith(
        "date of birth 2015-06-01 gives age 11 on 2026-10-27"
    )


def test_date_valid_issue_in_future_and_before_birth() -> None:
    future_issue = national_id_doc(
        "d1", "A", "S1234567D", "1985-03-12", issue="2027-02-01", expiry=None
    )
    before_birth = national_id_doc(
        "d2", "B", "S1234567D", "1985-03-12", issue="1985-03-12", expiry=None
    )
    flags = date_valid(nric_ctx(future_issue, before_birth))
    assert {(f.document_ids[0], f.id.split("~")[-1]) for f in flags} == {
        ("d1", "future"),
        ("d2", "order"),
    }


def test_date_valid_uses_ctx_today_for_age_boundary() -> None:
    from datetime import date

    doc = national_id_doc("d1", "A", "T0812345J", "2010-10-28")  # turns 16 on 2026-10-28
    on_the_eve = ctx_for([doc], registry=False, today=date(2026, 10, 27))
    on_the_day = ctx_for([doc], registry=False, today=date(2026, 10, 28))
    assert len(date_valid(on_the_eve)) == 1
    assert date_valid(on_the_day) == []


def test_date_valid_skips_when_nothing_extracted() -> None:
    doc = national_id_doc("d1", "A", "S1234567D", None, issue=None, expiry=None)
    with pytest.raises(CheckSkipped):
        date_valid(nric_ctx(doc))


def test_date_valid_flag_ids_are_unique_for_stacked_conditions() -> None:
    doc = national_id_doc(
        "d1", "A", "S1234567D", "2015-06-01"
    )  # age 11, S prefix, issued before birth
    flags = date_valid(nric_ctx(doc))
    assert len({f.id for f in flags}) == len(flags) == 3


def test_set_cell_helper_roundtrip() -> None:
    doc = national_id_doc("d1", "A", "S1234567D", "1985-03-12")
    set_cell(doc, "date_of_birth", "1990-01-01")
    assert doc.fields["date_of_birth"].value == "1990-01-01"
    assert SG_LINE1.startswith("P<SGP")
