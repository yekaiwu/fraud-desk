"""Ownership chart extractor (T-extract-kyc-02; test 27 part: a holder lacking a percentage).

The chart is the weakest evidence in the system: these tests pin what is promised (copy what is
printed, never invent a percentage or a parent) and the honest failure (not understood -> model).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.llm import FakeLLMClient
from app.extract.base import LayoutNotRecognised
from app.extract.cascade import extract_document
from app.extract.registry import get_extractors
from tests.extract_helpers import empty_page, make_case
from tests.extract_kyc_helpers import (
    Box,
    assert_all_boxed_and_verified,
    draw,
    parse,
    render_chart,
    row_values,
    settings,
    values,
)

DT = "ownership_chart"
APP = Box(300, 100, ["Sembrook Freight Pte. Ltd.", "UEN 201912345R"], 200)
TAN = Box(110, 230, ["Tan Wei Ming", "Individual", "45.00%", "ID S1234567D", "UBO"])
HAFIZ = Box(300, 230, ["Hafiz bin Abdullah", "30.00%"])
KESTREL = Box(490, 230, ["Kestrel Capital Holdings", "Pte. Ltd.", "25.00%", "UEN 201934567C"])
LIM = Box(490, 360, ["Lim Wei Jie", "100.00%", "S2345678A"])


def holders(result) -> list[dict]:  # noqa: ANN001
    return row_values(result.tables["holders"])


def test_clean_chart_tree_with_an_intermediate_company() -> None:
    result, _ctx, _doc = parse(render_chart([APP, TAN, HAFIZ, KESTREL, LIM]), DT)
    f = values(result.fields)
    assert f == {
        "company_name": "Sembrook Freight Pte. Ltd.",
        "uen": "201912345R",
        "chart_date": "2026-09-30",
        "ubo_threshold_percent": "25.00",
    }
    rows = holders(result)
    assert [(r["holder_name"], r["percent"], r["parent_name"]) for r in rows] == [
        ("Tan Wei Ming", "45.00", None),
        ("Hafiz bin Abdullah", "30.00", None),
        ("Kestrel Capital Holdings Pte. Ltd.", "25.00", None),  # wrapped name joined
        ("Lim Wei Jie", "100.00", "Kestrel Capital Holdings Pte. Ltd."),
    ]
    assert rows[0]["holder_type"] == "individual" and rows[0]["id_or_uen"] == "S1234567D"
    assert rows[2]["holder_type"] == "company" and rows[2]["id_or_uen"] == "201934567C"
    assert rows[3]["id_or_uen"] == "S2345678A"
    # percent of a child is the percentage held IN its parent, copied as printed (never multiplied)
    assert result.tables["holders"].rows[3].cells["percent"].raw == "100.00%"
    assert all(r.box is not None for r in result.tables["holders"].rows)
    assert result.row_pages["holders"] == [1, 1, 1, 1]


def test_cells_are_boxed_and_verified_and_a_geometric_parent_is_marked_derived() -> None:
    result, _ctx, _doc = parse(render_chart([APP, TAN, HAFIZ, KESTREL, LIM]), DT)
    assert_all_boxed_and_verified(result)
    parent = result.tables["holders"].rows[3].cells["parent_name"]
    assert parent.source == "derived" and parent.verified is None and parent.box is not None


def test_ubo_tags_true_where_printed_false_where_printed_or_unmarked_on_a_tagging_chart() -> None:
    not_ubo = Box(300, 230, ["Hafiz bin Abdullah", "30.00%", "Not UBO"])
    result, _ctx, _doc = parse(render_chart([APP, TAN, not_ubo, KESTREL]), DT)
    cells = [r.cells["is_ubo"] for r in result.tables["holders"].rows]
    assert [c.value for c in cells] == ["true", "false", "false"]
    assert cells[0].raw == "UBO" and cells[0].box is not None and cells[0].verified is True
    assert cells[1].raw == "Not UBO" and cells[1].source == "text_layer"  # printed negative
    # an untagged box on a chart that tags others: 'not marked', derived, no box, unverified
    assert (cells[2].source, cells[2].box, cells[2].verified) == ("derived", None, None)


def test_a_chart_with_no_ubo_text_at_all_leaves_is_ubo_empty() -> None:
    plain = Box(110, 230, ["Tan Wei Ming", "45.00%"])
    result, _ctx, _doc = parse(
        render_chart([APP, plain, HAFIZ], threshold="Limit threshold: 25.00%"), DT
    )
    assert [r["is_ubo"] for r in holders(result)] == [None, None]


def test_holder_without_a_percentage_stays_none() -> None:
    """One holder of four lacks a percentage (25%, not more): extracted, percent None."""
    nameless_pct = Box(490, 360, ["Lim Wei Jie", "S2345678A"])
    result, _ctx, _doc = parse(render_chart([APP, TAN, HAFIZ, KESTREL, nameless_pct]), DT)
    rows = holders(result)
    assert len(rows) == 4
    assert rows[3]["holder_name"] == "Lim Wei Jie" and rows[3]["percent"] is None
    cell = result.tables["holders"].rows[3].cells["percent"]
    assert cell.value is None and cell.raw is None
    assert rows[3]["parent_name"] == "Kestrel Capital Holdings Pte. Ltd."


def test_too_many_holders_without_percentages_is_not_understood(tmp_path: Path) -> None:
    pdf = render_chart(
        [
            APP,
            Box(110, 230, ["Tan Wei Ming", "S1234567D"]),
            Box(300, 230, ["Hafiz bin Abdullah"]),
            Box(490, 230, ["Elena Vasquez", "30.00%"]),
        ]
    )
    ctx, doc, _ = make_case(pdf, DT)
    with pytest.raises(LayoutNotRecognised, match="no readable percentage"):
        get_extractors(DT)["text_layer"].extract(doc, ctx)

    page = empty_page(DT)
    page["fields"]["company_name"] = "Sembrook Freight Pte. Ltd."
    page["tables"]["holders"] = [
        {
            "holder_name": "Elena Vasquez",
            "holder_type": "individual",
            "id_or_uen": None,
            "percent": "30.00%",
            "parent_name": None,
            "is_ubo": None,
        }
    ]
    llm = FakeLLMClient({"extract:ownership_chart": page})
    ctx, doc, _ = make_case(pdf, DT, llm=llm, settings=settings(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert meta.extractor == "llm:fake"
    assert any(w.startswith("layout_not_recognised") for w in result.warnings)
    assert holders(result)[0]["percent"] == "30.00"


def test_forged_chart_omitting_a_holder_is_extracted_as_printed() -> None:
    """The omission is the check's job; the extractor reports 45 + 30 = 75 exactly as drawn."""
    result, _ctx, _doc = parse(render_chart([APP, TAN, HAFIZ]), DT)
    pcts = [r["percent"] for r in holders(result)]
    assert pcts == ["45.00", "30.00"]


def test_name_and_percentage_side_by_side_on_one_line() -> None:
    items = [
        (300, 50, "OWNERSHIP STRUCTURE CHART", "c", 13, True),
        (40, 100, "Sembrook Freight Pte. Ltd.", "l", 9, True),
        (40, 112, "UEN 201912345R", "l", 8.5),
        (40, 180, "Tan Wei Ming", "l", 9),
        (400, 180, "45.00%", "l", 9),
        (40, 230, "Hafiz bin Abdullah", "l", 9),
        (400, 230, "55.00%", "l", 9),
    ]
    result, _ctx, _doc = parse(draw([items]), DT)
    assert [(r["holder_name"], r["percent"]) for r in holders(result)] == [
        ("Tan Wei Ming", "45.00"),
        ("Hafiz bin Abdullah", "55.00"),
    ]


def test_explicit_via_line_names_the_parent() -> None:
    via = Box(300, 230, ["Lim Wei Jie", "via Kestrel Capital Holdings Pte. Ltd.", "100.00%"], 240)
    result, _ctx, _doc = parse(render_chart([APP, TAN, via]), DT)
    row = result.tables["holders"].rows[1]
    assert row.cells["parent_name"].value == "Kestrel Capital Holdings Pte. Ltd."
    assert row.cells["parent_name"].source == "text_layer" and row.cells["parent_name"].verified


def test_threshold_not_printed_is_none_and_unlabelled_chart_still_parses() -> None:
    result, _ctx, _doc = parse(render_chart([APP, TAN, HAFIZ], threshold=None), DT)
    assert result.fields["ubo_threshold_percent"].value is None
    assert result.fields["ubo_threshold_percent"].raw is None


def test_threshold_printed_without_a_percent_sign() -> None:
    result, _ctx, _doc = parse(
        render_chart([APP, TAN, HAFIZ], threshold="UBO threshold (percent): 10.00"), DT
    )
    assert result.fields["ubo_threshold_percent"].value == "10.00"


def test_chart_with_no_holders_is_not_understood() -> None:
    ctx, doc, _ = make_case(render_chart([APP]), DT)
    with pytest.raises(LayoutNotRecognised):
        get_extractors(DT)["text_layer"].extract(doc, ctx)
