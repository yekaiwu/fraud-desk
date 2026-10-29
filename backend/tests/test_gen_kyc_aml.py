"""AML demo bundle: clean control, fraud, careful forger (T-gen-kyc-03, T-gen-kyc-06)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from scripts import make_bundle
from scripts._gen import kyc_plots, verify
from scripts._gen.bundle_aml import story_world
from scripts._gen.schema import BundleSpec, Manifest

from .test_gen_kyc_helpers import aml_bundle, digital_hashes, doc, docs_of, field, load, table_rows

D = Decimal
VARIANTS = ("clean", "fraud", "forger")
ROUTE = [
    ("VANTAGE CREST TRADING PTE LTD", "LUMEN GATEWAY HOLDINGS PTE LTD", date(2026, 7, 14), D("333333.33")),
    ("SILVERLINE IMPORTS PTE LTD", "HARBOUR SPIRE CAPITAL PTE LTD", date(2026, 7, 28), D("333333.33")),
    ("PACIFIC ANCHOR VENTURES PTE LTD", "REDCLIFF NOMINEES PTE LTD", date(2026, 8, 11), D("333333.34")),
]  # fmt: skip


def _rows(bdir: Path) -> list[dict]:
    """Flat statement rows: key, index, date, description, debit, credit, counterparty, c4, c5."""
    gt, lab = load(bdir, "ground_truth.json"), load(bdir, "labels.json")
    by = {(r["doc_key"], r["row"]): r for r in lab["rows"]}
    out = []
    for d in docs_of(gt, "bank_statement"):
        for r in d["tables"]["transactions"]["rows"]:
            c, lr = r["cells"], by[(d["key"], r["index"])]
            out.append(
                {
                    "doc": d["key"],
                    "index": r["index"],
                    "date": date.fromisoformat(c["date"]["value"]),
                    "description": c["description"]["value"],
                    "debit": D(c["debit"]["value"]) if c["debit"]["value"] else None,
                    "credit": D(c["credit"]["value"]) if c["credit"]["value"] else None,
                    "cp": lr["counterparty"],
                    "c4": lr["c4"],
                    "c5": lr["c5"],
                }
            )
    return out


def _credits(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["credit"] is not None]


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_verifies(variant: str) -> None:
    assert verify.verify_bundle(aml_bundle(variant)) == []


def test_clean_control_contents_and_expectations() -> None:
    b = aml_bundle("clean")
    m, gt = load(b, "manifest.json"), load(b, "ground_truth.json")
    kinds: dict[str, int] = {}
    for d in gt["documents"]:
        kinds[d["doc_type"]] = kinds.get(d["doc_type"], 0) + 1
    # W8 forces an ID document for every profile director, so two cards (not one) accompany the profile
    assert kinds == {
        "company_profile": 1,
        "national_id": 2,
        "source_of_funds_declaration": 1,
        "bank_statement": 6,
        "invoice": 10,
        "contract": 1,
    }
    assert (
        m["bundle_id"] == "aml-clean"
        and m["applicant"]["name"] == "Tanjong Ridge Trading Pte. Ltd."
    )
    assert m["planted"] == []
    e = m["expected"]
    assert (
        e["lane_any_of"] == ["fast", "aml"] and e["band_max"] == "low" and e["verdict"] == "approve"
    )
    assert sorted(d["key"] for d in docs_of(gt, "bank_statement")) == [
        f"bank_statement:2026-{n:02d}" for n in range(4, 10)
    ]
    assert all(
        d["layout"] == "uob_style" for d in m["documents"] if d["doc_type"] == "bank_statement"
    )
    # no pass-through and a supported SoF in the control
    w, _ = story_world(BundleSpec(scenario="aml", variant="clean"))
    assert not kyc_plots.pass_through_pattern(w)
    assert len(kyc_plots.pass_through_hops(w)) <= 2


def test_clean_sof_is_supported_by_a_paid_invoice_and_its_credit() -> None:
    b = aml_bundle("clean")
    gt = load(b, "ground_truth.json")
    sof = doc(gt, "source_of_funds_declaration:-")
    amount = D(field(sof, "declared_amount"))
    inv = next(
        d
        for d in docs_of(gt, "invoice")
        if field(d, "invoice_number") == field(sof, "invoice_reference")
    )
    assert amount == D(field(inv, "total")) and field(inv, "payment_status") == "paid"
    start, end = (
        date.fromisoformat(field(sof, "period_start")),
        date.fromisoformat(field(sof, "period_end")),
    )
    support = [
        r for r in _credits(_rows(b))
        if r["c4"] in ("trade_receipt", "customer_payment") and start <= r["date"] <= end
        and re.sub(r"\W", "", field(sof, "counterparty_name").upper().replace("PTE.", "PTE").replace("LTD.", "LTD")) == re.sub(r"\W", "", r["cp"])
    ]  # fmt: skip
    assert sum((r["credit"] for r in support), D(0)) >= amount


def test_fraud_and_forger_story_numbers() -> None:
    for v in ("fraud", "forger"):
        b = aml_bundle(v)
        rows = _rows(b)
        credits = _credits(rows)
        for src, dst, when, amt in ROUTE:
            cr = [r for r in credits if r["cp"] == src]
            assert len(cr) == 1, src
            assert cr[0]["credit"] == amt and cr[0]["date"] == when
            assert cr[0]["c4"] == "customer_payment"
            assert "INV" not in cr[0]["description"].upper()  # no invoice reference
            fwd = [r for r in rows if r["debit"] is not None and r["cp"] == dst]
            assert len(fwd) == 1, dst
            gap = (fwd[0]["date"] - when).days
            assert 1 <= gap <= 3 and fwd[0]["date"].weekday() < 5
            ratio = fwd[0]["debit"] / amt
            assert D("0.98") <= ratio <= D("0.995"), ratio
        assert sum(amt for *_x, amt in ROUTE) == D("1000000.00")
        # routed credits are at least 25% of all credits (bs_pass_through needs 20%)
        routed = sum((r["credit"] for r in credits if r["cp"] in {s for s, *_ in ROUTE}), D(0))
        assert routed / sum((r["credit"] for r in credits), D(0)) >= D("0.25")
        # Orchid Bay never appears as a remitter (or anywhere in the statements)
        assert not [r for r in rows if "ORCHID" in (r["description"] or "").upper()]
        assert not [r for r in rows if r["cp"] and "ORCHID" in r["cp"].upper()]


def test_pass_through_pattern_exists_in_fraud_and_forger_only() -> None:
    for v, expect in (("clean", False), ("fraud", True), ("forger", True)):
        w, _ = story_world(BundleSpec(scenario="aml", variant=v))  # type: ignore[arg-type]
        assert kyc_plots.pass_through_pattern(w) is expect, v
        if expect:
            hops = kyc_plots.pass_through_hops(w)
            pairs = {(c.counterparty, d.counterparty) for c, d in hops}
            assert {(s, t) for s, t, *_ in ROUTE} <= pairs


def test_sof_unsupported_by_construction_and_matches_statement_account() -> None:
    for v in ("fraud", "forger"):
        b = aml_bundle(v)
        gt = load(b, "ground_truth.json")
        sof = doc(gt, "source_of_funds_declaration:-")
        assert field(sof, "declared_amount") == "1000000.00" and field(sof, "currency") == "SGD"
        assert field(sof, "origin_category") == "sales_proceeds"
        assert field(sof, "counterparty_name") == "Orchid Bay Holdings Pte. Ltd."
        assert field(sof, "invoice_reference") == "INV-2026-0117"
        assert (field(sof, "period_start"), field(sof, "period_end")) == (
            "2026-07-01",
            "2026-08-31",
        )
        assert field(sof, "declarant_name") == "Rajesh s/o Kumar"
        accts = {field(d, "account_number") for d in docs_of(gt, "bank_statement")}
        assert accts == {field(sof, "receiving_account")}
        # credits that could support the claim: trade credits naming Orchid Bay or its invoice
        supporting = [
            r
            for r in _credits(_rows(b))
            if "ORCHID" in (r["cp"] or "").upper() or "0117" in r["description"]
        ]
        assert supporting == []
        unsupported = D(field(sof, "declared_amount")) - sum(
            (r["credit"] for r in supporting), D(0)
        )
        assert unsupported == D("1000000.00")


def test_remitter_never_matches_the_buyer_and_the_instalment_set_sums_to_the_invoice() -> None:
    for v in ("fraud", "forger"):
        b = aml_bundle(v)
        gt = load(b, "ground_truth.json")
        inv = doc(gt, "invoice:INV-2026-0117")
        total = D(field(inv, "total"))
        assert total == D("1000000.00")
        buyer = field(inv, "buyer_name")
        assert buyer == "Orchid Bay Holdings Pte. Ltd."
        start = date.fromisoformat(field(inv, "invoice_date"))
        end = date.fromisoformat(field(inv, "due_date")) + timedelta(days=30)
        window = [
            r for r in _credits(_rows(b)) if start <= r["date"] <= end and r["credit"] > total / 100
        ]
        top3 = sorted(window, key=lambda r: -r["credit"])[:3]
        assert sum((r["credit"] for r in top3), D(0)) == total
        assert all("ORCHID" not in (r["cp"] or "") for r in top3)  # mismatch on 3 of 3 credits
        assert {r["cp"] for r in top3} == {s for s, *_ in ROUTE}


def test_planted_invoice_dates_and_stamp() -> None:
    for v in ("fraud", "forger"):
        inv = doc(load(aml_bundle(v), "ground_truth.json"), "invoice:INV-2026-0117")
        assert field(inv, "invoice_date") == "2026-07-01" and field(inv, "due_date") == "2026-07-31"
        assert field(inv, "payment_status") is None and field(inv, "paid_date") is None
        assert field(inv, "supplier_name") == "Tanjong Ridge Trading Pte. Ltd."
        assert field(inv, "contract_reference") is None and field(inv, "po_reference") is None


def test_forger_invoice_is_arithmetically_clean_and_fraud_prints_8_percent() -> None:
    r2 = lambda x: x.quantize(D("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
    f = doc(load(aml_bundle("forger"), "ground_truth.json"), "invoice:INV-2026-0117")
    sub, gst, tot = D(field(f, "subtotal")), D(field(f, "gst_amount")), D(field(f, "total"))
    assert (sub, gst, tot) == (D("917431.19"), D("82568.81"), D("1000000.00"))
    assert gst == r2(sub * D("0.09")) and field(f, "gst_rate") == "9.00"
    lines = f["tables"]["lines"]["rows"]
    assert sum((D(r["cells"]["amount"]["value"]) for r in lines), D(0)) == sub
    for r in lines:
        c = r["cells"]
        assert r2(D(c["quantity"]["value"]) * D(c["unit_price"]["value"])) == D(
            c["amount"]["value"]
        )
    assert (
        verify.w3_invoices(
            docs_of(load(aml_bundle("forger"), "ground_truth.json"), "invoice"), False
        )
        == []
    )
    x = doc(load(aml_bundle("fraud"), "ground_truth.json"), "invoice:INV-2026-0117")
    xs, xg, xt = D(field(x, "subtotal")), D(field(x, "gst_amount")), D(field(x, "total"))
    assert field(x, "gst_rate") == "8.00" and xg == r2(xs * D("0.08")) and xt == D("1000000.00")
    assert xg != r2(xs * D("0.09"))  # 9% of this subtotal would be 83,333.33: inv_gst sees it


def test_invoice_numbering_is_contiguous_and_never_back_dated() -> None:
    """Regular invoices use their own series so INV-2026-0117 (dated 2026-07-01) cannot look
    back-dated inside one series (inv_sequence 'order')."""
    for v in VARIANTS:
        gt = load(aml_bundle(v), "ground_truth.json")
        series: dict[str, list[tuple[int, date]]] = {}
        for d in docs_of(gt, "invoice"):
            m = re.match(r"^(.*?)(\d+)$", field(d, "invoice_number"))
            series.setdefault(m.group(1), []).append(
                (int(m.group(2)), date.fromisoformat(field(d, "invoice_date")))
            )
        for nums in series.values():
            nums.sort()
            assert [n for n, _ in nums] == list(range(nums[0][0], nums[-1][0] + 1))
            dates = [d for _, d in nums]
            assert dates == sorted(dates)
        assert sorted(series) == (["INV-2026-", "SI-2026-"] if v != "clean" else ["SI-2026-"])
    # the credits still name the renumbered invoices
    rows = _rows(aml_bundle("clean"))
    assert any(re.search(r"SI-2026-01\d\d", r["description"]) for r in rows)
    assert not any("INV-2026" in r["description"] for r in rows)


@pytest.mark.parametrize("variant", ["fraud", "forger"])
def test_diff_against_clean_is_limited_to_planted_targets(variant: str) -> None:
    clean, other = aml_bundle("clean"), aml_bundle(variant)
    hc, ho = digital_hashes(clean), digital_hashes(other)
    changed = {k for k in ho if hc.get(k) != ho[k]}
    assert set(hc) <= set(ho)
    m = load(other, "manifest.json")
    planted = {p["target"]["doc_key"] for p in m["planted"]}
    assert (
        changed
        == planted
        == {
            "bank_statement:2026-07",
            "bank_statement:2026-08",
            "invoice:INV-2026-0117",
            "source_of_funds_declaration:-",
        }
    )
    # statements before July and the September statement (the retained slice is absorbed
    # in August's overdraft payment) are byte-identical to the control
    for k in (
        "bank_statement:2026-04",
        "bank_statement:2026-05",
        "bank_statement:2026-06",
        "bank_statement:2026-09",
    ):
        assert hc[k] == ho[k], k


def test_q07_overdraft_month_survives_the_plot() -> None:
    for v in VARIANTS:
        w, _ = story_world(BundleSpec(scenario="aml", variant=v))  # type: ignore[arg-type]
        assert kyc_plots.negative_months(w) == {"2026-08"}
    a, b = _rows(aml_bundle("clean")), _rows(aml_bundle("fraud"))
    # closing balance of the control and the story agree: only the routed legs and one payment differ
    last = lambda rs: [r for r in rs if r["doc"] == "bank_statement:2026-09"][-1]  # noqa: E731
    assert last(a)["description"] == last(b)["description"]


def test_expected_blocks() -> None:
    clean, fraud, forger = (load(aml_bundle(v), "manifest.json")["expected"] for v in VARIANTS)
    assert clean["must_fire"] == []
    for e in (fraud, forger):
        assert e["lane"] == "aml" and e["verdict"] == "escalate_aml"
        sev = {x["rule"]: x["min_severity"] for x in e["must_fire"]}
        assert sev["x_sof_support"] == "high" and sev["x_remitter_vs_buyer"] == "high"
        assert sev["bs_pass_through"] == "medium"
    assert fraud["band_min"] == "high" and forger["band_min"] == "medium"
    assert "inv_gst" in [x["rule"] for x in fraud["must_fire"]]
    assert {
        "inv_gst",
        "inv_total",
        "inv_sequence",
        "bs_running_balance",
        "bs_totals",
        "bs_period_continuity",
        "pdf_modified",
    } <= set(forger["must_not_fire"])


def test_planted_records_point_at_real_targets() -> None:
    for v in ("fraud", "forger"):
        b = aml_bundle(v)
        m, gt = load(b, "manifest.json"), load(b, "ground_truth.json")
        kinds = [p["kind"] for p in m["planted"]]
        assert kinds.count("pass_through") == 3 and "sof_unsupported" in kinds
        assert ("invoice_gst_8pct" in kinds) == (v == "fraud")
        assert all(p["deck_slide"] == 15 for p in m["planted"])
        files = {f["path"] for f in m["files"]}
        for p in m["planted"]:
            t = p["target"]
            assert t["box"] and t["box"]["file"] in files
            bx = t["box"]
            assert (
                bx["x"] >= 0
                and bx["y"] >= 0
                and bx["x"] + bx["w"] <= 1.0001
                and bx["y"] + bx["h"] <= 1.0001
            )
        for p in (p for p in m["planted"] if p["kind"] == "pass_through"):
            row = table_rows(gt, p["target"]["doc_key"], "transactions")[p["target"]["row"]]
            assert row["cells"]["credit"]["value"] == p["facts"]["credit"]


def test_determinism(tmp_path: Path) -> None:
    for v in VARIANTS:
        spec = BundleSpec(scenario="aml", variant=v)  # type: ignore[arg-type]
        a, b = tmp_path / f"a-{v}", tmp_path / f"b-{v}"
        make_bundle.build_bundle(spec, a)
        make_bundle.build_bundle(spec, b)
        fa = {
            p.relative_to(a): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(a.rglob("*"))
            if p.is_file()
        }
        fb = {
            p.relative_to(b): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(b.rglob("*"))
            if p.is_file()
        }
        assert fa == fb and fa
    m = Manifest.model_validate(load(aml_bundle("forger"), "manifest.json"))
    make_bundle.build_bundle(m.spec, tmp_path / "again")
    assert (tmp_path / "again" / "manifest.json").read_text() == (
        aml_bundle("forger") / "manifest.json"
    ).read_text()


def test_other_companies_build_and_verify() -> None:
    for idx in (2, 100, 102):
        for v in VARIANTS:
            assert verify.verify_bundle(aml_bundle(v, company_index=idx)) == [], (idx, v)
    gt = load(aml_bundle("fraud", company_index=2), "ground_truth.json")
    assert doc(gt, "invoice:INV-2026-0117")
    assert (
        load(aml_bundle("fraud", company_index=2), "manifest.json")["bundle_id"] == "aml-fraud-c02"
    )


def test_registry_for_the_aml_company_is_variant_independent() -> None:
    from scripts._gen import registry_writer as rw

    ents = [
        json.dumps(rw.entities_for_spec(BundleSpec(scenario="aml", variant=v)), sort_keys=True)
        for v in VARIANTS
    ]  # type: ignore[arg-type]
    assert ents[0] == ents[1] == ents[2]
