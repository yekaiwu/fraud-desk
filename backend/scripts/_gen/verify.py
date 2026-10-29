"""``verify_bundle``: world invariants W1-W14 (docs 4.1) read from a bundle folder.

Uses ``ground_truth.json`` (what is printed), ``labels.json`` and ``manifest.json``; never imports
the application. Amounts are ``Decimal``; ``[]`` means every applicable invariant holds. Planted
cross-document disagreements (``manifest.planted[*].target.doc_key``) are exempt from the
cross-document invariants they are designed to break (W4, W5, W6, W8, W9); the helper functions
below are public so tests can still assert that e.g. a forger's financial statement balances.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from stdnum.sg import uen as _uen

from . import ids as idm
from . import lexicon as lx
from .world_fs import fs_identity_violations

D = Decimal
TITLE_WORDS = {"DR", "MDM", "PROF", "DATUK", "SERI", "DATIN", "MR", "MS", "MRS", "DATO"}
PATRONYMIC = {"BIN", "BINTE", "BINTI", "BTE", "B", "B.", "S/O", "D/O", "SO", "DO"}
STANDARD_OTHER = {
    "PAYROLL",
    "CPF",
    "IRAS",
    "LANDLORD",
    "TERM LOAN",
    "CITYGRID UTILITIES",
    "LINKNET TELECOM",
    "COVERLINE INSURANCE",
}
LEGAL_TOKENS = {
    "PTE",
    "LTD",
    "PRIVATE",
    "LIMITED",
    "LLP",
    "CO",
    "COMPANY",
    "INC",
    "CORP",
    "SDN",
    "BHD",
}
TRADE_C4 = {"trade_receipt", "customer_payment"}


def _d(cell: dict[str, Any] | None) -> Decimal | None:
    if not cell or cell.get("value") is None:
        return None
    return D(cell["value"])


def _v(doc: dict[str, Any], name: str) -> str | None:
    f = doc["fields"].get(name)
    return None if f is None else f.get("value")


def _rows(doc: dict[str, Any], table: str) -> list[dict[str, Any]]:
    return doc["tables"].get(table, {}).get("rows", [])


def company_key(name: str | None) -> str:
    toks = re.sub(r"[.,'\"()\-/&]", " ", (name or "").upper().replace("&", " AND ")).split()
    while toks and toks[-1] in LEGAL_TOKENS:
        toks.pop()
    return " ".join(toks)


def person_key(name: str | None) -> str:
    toks = re.sub(r"[,.]", " ", (name or "").upper()).split()
    toks = [t for t in toks if t not in TITLE_WORDS and t not in PATRONYMIC]
    return " ".join(sorted(toks))


def _load(bundle_dir: Path) -> tuple[dict, dict, dict]:
    man = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    gt = json.loads((bundle_dir / "ground_truth.json").read_text(encoding="utf-8"))
    lab = json.loads((bundle_dir / "labels.json").read_text(encoding="utf-8"))
    return man, gt, lab


def verify_bundle(bundle_dir: Path) -> list[str]:
    bundle_dir = Path(bundle_dir)
    man, gt, lab = _load(bundle_dir)
    docs: list[dict[str, Any]] = gt["documents"]
    out: list[str] = []
    planted_docs = {p["target"]["doc_key"] for p in man.get("planted", []) if p.get("target")}
    today = date.fromisoformat(man["spec"]["today"])
    quirks = set(man["spec"].get("quirks", []))
    clean = man["spec"].get("variant", "clean") == "clean"
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in docs:
        by_type[d["doc_type"]].append(d)
    out += w1_statements(by_type["bank_statement"], "Q07" in quirks)
    out += w2_dates(by_type["bank_statement"])
    out += w3_invoices(by_type["invoice"], "H3" in quirks)
    out += w4_payments(by_type["invoice"], by_type["bank_statement"], planted_docs)
    out += w5_financials(
        by_type["financial_statement"], by_type["bank_statement"], lab, planted_docs
    )
    out += w6_contracts(
        by_type["contract"], by_type["purchase_order"], by_type["invoice"], planted_docs
    )
    out += w7_ids(by_type["national_id"], by_type["passport"], today)
    out += w8_ownership(by_type, bundle_dir, man, planted_docs)
    out += w9_entities(docs, planted_docs)
    out += w10_labels(lab, by_type["bank_statement"])
    out += w11_fingerprints(by_type["invoice"])
    out += w12_files(bundle_dir, man)
    out += w13_clean(man)
    if clean:
        out += w14_thresholds(by_type, lab)
    return out


# ------------------------------------------------------------------------------------- W1


def w1_statements(stmts: list[dict[str, Any]], allow_overdraft: bool) -> list[str]:
    out: list[str] = []
    ordered = sorted(stmts, key=lambda d: _v(d, "period_start") or "")
    prev_close: tuple[str, Decimal] | None = None
    neg_months = 0
    for d in ordered:
        k = d["key"]
        opening, closing = _d(d["fields"]["opening_balance"]), _d(d["fields"]["closing_balance"])
        if opening is None or closing is None:
            out.append(f"W1: {k}: opening or closing balance missing")
            continue
        if prev_close and prev_close[1] != opening:
            out.append(f"W1: {k}: opening {opening} != closing {prev_close[1]} of {prev_close[0]}")
        bal = opening
        deb = cred = D(0)
        negative = False
        for r in _rows(d, "transactions"):
            c = r["cells"]
            debit, credit, balance = _d(c["debit"]), _d(c["credit"]), _d(c["balance"])
            if debit is not None and credit is not None:
                out.append(f"W1: {k} row {r['index']}: both debit and credit")
            bal = bal + (credit or D(0)) - (debit or D(0))
            deb += debit or D(0)
            cred += credit or D(0)
            if balance != bal:
                out.append(f"W1: {k} row {r['index']}: balance {balance} != expected {bal}")
                bal = balance if balance is not None else bal
            if balance is not None and balance < 0:
                negative = True
        if bal != closing:
            out.append(f"W1: {k}: closing {closing} != last balance {bal}")
        for name, total in (("total_debits", deb), ("total_credits", cred)):
            printed = _d(d["fields"].get(name))
            if printed is not None and printed != total:
                out.append(f"W1: {k}: printed {name} {printed} != sum {total}")
        if negative:
            neg_months += 1
        prev_close = (k, closing)
    if neg_months and not allow_overdraft:
        out.append(f"W1: {neg_months} month(s) with negative balances but quirk Q07 is not set")
    if neg_months > 1:
        out.append("W1: negative balances in more than one month")
    return out


# ------------------------------------------------------------------------------------- W2


def w2_dates(stmts: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for d in stmts:
        k = d["key"]
        ps, pe = date.fromisoformat(_v(d, "period_start")), date.fromisoformat(_v(d, "period_end"))  # type: ignore[arg-type]
        last: date | None = None
        for r in _rows(d, "transactions"):
            raw = r["cells"]["date"]["value"]
            try:
                dt = date.fromisoformat(raw)
            except (TypeError, ValueError):
                out.append(f"W2: {k} row {r['index']}: not a calendar date {raw!r}")
                continue
            bf = (
                (r["cells"]["description"]["value"] or "")
                .upper()
                .startswith("BALANCE BROUGHT FORWARD")
            )
            if dt.weekday() >= 5 and not bf:
                out.append(f"W2: {k} row {r['index']}: {dt} is not a weekday")
            if not ps <= dt <= pe:
                out.append(f"W2: {k} row {r['index']}: {dt} outside period {ps}..{pe}")
            if last and dt < last:
                out.append(f"W2: {k} row {r['index']}: {dt} precedes {last}")
            last = dt
        chain = _rows(d, "page_chain")
        acct = _v(d, "account_number")
        ys = {r["cells"]["page_y"]["value"] for r in chain}
        xs = [r["cells"]["page_x"]["value"] for r in chain]
        if (
            len(ys) != 1
            or xs != [str(i) for i in range(1, len(chain) + 1)]
            or (ys and int(next(iter(ys))) != len(chain))
        ):
            out.append(f"W2: {k}: page chain incomplete {xs} of {sorted(ys)}")
        for r in chain:
            if r["cells"]["account_number"]["value"] != acct:
                out.append(f"W2: {k}: account number differs on page {r['page']}")
    return out


# ------------------------------------------------------------------------------------- W3


def q2(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"))  # ROUND_HALF_EVEN default would differ: use explicit below


def _r2(x: Decimal) -> Decimal:
    from decimal import ROUND_HALF_UP

    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def w3_invoices(invs: list[dict[str, Any]], allow_gap: bool) -> list[str]:
    out: list[str] = []
    by_supplier: dict[str, list[int]] = defaultdict(list)
    for d in invs:
        k = d["key"]
        rate = _d(d["fields"]["gst_rate"]) or D("9")
        lines = _rows(d, "lines")
        total_lines = D(0)
        gst_sum = D(0)
        per_line = False
        for r in lines:
            c = r["cells"]
            qty, price, amt = _d(c["quantity"]), _d(c["unit_price"]), _d(c["amount"])
            if qty is None or price is None or amt is None or _r2(qty * price) != amt:
                out.append(f"W3: {k} line {r['index']}: quantity x unit price != amount")
            total_lines += amt or D(0)
            if c["gst_amount"]["value"] is not None:
                per_line = True
                g = _d(c["gst_amount"])
                if g != _r2((amt or D(0)) * rate / 100):
                    out.append(f"W3: {k} line {r['index']}: line GST {g} != 9% of the line")
                gst_sum += g or D(0)
        sub, gst, tot = (
            _d(d["fields"]["subtotal"]),
            _d(d["fields"]["gst_amount"]),
            _d(d["fields"]["total"]),
        )
        if sub != total_lines:
            out.append(f"W3: {k}: subtotal {sub} != sum of lines {total_lines}")
        expect_gst = gst_sum if per_line else _r2((sub or D(0)) * rate / 100)
        if gst != expect_gst:
            out.append(f"W3: {k}: GST {gst} != expected {expect_gst}")
        if tot != (sub or D(0)) + (gst or D(0)):
            out.append(f"W3: {k}: total {tot} != subtotal + GST")
        inv_d, due = (
            date.fromisoformat(_v(d, "invoice_date")),
            date.fromisoformat(_v(d, "due_date")),
        )  # type: ignore[arg-type]
        if not inv_d <= due <= inv_d + timedelta(days=60):
            out.append(f"W3: {k}: due date {due} outside invoice date {inv_d} .. +60 days")
        su = _v(d, "supplier_uen") or ""
        if not _uen.is_valid(su):
            out.append(f"W3: {k}: supplier UEN {su} invalid")
        if _v(d, "buyer_uen") and not _uen.is_valid(_v(d, "buyer_uen")):  # type: ignore[arg-type]
            out.append(f"W3: {k}: buyer UEN invalid")
        gstno = _v(d, "supplier_gst_no")
        if gstno and not idm.is_gst_format(gstno):
            out.append(f"W3: {k}: GST number format {gstno}")
        m = re.match(r"^(.*?)(\d+)$", _v(d, "invoice_number") or "")
        if m:
            by_supplier[su + "|" + m.group(1)].append(int(m.group(2)))
    for sup, nums in by_supplier.items():
        nums.sort()
        gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
        if len(set(nums)) != len(nums):
            out.append(f"W3: {sup}: duplicate invoice numbers")
        if gaps and not (allow_gap and len(gaps) == 1):
            out.append(f"W3: {sup}: invoice numbers not contiguous, missing {gaps}")
    return out


# ------------------------------------------------------------------------------------- W4


def _credits(stmts: list[dict[str, Any]]) -> list[tuple[date, Decimal, str, str]]:
    cr = []
    for d in stmts:
        for r in _rows(d, "transactions"):
            c = r["cells"]
            if c["credit"]["value"] is not None:
                cr.append(
                    (
                        date.fromisoformat(c["date"]["value"]),
                        D(c["credit"]["value"]),
                        (c["description"]["value"] or "").upper(),
                        d["key"],
                    )
                )
    return cr


def w4_payments(
    invs: list[dict[str, Any]], stmts: list[dict[str, Any]], exempt: set[str]
) -> list[str]:
    if not stmts:
        return []
    out: list[str] = []
    credits = _credits(stmts)
    end = max(date.fromisoformat(_v(s, "period_end")) for s in stmts)  # type: ignore[arg-type]
    paid = [d for d in invs if _v(d, "payment_status") == "paid" and d["key"] not in exempt]
    with_number = 0
    used: set[int] = set()
    for d in paid:
        k = d["key"]
        total = D(_v(d, "total"))  # type: ignore[arg-type]
        inv_date = date.fromisoformat(_v(d, "invoice_date"))  # type: ignore[arg-type]
        paid_date = date.fromisoformat(_v(d, "paid_date") or "1900-01-01")
        buyer = company_key(_v(d, "buyer_name")).replace(" ", "")
        number = _v(d, "invoice_number")
        cand = [
            i
            for i, c in enumerate(credits)
            if buyer in re.sub(r"[^A-Z0-9]", "", c[2]) and inv_date <= c[0] <= end
        ]
        found = None
        for i in cand:  # one credit (also covers H1 when the credit equals this + a sibling)
            if credits[i][1] == total:
                found = [i]
                break
        if not found:
            for a in cand:
                for b in cand:
                    if a < b and credits[a][1] + credits[b][1] == total:
                        found = [a, b]
                        break
                if found:
                    break
        if not found:
            # H1: one credit paying this invoice and another from the same buyer
            sibling_totals = [
                D(_v(o, "total"))
                for o in paid
                if o is not d
                and company_key(_v(o, "buyer_name")) == company_key(_v(d, "buyer_name"))
            ]  # type: ignore[arg-type]
            for i in cand:
                if any(credits[i][1] == total + s for s in sibling_totals):
                    found = [i]
                    break
        if not found:
            out.append(f"W4: {k}: marked paid but no credit(s) sum to {total}")
            continue
        last = max(credits[i][0] for i in found)
        first = min(credits[i][0] for i in found)
        if last != paid_date:
            out.append(f"W4: {k}: paid_date {paid_date} != date of last credit {last}")
        if not (inv_date + timedelta(days=5) <= first and last <= inv_date + timedelta(days=40)):
            out.append(
                f"W4: {k}: credit dated {first}..{last} not 5-40 days after invoice date {inv_date}"
            )
        if last > end:
            out.append(f"W4: {k}: credit after statement end")
        if any(number and number in credits[i][2] for i in found):
            with_number += 1
        used.update(found)
    if len(paid) >= 6 and with_number < 0.3 * len(paid):
        out.append(
            f"W4: only {with_number}/{len(paid)} paid invoices name the invoice number in the credit description"
        )
    return out


# ------------------------------------------------------------------------------------- W5


def fs_tables(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Ground-truth FS tables as ``{table: [{line, section, current, prior, is_subtotal}]}``."""
    out = {}
    for t in ("income_statement", "balance_sheet", "cash_flow"):
        out[t] = [
            {
                "line": r["cells"]["line"]["value"],
                "section": r["cells"]["section"]["value"],
                "current": _d(r["cells"]["current"]),
                "prior": _d(r["cells"]["prior"]),
                "is_subtotal": r["cells"]["is_subtotal"]["value"] == "true",
            }
            for r in _rows(doc, t)
        ]
    return out


def fs_doc_violations(doc: dict[str, Any]) -> list[str]:
    """Identities of one financial statement document (current and, if printed, prior)."""
    scale = int(_v(doc, "scale_printed") or "1")
    tabs = fs_tables(doc)
    out = [f"W5: {doc['key']}: {m}" for m in fs_identity_violations(tabs, scale, "current")]
    if any(r["prior"] is not None for t in tabs.values() for r in t):
        out += [
            f"W5: {doc['key']} prior: {m}" for m in fs_identity_violations(tabs, scale, "prior")
        ]
    return out


def _line(doc: dict[str, Any], table: str, line: str, col: str) -> Decimal | None:
    for r in _rows(doc, table):
        if r["cells"]["line"]["value"] == line:
            return _d(r["cells"][col])
    return None


def w5_financials(
    fss: list[dict[str, Any]], stmts: list[dict[str, Any]], lab: dict[str, Any], exempt: set[str]
) -> list[str]:
    out: list[str] = []
    for d in fss:
        if d["key"] in exempt:
            continue
        out += fs_doc_violations(d)
        cash_cf, cash_bs = (
            _line(d, "cash_flow", "cash_closing", "current"),
            _line(d, "balance_sheet", "cash_and_equivalents", "current"),
        )
        if stmts and cash_cf is not None and cash_bs is not None:
            pe = date.fromisoformat(_v(d, "period_end"))  # type: ignore[arg-type]
            cover = [
                s
                for s in stmts
                if date.fromisoformat(_v(s, "period_start"))
                <= pe
                <= date.fromisoformat(_v(s, "period_end"))
            ]  # type: ignore[arg-type]
            if cover:
                closing = _d(cover[0]["fields"]["closing_balance"])
                if closing is not None and abs(closing - cash_bs) > D("1.00"):
                    out.append(
                        f"W5: {d['key']}: balance-sheet cash {cash_bs} != bank closing {closing} at {pe}"
                    )
            if abs(cash_cf - cash_bs) > D("1.00"):
                out.append(
                    f"W5: {d['key']}: cash-flow closing cash {cash_cf} != balance-sheet cash {cash_bs}"
                )
    ordered = sorted(fss, key=lambda d: _v(d, "period_end") or "")
    for a, b in zip(ordered, ordered[1:], strict=False):
        if b["key"] in exempt or a["key"] in exempt:
            continue
        tol = D(500) if (int(_v(a, "scale_printed") or "1") > 1) else D("1.00")
        for t in ("income_statement", "balance_sheet", "cash_flow"):
            cur = {
                r["cells"]["line"]["value"]: _d(r["cells"]["current"])
                for r in _rows(a, t)
                if r["cells"]["line"]["value"] != "other"
            }
            for r in _rows(b, t):
                ln = r["cells"]["line"]["value"]
                p = _d(r["cells"]["prior"])
                if p is not None and ln in cur and cur[ln] is not None and abs(p - cur[ln]) > tol:
                    out.append(
                        f"W5: {b['key']} prior {ln} {p} differs from {a['key']} current {cur[ln]} by more than {tol}"
                    )
    # annualised trade credits vs FY revenue
    if stmts and ordered:
        last = ordered[-1]
        if last["key"] not in exempt:
            rev = _line(last, "income_statement", "revenue", "current")
            c4_by = {(r["doc_key"], r["row"]): r["c4"] for r in lab["rows"]}
            tc = D(0)
            for s in stmts:
                for r in _rows(s, "transactions"):
                    if (
                        c4_by.get((s["key"], r["index"])) in TRADE_C4
                        and r["cells"]["credit"]["value"] is not None
                    ):
                        tc += D(r["cells"]["credit"]["value"])
            fs0, fs1 = (
                date.fromisoformat(_v(last, "period_start")),
                date.fromisoformat(_v(last, "period_end")),
            )  # type: ignore[arg-type]
            st0 = min(date.fromisoformat(_v(s, "period_start")) for s in stmts)  # type: ignore[arg-type]
            st1 = max(date.fromisoformat(_v(s, "period_end")) for s in stmts)  # type: ignore[arg-type]
            lo, hi = max(fs0, st0), min(fs1, st1)
            days = (hi - lo).days + 1
            if rev and days > 30:
                ratio = tc * D((fs1 - fs0).days + 1) / D(days) / rev
                if not D("0.92") <= ratio <= D("1.08"):
                    out.append(
                        f"W5: annualised trade credits / revenue = {ratio:.3f} outside [0.92, 1.08]"
                    )
    return out


# ------------------------------------------------------------------------------------- W6


def w6_contracts(
    contracts: list[dict[str, Any]],
    pos: list[dict[str, Any]],
    invs: list[dict[str, Any]],
    exempt: set[str],
) -> list[str]:
    out: list[str] = []
    by_contract: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_po: dict[str, dict[str, Any]] = {}
    for inv in invs:
        if _v(inv, "contract_reference"):
            by_contract[_v(inv, "contract_reference")].append(inv)  # type: ignore[index]
        if _v(inv, "po_reference"):
            by_po[_v(inv, "po_reference")] = inv  # type: ignore[index]
    for c in contracts:
        num = _v(c, "contract_number") or ""
        value = D(_v(c, "contract_value"))  # type: ignore[arg-type]
        mine = by_contract.get(num, [])
        billed = sum((D(_v(i, "total")) for i in mine), D(0))  # type: ignore[arg-type]
        if billed > value and not any(i["key"] in exempt for i in mine):
            out.append(f"W6: {c['key']}: billed {billed} exceeds contract value {value}")
        for i in mine:
            if i["key"] in exempt:
                continue
            if _v(i, "supplier_uen") != _v(c, "seller_uen") or _v(i, "buyer_uen") != _v(
                c, "buyer_uen"
            ):
                out.append(f"W6: {i['key']}: UENs differ from {c['key']}")
    for p in pos:
        inv = by_po.get(_v(p, "po_number") or "")
        if inv is None or inv["key"] in exempt:
            continue
        if _v(p, "total") != _v(inv, "total"):
            out.append(
                f"W6: {p['key']}: PO total {_v(p, 'total')} != invoice {inv['key']} total {_v(inv, 'total')}"
            )
        if _v(p, "buyer_uen") != _v(inv, "buyer_uen") or _v(p, "supplier_uen") != _v(
            inv, "supplier_uen"
        ):
            out.append(f"W6: {p['key']}: parties differ from invoice {inv['key']}")
    return out


# ------------------------------------------------------------------------------------- W7


def w7_ids(nids: list[dict[str, Any]], passports: list[dict[str, Any]], today: date) -> list[str]:
    out: list[str] = []
    for d in nids:
        num = _v(d, "id_number") or ""
        if not idm.is_valid_nric(num):
            out.append(f"W7: {d['key']}: NRIC {num} fails the check letter")
            continue
        dob = date.fromisoformat(_v(d, "date_of_birth"))  # type: ignore[arg-type]
        if num[0] in "ST" and ((num[0] == "S") != (dob.year < 2000)):
            out.append(f"W7: {d['key']}: prefix {num[0]} inconsistent with birth year {dob.year}")
        age = (today - dob).days / 365.25
        if dob >= today or not 21 <= age <= 75:
            out.append(f"W7: {d['key']}: date of birth {dob} gives age {age:.1f}")
    for d in passports:
        l2 = _v(d, "mrz_line2") or ""
        if not idm.mrz_line2_valid(l2):
            out.append(f"W7: {d['key']}: MRZ line 2 check digits invalid")
        exp = date.fromisoformat(_v(d, "date_of_expiry"))  # type: ignore[arg-type]
        if exp <= today:
            out.append(f"W7: {d['key']}: passport expired {exp}")
        dob = date.fromisoformat(_v(d, "date_of_birth"))  # type: ignore[arg-type]
        if not 21 <= (today - dob).days / 365.25 <= 75:
            out.append(f"W7: {d['key']}: date of birth {dob} outside age 21-75")
    return out


# ------------------------------------------------------------------------------------- W8


def _holders(doc: dict[str, Any], table: str, name_col: str) -> dict[str, Decimal]:
    return {
        person_key(r["cells"][name_col]["value"]) or company_key(r["cells"][name_col]["value"]): D(
            r["cells"]["percent"]["value"]
        )
        for r in _rows(doc, table)
    }


def w8_ownership(
    by_type: dict[str, list[dict[str, Any]]],
    bundle_dir: Path,
    man: dict[str, Any],
    planted: set[str],
) -> list[str]:
    out: list[str] = []
    profile = (by_type.get("company_profile") or [None])[0]
    register = (by_type.get("shareholder_register") or [None])[0]
    chart = (by_type.get("ownership_chart") or [None])[0]
    reg_ent = None
    reg_path = bundle_dir / man.get("registry_path", "")
    uen = None
    if profile is not None:
        uen = _v(profile, "uen")
    elif register is not None:
        uen = _v(register, "uen")
    if uen and reg_path.exists():
        try:
            for e in json.loads(reg_path.read_text(encoding="utf-8"))["entities"]:
                if e["uen"] == uen:
                    reg_ent = e
        except (ValueError, KeyError):
            reg_ent = None
    sums: list[tuple[str, Decimal]] = []
    if profile is not None and profile["key"] not in planted:
        sums.append(
            (
                "profile",
                sum(
                    (D(r["cells"]["percent"]["value"]) for r in _rows(profile, "shareholders")),
                    D(0),
                ),
            )
        )
    if register is not None and register["key"] not in planted:
        sums.append(
            (
                "register",
                sum(
                    (D(r["cells"]["percent"]["value"]) for r in _rows(register, "shareholders")),
                    D(0),
                ),
            )
        )
    if chart is not None and chart["key"] not in planted:
        direct = sum(
            (
                D(r["cells"]["percent"]["value"])
                for r in _rows(chart, "holders")
                if not r["cells"]["parent_name"]["value"]
            ),
            D(0),
        )
        sums.append(("chart", direct))
    if reg_ent is not None:
        sums.append(("registry", sum((D(s["percent"]) for s in reg_ent["shareholders"]), D(0))))
    for name, s in sums:
        if s != D("100.00"):
            out.append(f"W8: {name} shareholdings sum to {s}, not 100.00")
    if (
        profile is not None
        and reg_ent is not None
        and profile["key"] not in planted
        and not any(k.startswith("national_id") for k in planted)
    ):
        prof_dirs = {
            person_key(r["cells"]["director_name"]["value"]) for r in _rows(profile, "directors")
        }
        reg_off = {
            person_key(o["name"]) for o in reg_ent["officers"] if not o.get("cessation_date")
        }
        id_people = {person_key(_v(d, "holder_name")) for d in by_type.get("national_id", [])} | {
            person_key(_v(d, "holder_name")) for d in by_type.get("passport", [])
        }
        if prof_dirs != reg_off:
            out.append(
                f"W8: profile directors {sorted(prof_dirs)} != registry officers {sorted(reg_off)}"
            )
        if id_people and id_people != prof_dirs:
            out.append(
                f"W8: people with ID documents {sorted(id_people)} != directors {sorted(prof_dirs)}"
            )
        prof_h = _holders(profile, "shareholders", "holder_name")
        reg_h = {
            person_key(s["name"]) or company_key(s["name"]): D(s["percent"])
            for s in reg_ent["shareholders"]
        }
        if prof_h != reg_h:
            out.append(f"W8: profile shareholders {prof_h} != registry {reg_h}")
    if register is not None and reg_ent is not None and register["key"] not in planted:
        reg_h = {
            person_key(s["name"]) or company_key(s["name"]): D(s["percent"])
            for s in reg_ent["shareholders"]
        }
        if _holders(register, "shareholders", "holder_name") != reg_h:
            out.append("W8: register holders differ from the registry")
    if chart is not None and reg_ent is not None and chart["key"] not in planted:
        reg_h = {
            person_key(s["name"]) or company_key(s["name"]): D(s["percent"])
            for s in reg_ent["shareholders"]
        }
        ch = {
            person_key(r["cells"]["holder_name"]["value"])
            or company_key(r["cells"]["holder_name"]["value"]): D(r["cells"]["percent"]["value"])
            for r in _rows(chart, "holders")
            if not r["cells"]["parent_name"]["value"]
        }
        if ch != reg_h:
            out.append(f"W8: chart holders {ch} differ from the registry {reg_h}")
    return out


# ------------------------------------------------------------------------------------- W9


def w9_entities(docs: list[dict[str, Any]], planted: set[str]) -> list[str]:
    """The applicant's name and UEN agree on every document that names it as the subject."""
    claims: list[tuple[str, str, str]] = []
    for d in docs:
        if d["key"] in planted:
            continue
        t = d["doc_type"]
        if t == "bank_statement":
            claims.append((d["key"], _v(d, "account_holder") or "", _v(d, "uen") or ""))
        elif t == "invoice":
            claims.append((d["key"], _v(d, "supplier_name") or "", _v(d, "supplier_uen") or ""))
        elif t in (
            "financial_statement",
            "company_profile",
            "certificate_of_incorporation",
            "ownership_chart",
            "shareholder_register",
            "source_of_funds_declaration",
        ):
            claims.append((d["key"], _v(d, "company_name") or "", _v(d, "uen") or ""))
        elif t == "contract":
            claims.append((d["key"], _v(d, "seller_name") or "", _v(d, "seller_uen") or ""))
        elif t == "purchase_order":
            claims.append((d["key"], _v(d, "supplier_name") or "", _v(d, "supplier_uen") or ""))
    claims = [c for c in claims if c[1]]
    out: list[str] = []
    if not claims:
        return out
    names = defaultdict(list)
    uens = defaultdict(list)
    for k, n, u in claims:
        names[company_key(n)].append(k)
        if u:
            uens[u].append(k)
    if len(names) > 1:
        out.append(f"W9: applicant name differs between documents: {sorted(names)}")
    if len(uens) > 1:
        out.append(f"W9: applicant UEN differs between documents: {sorted(uens)}")
    return out


# ------------------------------------------------------------------------------------ W10


def w10_labels(lab: dict[str, Any], stmts: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for p in lab["pages"]:
        if not p.get("c1"):
            out.append(f"W10: page {p['file']}#{p['page']} has no c1")
    c3 = {d["key"]: d.get("c3") for d in lab["documents"]}
    for s in stmts:
        if not c3.get(s["key"]):
            out.append(f"W10: {s['key']} has no c3")
    rows = {(r["doc_key"], r["row"]): r for r in lab["rows"]}
    n = hard = 0
    for s in stmts:
        for r in _rows(s, "transactions"):
            lr = rows.get((s["key"], r["index"]))
            if lr is None:
                out.append(f"W10: {s['key']} row {r['index']} has no label row")
                continue
            if not lr.get("c5"):
                out.append(f"W10: {s['key']} row {r['index']} has no c5")
            if r["cells"]["credit"]["value"] is not None and not lr.get("c4"):
                out.append(f"W10: {s['key']} row {r['index']} is a credit without c4")
            n += 1
            cp = lr.get("counterparty") or ""
            derived = lx.label_from_tokens(cp)
            if derived is not None and derived != lr.get("c5") and lr.get("c4") != "owner_transfer":
                out.append(
                    f"W10: {s['key']} row {r['index']}: c5 {lr.get('c5')} contradicts token label {derived}"
                )
            if derived is None and cp not in STANDARD_OTHER and lr.get("c5") != "other":
                hard += 1
    if n >= 100 and not 0.06 <= hard / n <= 0.14:
        out.append(f"W10: hard rows {hard}/{n} = {hard / n:.2%}, expected about 10%")
    return out


# ------------------------------------------------------------------------------------ W11


def invoice_fp(uen: str, number: str, inv_date: str, total: str) -> str:
    return hashlib.sha256(
        (
            "inv|"
            + "|".join(
                [uen.upper().replace(" ", ""), "".join(number.upper().split()), inv_date, total]
            )
        ).encode()
    ).hexdigest()


def w11_fingerprints(invs: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, str] = {}
    out = []
    for d in invs:
        fp = invoice_fp(
            _v(d, "supplier_uen") or "",
            _v(d, "invoice_number") or "",
            _v(d, "invoice_date") or "",
            _v(d, "total") or "",
        )
        if fp in seen:
            out.append(f"W11: {d['key']} has the same fingerprint as {seen[fp]}")
        seen[fp] = d["key"]
    return out


# ------------------------------------------------------------------------------------ W12


def w12_files(bundle_dir: Path, man: dict[str, Any]) -> list[str]:
    out = []
    for f in man["files"]:
        p = (bundle_dir / f["path"]).resolve()
        if not p.exists():
            out.append(f"W12: {f['path']} is missing")
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if sha != f["sha256"]:
            out.append(f"W12: {f['path']} sha256 differs from the manifest")
        for pe in f["pages"]:
            if (
                pe["page_sha256"]
                != hashlib.sha256(f"{f['sha256']}:{pe['page']}".encode()).hexdigest()
            ):
                out.append(f"W12: {f['path']} page {pe['page']} page_sha256 does not recompute")
    return out


# ------------------------------------------------------------------------------------ W13


def w13_clean(man: dict[str, Any]) -> list[str]:
    if man["spec"].get("variant", "clean") == "clean" and man["kind"] == "bundle":
        out = []
        if man.get("planted"):
            out.append("W13: a clean bundle has planted entries")
        if man.get("tampers"):
            out.append("W13: a clean bundle has tamper records")
        return out
    return []


# ------------------------------------------------------------------------------------ W14

RISKY = ("crypto_virtual_assets", "gambling", "money_services")


def w14_thresholds(by_type: dict[str, list[dict[str, Any]]], lab: dict[str, Any]) -> list[str]:
    out: list[str] = []
    stmts = by_type.get("bank_statement", [])
    profile = (by_type.get("company_profile") or [None])[0]
    activity = (_v(profile, "principal_activity") or "").lower() if profile else ""
    declared = {
        lab_ for lab_, toks in lx.C5_TOKENS.items() if any(t.lower() in activity for t in toks)
    }
    if "freight" in activity or "logistics" in activity:
        declared.add("logistics")
    if "wholesale" in activity or "trading" in activity or "trade" in activity:
        declared.add("trading")
    rows = {(r["doc_key"], r["row"]): r for r in lab["rows"]}
    by_label: dict[str, Decimal] = defaultdict(lambda: D(0))
    total = D(0)
    debits: list[tuple[date, Decimal, str]] = []
    credits: list[tuple[date, Decimal, str]] = []
    for s in stmts:
        for r in _rows(s, "transactions"):
            c = r["cells"]
            lr = rows.get((s["key"], r["index"]), {})
            label = lr.get("c5") or "other"
            amt = D(c["debit"]["value"] or c["credit"]["value"] or "0")
            by_label[label] += amt
            total += amt
            dt = date.fromisoformat(c["date"]["value"])
            cp = lr.get("counterparty") or ""
            if c["debit"]["value"] is not None:
                debits.append((dt, amt, cp))
            elif c["credit"]["value"] is not None:
                credits.append((dt, amt, cp))
    for lab_ in RISKY:
        if by_label.get(lab_, D(0)) > 0 and lab_ not in declared:
            out.append(f"W14: {lab_} counterparties without a declared matching activity")
    if total > 0:
        for lab_, v in by_label.items():
            if lab_ != "other" and lab_ not in declared and v / total >= D("0.5"):
                out.append(f"W14: undeclared activity {lab_} carries {v / total:.0%} of turnover")
    hits = 0
    for cd, ca, ccp in credits:
        if ca < 5000:
            continue
        for dd, da, dcp in debits:
            if cd <= dd <= cd + timedelta(days=5) and abs(da - ca) <= ca * D("0.03") and dcp != ccp:
                hits += 1
                break
    if hits >= 3:
        out.append(f"W14: {hits} credit/debit pass-through pairs (limit 2)")
    chart = (by_type.get("ownership_chart") or [None])[0]
    if chart is not None:
        parents = {
            r["cells"]["parent_name"]["value"]
            for r in _rows(chart, "holders")
            if r["cells"]["parent_name"]["value"]
        }
        for r in _rows(chart, "holders"):
            c = r["cells"]
            if (
                c["holder_type"]["value"] == "company"
                and not c["parent_name"]["value"]
                and D(c["percent"]["value"]) >= D("25")
                and c["holder_name"]["value"] not in parents
            ):
                out.append(f"W14: chart company holder {c['holder_name']['value']} has no children")
    return out
