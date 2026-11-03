"""Family 2: edits made directly in the page content stream (no overlay, no new fonts),
plus calibration attacks that the pipeline should catch."""

from __future__ import annotations

import random
from decimal import Decimal

from ..core import Bundle, Outcome, attack
from ..pdfkit import find, fmt_money
from ._util import inv_names, invoice, stmts

F = "F2 content-stream edits"


@attack(
    "R10",
    F,
    "Unpaid invoice with no cross-reference: quantities +60%, lines, subtotal, GST and total recomputed in the stream",
    consistent=True,
)
def r10(b: Bundle, rng: random.Random) -> Outcome:
    name = "invoice_INV-2025-0107.pdf"  # no PO or contract reference, not paid
    inv = invoice(b, name)
    for k in range(len(inv.lines)):
        inv.set_qty(k, int(int(inv.lines[k][1].text) * 1.6))
    sub, gst, total = inv.recompute()
    b.replace(name, inv.dump())
    return Outcome(
        "Inflate an unreferenced, unpaid invoice by 60% with every figure on the page recomputed; nothing else in the bundle mentions it.",
        [name],
        [fmt_money(total)],
    )


@attack(
    "R11",
    F,
    "Invoice that cites a contract: +60% and the contract reference line deleted so no contract comparison can run",
)
def r11(b: Bundle, rng: random.Random) -> Outcome:
    name = "invoice_INV-2025-0104.pdf"
    inv = invoice(b, name)
    for k in range(len(inv.lines)):
        inv.set_qty(k, int(int(inv.lines[k][1].text) * 1.6))
    sub, gst, total = inv.recompute()
    inv.edit.remove(inv.item("Contract Ref"))
    b.replace(name, inv.dump())
    return Outcome(
        "Make an invoice 60% larger and drop the 'Contract Ref' line so the invoice-versus-contract check has nothing to compare.",
        [name],
        [fmt_money(total)],
    )


@attack(
    "R12",
    F,
    "Invoice cites contract C-2025-001, +60% kept consistent: the contract reference stays (control for R11)",
    sanity=True,
)
def r12(b: Bundle, rng: random.Random) -> Outcome:
    # every invoice against C-2025-001 is inflated so the contract value (120,000) is exceeded
    names = [n for n in inv_names(b) if "Contract Ref: C-2025-001" in _text(b.files[n])]
    for n in names:
        inv = invoice(b, n)
        for k in range(len(inv.lines)):
            inv.set_qty(k, int(int(inv.lines[k][1].text) * 3))
        inv.recompute()
        b.replace(n, inv.dump())
    return Outcome(
        "Triple every invoice that bills contract C-2025-001 (arithmetic consistent); calibration: the contract-value comparison should fire.",
        names,
    )


def _text(data: bytes) -> str:
    import fitz

    return "".join(p.get_text() for p in fitz.open(stream=data, filetype="pdf"))


@attack(
    "R13",
    F,
    "One statement balance edited in the stream, nothing recomputed (calibration)",
    sanity=True,
)
def r13(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    s = ss.stmts[2]
    row = s.rows[8]
    s.edit.set_text(row.bal, fmt_money(row.balance + Decimal("5000")))
    ss.write_back(b.files)
    return Outcome(
        "Change one running balance by S$5,000 only; calibration for the running-balance check.",
        [s.name],
    )


@attack("R14", F, "Statement: change the account holder to a near-match spelling, UEN unchanged")
def r14(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    for s in ss.stmts[:3]:
        it = find(s.edit.items(0), "MERIDIAN FASTENER SUPPLIES PTE. LTD.")
        s.edit.set_text(it, "MERIDIAN FASTENER SUPPLY PTE. LTD.", align="left")
    ss.write_back(b.files)
    return Outcome(
        "Three statements show 'SUPPLY' instead of 'SUPPLIES' as the account holder; the UEN printed is still the applicant's.",
        [s.name for s in ss.stmts[:3]],
    )


@attack(
    "R15",
    F,
    "Statement from another holder: name and UEN both changed to a different company on three months",
)
def r15(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    for s in ss.stmts[:3]:
        it = find(s.edit.items(0), "MERIDIAN FASTENER SUPPLIES PTE. LTD.")
        s.edit.set_text(it, "BRIGHTWELL COMPONENTS PTE. LTD.", align="left")
        u = [i for i in s.edit.items(0) if i.text.startswith("UEN:")][0]
        s.edit.set_text(u, "UEN: 201522334G", align="left")
    ss.write_back(b.files)
    return Outcome(
        "Statements belonging to a different company are presented (name and UEN both replaced).",
        [s.name for s in ss.stmts[:3]],
    )


@attack(
    "R16",
    F,
    "Credit hijack: small debit rows rewritten as S$9,000 trade credits from the real customer, chain recomputed to Dec",
)
def r16(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    s = ss.stmts[3]
    fees = [r for r in s.rows if r.kind == "wd" and r.amount < Decimal("1")][:3]
    for _k, r in enumerate(fees):
        s.retarget(r, "FAST PAYMENT FROM ACME TRADING PTE LTD", "dep", Decimal("9000"))
    ss.recompute_from(3)
    ss.write_back(b.files)
    return Outcome(
        "Turn three 0.15-0.50 fee rows in October into three S$9,000 payments from the main customer (no row added or moved), recompute through December.",
        [x.name for x in ss.stmts[3:]],
        ["FAST PAYMENT FROM ACME TRADING PTE LTD"],
    )


@attack(
    "R17",
    F,
    "Delete the largest December debit row and recompute (closing balance rises, FS cash not edited)",
)
def r17(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    s = ss.stmts[5]
    big = max((r for r in s.rows if r.kind == "wd"), key=lambda r: r.amount)
    s.drop_row(big)
    ss.recompute_from(5)
    ss.write_back(b.files)
    return Outcome(
        "Remove the largest outflow of December so the closing balance climbs; every balance and total recomputed.",
        [s.name],
    )
