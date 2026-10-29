"""Company profile and certificate of incorporation extractors (T-extract-kyc-01)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.llm import FakeLLMClient
from app.extract.base import LayoutNotRecognised
from app.extract.cascade import extract_document
from tests.extract_helpers import draw_pages, empty_page, make_case
from tests.extract_kyc_helpers import (
    ProfileSpec,
    assert_all_boxed_and_verified,
    parse,
    render_certificate,
    render_profile,
    row_values,
    settings,
    values,
)

PROFILE = "company_profile"
CERT = "certificate_of_incorporation"


@pytest.mark.parametrize("right_numbers", [False, True])
def test_profile_parses_fields_and_both_tables(right_numbers: bool) -> None:
    result, _ctx, _doc = parse(render_profile(ProfileSpec(right_numbers=right_numbers)), PROFILE)
    f = values(result.fields)
    assert f["company_name"] == "Sembrook Freight Pte. Ltd."
    assert f["uen"] == "201912345R"
    assert f["entity_type"] == "Exempt Private Company Limited by Shares"
    assert f["status"] == "Live"
    assert f["incorporation_date"] == "2019-03-12"
    assert f["principal_activity"] == "Freight forwarding and logistics services"
    assert f["ssic_code"] == "52291"
    assert (f["paid_up_capital"], f["currency"]) == ("100000.00", "SGD")
    assert f["retrieved_date"] == "2026-09-30"
    assert row_values(result.tables["directors"]) == [
        {
            "director_name": "Tan Wei Ming",
            "id_number": "S1234567D",
            "nationality": "Singaporean",
            "position": "Director",
            "appointment_date": "2019-03-12",
        },
        {
            "director_name": "Hafiz bin Abdullah",
            "id_number": "S7654321A",
            "nationality": "Singaporean",
            "position": "Director",
            "appointment_date": "2020-06-01",
        },
        {
            "director_name": "Elena Vasquez",
            "id_number": "X1234567",
            "nationality": "Spanish",
            "position": "Director",
            "appointment_date": "2022-01-15",
        },
    ]
    holders = row_values(result.tables["shareholders"])
    # percentages are printed percentage points as strings, share counts plain digits
    assert [(h["holder_name"], h["shares"], h["percent"]) for h in holders] == [
        ("Tan Wei Ming", "45000", "45.00"),
        ("Hafiz bin Abdullah", "30000", "30.00"),
        ("Kestrel Capital Holdings Pte. Ltd.", "25000", "25.00"),
    ]
    assert holders[2]["id_or_uen"] == "201934567C"
    assert_all_boxed_and_verified(result)
    assert result.warnings == []


def test_profile_row_boxes_and_pages() -> None:
    result, _ctx, _doc = parse(render_profile(), PROFILE)
    for name in ("directors", "shareholders"):
        table = result.tables[name]
        assert table.pages == [1]
        assert all(r.box is not None for r in table.rows)
        assert result.row_pages[name] == [1, 1, 1]


def test_clean_profile_needs_zero_model_calls(tmp_path: Path) -> None:
    llm = FakeLLMClient()
    ctx, doc, _ = make_case(render_profile(), PROFILE, llm=llm, settings=settings(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert llm.calls == []
    assert (meta.extractor, meta.status) == ("text_layer@1", "ok")
    assert result.fields["uen"].verified is True


def test_profile_address_wrapping_onto_a_second_line() -> None:
    spec = ProfileSpec(
        address="1 Raffles Quay, #12-01", address2="One Raffles Quay Singapore 048583"
    )
    result, _ctx, _doc = parse(render_profile(spec), PROFILE)
    assert result.fields["registered_address"].value == (
        "1 Raffles Quay, #12-01 One Raffles Quay Singapore 048583"
    )
    assert result.fields["principal_activity"].value == "Freight forwarding and logistics services"


def test_profile_without_a_shareholders_section_still_parses() -> None:
    spec = ProfileSpec(holders=[])
    pdf = render_profile(spec)
    result, _ctx, _doc = parse(pdf, PROFILE)
    assert len(result.tables["directors"].rows) == 3
    assert result.tables["shareholders"].rows == []  # the table is present, honestly empty


def test_profile_unprinted_optional_cells_stay_empty_and_the_date_is_not_invented() -> None:
    spec = ProfileSpec(
        directors=[("Tan Wei Ming", "S1234567D", "Singaporean", "Director", "31 Feb 2019")]
    )
    result, _ctx, _doc = parse(render_profile(spec), PROFILE)
    appt = result.tables["directors"].rows[0].cells["appointment_date"]
    assert (
        appt.value is None and appt.raw == "31 Feb 2019"
    )  # impossible date: raw kept, never rolled
    assert any(w.startswith("unparsed: directors[0].appointment_date") for w in result.warnings)


def test_profile_with_a_nric_typo_keeps_what_is_printed() -> None:
    spec = ProfileSpec(
        directors=[("Tan Wei Ming", "S123456D", "Singaporean", "Director", "12 Mar 2019")]
    )
    result, _ctx, _doc = parse(render_profile(spec), PROFILE)
    assert result.tables["directors"].rows[0].cells["id_number"].value == "S123456D"


def test_unknown_profile_layout_raises_and_the_cascade_uses_the_model(tmp_path: Path) -> None:
    pdf = draw_pages([[(40, 80, "Hello world this is a free text page about nothing", "l", 10)]])
    ctx, doc, _ = make_case(pdf, PROFILE)
    from app.extract.registry import get_extractors

    with pytest.raises(LayoutNotRecognised):
        get_extractors(PROFILE)["text_layer"].extract(doc, ctx)

    page = empty_page(PROFILE)
    page["fields"]["company_name"] = "Hello"  # a value that is printed on the page
    llm = FakeLLMClient({"extract:company_profile": page})
    ctx, doc, _ = make_case(pdf, PROFILE, llm=llm, settings=settings(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert [c.purpose for c in llm.calls] == ["extract:company_profile"]
    assert meta.extractor == "llm:fake"
    assert any(w.startswith("layout_not_recognised") for w in result.warnings)
    assert result.fields["company_name"].value == "Hello"
    assert result.fields["company_name"].verified is True
    assert meta.status == "partial"  # required fields are missing: said, not hidden


# ----------------------------------------------------------------------------------- certificate


def test_certificate_labelled_form() -> None:
    result, _ctx, _doc = parse(render_certificate(), CERT)
    assert values(result.fields) == {
        "company_name": "SEMBROOK FREIGHT PTE. LTD.",
        "uen": "201912345R",
        "entity_type": "Private Company Limited by Shares",
        "incorporation_date": "2019-03-12",
        "certificate_number": "CERT-2019-000123",
        "issue_date": "2019-03-14",
        "issuer": "Registrar of Companies (mock)",
    }
    assert result.tables == {}
    assert_all_boxed_and_verified(result)


def test_certificate_sentence_form() -> None:
    result, _ctx, _doc = parse(render_certificate(labelled=False), CERT)
    f = values(result.fields)
    assert f["company_name"] == "SEMBROOK FREIGHT PTE. LTD."
    assert f["uen"] == "201912345R"
    assert f["incorporation_date"] == "2019-03-12"
    assert f["entity_type"] == "private company limited by shares"
    assert f["issue_date"] is None and f["certificate_number"] is None  # not printed, not invented
    assert_all_boxed_and_verified(result)


def test_certificate_with_nothing_recognisable_raises() -> None:
    ctx, doc, _ = make_case(draw_pages([[(40, 80, "A blank looking page", "l", 10)]]), CERT)
    from app.extract.registry import get_extractors

    with pytest.raises(LayoutNotRecognised):
        get_extractors(CERT)["text_layer"].extract(doc, ctx)
