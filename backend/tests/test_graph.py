"""In-case entity graph (checks-kyc-aml.md 4.10; test 25)."""

from __future__ import annotations

import json
from decimal import Decimal

from app.core.models import Annotation, Cell, Row
from app.graph import APPLICANT_KEY, EntityGraph, counterparty_of
from tests.test_checks_kyc_fixtures import (
    APPLICANT_NAME,
    chart_doc,
    company_profile_doc,
    ctx_for,
    invoice_doc,
    register_doc,
    registry_with,
    statement_doc,
)


def row_with(description: str, label: str | None = None) -> Row:
    row = Row(index=0, cells={"description": Cell(value=description)})
    if label is not None:
        row.annotations["counterparty"] = Annotation(
            label=label, probability=1.0, classifier="counterparty-parser", version="1"
        )
    return row


def test_counterparty_of_prefers_annotation_then_strips_channel_prefix() -> None:
    assert counterparty_of(row_with("anything", "ACME TRADING PTE LTD")) == "ACME TRADING PTE LTD"
    assert (
        counterparty_of(row_with("FAST PAYMENT FROM ACME TRADING PTE LTD"))
        == "ACME TRADING PTE LTD"
    )
    assert counterparty_of(row_with("GIRO TRANSFER TO BETA ENTERPRISE")) == "BETA ENTERPRISE"
    assert counterparty_of(row_with("PAYNOW CREDIT FROM TAN AH KOW")) == "TAN AH KOW"
    assert counterparty_of(row_with("TT INWARD REMITTANCE")) == "INWARD REMITTANCE"
    assert counterparty_of(row_with("OFFICE RENT")) == "OFFICE RENT"
    assert counterparty_of(row_with("FAST PAYMENT FROM ")) is None
    assert counterparty_of(row_with("   ")) is None


def build(rows, **kw):  # noqa: ANN001, ANN201
    return EntityGraph.build(
        ctx_for([statement_doc("d1", rows)], use_case="aml", registry=False, **kw)
    )


def test_own_account_transfer_is_dropped_and_applicant_node_is_fixed() -> None:
    g = build(
        [
            {"date": "2026-02-02", "credit": "9000.00", "cp": "EXAMPLE LOGISTICS PTE LTD"},
            {"date": "2026-02-03", "credit": "7000.00", "cp": "ACME TRADING PTE LTD"},
        ]
    )
    assert len(g.flows()) == 1
    assert g.flows()[0].src != APPLICANT_KEY and g.flows()[0].dst == APPLICANT_KEY
    assert g.entity(APPLICANT_KEY).display == APPLICANT_NAME
    assert g.entity(APPLICANT_KEY).kind == "applicant"


def test_truncated_name_merges_and_typo_does_not() -> None:
    g = build(
        [
            {"date": "2026-02-02", "credit": "9000.00", "cp": "ACME TRADING PTE L"},
            {"date": "2026-02-03", "credit": "7000.00", "cp": "ACME TRADING PTE LTD"},
            {"date": "2026-02-04", "credit": "7000.00", "cp": "Acme Trading Pte. Ltd."},
            {"date": "2026-02-05", "credit": "7000.00", "cp": "ACME TRADNG PTE LTD"},
        ]
    )
    others = [e for e in g.entities() if e.key != APPLICANT_KEY]
    assert len(others) == 2
    first = others[0]
    assert first.display == "ACME TRADING PTE LTD"  # the fuller spelling replaced the truncated one
    in_flows = g.flows("in")
    assert [f.src for f in in_flows][:3] == [first.key] * 3
    assert in_flows[3].src != first.key


def test_uen_in_description_merges_different_spellings() -> None:
    g = build(
        [
            {
                "date": "2026-02-02",
                "credit": "9000.00",
                "cp": "ALPHA CO",
                "desc": "FAST FROM ALPHA CO 201512345K",
            },
            {
                "date": "2026-02-03",
                "credit": "9000.00",
                "cp": "ALPHA COMPANY LIMITED",
                "desc": "FAST FROM ALPHA CO 201512345K",
            },
        ]
    )
    assert len([e for e in g.entities() if e.key != APPLICANT_KEY]) == 1


def test_flows_are_sorted_by_date_doc_row_and_filterable() -> None:
    d1 = statement_doc(
        "d2",
        [
            {"date": "2026-03-01", "debit": "100.00", "cp": "B CORP PTE LTD"},
            {"date": "2026-02-01", "credit": "200.00", "cp": "A CORP PTE LTD"},
        ],
    )
    d0 = statement_doc("d10", [{"date": "2026-02-01", "credit": "300.00", "cp": "C CORP PTE LTD"}])
    g = EntityGraph.build(ctx_for([d0, d1], use_case="aml", registry=False))
    flows = g.flows()
    assert [(f.document_id, f.row) for f in flows] == [("d2", 1), ("d10", 0), ("d2", 0)]
    assert [f.direction for f in g.flows("out")] == ["out"]
    assert [f.amount for f in g.flows("in")] == [Decimal("200.00"), Decimal("300.00")]


def test_confident_annotations_only_on_flows() -> None:
    g = build(
        [
            {
                "date": "2026-02-02",
                "credit": "9000.00",
                "cp": "A CORP PTE LTD",
                "c4": "refund",
                "c5": "trading",
            },
            {
                "date": "2026-02-03",
                "credit": "9000.00",
                "cp": "B CORP PTE LTD",
                "c4": "refund",
                "c5": "trading",
                "p": 0.3,
            },
        ]
    )
    a, b = g.flows("in")
    assert (a.c4, a.c5) == ("refund", "trading") and (b.c4, b.c5) == (None, None)


def test_summary_is_deterministic_json_with_all_edge_kinds() -> None:
    docs = [
        company_profile_doc("d1"),
        register_doc("d2"),
        chart_doc("d3"),
        invoice_doc("d4"),
        statement_doc(
            "d5",
            [
                {"date": "2026-02-02", "credit": "12500.50", "cp": "ALPHA TRADING PTE LTD"},
                {"date": "2026-02-04", "debit": "100.00", "cp": "TAN WEI MING"},
            ],
        ),
    ]

    def make():  # noqa: ANN202
        return EntityGraph.build(ctx_for(docs, registry=registry_with()))

    a, b = make().summary(), make().summary()
    assert a == b
    json.dumps(a)  # plain JSON
    kinds = {e["kind"] for e in a["edges"]}
    assert kinds == {"flow", "invoice", "owns", "director_of"}
    flow = next(e for e in a["edges"] if e["kind"] == "flow" and e["direction"] == "in")
    assert flow["amount"] == "12500.50" and flow["date"] == "2026-02-02"
    keys = [n["key"] for n in a["nodes"]]
    assert keys[0] == APPLICANT_KEY and len(keys) == len(set(keys))
    # Tan Wei Ming is one node across director, owner and a bank counterparty
    tans = [n for n in a["nodes"] if n["display"].lower() == "tan wei ming"]
    assert len(tans) == 1 and tans[0]["kind"] == "person"
    owners = [e for e in a["edges"] if e["kind"] == "owns" and e["src"] == tans[0]["key"]]
    assert {e["document_id"] for e in owners} == {"d2", "d1", "d3", "registry"}


def test_resolve_is_deterministic_in_first_seen_order() -> None:
    g1 = EntityGraph()
    g2 = EntityGraph()
    for g in (g1, g2):
        a = g.resolve("Zeta Pte Ltd")
        b = g.resolve("Alpha Pte Ltd")
        assert (a.key, b.key) == ("zeta", "alpha")
    again = g1.resolve("ZETA PTE. LTD.")
    assert again.key == "zeta"
    assert g1.summary() == g2.summary()


def test_same_key_different_uen_creates_a_second_entity() -> None:
    g = EntityGraph()
    first = g.resolve("Acme Pte Ltd", "201912345R")
    second = g.resolve("Acme Pte Ltd", "202012345H")
    assert first.key != second.key and second.key.startswith("acme")


def test_unknown_applicant_name_never_swallows_counterparties() -> None:
    from app.core.models import Applicant

    ctx = ctx_for(
        [statement_doc("d1", [{"date": "2026-02-02", "credit": "9000.00", "cp": "Applicant"}])],
        use_case="aml",
        registry=False,
        applicant=Applicant(),
    )
    g = EntityGraph.build(ctx)
    assert len(g.flows()) == 1
