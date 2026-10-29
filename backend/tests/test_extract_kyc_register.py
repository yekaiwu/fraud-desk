"""Shareholder register extractor (T-extract-kyc-03)."""

from __future__ import annotations

import pytest

from app.extract.base import LayoutNotRecognised
from app.extract.registry import get_extractors
from tests.extract_helpers import draw_pages, make_case
from tests.extract_kyc_helpers import (
    assert_all_boxed_and_verified,
    parse,
    render_register,
    row_values,
    values,
)

DT = "shareholder_register"


@pytest.mark.parametrize("right_numbers", [True, False])
def test_rows_and_totals_parse_with_percentages_as_strings(right_numbers: bool) -> None:
    result, _ctx, _doc = parse(render_register(right_numbers=right_numbers), DT)
    f = values(result.fields)
    assert f["company_name"] == "Sembrook Freight Pte. Ltd."
    assert f["uen"] == "201912345R"
    assert f["register_date"] == "2026-09-30"
    assert f["total_shares"] == "100000"
    assert f["currency"] == "SGD"
    assert f["issued_capital"] is None  # not printed, not invented
    assert row_values(result.tables["shareholders"]) == [
        {
            "holder_name": "Tan Wei Ming",
            "id_or_uen": "S1234567D",
            "shares": "45000",
            "percent": "45.00",
            "share_class": "Ordinary",
            "date_acquired": "2019-03-12",
        },
        {
            "holder_name": "Hafiz bin Abdullah",
            "id_or_uen": "S7654321A",
            "shares": "30000",
            "percent": "30.00",
            "share_class": "Ordinary",
            "date_acquired": "2020-06-01",
        },
        {
            "holder_name": "Kestrel Capital Holdings Pte. Ltd.",
            "id_or_uen": "201934567C",
            "shares": "25000",
            "percent": "25.00",
            "share_class": "Ordinary",
            "date_acquired": "2022-01-15",
        },
    ]
    assert_all_boxed_and_verified(result)
    assert result.warnings == []


def test_the_total_row_is_not_a_holder() -> None:
    result, _ctx, _doc = parse(render_register(), DT)
    names = result.tables["shareholders"].values("holder_name")
    assert "Total" not in names and len(names) == 3
    total = result.fields["total_shares"]
    assert total.raw == "100,000" and total.box is not None


def test_a_register_that_does_not_add_up_is_reported_as_printed() -> None:
    """Rows sum to 100,000 but the printed total says 90,000: extracted unchanged for the checks."""
    result, _ctx, _doc = parse(render_register(total="90,000"), DT)
    assert result.fields["total_shares"].value == "90000"
    shares = [int(v) for v in result.tables["shareholders"].values("shares") if v]
    assert sum(shares) == 100_000


def test_register_continuing_on_a_second_page_with_a_repeated_header() -> None:
    result, _ctx, _doc = parse(render_register(pages=2), DT)
    table = result.tables["shareholders"]
    assert len(table.rows) == 3 and table.pages == [1, 2]
    assert result.row_pages["shareholders"] == [1, 1, 2]
    assert result.fields["total_shares"].value == "100000"


def test_holder_without_an_id_or_date_keeps_those_cells_empty() -> None:
    holders = [
        ("Tan Wei Ming", "", "60,000", "60.00%", "Ordinary", ""),
        ("Hafiz bin Abdullah", "S7654321A", "40,000", "40.00%", "Ordinary", "01 Jun 2020"),
    ]
    result, _ctx, _doc = parse(render_register(holders=holders), DT)
    first = result.tables["shareholders"].rows[0].cells
    assert first["id_or_uen"].value is None and first["date_acquired"].value is None
    assert first["shares"].value == "60000"
    assert first["id_or_uen"].box is None  # empty cells carry no box


def test_register_without_a_table_is_not_understood() -> None:
    ctx, doc, _ = make_case(
        draw_pages([[(40, 80, "Company name: Sembrook Freight Pte. Ltd.", "l", 10)]]), DT
    )
    with pytest.raises(LayoutNotRecognised):
        get_extractors(DT)["text_layer"].extract(doc, ctx)
