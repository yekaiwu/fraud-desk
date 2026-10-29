"""Layouts as the generator draws them (replicated in extract_kyc_helpers): the tight and right
aligned columns, badges and notes that a naive parser trips over."""

from __future__ import annotations

from tests.extract_kyc_helpers import (
    MRZ1,
    MRZ2,
    assert_all_boxed_and_verified,
    parse,
    render_gen_certificate,
    render_gen_chart,
    render_gen_passport,
    render_gen_register,
    render_gen_sof,
    row_values,
    values,
)


def test_chart_with_badges_notes_and_held_in_lines() -> None:
    result, _ctx, _doc = parse(render_gen_chart(), "ownership_chart")
    f = values(result.fields)
    assert f["company_name"] == "Sembrook Freight Pte. Ltd."  # not the subtitle or the date line
    assert f["uen"] == "201912345R"
    assert f["chart_date"] == "2026-09-30"
    assert f["ubo_threshold_percent"] == "25.00"
    rows = row_values(result.tables["holders"])
    assert [(r["holder_name"], r["percent"], r["parent_name"]) for r in rows] == [
        ("Tan Wei Ming", "45.00", None),
        ("Hafiz bin Abdullah", "30.00", None),
        ("Kestrel Capital Holdings Pte. Ltd.", "25.00", None),
        ("Lim Wei Jie", "100.00", "Kestrel Capital Holdings Pte. Ltd."),  # printed 'Held in'
    ]
    assert [r["holder_type"] for r in rows] == ["individual", "individual", "company", "individual"]
    assert [r["id_or_uen"] for r in rows] == ["S1234567D", "S7654321A", "201934567C", "S2345678A"]
    # a UBO badge drawn beside the percentage; no badge on a chart that uses badges = not marked
    assert [r["is_ubo"] for r in rows] == ["true", "false", "true", "true"]
    tags = [r.cells["is_ubo"] for r in result.tables["holders"].rows]
    assert tags[0].source == "text_layer" and tags[0].box is not None and tags[0].verified
    assert tags[1].source == "derived" and tags[1].box is None and tags[1].verified is None
    held_in = result.tables["holders"].rows[3].cells["parent_name"]
    assert held_in.source == "text_layer" and held_in.verified and held_in.box is not None
    assert result.warnings == []


def test_register_with_tight_columns_and_a_right_aligned_multiword_date() -> None:
    result, _ctx, _doc = parse(render_gen_register(), "shareholder_register")
    assert row_values(result.tables["shareholders"]) == [
        {
            "holder_name": "Tan Wei Ming",
            "id_or_uen": "S1234567D",
            "shares": "45000",
            "percent": "45.00",
            "share_class": "Ordinary",
            "date_acquired": "2016-06-18",
        },
        {
            "holder_name": "Hafiz bin Abdullah",
            "id_or_uen": "S7654321A",
            "shares": "30000",
            "percent": "30.00",
            "share_class": "Ordinary",
            "date_acquired": "2016-06-18",
        },
        {
            "holder_name": "Kestrel Capital Holdings Pte. Ltd.",
            "id_or_uen": "201934567C",
            "shares": "25000",
            "percent": "25.00",
            "share_class": "Ordinary",
            "date_acquired": "2018-06-10",
        },
    ]
    f = values(result.fields)
    assert (f["total_shares"], f["issued_capital"], f["currency"]) == ("100000", "500000.00", "SGD")
    assert f["register_date"] == "2026-09-30"
    assert_all_boxed_and_verified(result)
    assert result.warnings == []


def test_certificate_with_the_name_between_two_lines_of_prose() -> None:
    result, _ctx, _doc = parse(render_gen_certificate(), "certificate_of_incorporation")
    assert values(result.fields) == {
        "company_name": "Sembrook Freight Pte. Ltd.",
        "uen": "201912345R",  # label "Company number (UEN)"
        "entity_type": "Private Company Limited by Shares",
        "incorporation_date": "2016-06-18",
        "certificate_number": "COI-201912345R",
        "issue_date": "2016-06-18",
        "issuer": "Mock Registry of Companies (specimen)",
    }
    assert_all_boxed_and_verified(result)


def test_sof_with_a_subtitle_that_looks_like_a_label_and_a_bank_name_before_the_account() -> None:
    result, _ctx, _doc = parse(render_gen_sof(), "source_of_funds_declaration")
    f = values(result.fields)
    assert f["declarant_name"] == "Tan Wei Ming"
    assert f["declared_amount"] == "1000000.00" and f["currency"] == "SGD"
    assert f["origin_category"] == "sales_proceeds"
    assert f["origin_description"] == (
        "Proceeds from the sale of goods to Orchid Bay Holdings Pte. Ltd. under invoice "
        "INV-2026-0117, received between 01 Jul 2026 and 31 Aug 2026."
    )
    assert (
        f["counterparty_name"] == "Orchid Bay Holdings Pte. Ltd."
    )  # not 'Customer declaration ...'
    assert f["invoice_reference"] == "INV-2026-0117"
    assert (f["period_start"], f["period_end"]) == ("2026-07-01", "2026-08-31")
    assert f["receiving_account"] == "6323451406"  # not the bank name beside it
    assert f["declaration_date"] == "2026-09-02"
    assert result.warnings == []


def test_passport_with_labels_on_one_line_and_values_below() -> None:
    result, _ctx, _doc = parse(render_gen_passport(), "passport")
    f = values(result.fields)
    assert f["issuing_country"] == "SGP"  # 'Country code' next to 'Type'
    assert (f["passport_number"], f["surname"], f["given_names"]) == (
        "K1234567",
        "VASQUEZ",
        "ELENA",
    )
    assert f["holder_name"] == "ELENA VASQUEZ"
    assert (f["mrz_line1"], f["mrz_line2"]) == (MRZ1, MRZ2)
    assert f["date_of_birth"] == "1988-02-03" and f["sex"] == "F"
    assert result.warnings == []


def test_sof_account_slot_printed_as_company_account_is_not_read_as_the_company_label() -> None:
    """The generator prints ``Company account`` where a bank name would be (the company's own
    account). That value starts with the ``Company`` label: it must stay the bank slot, not turn
    into a repeated company label that pulled in the declaration sentence as the account
    (false ``x_sof_support`` on aml-clean-c03 and c07)."""
    result, _ctx, _doc = parse(render_gen_sof("Company account"), "source_of_funds_declaration")
    f = values(result.fields)
    assert f["receiving_account"] == "6323451406"
    assert f["company_name"] == "Tanjong Ridge Trading Pte. Ltd."
    assert result.warnings == []
