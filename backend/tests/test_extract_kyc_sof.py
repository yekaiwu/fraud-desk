"""Source-of-funds declaration extractor (T-extract-kyc-05)."""

from __future__ import annotations

import pytest

from app.extract.base import LayoutNotRecognised
from app.extract.kyc_sof_declaration import map_category
from app.extract.registry import get_extractors
from tests.extract_helpers import draw_pages, make_case
from tests.extract_kyc_helpers import (
    assert_all_boxed_and_verified,
    parse,
    render_sof,
    values,
)

DT = "source_of_funds_declaration"
BODY = (
    "I, Tan Wei Ming, a director of Tanjong Ridge Trading Pte. Ltd., declare that the amount of "
    "S$1,000,000.00 represents sales proceeds from Orchid Bay Holdings Pte. Ltd. under invoice "
    "INV-2026-0117 received between 01 Jul 2026 and 31 Aug 2026 into account 123-456-789-0."
)


def test_labelled_form_fills_every_field() -> None:
    result, _ctx, _doc = parse(render_sof(), DT)
    f = values(result.fields)
    assert f["declarant_name"] == "Tan Wei Ming"
    assert f["company_name"] == "Tanjong Ridge Trading Pte. Ltd."
    assert f["uen"] == "201845678K"
    assert f["declaration_date"] == "2026-09-02"
    assert (f["declared_amount"], f["currency"]) == ("1000000.00", "SGD")
    assert f["origin_category"] == "sales_proceeds"  # "Sales proceeds" as printed
    assert result.fields["origin_category"].raw == "Sales proceeds"
    assert f["counterparty_name"] == "Orchid Bay Holdings Pte. Ltd."
    assert f["invoice_reference"] == "INV-2026-0117"
    assert (f["period_start"], f["period_end"]) == ("2026-07-01", "2026-08-31")
    assert f["receiving_account"] == "123-456-789-0"
    assert f["origin_description"] == BODY
    assert_all_boxed_and_verified(result)
    assert result.warnings == []


def test_sentence_only_declaration_is_read_from_the_prose() -> None:
    result, _ctx, _doc = parse(render_sof(labelled=False), DT)
    f = values(result.fields)
    assert f["declared_amount"] == "1000000.00"  # first money token after "amount of"
    assert result.fields["declared_amount"].raw == "S$1,000,000.00"
    assert f["currency"] == "SGD"
    assert f["origin_category"] == "sales_proceeds"
    assert f["counterparty_name"] == "Orchid Bay Holdings Pte. Ltd."
    assert f["invoice_reference"] == "INV-2026-0117"
    assert (f["period_start"], f["period_end"]) == ("2026-07-01", "2026-08-31")
    assert f["receiving_account"] == "123-456-789-0"
    assert f["declarant_name"] == "Tan Wei Ming"
    assert f["company_name"] == "Tanjong Ridge Trading Pte. Ltd."
    assert f["declaration_date"] == "2026-09-02"
    assert f["origin_description"] == BODY


def test_the_title_is_not_mistaken_for_the_category_label() -> None:
    """'SOURCE OF FUNDS DECLARATION' looks like the label 'Source of funds' followed by a value."""
    result, _ctx, _doc = parse(render_sof(), DT)
    assert result.fields["origin_category"].raw == "Sales proceeds"


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Sales proceeds", "sales_proceeds"),
        ("sales_proceeds", "sales_proceeds"),
        ("SALARY", "salary"),
        ("Investment returns", "investment_returns"),
        ("Inheritance", "inheritance"),
        ("Loan", "loan"),
        ("Asset sale", "asset_sale"),
        ("Other", "other"),
        ("Crowdfunding windfall", None),
    ],
)
def test_enum_origin_categories(printed: str, expected: str | None) -> None:
    assert map_category(printed) == expected


def test_an_unlisted_category_is_kept_as_read_and_warned_never_mapped_to_other() -> None:
    pdf = draw_pages(
        [
            [
                (40, 100, "Declarant", "l", 8.5),
                (200, 100, "Tan Wei Ming", "l", 9.5),
                (40, 117, "Declared amount", "l", 8.5),
                (200, 117, "S$ 50,000.00", "l", 9.5),
                (40, 134, "Source of funds", "l", 8.5),
                (200, 134, "Crowdfunding windfall", "l", 9.5),
            ]
        ]
    )
    result, _ctx, _doc = parse(pdf, DT)
    cat = result.fields["origin_category"]
    assert cat.value is None and cat.raw == "Crowdfunding windfall"
    assert any(w.startswith("unparsed: fields.origin_category") for w in result.warnings)


def test_description_is_capped_at_500_characters() -> None:
    long_line = ("funds received from the sale of goods under the invoice " * 12).strip()
    words = long_line.split()
    lines = [" ".join(words[i : i + 14]) for i in range(0, len(words), 14)]
    items = [(40, 100, "Declarant", "l", 8.5), (200, 100, "Tan Wei Ming", "l", 9.5)]
    items += [(40, 140 + 12 * i, line, "l", 8) for i, line in enumerate(lines)]
    items += [(40, 117, "Declared amount", "l", 8.5), (200, 117, "S$ 5.00", "l", 9.5)]
    result, _ctx, _doc = parse(draw_pages([items]), DT)
    desc = result.fields["origin_description"]
    assert desc.value is not None and len(desc.value) == 500
    assert desc.raw == desc.value


def test_amount_with_a_trailing_iso_code_in_the_sentence() -> None:
    pdf = draw_pages(
        [
            [
                (40, 100, "Declarant", "l", 8.5),
                (200, 100, "Tan Wei Ming", "l", 9.5),
                (
                    40,
                    140,
                    "I declare that the sum of 250,000.00 SGD represents salary and bonus paid by",
                    "l",
                    8,
                ),
                (
                    40,
                    152,
                    "my employer over the stated period for services rendered to the company.",
                    "l",
                    8,
                ),
            ]
        ]
    )
    result, _ctx, _doc = parse(pdf, DT)
    assert result.fields["declared_amount"].value == "250000.00"
    assert result.fields["currency"].value == "SGD"
    assert result.fields["origin_category"].value == "salary"


def test_a_declared_amount_that_is_not_money_is_never_guessed() -> None:
    pdf = draw_pages(
        [
            [
                (40, 100, "Declarant", "l", 8.5),
                (200, 100, "Tan Wei Ming", "l", 9.5),
                (40, 117, "Declared amount", "l", 8.5),
                (200, 117, "about a million", "l", 9.5),
            ]
        ]
    )
    result, _ctx, _doc = parse(pdf, DT)
    amount = result.fields["declared_amount"]
    assert amount.value is None and amount.raw == "about a million"
    assert any(w.startswith("unparsed: fields.declared_amount") for w in result.warnings)


def test_a_page_with_nothing_declared_is_not_understood() -> None:
    ctx, doc, _ = make_case(draw_pages([[(40, 100, "Just a heading", "l", 12)]]), DT)
    with pytest.raises(LayoutNotRecognised):
        get_extractors(DT)["text_layer"].extract(doc, ctx)
