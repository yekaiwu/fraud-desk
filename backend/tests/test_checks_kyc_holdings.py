"""kyc_shareholdings (checks-kyc-aml.md 4.3; tests 8, 9, 10, 11)."""

from __future__ import annotations

import pytest

from app.checks.kyc import kyc_shareholdings
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Document, Region
from app.names import entity_key
from tests.test_checks_kyc_fixtures import (
    LIM_ID,
    RAVI_ID,
    TAN_ID,
    chart_doc,
    ctx_for,
    register_doc,
)


def run(*docs: Document, **kw):  # noqa: ANN003, ANN201
    return kyc_shareholdings(ctx_for(docs, registry=False, **kw))


def set_raw(doc: Document, table: str, raws: list[str]) -> None:
    for row, raw in zip(doc.tables[table].rows, raws, strict=True):
        old = row.cells["percent"]
        row.cells["percent"] = Cell(value=old.value, raw=raw, kind="percent", box=old.box)


def reg(rows):  # noqa: ANN001, ANN201
    return register_doc("d2", rows, total_shares=None)


# ------------------------------------------------------------------ test 8


def test_clean_register_and_chart_pass() -> None:
    assert run(register_doc("d2"), chart_doc("d3")) == []


def test_register_sum_95_is_flagged_with_tolerance_in_evidence() -> None:
    doc = reg(
        [("A", TAN_ID, None, "60.00"), ("B", LIM_ID, None, "25.00"), ("C", RAVI_ID, None, "10.00")]
    )
    set_raw(doc, "shareholders", ["60.00", "25.00", "10.00"])
    flags = run(doc)
    assert len(flags) == 1
    assert flags[0].evidence == "percentages sum to 95.00, expected 100.00 (tolerance 0.016)"
    assert flags[0].table == "shareholders" and flags[0].row is None and flags[0].severity == "high"
    assert flags[0].id == "kyc_shareholdings.d2.-"


@pytest.mark.parametrize(
    ("raws", "ok"), [(["33.33"] * 3, True), (["33.3"] * 3, True), (["33"] * 3, True)]
)
def test_rounded_thirds_pass(raws: list[str], ok: bool) -> None:
    doc = reg([("A", None, None, raws[0]), ("B", None, None, raws[1]), ("C", None, None, raws[2])])
    set_raw(doc, "shareholders", raws)
    assert (run(doc) == []) is ok


def test_a_missing_one_percent_holder_fails() -> None:
    doc = reg([("A", None, None, "33.33"), ("B", None, None, "33.33"), ("C", None, None, "32.33")])
    set_raw(doc, "shareholders", ["33.33", "33.33", "32.33"])
    assert len(run(doc)) == 1


def test_total_shares_mismatch() -> None:
    doc = register_doc(
        "d2",
        [
            ("A", TAN_ID, "300000", "62.50"),
            ("B", LIM_ID, "150000", "31.25"),
            ("C", RAVI_ID, "30000", "6.25"),
        ],
        total_shares="500000",
    )
    flags = run(doc)
    shares = [f for f in flags if f.field == "total_shares"]
    assert (
        len(shares) == 1
        and shares[0].evidence == "rows total 480000 shares, register states 500000"
    )


def test_one_edited_row_percent_is_flagged_on_that_row() -> None:
    doc = register_doc(
        "d2",
        [
            ("A", TAN_ID, "300000", "60.00"),
            ("B", LIM_ID, "150000", "40.00"),
            ("C", RAVI_ID, "50000", "10.00"),
        ],
    )
    flags = run(doc)
    row_flags = [f for f in flags if f.row is not None]
    assert [f.row for f in row_flags] == [1]
    assert "150000 of 500000 shares = 30.00%, printed 40.00%" in row_flags[0].evidence
    assert any(f.row is None and "sum to 110.00" in f.evidence for f in flags)  # the sum breaks too


def test_register_without_total_shares_skips_share_tests() -> None:
    doc = register_doc("d2", total_shares=None)
    assert run(doc) == []


def test_no_readable_percentages_skips() -> None:
    doc = reg([("A", None, None, None)])
    with pytest.raises(CheckSkipped, match="no readable percentages"):
        run(doc)


# ------------------------------------------------------------------ test 9


def chart(rows, **kw):  # noqa: ANN001, ANN201, ANN003
    return chart_doc("d3", rows, **kw)


def test_nominee_above_threshold_is_unnamed_high() -> None:
    doc = chart(
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Nominee", "individual", None, "30.00", None, None),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ]
    )
    flags = run(doc)
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "high" and f.row == 1
    assert f.evidence == "holder of 30.00% is not named ('Nominee')"
    assert f.id == f"kyc_shareholdings.d3.holders:1~{entity_key('Nominee')}"


@pytest.mark.parametrize(
    "name", ["", "-", "N/A", "Various", "others", "Undisclosed", "Bearer", "TBC"]
)
def test_placeholder_names_count_as_unnamed(name: str) -> None:
    doc = chart(
        [
            ("Tan Wei Ming", "individual", TAN_ID, "70.00", None, "true"),
            (name, "individual", None, "30.00", None, None),
        ]
    )
    assert len(run(doc)) == 1


def test_company_holder_without_children_is_medium() -> None:
    doc = chart(
        [
            ("Alpha Holdings Pte Ltd", "company", "202012345H", "40.00", None, None),
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
        ]
    )
    flags = run(doc)
    assert len(flags) == 1 and flags[0].severity == "medium"
    assert flags[0].evidence == "no natural person shown behind 'Alpha Holdings Pte Ltd' (40.00%)"


def test_individual_marked_not_ubo_above_threshold() -> None:
    doc = chart(
        [
            ("Tan Wei Ming", "individual", TAN_ID, "74.00", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "26.00", None, "false"),
        ]
    )
    flags = run(doc)
    assert len(flags) == 1 and flags[0].severity == "high"
    assert "not marked as UBO" in flags[0].evidence


def test_chart_threshold_cannot_raise_the_bar_but_can_lower_it() -> None:
    rows = [
        ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
        ("Nominee", "individual", None, "12.00", None, None),
        ("Ravi Kumar", "individual", RAVI_ID, "28.00", None, "true"),
    ]
    assert len(run(chart(rows, threshold="10"))) == 1  # 10 < 25, so 12% needs a name
    assert (
        run(chart(rows, threshold="50")) == []
    )  # 50 is ignored: min(50, 25) = 25; 12% is below it
    rows[1] = ("Nominee", "individual", None, "30.00", None, None)
    rows[2] = ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false")
    assert len(run(chart(rows, threshold="50"))) == 1  # 30% still needs a name


def test_threshold_comes_from_settings() -> None:
    from decimal import Decimal

    from app.core.config import Settings

    rows = [
        ("Tan Wei Ming", "individual", TAN_ID, "80.00", None, "true"),
        ("Nominee", "individual", None, "20.00", None, None),
    ]
    settings = Settings(
        _env_file=None, db_path=":memory:", llm_backend="fake", ubo_threshold_percent=Decimal("10")
    )
    assert len(run(chart(rows), settings=settings)) == 1
    assert run(chart(rows)) == []


def test_chart_hierarchy_uses_percent_in_parent() -> None:
    alpha = "Alpha Holdings Pte Ltd"
    ok = chart(
        [
            (alpha, "company", "202012345H", "40.00", None, None),
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "50.00", alpha, "false"),
            ("Ravi Kumar", "individual", RAVI_ID, "50.00", alpha, "false"),
        ]
    )
    assert run(ok) == []  # effective 20% each: below the threshold, children sum to 100
    heavy = chart(
        [
            (alpha, "company", "202012345H", "40.00", None, None),
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "80.00", alpha, "false"),
            ("Ravi Kumar", "individual", RAVI_ID, "20.00", alpha, "true"),
        ]
    )
    flags = run(heavy)  # Lim holds 32% effective, marked not UBO
    assert len(flags) == 1 and "holds 32.00%" in flags[0].evidence


def test_children_not_summing_to_100_are_flagged_on_the_company_row() -> None:
    alpha = "Alpha Holdings Pte Ltd"
    doc = chart(
        [
            (alpha, "company", None, "40.00", None, None),
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "50.00", alpha, "false"),
            ("Ravi Kumar", "individual", RAVI_ID, "30.00", alpha, "false"),
        ]
    )
    flags = run(doc)
    assert len(flags) == 1 and flags[0].row == 0
    assert flags[0].evidence.startswith("holders under 'Alpha Holdings Pte Ltd' sum to 80.00")


def test_unknown_parent_is_low_and_the_subtree_is_not_evaluated() -> None:
    doc = chart(
        [
            ("Tan Wei Ming", "individual", TAN_ID, "100.00", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "100.00", "Ghost Holdings", "false"),
        ]
    )
    flags = run(doc)
    assert len(flags) == 1 and flags[0].severity == "low"
    assert flags[0].evidence == "parent 'Ghost Holdings' is not on the chart"


# ------------------------------------------------------------------ tests 10 and 11 (this rule's half)


def test_naive_forger_chart_sum_is_flagged() -> None:
    doc = chart(
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ]
    )
    flags = run(doc)
    assert len(flags) == 1 and "sum to 70.00" in flags[0].evidence


def test_careful_forger_rescaled_chart_is_silent_here() -> None:
    doc = chart(
        [
            ("Tan Wei Ming", "individual", TAN_ID, "85.71", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "14.29", None, "false"),
        ]
    )
    assert run(doc) == []


def test_unverified_percent_is_noted() -> None:
    doc = reg([("A", TAN_ID, None, "60.00"), ("B", LIM_ID, None, "25.00")])
    doc.tables["shareholders"].rows[0].cells["percent"] = Cell(
        value="60.00", kind="percent", verified=False
    )
    assert run(doc)[0].evidence.endswith("; value not confirmed in the text layer")


def test_sum_flag_region_is_the_union_of_percent_cells() -> None:
    doc = reg([("A", TAN_ID, None, "60.00"), ("B", LIM_ID, None, "25.00")])
    for i, y in enumerate((0.30, 0.40)):
        c = doc.tables["shareholders"].rows[i].cells["percent"]
        doc.tables["shareholders"].rows[i].cells["percent"] = Cell(
            value=c.value,
            kind="percent",
            box=Region(file_id="f1", page=1, x=0.5, y=y, w=0.1, h=0.03),
        )
    region = run(doc)[0].region
    assert region is not None and region.y == 0.30 and round(region.y + region.h, 2) == 0.43
