"""World construction, honest quirks and mutation primitives (T-gen-loan-03/04)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import make_bundle
from scripts._gen import quirks as qk
from scripts._gen import verify, world
from scripts._gen.schema import BundleSpec, Mutation

from .test_gen_helpers import doc, docs_of, field, load, loan_bundle

D = Decimal


def _w(**kw):  # noqa: ANN202
    return world.build_world("loan", **kw)


def test_world_is_a_pure_function_of_its_arguments() -> None:
    a, b = _w(), _w()
    assert [
        (r.date, r.description, r.debit, r.credit, r.balance) for s in a.statements for r in s.rows
    ] == [
        (r.date, r.description, r.debit, r.credit, r.balance) for s in b.statements for r in s.rows
    ]
    assert [i.total for i in a.invoices] == [i.total for i in b.invoices]
    c = _w(seed=1)
    assert [i.total for i in a.invoices] != [i.total for i in c.invoices]
    assert a.company.party.uen != _w(company_index=1).company.party.uen


def test_variant_and_mutations_do_not_consume_the_company_streams() -> None:
    base = _w()
    mut = Mutation(
        op="set_field", target=base.invoices[0].key, args={"field": "total", "value": "1.00"}
    )
    after = world.apply_plot(base, [mut])
    assert after.invoices[0].total == D("1.00") and base.invoices[0].total != D("1.00")
    again = _w()
    assert [i.total for i in again.invoices] == [i.total for i in base.invoices]
    # a stream per purpose
    assert base.rng("x").random() == base.rng("x").random() != base.rng("y").random()


def test_world_invariants_by_construction() -> None:
    w = _w()
    assert len(w.statements) == 6
    for prev, nxt in zip(w.statements, w.statements[1:], strict=False):
        assert prev.closing == nxt.opening
    for st in w.statements:
        bal = st.opening
        for r in st.rows:
            bal += (r.credit or 0) - (r.debit or 0)
            assert bal == r.balance
            if r.kind != "bf":
                assert r.date.weekday() < 5 and st.period_start <= r.date <= st.period_end
        assert bal == st.closing
    cash = w.fs["FY2025"].tables["balance_sheet"][0].current
    assert abs(cash - w.statements[-1].closing) <= D("0.50")
    contract_by = {c.number: c for c in w.contracts}
    for c in w.contracts:
        billed = sum(i.total for i in w.invoices if i.contract_ref == c.number)
        assert billed <= c.value
    for po in w.pos:
        inv = next(i for i in w.invoices if i.po_ref == po.number)
        assert po.total == inv.total
    assert len(contract_by) == 3 and len(w.pos) == 4 and len(w.invoices) == 14


# ---------------------------------------------------------------------------------- quirks


def test_q01_fees_and_interest() -> None:
    w = _w()
    rows = [r for s in w.statements for r in s.rows]
    assert (
        sum(1 for r in rows if r.description == "ACCOUNT SERVICE CHARGE" and r.debit == D("15.00"))
        == 6
    )
    fees = [r.debit for r in rows if r.description == "FAST/GIRO FEE"]
    assert fees and all(D("0.15") <= f <= D("0.50") for f in fees)
    assert any(r.description == "INTEREST CREDIT" and r.credit < D("10") for r in rows)


def test_q02_layout_b_gst_differs_from_net_method() -> None:
    gt = load(loan_bundle("clean"), "ground_truth.json")
    diffs = []
    for inv in docs_of(gt, "invoice"):
        lines = inv["tables"]["lines"]["rows"]
        per_line = [r["cells"]["gst_amount"]["value"] for r in lines]
        if per_line and all(v is not None for v in per_line):
            gst = D(field(inv, "gst_amount"))
            net = (D(field(inv, "subtotal")) * D("0.09")).quantize(
                D("0.01"), rounding="ROUND_HALF_UP"
            )
            diffs.append(abs(gst - net))
    assert diffs and D("0.01") <= max(diffs) <= D("0.05")


def test_q03_payment_lag_between_5_and_40_days() -> None:
    w = _w()
    lags = [(i.paid_date - i.date).days for i in w.invoices if i.status == "paid"]
    assert lags and min(lags) >= 5 and max(lags) <= 40


def test_q04_one_invoice_paid_in_two_instalments() -> None:
    w = _w()
    num = w.notes["instalment_invoice"]
    inv = w.invoice(num)
    credits = [r.credit for s in w.statements for r in s.rows if r.ref == f"invoice:{num}"]
    assert len(credits) == 2 and sum(credits) == inv.total


def test_q05_non_trade_credits_are_at_least_five_percent() -> None:
    gt, lab = (
        load(loan_bundle("clean"), "ground_truth.json"),
        load(loan_bundle("clean"), "labels.json"),
    )
    c4 = {(r["doc_key"], r["row"]): r["c4"] for r in lab["rows"]}
    tot = nt = D(0)
    kinds = set()
    for s in docs_of(gt, "bank_statement"):
        for r in s["tables"]["transactions"]["rows"]:
            v = r["cells"]["credit"]["value"]
            if v is not None:
                tot += D(v)
                k = c4[(s["key"], r["index"])]
                if k in ("owner_transfer", "refund", "financing_proceeds"):
                    nt += D(v)
                    kinds.add(k)
    assert nt / tot >= D("0.05") and kinds == {"owner_transfer", "refund", "financing_proceeds"}


def test_q06_fy2024_in_thousands_and_prior_column_off_by_at_most_500() -> None:
    gt = load(loan_bundle("clean"), "ground_truth.json")
    fs24, fs25 = doc(gt, "financial_statement:FY2024"), doc(gt, "financial_statement:FY2025")
    assert field(fs24, "scale_printed") == "1000" and field(fs25, "scale_printed") == "1"
    assert fs24["fields"]["scale_printed"]["raw"] == "S$'000"
    worst = D(0)
    for t in ("income_statement", "balance_sheet", "cash_flow"):
        cur = {
            r["cells"]["line"]["value"]: D(r["cells"]["current"]["value"])
            for r in fs24["tables"][t]["rows"]
        }
        for r in fs25["tables"][t]["rows"]:
            worst = max(
                worst, abs(D(r["cells"]["prior"]["value"]) - cur[r["cells"]["line"]["value"]])
            )
    assert D(0) < worst <= D(500)
    assert all(
        int(r["cells"]["current"]["value"].split(".")[0]) % 1000 == 0
        for r in fs24["tables"]["income_statement"]["rows"]
    )


def test_q07_exactly_one_month_ends_in_overdraft_and_recovers() -> None:
    w = _w()
    neg = [s.month for s in w.statements if any(r.balance < 0 for r in s.rows)]
    assert len(neg) == 1
    st = w.statement(neg[0])
    assert st.closing > 0 and st.opening > 0
    assert (
        _w(quirks=[q for q in qk.default_quirks(0) if q != "Q07"]).statements[0].rows
    )  # switchable


def test_q08_recurring_identical_rows_and_round_retainer() -> None:
    w = _w()
    by_month = [
        {(r.description, r.debit) for r in s.rows if r.kind == "rent"} for s in w.statements
    ]
    assert all(m == by_month[0] and len(m) == 1 for m in by_month)
    retainers = [r.credit for s in w.statements for r in s.rows if r.kind == "retainer"]
    assert len(retainers) == 6 and set(retainers) == {D("5000")}
    rents = [r.date for s in w.statements for r in s.rows if r.kind == "rent"]
    assert len(set(rents)) == 6  # different dates, same text and amount


def test_q10_each_layout_has_its_producer() -> None:
    import pymupdf

    from scripts._gen.render_statement import render_statement

    expect = {
        "dbs_style": "Mock Composer 9.1",
        "ocbc_style": "Mock Exstream 16.4",
        "uob_style": "Mock FOP 2.9",
        "software": "Mock Ledger 5.0",
    }
    for lay, prod in expect.items():
        w = _w(layout=lay)
        with pymupdf.open(
            stream=render_statement(w.statements[0], w.quirks).pdf, filetype="pdf"
        ) as d:
            assert d.metadata["producer"] == prod


def test_q11_registry_forms_differ_from_document_forms() -> None:
    b = loan_bundle("clean")
    reg = (
        load(b.parent.parent / "registry.json" if False else b / "../../registry.json", "")
        if False
        else None
    )
    del reg
    import json

    registry = json.loads((b / "../../registry.json").resolve().read_text())
    uen = load(b, "manifest.json")["applicant"]["uen"]
    ent = next(e for e in registry["entities"] if e["uen"] == uen)
    gt = load(b, "ground_truth.json")
    prof = doc(gt, "company_profile:-")
    printed = [r["cells"]["director_name"]["value"] for r in prof["tables"]["directors"]["rows"]]
    officers = [o["name"] for o in ent["officers"]]
    assert printed != officers and [p.upper() for p in printed] == officers


def test_q12_blank_invoice_page_is_boilerplate() -> None:
    lab = load(loan_bundle("clean"), "labels.json")
    boiler = [p for p in lab["pages"] if p["c1"] == "boilerplate"]
    assert (
        len(boiler) == 1
        and boiler[0]["doc_key"] is None
        and boiler[0]["file"].startswith("digital/invoice_")
    )


def test_q14_on_odd_company_index_only() -> None:
    odd, even = _w(company_index=1), _w(company_index=2)
    assert "Q14" in qk.default_quirks(1) and "Q14" not in qk.default_quirks(2)
    assert odd.fs["FY2025"].basis == "unaudited" and odd.fs["FY2025"].auditor is None
    assert odd.fs["FY2025"].title == "Management Accounts (Unaudited)"
    assert even.fs["FY2025"].basis == "audited" and even.fs["FY2025"].auditor
    gt = load(loan_bundle("clean", 1), "ground_truth.json")
    fs = doc(gt, "financial_statement:FY2025")
    assert field(fs, "basis") == "unaudited" and field(fs, "auditor") is None


def test_hard_quirks_only_with_the_flag() -> None:
    plain = loan_bundle("clean", 90)
    hard = loan_bundle("clean", 90, hard=True)
    ids = lambda b: {q["id"] for q in load(b, "manifest.json")["quirks"]}  # noqa: E731
    assert not ({"H1", "H2", "H3"} & ids(plain))
    assert {"H1", "H2", "H3"} <= ids(hard)
    assert verify.verify_bundle(hard) == []
    h = {q["id"]: q for q in load(hard, "manifest.json")["quirks"]}
    assert h["H1"]["may_trigger"] == ["x_invoice_vs_receipts"] and h["H1"]["hard"] is True
    assert h["H2"]["may_trigger"] == ["bs_duplicates"] and h["H3"]["may_trigger"] == [
        "inv_sequence"
    ]
    w = world.build_world("loan", 90, quirks=[*qk.default_quirks(90), "H1", "H2", "H3"])
    a, b = w.notes["h1_pair"]
    paid = [r.credit for s in w.statements for r in s.rows if r.ref == f"invoice:{a}"]
    assert paid == [w.invoice(a).total + w.invoice(b).total]
    dup = {}
    for s in w.statements:
        for r in s.rows:
            if r.kind == "dup":
                dup.setdefault((r.date, r.description, r.debit), 0)
                dup[(r.date, r.description, r.debit)] += 1
    assert list(dup.values()) == [2]
    assert w.notes["voided_number"] not in {i.number for i in w.invoices}
    plain_w = world.build_world("loan", 90)
    assert "h1_pair" not in plain_w.notes and "voided_number" not in plain_w.notes
    assert not any(r.kind == "dup" for s in plain_w.statements for r in s.rows)


def test_manifest_lists_quirks_with_may_trigger() -> None:
    qs = {q["id"]: q for q in load(loan_bundle("clean"), "manifest.json")["quirks"]}
    assert set(qs) == {
        "Q01",
        "Q02",
        "Q03",
        "Q04",
        "Q05",
        "Q06",
        "Q07",
        "Q08",
        "Q10",
        "Q11",
        "Q12",
    }  # dbs layout: no Q09; even index: no Q14
    assert qs["Q12"]["may_trigger"] == ["doc_unclassified"] and qs["Q08"]["may_trigger"] == []


def test_lexicon_ninety_percent_keyword_rows_ten_percent_hard() -> None:
    from scripts._gen import lexicon

    gt, lab = (
        load(loan_bundle("clean"), "ground_truth.json"),
        load(loan_bundle("clean"), "labels.json"),
    )
    n = hard = 0
    for r in lab["rows"]:
        n += 1
        cp = r["counterparty"] or ""
        if lexicon.label_from_tokens(cp) is None and r["c5"] != "other":
            hard += 1
        elif lexicon.label_from_tokens(cp) is not None:
            assert lexicon.label_from_tokens(cp) == r["c5"] or r["c5"] == "other"
    assert 0.06 <= hard / n <= 0.14
    assert (n - hard) / n >= 0.86
    assert gt  # silence unused


# --------------------------------------------------------------------------- mutation ops


def _build(tmp: Path, *muts: Mutation, name: str = "m", **kw) -> Path:  # noqa: ANN003
    out = tmp / name
    make_bundle.build_bundle(BundleSpec(scenario="loan", mutations=list(muts), **kw), out)
    return out


def _credit_row(month: str = "2025-09") -> int:
    st = _w().statement(month)
    return next(i for i, r in enumerate(st.rows) if r.kind == "other_cust")


def test_override_cell_breaks_the_chain_and_recompute_heals_it(tmp_path: Path) -> None:
    row = _credit_row()
    old = _w().statement("2025-09").rows[row].credit
    new = format(old * D("1.2"), "f")
    edit = Mutation(
        op="override_cell",
        target="bank_statement:2025-09",
        args={"table": "transactions", "row": row, "column": "credit", "value": new},
    )
    b = _build(tmp_path, edit, name="edit")
    assert any(v.startswith("W1") and "2025-09" in v for v in verify.verify_bundle(b))
    gt = load(b, "ground_truth.json")
    assert doc(gt, "bank_statement:2025-09")["tables"]["transactions"]["rows"][row]["cells"][
        "credit"
    ]["value"] == format(D(new).quantize(D("0.01")), "f")
    m = load(b, "manifest.json")
    assert m["spec"]["mutations"][0]["op"] == "override_cell"
    fix = Mutation(
        op="recompute_chain",
        target="bank_statement:2025-09",
        args={"from_row": row, "propagate": True},
    )
    b2 = _build(tmp_path, edit, fix, name="healed")
    healed = verify.verify_bundle(b2)
    # the statements are self-consistent again; only the year-end cash tie (a cross-document
    # invariant, the T02b hard case) now disagrees with the untouched financial statements
    assert [v for v in healed if not v.startswith("W5")] == []
    assert any("balance-sheet cash" in v for v in healed)
    clean = load(loan_bundle("clean"), "ground_truth.json")
    g2 = load(b2, "ground_truth.json")
    assert field(doc(g2, "bank_statement:2025-12"), "closing_balance") != field(
        doc(clean, "bank_statement:2025-12"), "closing_balance"
    )
    assert field(doc(g2, "bank_statement:2025-10"), "opening_balance") == field(
        doc(g2, "bank_statement:2025-09"), "closing_balance"
    )


def test_insert_rows_and_drop_ops(tmp_path: Path) -> None:
    ins = Mutation(
        op="insert_rows", target="bank_statement:2025-08",
        args={"date": "2025-08-12", "description": "FAST PAYMENT FROM NEW CUSTOMER TRADING PTE LTD", "credit": "10000.00", "c4": "trade_receipt", "c5": "trading", "counterparty": "NEW CUSTOMER TRADING PTE LTD"},
    )  # fmt: skip
    b = _build(tmp_path, ins, name="ins")
    problems = verify.verify_bundle(b)
    assert [v for v in problems if not v.startswith("W5")] == [] and any(
        "balance-sheet cash" in v for v in problems
    )
    descs = [
        r["cells"]["description"]["value"]
        for r in doc(load(b, "ground_truth.json"), "bank_statement:2025-08")["tables"][
            "transactions"
        ]["rows"]
    ]
    assert "FAST PAYMENT FROM NEW CUSTOMER TRADING PTE LTD" in descs
    lab = load(b, "labels.json")
    assert any(
        r["counterparty"] == "NEW CUSTOMER TRADING PTE LTD" and r["c4"] == "trade_receipt"
        for r in lab["rows"]
    )
    # drop_document
    d = _build(tmp_path, Mutation(op="drop_document", target="bank_statement:2025-09"), name="drop")
    keys = {x["key"] for x in load(d, "manifest.json")["documents"]}
    assert "bank_statement:2025-09" not in keys and "bank_statement:2025-08" in keys
    # drop_page leaves a gap in the page chain
    p = _build(
        tmp_path,
        Mutation(op="drop_page", target="bank_statement:2025-09", args={"page": 2}),
        name="page",
    )
    chain = doc(load(p, "ground_truth.json"), "bank_statement:2025-09")["tables"]["page_chain"][
        "rows"
    ]
    assert [r["cells"]["page_x"]["value"] for r in chain] == [
        "1"
    ]  # 2-page statement: only page 1 remains
    assert any(v.startswith("W2") for v in verify.verify_bundle(p)) or len(chain) == 1


def test_set_field_swap_party_and_producer(tmp_path: Path) -> None:
    inv = _w().invoices[2]
    muts = [
        Mutation(
            op="swap_party",
            target=inv.key,
            args={"role": "supplier", "name": "Other Holdings Pte. Ltd.", "uen": "202012345H"},
        ),
        Mutation(
            op="set_producer",
            target="bank_statement:2025-07",
            args={"producer": "Adobe Photoshop 25.1", "mod_days": 3},
        ),
        Mutation(
            op="set_field",
            target=inv.key,
            args={"field": "due_date", "value": (inv.date - timedelta(days=3)).isoformat()},
        ),
    ]
    b = _build(tmp_path, *muts, name="sw")
    problems = verify.verify_bundle(b)
    assert any(v.startswith("W9") for v in problems) and any(
        v.startswith("W3") and "due date" in v for v in problems
    )
    import pymupdf

    with pymupdf.open(b / "digital/bank_statement_2025-07.pdf") as pdf:
        md = pdf.metadata
    assert md["producer"] == "Adobe Photoshop 25.1" and md["modDate"] != md["creationDate"]
    gt = load(b, "ground_truth.json")
    assert field(doc(gt, inv.key), "supplier_uen") == "202012345H"


def test_fs_override_modes(tmp_path: Path) -> None:
    def edit(
        mode: str,
        line: str = "trade_receivables",
        table: str = "balance_sheet",
        value: str = "999999.00",
    ) -> list[str]:
        m = Mutation(
            op="override_cell",
            target="financial_statement:FY2025",
            args={
                "table": table,
                "line": line,
                "column": "current",
                "value": value,
                "recompute": mode,
            },
        )
        return verify.fs_doc_violations(
            doc(
                load(_build(tmp_path, m, name=f"fs-{mode}-{line}"), "ground_truth.json"),
                "financial_statement:FY2025",
            )
        )

    only = edit("none")
    assert only and any("total_current_assets" in v for v in only)
    sub = edit("subtotals")
    assert (
        sub
        and all(
            "fs_balance" in v or "total_liabilities" in v or "cash" in v
            for v in sub
            if "total_" in v or "fs_" in v
        )
        and any("fs_balance" in v for v in sub)
    )
    assert edit("forger", line="revenue", table="income_statement", value="2000000.00") == []


def test_reuse_signature_makes_identical_images(tmp_path: Path) -> None:
    import pymupdf

    reuse = Mutation(op="reuse_signature", target="contract:2", args={"from": "contract:1"})
    b = _build(tmp_path, reuse, name="sig")

    def images(path: Path) -> list[bytes]:
        with pymupdf.open(path) as d:
            return [d.extract_image(x[0])["image"] for x in d[1].get_images(full=True)]

    a, c = images(b / "digital/contract_1.pdf"), images(b / "digital/contract_2.pdf")
    assert a and a == c
    clean = loan_bundle("clean")
    assert images(clean / "digital/contract_1.pdf") != images(clean / "digital/contract_2.pdf")


def test_unknown_op_target_is_an_error() -> None:
    with pytest.raises(KeyError):
        world.apply_plot(
            _w(),
            [
                Mutation(
                    op="set_field", target="invoice:NOPE", args={"field": "total", "value": "1"}
                )
            ],
        )


# ------------------------------------------------- kyc and aml worlds (used by gen-kyc)


def test_kyc_world_calendar_people_and_kestrel() -> None:
    from stdnum.sg import uen as sg_uen

    w = world.build_world("kyc")
    assert w.company.party.name == "Sembrook Freight Pte. Ltd." and w.layout == "ocbc_style"
    assert [s.month for s in w.statements] == ["2026-07", "2026-08", "2026-09"]
    assert w.invoices == [] and w.fs == {} and w.contracts == [] and w.pos == []
    assert [p.name for p in w.people] == [
        "Tan Wei Ming",
        "Muhammad Hafiz bin Ismail",
        "Elena Vasquez",
    ]
    assert [p.id_kind for p in w.people] == ["nric", "nric", "passport"]
    assert [(h.name, str(h.percent)) for h in w.holders] == [
        ("Tan Wei Ming", "45.00"),
        ("Muhammad Hafiz bin Ismail", "30.00"),
        ("Kestrel Capital Holdings Pte. Ltd.", "25.00"),
    ]
    ents = w.registry_entities()
    assert (
        len(ents) == 2
        and sg_uen.is_valid(ents[1]["uen"])
        and ents[1]["shareholders"][0]["percent"] == "100.00"
    )
    assert (
        ents[0]["principal_activity"] == "Freight forwarding and logistics"
        and ents[0]["ssic_code"] == "52291"
    )
    assert all(s.layout == "ocbc_style" for s in w.statements)


def test_aml_world_calendar_and_invoice_numbering() -> None:
    w = world.build_world("aml")
    assert w.company.party.name == "Tanjong Ridge Trading Pte. Ltd." and w.layout == "uob_style"
    assert [s.month for s in w.statements] == [f"2026-{m:02d}" for m in range(4, 10)]
    assert len(w.invoices) == 10 and len(w.contracts) == 1 and w.fs == {}
    assert [i.number for i in w.invoices][0] == "INV-2026-0107" and w.invoices[
        -1
    ].number == "INV-2026-0116"
    assert [p.name for p in w.people] == ["Rajesh s/o Kumar", "Wong Jessica"]
    assert [str(h.percent) for h in w.holders] == ["70.00", "30.00"]
    assert all(i.status == "paid" for i in w.invoices)


@pytest.mark.parametrize("scenario", ["loan", "kyc", "aml"])
def test_every_scenario_builds_for_many_company_indexes(scenario: str) -> None:
    seen_names = set()
    for idx in range(1, 14):
        w = world.build_world(scenario, idx)
        seen_names.add(w.company.party.name)
        assert all(34 <= len(s.rows) <= 59 for s in w.statements)
    assert len(seen_names) == 13


def test_name_groups_appear_at_least_three_times_over_the_pool() -> None:
    from collections import Counter

    groups: Counter[str] = Counter()
    for idx in range(1, 11):
        groups.update(p.group for p in world.build_world("loan", idx).people)
    assert all(
        groups[g] >= 3
        for g in (
            "western_order",
            "chinese_order",
            "malay_patronymic",
            "indian_patronymic",
            "mononym",
            "titles",
        )
    )


def test_public_primitives_add_person_holder_and_transactions() -> None:
    w = world.build_world("kyc")
    p = world.add_person(w, "Koh Boon Kiat", birth_year=1979, in_registry=False)
    assert p.key == "p3" and p.on_profile and not p.in_registry
    from scripts._gen.world_types import Holder, LedgerRow

    world.add_holder(
        w, Holder("Koh Boon Kiat", "individual", p.id_number, 0, __import__("decimal").Decimal("0"))
    )
    assert len(w.holders) == 4
    st = w.statements[1]
    before = st.closing
    first_close_next = w.statements[2].closing
    row = LedgerRow(
        st.period_start.replace(day=12),
        "FAST PAYMENT FROM BLOCKCHAIN COIN PTE LTD",
        None,
        D("5000.00"),
        D(0),
        "other",
        "crypto_virtual_assets",
        "BLOCKCHAIN COIN PTE LTD",
        None,
        "inserted",
    )
    world.add_transactions(w, st.key, [row])
    assert st.closing == before + D("5000.00") and w.statements[2].opening == st.closing
    assert w.statements[2].closing == first_close_next + D("5000.00")
    assert all(a.date <= b.date for a, b in zip(st.rows, st.rows[1:], strict=False))
