from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.models import (
    BAND_THRESHOLDS,
    C1_LABELS,
    SCORE_WEIGHTS,
    Case,
    Cell,
    Cost,
    Flag,
    Provenance,
    Region,
    Row,
    StageOutcome,
    StageRecord,
    Table,
    truncate,
)

DATA_MODEL_ROW = {
    "index": 17,
    "cells": {
        "date": {
            "value": "2026-03-05",
            "raw": "05 Mar 2026",
            "kind": "date",
            "box": {"file_id": "f1", "page": 2, "x": 0.06, "y": 0.41, "w": 0.1, "h": 0.015},
            "source": "text_layer",
            "verified": True,
        },
        "description": {
            "value": "FAST PAYMENT FROM ACME TRADING PTE LTD",
            "kind": "text",
            "source": "text_layer",
        },
        "debit": {"value": None, "kind": "money"},
        "credit": {"value": "12500.00", "raw": "12,500.00", "kind": "money"},
        "balance": {"value": "48210.75", "raw": "48,210.75", "kind": "money"},
    },
    "annotations": {
        "c4": {
            "label": "trade_receipt",
            "probability": 0.94,
            "classifier": "rules",
            "version": "1",
        },
        "c5": {"label": "trading", "probability": 0.81, "classifier": "rules", "version": "1"},
        "counterparty": {
            "label": "ACME TRADING PTE LTD",
            "probability": 1.0,
            "classifier": "counterparty-parser",
            "version": "1",
        },
    },
}
DATA_MODEL_FLAG = {
    "id": "bs_running_balance.d3.transactions:17",
    "rule": "bs_running_balance",
    "stage": "rules",
    "severity": "high",
    "document_ids": ["d3"],
    "table": "transactions",
    "field": None,
    "row": 17,
    "region": {
        "file_id": "f1",
        "page": 2,
        "x": 0.62,
        "y": 0.41,
        "w": 0.12,
        "h": 0.015,
        "label": "balance",
    },
    "extra_regions": [],
    "evidence": "previous 35710.75 + credit 12500.00 = 48210.75, printed 58210.75",
    "explanation": "The balance on this row is 10000.00 higher than the previous balance plus the deposit.",
    "disposition": "open",
}


def test_data_model_examples_validate() -> None:
    row = Row.model_validate(DATA_MODEL_ROW)
    assert row.cells["credit"].money() == Decimal("12500.00")
    assert row.cells["date"].date_().isoformat() == "2026-03-05"
    flag = Flag.model_validate(DATA_MODEL_FLAG)
    assert flag.region and flag.region.label == "balance"


def test_typo_in_a_field_name_raises() -> None:
    with pytest.raises(ValidationError):
        Cell.model_validate({"value": "1", "knid": "money"})
    with pytest.raises(ValidationError):
        Flag.model_validate({**DATA_MODEL_FLAG, "evidense": "x"})


def test_truncation_limits() -> None:
    assert len(Cell(value="x", raw="a" * 900).raw or "") == 500
    assert (Cell(value="x", raw="a" * 900).raw or "").endswith("...")
    flag = Flag.model_validate(
        {**DATA_MODEL_FLAG, "evidence": "e" * 2000, "explanation": "x" * 500}
    )
    assert len(flag.evidence) == 1000 and flag.evidence.endswith("...")
    assert len(flag.explanation) == 300
    assert truncate("short", 10) == "short"


def test_region_bounds() -> None:
    Region(file_id="f1", page=1, x=0.5, y=0.5, w=0.5001, h=0.5)  # within the 1.0001 tolerance
    with pytest.raises(ValidationError):
        Region(file_id="f1", page=1, x=0.9, y=0.1, w=0.2, h=0.1)
    with pytest.raises(ValidationError):
        Region(file_id="f1", page=0, x=0, y=0, w=0.1, h=0.1)
    with pytest.raises(ValidationError):
        Region(file_id="f1", page=1, x=0, y=0, w=0, h=0.1)


def test_decimal_amounts_serialise_as_strings() -> None:
    cost = Cost(stage="extract", purpose="extract:invoice", model="m", usd=Decimal("0.000123"))
    dumped = cost.model_dump(mode="json")
    assert dumped["usd"] == "0.000123"
    assert Cost.model_validate_json(cost.model_dump_json()).usd == Decimal("0.000123")
    rec = StageRecord(stage="ingest", cost_usd=Decimal("1.5"))
    assert rec.model_dump(mode="json")["cost_usd"] == "1.5"


def test_timestamps_are_utc_z_strings() -> None:
    rec = StageRecord(stage="ingest", started_at=datetime(2026, 10, 27, 9, 15, 0))  # naive = UTC
    assert rec.started_at is not None and rec.started_at.tzinfo is UTC
    assert rec.model_dump(mode="json")["started_at"] == "2026-10-27T09:15:00Z"
    micro = StageRecord(stage="ingest", started_at=datetime(2026, 10, 27, 9, 15, 0, 5, tzinfo=UTC))
    assert micro.model_dump(mode="json")["started_at"].endswith("Z")


def test_case_round_trip_json(tmp_path) -> None:  # noqa: ANN001
    from app.core.testing import FIXED_NOW, FIXED_TODAY, make_doc

    doc = make_doc(
        "bank_statement",
        fields={"opening_balance": "10.00"},
        tables={"transactions": [{"date": "2026-03-01", "credit": "5.00", "balance": "15.00"}]},
    )
    case = Case(
        id="c_1",
        use_case="loan",
        today=FIXED_TODAY,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        documents=[doc],
    )
    again = Case.model_validate_json(case.model_dump_json())
    assert again == case
    assert again.model_dump(mode="json")["today"] == "2026-10-27"


def test_table_helpers() -> None:
    from app.core.testing import make_table

    t = make_table("transactions", [{"credit": "1.00"}, {"debit": "2.00"}])
    assert isinstance(t, Table)
    assert t.values("credit") == ["1.00", None]
    assert [c.value for c in t.col("debit")] == [None, "2.00"]


def test_constants() -> None:
    assert SCORE_WEIGHTS == {"high": 30, "medium": 15, "low": 5, "info": 0}
    assert BAND_THRESHOLDS == {"medium": 30, "high": 60}
    assert "bank_statement" in C1_LABELS and C1_LABELS[-1] == "other" and len(C1_LABELS) == 14


def test_provenance_and_outcome_defaults() -> None:
    assert StageOutcome().partial is False
    with pytest.raises(ValidationError):
        Provenance.model_validate({"app_version": "1"})
