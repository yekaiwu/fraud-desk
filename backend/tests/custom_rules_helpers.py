"""Builders shared by the custom-rule engine tests: specs, loaded rules and documents."""

from __future__ import annotations

from typing import Any

from app.core.models import Cell, Document, FileRef, PageInfo, Region
from app.core.testing import make_ctx, make_doc
from app.custom_rules.engine import LoadedCustomRule
from app.custom_rules.readback import readback
from app.custom_rules.spec import CustomRuleSpec, parse_spec


def spec_of(condition: dict[str, Any], severity: str = "low") -> CustomRuleSpec:
    spec, problems = parse_spec({"spec_version": 1, "severity": severity, "condition": condition})
    assert spec is not None, problems
    return spec


def t1(field: str, operator: str, value: str | None = None, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "template": "field_vs_constant",
        "field": field,
        "operator": operator,
        **kw,
    }
    if value is not None:
        out["value"] = value
    return out


def t2(field: str, direction: str, days: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"template": "date_window", "field": field, "direction": direction}
    if days is not None:
        out["days"] = days
    return out


def t3(field: str) -> dict[str, Any]:
    return {"template": "field_missing", "field": field}


def t4(
    left: list[str],
    right: str,
    operator: str = "gt",
    multiplier: str = "2",
    column: str = "current",
) -> dict[str, Any]:
    return {
        "template": "fs_ratio",
        "left": left,
        "operator": operator,
        "multiplier": multiplier,
        "right": right,
        "column": column,
    }


DEBT = ["balance_sheet.borrowings_current", "balance_sheet.borrowings_non_current"]
EQUITY = "balance_sheet.total_equity"


def loaded(
    condition: dict[str, Any],
    severity: str = "low",
    *,
    rule_id: str = "cus_0123456789",
    version: int = 1,
    name: str = "My secret rule name",
    author: str = "alice",
    origin: str = "own",
) -> LoadedCustomRule:
    spec = spec_of(condition, severity)
    return LoadedCustomRule(
        rule_id=rule_id,
        version=version,
        spec=spec,
        spec_hash=spec.spec_hash(),
        name=name,
        author=author,
        origin=origin,  # type: ignore[arg-type]
        readback=readback(spec),
    )


def invoice(id: str = "d1", **fields: str | None) -> Document:  # noqa: A002
    base = {"invoice_number": "INV-1", "invoice_date": "2026-10-01", "total": "120000.00"}
    base.update(fields)
    return make_doc("invoice", id=id, fields=base)


def fs_rows(**lines: tuple[str | None, str | None] | str) -> list[dict[str, str | None]]:
    """Balance sheet rows: ``name=(current, prior)`` or ``name="current"``. Names ending in ``_sub``
    are printed subtotals (the suffix is dropped)."""
    rows = []
    for name, value in lines.items():
        current, prior = value if isinstance(value, tuple) else (value, None)
        sub = name.endswith("_sub")
        key = name[:-4] if sub else name
        rows.append(
            {
                "line": key,
                "label": key.replace("_", " "),
                "section": "totals",
                "current": current,
                "prior": prior,
                "is_subtotal": "true" if sub else "false",
            }
        )
    return rows


def statement_doc(id: str = "d1", **lines: tuple[str | None, str | None] | str) -> Document:  # noqa: A002
    return make_doc(
        "financial_statement",
        id=id,
        fields={"company_name": "Acme Pte Ltd", "uen": "201912345R"},
        tables={"balance_sheet": fs_rows(**lines), "income_statement": []},
    )


def with_cell(doc: Document, name: str, **changes: Any) -> Document:
    """A copy of ``doc`` whose cell ``name`` is changed (verified, alt_raw, box ...)."""
    copy = doc.model_copy(deep=True)
    copy.fields[name] = copy.fields[name].model_copy(update=changes)
    return copy


BOX = Region(file_id="f1", page=1, x=0.1, y=0.1, w=0.2, h=0.05)


def scan_file(file_id: str = "f1") -> FileRef:
    return FileRef(
        id=file_id,
        name="scan.pdf",
        sha256="0" * 64,
        size_bytes=10,
        mime="application/pdf",
        pages=[PageInfo(page=1, width_pt=600, height_pt=800, has_text_layer=False, is_scan=True)],
    )


def ctx_with(documents: list[Document], *rules: LoadedCustomRule, **kw: Any):  # noqa: ANN201
    return make_ctx(documents, custom_rules=list(rules), **kw)


__all__ = [
    "BOX",
    "DEBT",
    "EQUITY",
    "Cell",
    "ctx_with",
    "fs_rows",
    "invoice",
    "loaded",
    "scan_file",
    "spec_of",
    "statement_doc",
    "t1",
    "t2",
    "t3",
    "t4",
    "with_cell",
]
