"""Family 10: applicability. Change how the document describes itself so that the rules written for
its type never run, while a person still reads it as that type."""

from __future__ import annotations

import random
from decimal import Decimal

from ..core import Bundle, Outcome, attack
from ..pdfkit import fmt_money
from ._util import fin, stmts

F = "F10 type confusion and applicability"
INV = "invoice_INV-2025-0107.pdf"


def _inflate_total_only(b: Bundle, name: str = INV):
    from ._util import invoice

    inv = invoice(b, name)
    cur = Decimal(inv.total.text.replace(",", ""))
    inv.edit.set_text(inv.total, fmt_money(cur * Decimal("1.10")))
    return inv


@attack(
    "R100",
    F,
    "Invoice with inconsistent total (+10%) retitled 'BILLING NOTE' and 'Invoice No' relabelled 'Ref No'",
)
def r100(b: Bundle, rng: random.Random) -> Outcome:
    inv = _inflate_total_only(b)
    t = [i for i in inv.edit.items(0) if i.text == "TAX INVOICE"][0]
    inv.edit.set_text(t, "BILLING NOTE", align="right")
    n = [i for i in inv.edit.items(0) if i.text.startswith("Invoice No:")][0]
    inv.edit.set_text(n, n.text.replace("Invoice No:", "Ref No:"), align="right")
    b.replace(INV, inv.dump())
    return Outcome(
        "A person reads an invoice; the classifier may not, and an unclassified document runs no invoice rule.",
        [INV],
    )


@attack(
    "R101",
    F,
    "Same inconsistent total (+10%), heading kept but 'Total payable' relabelled 'Amount due' and 'Subtotal' 'Net amount'",
)
def r101(b: Bundle, rng: random.Random) -> Outcome:
    inv = _inflate_total_only(b)
    for it in inv.edit.items(0):
        if it.text == "Total payable":
            inv.edit.set_text(it, "Amount due", align="right")
        elif it.text == "Subtotal":
            inv.edit.set_text(it, "Net amount", align="right")
    b.replace(INV, inv.dump())
    return Outcome(
        "Labels the arithmetic rules look for are renamed; a human reads the same page.", [INV]
    )


@attack(
    "R102",
    F,
    "Bank statement with an unreconciled balance, title and column headings renamed ('Account Activity', 'Debit', 'Credit', 'Running')",
)
def r102(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    s = ss.stmts[3]
    row = s.rows[9]
    s.edit.set_text(row.bal, fmt_money(row.balance + Decimal("8000")))
    ren = {
        "Statement of Account - Current Account": "Account Activity Report",
        "Withdrawals": "Debit",
        "Deposits": "Credit",
        "Balance": "Running",
    }
    for p in range(s.edit.n_pages):
        for it in s.edit.items(p):
            if it.text in ren:
                s.edit.set_text(it, ren[it.text], align="right" if it.x > 300 else "left")
    ss.write_back(b.files)
    return Outcome(
        "Inconsistent balance in a statement whose headings no longer match the template.", [s.name]
    )


@attack(
    "R103",
    F,
    "Financial statements with a broken subtotal and the statement headings renamed ('Income Summary', 'Balance Sheet Summary')",
)
def r103(b: Bundle, rng: random.Random) -> Outcome:
    f = fin(b, 2025)
    f.put("Gross profit", f.get("Gross profit") + 90_000)
    ren = {
        "Statement of Comprehensive Income": "Income Summary",
        "Statement of Financial Position": "Balance Sheet Summary",
        "Statement of Cash Flows": "Cash Summary",
    }
    for p in range(f.edit.n_pages):
        for it in f.edit.items(p):
            if it.text in ren:
                f.edit.set_text(it, ren[it.text], align="left")
    b.replace("financial_statement_FY2025.pdf", f.dump())
    return Outcome(
        "An inconsistent subtotal under headings the extractor may not map.",
        ["financial_statement_FY2025.pdf"],
    )


@attack(
    "R104",
    F,
    "Inconsistent invoice total (+10%) with the whole page rotated 90 degrees via /Rotate (geometry checks skip rotated pages)",
)
def r104(b: Bundle, rng: random.Random) -> Outcome:
    import io

    import pikepdf

    inv = _inflate_total_only(b)
    data = inv.dump()
    pdf = pikepdf.open(io.BytesIO(data))
    pdf.pages[0].Rotate = 90
    out = io.BytesIO()
    pdf.save(out)
    b.replace(INV, out.getvalue())
    return Outcome(
        "A rotated page looks like a rotated scan to a person; geometry-based forensic checks skip rotated pages.",
        [INV],
    )
