"""``verify_bundle``: a deliberately corrupted copy of loan-clean fails the right invariant (T-gen-loan-11/18)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts import make_bundle
from scripts._gen import verify
from scripts._gen.schema import BundleSpec, Mutation

from .test_gen_helpers import doc, load, loan_bundle


def _copy(tmp: Path, name: str) -> Path:
    src = loan_bundle("clean")
    dst = tmp / "bundles" / name
    shutil.copytree(src, dst)
    shutil.copy(
        src.parent.parent / "registry.json", tmp / "registry.json"
    )  # registry_path is ../../registry.json
    return dst


def _edit_gt(bdir: Path, fn: Callable[[dict[str, Any]], None]) -> None:
    gt = json.loads((bdir / "ground_truth.json").read_text())
    fn(gt)
    (bdir / "ground_truth.json").write_text(json.dumps(gt, sort_keys=True, indent=2) + "\n")


def _ids(problems: list[str]) -> set[str]:
    return {p.split(":", 1)[0] for p in problems}


def test_clean_copy_is_clean(tmp_path: Path) -> None:
    assert verify.verify_bundle(_copy(tmp_path, "c")) == []


def test_w1_running_balance(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w1")

    def f(gt: dict) -> None:
        r = doc(gt, "bank_statement:2025-09")["tables"]["transactions"]["rows"][4]
        r["cells"]["balance"]["value"] = "1.00"

    _edit_gt(b, f)
    assert "W1" in _ids(verify.verify_bundle(b))


def test_w1_printed_totals_and_opening_continuity(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w1b")

    def f(gt: dict) -> None:
        doc(gt, "bank_statement:2025-08")["fields"]["opening_balance"]["value"] = "5.00"
        doc(gt, "bank_statement:2025-07")["fields"]["total_debits"]["value"] = "5.00"

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W1")]
    assert any("opening" in m for m in msgs) and any("total_debits" in m for m in msgs)


def test_w2_dates_and_page_chain(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w2")

    def f(gt: dict) -> None:
        st = doc(gt, "bank_statement:2025-09")
        st["tables"]["transactions"]["rows"][3]["cells"]["date"]["value"] = "2025-09-06"  # Saturday
        del st["tables"]["page_chain"]["rows"][1]

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W2")]
    assert any("weekday" in m for m in msgs) and any("page chain" in m for m in msgs)


def test_w3_invoice_arithmetic(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w3")

    def f(gt: dict) -> None:
        doc(gt, "invoice:INV-2025-0102")["fields"]["total"]["value"] = "99999.00"
        doc(gt, "invoice:INV-2025-0103")["fields"]["supplier_uen"]["value"] = "201912345K"

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W3")]
    assert any("total" in m for m in msgs) and any("UEN" in m for m in msgs)


def test_w4_paid_invoice_without_credit(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w4")

    def f(gt: dict) -> None:
        # mark an unpaid invoice paid (the loan-fraud plant, done by hand)
        d = doc(gt, "invoice:INV-2025-0104")
        d["fields"]["payment_status"]["value"] = "paid"
        d["fields"]["paid_date"]["value"] = "2025-09-30"

    _edit_gt(b, f)
    assert any(p.startswith("W4") and "INV-2025-0104" in p for p in verify.verify_bundle(b))


def test_w4_is_exempt_for_planted_targets() -> None:
    assert verify.verify_bundle(loan_bundle("fraud")) == []


def test_w5_financial_statements(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w5")

    def f(gt: dict) -> None:
        fs = doc(gt, "financial_statement:FY2025")
        rev = fs["tables"]["income_statement"]["rows"][0]["cells"]
        rev["prior"]["value"] = "123456.00"  # comparative differs from FY2024
        cash = next(
            r
            for r in fs["tables"]["balance_sheet"]["rows"]
            if r["cells"]["line"]["value"] == "cash_and_equivalents"
        )
        cash["cells"]["current"]["value"] = "1.00"

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W5")]
    assert any("prior revenue" in m for m in msgs) and any("cash" in m for m in msgs)


def test_w5_revenue_ratio(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w5r")

    def f(gt: dict) -> None:
        for d in gt["documents"]:
            if d["key"] == "financial_statement:FY2025":
                d["tables"]["income_statement"]["rows"][0]["cells"]["current"]["value"] = (
                    "2400000.00"
                )

    _edit_gt(b, f)
    assert any("annualised trade credits / revenue" in p for p in verify.verify_bundle(b))


def test_w6_contract_value_and_po(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w6")

    def f(gt: dict) -> None:
        doc(gt, "contract:1")["fields"]["contract_value"]["value"] = "1000.00"
        doc(gt, "purchase_order:1")["fields"]["total"]["value"] = "12.00"

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W6")]
    assert any("contract value" in m for m in msgs) and any("PO total" in m for m in msgs)


def test_w7_ids(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w7")

    def f(gt: dict) -> None:
        d = doc(gt, "national_id:1")
        v = d["fields"]["id_number"]["value"]
        d["fields"]["id_number"]["value"] = v[:-1] + ("A" if v[-1] != "A" else "B")
        doc(gt, "national_id:2")["fields"]["date_of_birth"]["value"] = "2031-03-04"

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W7")]
    assert any("check letter" in m for m in msgs) and any("date of birth" in m for m in msgs)


def test_w8_directors_against_registry(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w8")

    def f(gt: dict) -> None:
        rows = doc(gt, "company_profile:-")["tables"]["directors"]["rows"]
        rows[0]["cells"]["director_name"]["value"] = "Somebody Else"
        rows[0]["cells"]["director_name"]["raw"] = "Somebody Else"

    _edit_gt(b, f)
    assert "W8" in _ids(verify.verify_bundle(b))


def test_w9_applicant_name_and_uen(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w9")

    def f(gt: dict) -> None:
        d = doc(gt, "bank_statement:2025-10")
        d["fields"]["account_holder"]["value"] = "Other Holdings Pte. Ltd."
        d["fields"]["uen"]["value"] = "202012345H"

    _edit_gt(b, f)
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W9")]
    assert len(msgs) == 2


def test_w10_labels(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w10")
    lab = json.loads((b / "labels.json").read_text())
    lab["rows"][0]["c5"] = None
    credit = next(r for r in lab["rows"] if r["c4"])
    credit["c4"] = None
    lab["pages"][0]["c1"] = None
    next(d for d in lab["documents"] if d["c1"] == "bank_statement")["c3"] = None
    (b / "labels.json").write_text(json.dumps(lab))
    msgs = [p for p in verify.verify_bundle(b) if p.startswith("W10")]
    assert len(msgs) >= 4


def test_w11_duplicate_fingerprint(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w11")

    def f(gt: dict) -> None:
        a, c = doc(gt, "invoice:INV-2025-0101"), doc(gt, "invoice:INV-2025-0102")
        for k in ("invoice_number", "invoice_date", "total", "supplier_uen"):
            c["fields"][k]["value"] = a["fields"][k]["value"]

    _edit_gt(b, f)
    assert "W11" in _ids(verify.verify_bundle(b))


def test_w12_file_hashes(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w12")
    p = b / "digital/invoice_INV-2025-0101.pdf"
    p.write_bytes(p.read_bytes() + b"\n")
    assert any(
        m.startswith("W12") and "invoice_INV-2025-0101" in m for m in verify.verify_bundle(b)
    )
    (b / "digital/invoice_INV-2025-0102.pdf").unlink()
    assert any("is missing" in m for m in verify.verify_bundle(b))
    m = json.loads((b / "manifest.json").read_text())
    m["files"][3]["pages"][0]["page_sha256"] = "0" * 64
    (b / "manifest.json").write_text(json.dumps(m))
    assert any("page_sha256" in x for x in verify.verify_bundle(b))


def test_w13_clean_has_no_plants(tmp_path: Path) -> None:
    b = _copy(tmp_path, "w13")
    m = json.loads((b / "manifest.json").read_text())
    m["planted"] = [
        {
            "id": "P1",
            "kind": "x",
            "description": "d",
            "deck_slide": None,
            "target": None,
            "facts": {},
        }
    ]
    (b / "manifest.json").write_text(json.dumps(m))
    assert "W13" in _ids(verify.verify_bundle(b))


def _insert(
    date_: str,
    desc: str,
    *,
    debit: str | None = None,
    credit: str | None = None,
    cp: str,
    c5: str,
    c4: str | None = None,
) -> Mutation:
    args: dict[str, Any] = {
        "date": date_,
        "description": desc,
        "c5": c5,
        "counterparty": cp,
        "recompute": True,
    }
    if debit:
        args["debit"] = debit
    if credit:
        args["credit"] = credit
        args["c4"] = c4 or "trade_receipt"
    return Mutation(op="insert_rows", target="bank_statement:2025-08", args=args)


def test_w14_planted_pass_through_pattern_fails(tmp_path: Path) -> None:
    muts = []
    for i, (d_in, d_out) in enumerate(
        (("2025-08-11", "2025-08-12"), ("2025-08-13", "2025-08-14"), ("2025-08-18", "2025-08-19"))
    ):
        muts.append(
            _insert(
                d_in,
                f"FAST PAYMENT FROM VANTAGE CREST TRADING PTE LTD {i}",
                credit="20000.00",
                cp="VANTAGE CREST TRADING PTE LTD",
                c5="trading",
            )
        )
        muts.append(
            _insert(
                d_out,
                "FAST PAYMENT TO LUMEN GATEWAY HOLDINGS PTE LTD",
                debit="19700.00",
                cp="LUMEN GATEWAY HOLDINGS PTE LTD",
                c5="other",
            )
        )
    out = tmp_path / "pt"
    make_bundle.build_bundle(BundleSpec(scenario="loan", mutations=muts), out)
    assert any(p.startswith("W14") and "pass-through" in p for p in verify.verify_bundle(out))
    # a single pair is allowed
    out2 = tmp_path / "pt2"
    make_bundle.build_bundle(BundleSpec(scenario="loan", mutations=muts[:2]), out2)
    assert not any(p.startswith("W14") for p in verify.verify_bundle(out2))


def test_w14_risky_counterparty_and_dominant_activity(tmp_path: Path) -> None:
    muts = [
        _insert(
            "2025-08-11",
            "FAST PAYMENT FROM ZENITH CRYPTO PTE LTD",
            credit="1500.00",
            cp="ZENITH CRYPTO PTE LTD",
            c5="crypto_virtual_assets",
        )
    ]
    out = tmp_path / "crypto"
    make_bundle.build_bundle(BundleSpec(scenario="loan", mutations=muts), out)
    assert any(
        p.startswith("W14") and "crypto_virtual_assets" in p for p in verify.verify_bundle(out)
    )


def test_w14_only_applies_to_clean_variants(tmp_path: Path) -> None:
    muts = [
        _insert(
            "2025-08-11",
            "FAST PAYMENT FROM ZENITH CRYPTO PTE LTD",
            credit="1500.00",
            cp="ZENITH CRYPTO PTE LTD",
            c5="crypto_virtual_assets",
        )
    ]
    out = tmp_path / "crypto-fraud"
    make_bundle.build_bundle(BundleSpec(scenario="loan", variant="fraud", mutations=muts), out)
    assert not any(p.startswith("W14") for p in verify.verify_bundle(out))


def test_ownership_checks_for_kyc_documents() -> None:
    """The W8 and W14 branches that kyc bundles use (shareholder register, chart children)."""

    def row(name: str, pct: str, typ: str = "individual", parent: str | None = None) -> dict:
        cell = lambda v: {"value": v, "raw": v, "box": None}  # noqa: E731
        return {
            "index": 0,
            "page": 1,
            "box": None,
            "cells": {
                "holder_name": cell(name),
                "holder_type": cell(typ),
                "id_or_uen": cell(None),
                "percent": cell(pct),
                "parent_name": cell(parent),
                "is_ubo": cell("true"),
            },
        }

    chart = {
        "key": "ownership_chart:-",
        "doc_type": "ownership_chart",
        "fields": {"uen": {"value": "X"}},
        "tables": {
            "holders": {
                "rows": [
                    row("Tan Wei Ming", "45.00"),
                    row("Kestrel Capital Holdings Pte. Ltd.", "25.00", "company"),
                    row("Hafiz", "20.00"),
                ]
            }
        },
    }
    problems = verify.w8_ownership(
        {"ownership_chart": [chart]}, Path("."), {"registry_path": "nope.json"}, set()
    )
    assert any("sum to 90.00" in p for p in problems)
    w14 = verify.w14_thresholds({"ownership_chart": [chart], "bank_statement": []}, {"rows": []})
    assert any("Kestrel" in p and "no children" in p for p in w14)
    with_child = {
        **chart,
        "tables": {
            "holders": {
                "rows": [
                    *chart["tables"]["holders"]["rows"],
                    row("Teo Boon Heng", "100.00", parent="Kestrel Capital Holdings Pte. Ltd."),
                ]
            }
        },
    }
    assert (
        verify.w14_thresholds({"ownership_chart": [with_child], "bank_statement": []}, {"rows": []})
        == []
    )


def test_mrz_and_passport_branch() -> None:
    from scripts._gen import ids

    l1, l2 = ids.build_td3(
        issuing="ESP",
        surname="Vasquez",
        given_names="Elena",
        passport_no="XB1234567",
        nationality="ESP",
        dob=date(1988, 5, 3),
        sex="F",
        expiry=date(2031, 6, 1),
    )
    cell = lambda v: {"value": v, "raw": v, "box": None}  # noqa: E731
    good = {
        "key": "passport:1",
        "doc_type": "passport",
        "fields": {
            "mrz_line2": cell(l2),
            "date_of_expiry": cell("2031-06-01"),
            "date_of_birth": cell("1988-05-03"),
        },
        "tables": {},
    }
    assert verify.w7_ids([], [good], date(2026, 10, 27)) == []
    bad = {
        **good,
        "fields": {
            **good["fields"],
            "mrz_line2": cell(l2[:9] + ("1" if l2[9] != "1" else "2") + l2[10:]),
        },
    }
    assert any("MRZ" in p for p in verify.w7_ids([], [bad], date(2026, 10, 27)))
    assert l1.startswith("P<ESP")


@pytest.mark.parametrize("variant", ["fraud", "forger"])
def test_planted_variants_still_verify_empty(variant: str) -> None:
    assert verify.verify_bundle(loan_bundle(variant)) == []
    assert load(loan_bundle(variant), "manifest.json")["planted"]
