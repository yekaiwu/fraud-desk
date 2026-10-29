"""Bank statement renderer: four invented layouts (docs 4.1 layouts table).

The layouts are ours, not real bank templates (plan 0.4). Headers carry an issuer label text
(``DBS-STYLE MOCK BANK`` ...) the classifier can anchor on."""

from __future__ import annotations

from typing import Any

from reportlab.pdfbase.pdfmetrics import stringWidth

from .render_common import (
    A4_H,
    A4_W,
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    add_placeholder_signature,
    creation_date_for,
    finalize_pdf,
    fmt_amount,
    fmt_date,
    money_str,
    union_box,
)
from .schema import LAYOUT_C3, LAYOUT_PRODUCER
from .world_types import LedgerRow, Statement

ROWS_PER_PAGE = 22
ROW_H = 15.0
LEFT, RIGHT = 36.0, 559.0

# column key -> (header, anchor x, align); keys: date, vdate, desc, cheque, debit, credit, amount, balance
LAYOUT_SPEC: dict[str, dict[str, Any]] = {
    "dbs_style": {
        "title": "DBS-STYLE MOCK BANK",
        "subtitle": "Statement of Account - Current Account",
        "date_style": "dmy_mon",
        "cols": [
            ("date", "Date", 36, "l"),
            ("desc", "Description", 100, "l"),
            ("debit", "Withdrawals", 380, "r"),
            ("credit", "Deposits", 465, "r"),
            ("balance", "Balance", 559, "r"),
        ],
        "desc_right": 300,
        "totals": "row",
        "marker": "Page {n} of {m}",
        "upper_name": True,
    },
    "ocbc_style": {
        "title": "OCBC-STYLE MOCK BANK",
        "subtitle": "Statement of Account - Business Current",
        "date_style": "dmy_slash",
        "cols": [
            ("date", "Date", 36, "l"),
            ("vdate", "Value Date", 88, "l"),
            ("desc", "Description", 142, "l"),
            ("cheque", "Cheque", 318, "r"),
            ("debit", "Withdrawal", 400, "r"),
            ("credit", "Deposit", 480, "r"),
            ("balance", "Balance", 559, "r"),
        ],
        "desc_right": 280,
        "totals": None,
        "marker": "Page {n}/{m}",
        "upper_name": True,
    },
    "uob_style": {
        "title": "UOB-STYLE MOCK BANK",
        "subtitle": "Account Statement - eBusiness Account",
        "date_style": "dmy_dash",
        "cols": [
            ("date", "Date", 36, "l"),
            ("desc", "Transaction", 104, "l"),
            ("amount", "Amount", 450, "r"),
            ("balance", "Balance", 559, "r"),
        ],
        "desc_right": 360,
        "totals": "summary",
        "marker": "{m} pages, this is page {n}",
        "upper_name": True,
    },
    "software": {
        "title": "MockLedger Desktop 5.0 - Bank Ledger Report",
        "subtitle": "Bank account ledger (company-printed)",
        "date_style": "iso",
        "cols": [
            ("date", "Date", 36, "l"),
            ("desc", "Description", 100, "l"),
            ("debit", "Debit", 395, "r"),
            ("credit", "Credit", 477, "r"),
            ("balance", "Balance", 559, "r"),
        ],
        "desc_right": 320,
        "totals": None,
        "marker": "{n}/{m}",
        "upper_name": False,
    },
}


def fit_font(text: str, width: float, size: float = 7.5, font: str = "Helvetica") -> float:
    while size > 5.0 and stringWidth(text, font, size) > width:
        size -= 0.25
    return size


def _bal_text(layout: str, bal) -> str:  # noqa: ANN001
    if layout == "uob_style" and bal < 0:
        return f"{fmt_amount(-bal)} DR"
    return fmt_amount(bal)


TNC_TEXT = [
    "Terms and Conditions",
    "1. This statement is issued for information. Please notify the bank within 14 days of any discrepancy.",
    "2. Fees and charges are debited from the account in accordance with the published tariff.",
    "3. The bank may correct entries made in error without prior notice.",
    "4. Interest, where payable, is credited at the end of each month.",
    "5. This is a fictitious specimen document generated for software testing only.",
]


def render_statement(
    st: Statement, quirks: list[str], overrides: dict[str, Any] | None = None
) -> RenderedDoc:
    spec = LAYOUT_SPEC[st.layout]
    key = st.key
    truth = DocTruth(key=key, doc_type="bank_statement")
    rows = st.rows
    # paginate
    n_pages = max(1, -(-len(rows) // ROWS_PER_PAGE))
    pages: list[list[tuple[int, LedgerRow]]] = [
        [(i, r) for i, r in enumerate(rows) if i // ROWS_PER_PAGE == p] for p in range(n_pages)
    ]
    dropped = set(st.dropped_pages)
    cv = DocCanvas()
    first_physical = True
    t_rows = truth.table("transactions", ["date", "description", "debit", "credit", "balance"])
    chain = truth.table("page_chain", ["page", "page_x", "page_y", "account_number"])
    del t_rows, chain
    row_labels: list[dict[str, Any]] = []
    phys_page = 0
    holder_raw = st.account_holder.upper() if spec["upper_name"] else st.account_holder
    chain_markers: list[str] = []
    page_labels: list[PageLabel] = []
    for pno in range(1, n_pages + 1):
        if pno in dropped:
            continue
        if not first_physical:
            cv.new_page()
        first_physical = False
        phys_page += 1
        _page_header(cv, spec, st, truth, pno, n_pages, holder_raw)
        y = 252.0 if pno == 1 else 112.0
        _table_header(cv, spec, y - 20)
        for _idx, r in pages[pno - 1]:
            cells = _draw_row(cv, spec, st.layout, r, y)
            box = union_box([c[2] for c in cells.values() if c[2]])
            tidx = truth.add_row("transactions", cells, phys_page, box)
            row_labels.append(
                {
                    "row": tidx,
                    "c4": r.c4 if r.credit is not None else None,
                    "c5": r.c5,
                    "counterparty": r.counterparty,
                }
            )
            y += ROW_H
        if pno == n_pages:
            _draw_totals(cv, spec, st, truth, y, phys_page)
        mbox = cv.footer(spec["marker"].format(n=pno, m=n_pages), marker_x=RIGHT)
        chain_markers.append(f"{pno}/{n_pages}")
        truth.add_row(
            "page_chain",
            {
                "page": (str(phys_page), str(phys_page), mbox),
                "page_x": (str(pno), spec["marker"].format(n=pno, m=n_pages), mbox),
                "page_y": (str(n_pages), spec["marker"].format(n=pno, m=n_pages), mbox),
                "account_number": (st.account_number, st.account_number, None),
            },
            phys_page,
            mbox,
        )
        page_labels.append(PageLabel("bank_statement", key, st.layout))
    doc_pages = phys_page
    truth.fields["page_x_of_y"] = {
        "value": ",".join(chain_markers),
        "raw": None,
        "page": 1,
        "box": None,
    }
    if st.layout == "ocbc_style" and "Q12" in quirks:
        cv.new_page()
        y = 80.0
        for i, line in enumerate(TNC_TEXT):
            cv.text(
                LEFT,
                y,
                line,
                size=11 if i == 0 else 8.5,
                font="Helvetica-Bold" if i == 0 else "Helvetica",
            )
            y += 22 if i == 0 else 16
        cv.footer(None)
        page_labels.append(PageLabel("boilerplate", None, st.layout))
    raw = cv.finish()
    producer, creator = LAYOUT_PRODUCER[st.layout]
    created = creation_date_for(st.period_end)
    ov = overrides or {}
    producer = str(ov.get("producer", producer))
    creator = str(ov.get("creator", creator))
    mod = None
    if ov.get("mod_days"):
        from datetime import timedelta

        mod = created + timedelta(days=int(ov["mod_days"]))
    pdf = finalize_pdf(
        raw,
        producer=producer,
        creator=creator,
        title=f"Statement of Account {st.month}",
        doc_date=st.period_end,
        mod=mod,
    )
    if st.layout == "ocbc_style" and "Q09" in quirks:
        pdf = add_placeholder_signature(pdf, signer="OCBC-STYLE MOCK BANK", signed=created)
    return RenderedDoc(
        key=key,
        doc_type="bank_statement",
        stem=f"bank_statement_{st.month}",
        pdf=pdf,
        page_labels=page_labels,
        truth=truth,
        doc_date=st.period_end,
        layout=st.layout,
        issuer=LAYOUT_C3[st.layout],
        row_labels=row_labels,
        page_start=1,
        page_end=doc_pages,
    )


def _page_header(
    cv: DocCanvas,
    spec: dict[str, Any],
    st: Statement,
    truth: DocTruth,
    pno: int,
    n_pages: int,
    holder_raw: str,
) -> None:
    ds = spec["date_style"]
    if pno > 1:
        cv.text(LEFT, 40, spec["title"], size=11, font="Helvetica-Bold")
        cv.text(
            LEFT, 58, f"{holder_raw}  -  Account No: {st.account_number}  (continued)", size=8.5
        )
        return
    f = truth.set_field
    tb = cv.text(LEFT, 40, spec["title"], size=15, font="Helvetica-Bold")
    if st.bank_name:
        f("bank_name", st.bank_name, st.bank_name, 1, tb)
    else:
        f("bank_name", None, None, 1, None)
    cv.text(LEFT, 64, spec["subtitle"], size=10.5, font="Helvetica-Bold")
    b = cv.text(LEFT, 96, holder_raw, size=10.5, font="Helvetica-Bold")
    f("account_holder", st.account_holder, holder_raw, 1, b)
    if st.uen:
        b = cv.text(LEFT, 112, f"UEN: {st.uen}", size=8.5)
        f("uen", st.uen, st.uen, 1, b)
    else:
        f("uen", None, None, 1, None)
    cv.text(LEFT, 126, st.account_holder if False else "Singapore", size=8.5)
    b = cv.text(LEFT, 140, f"Account No: {st.account_number}", size=8.5)
    f("account_number", st.account_number, st.account_number, 1, b)
    ps, pe = fmt_date(st.period_start, ds), fmt_date(st.period_end, ds)
    cv.text(LEFT, 154, "Statement period: ", size=8.5)
    x = LEFT + stringWidth("Statement period: ", "Helvetica", 8.5)
    b1 = cv.text(x, 154, ps, size=8.5)
    x += stringWidth(ps, "Helvetica", 8.5)
    cv.text(x, 154, " to ", size=8.5)
    x += stringWidth(" to ", "Helvetica", 8.5)
    b2 = cv.text(x, 154, pe, size=8.5)
    f("period_start", st.period_start.isoformat(), ps, 1, b1)
    f("period_end", st.period_end.isoformat(), pe, 1, b2)
    b = cv.text(LEFT, 168, "Currency: SGD", size=8.5)
    f("currency", "SGD", "SGD", 1, b)
    # summary box
    cv.rect(330, 92, 229, 86 if spec["totals"] == "summary" else 52)
    cv.text(340, 98, "Account summary", size=8.5, font="Helvetica-Bold")
    cv.text(340, 114, "Opening balance", size=8.5)
    b = cv.text(549, 114, _bal_text(st.layout, st.opening), size=8.5, align="r")
    f("opening_balance", money_str(st.opening), _bal_text(st.layout, st.opening), 1, b)
    cv.text(340, 128, "Closing balance", size=8.5)
    b = cv.text(549, 128, _bal_text(st.layout, st.closing), size=8.5, align="r")
    f("closing_balance", money_str(st.closing), _bal_text(st.layout, st.closing), 1, b)
    if spec["totals"] == "summary":
        cv.text(340, 142, "Total debits", size=8.5)
        b = cv.text(549, 142, fmt_amount(st.total_debits), size=8.5, align="r")
        f("total_debits", money_str(st.total_debits), fmt_amount(st.total_debits), 1, b)
        cv.text(340, 156, "Total credits", size=8.5)
        b = cv.text(549, 156, fmt_amount(st.total_credits), size=8.5, align="r")
        f("total_credits", money_str(st.total_credits), fmt_amount(st.total_credits), 1, b)
    elif spec["totals"] is None:
        f("total_debits", None, None, 1, None)
        f("total_credits", None, None, 1, None)


def _table_header(cv: DocCanvas, spec: dict[str, Any], y: float) -> None:
    cv.line(LEFT, y + 13, RIGHT, y + 13, 0.8)
    for _key, head, x, align in spec["cols"]:
        cv.text(x, y, head, size=8, font="Helvetica-Bold", align=align)


def _draw_row(
    cv: DocCanvas, spec: dict[str, Any], layout: str, r: LedgerRow, y: float
) -> dict[str, tuple[str | None, str | None, dict[str, Any] | None]]:
    cols = {k: (x, a) for k, _h, x, a in spec["cols"]}
    ds = spec["date_style"]
    cells: dict[str, tuple[str | None, str | None, dict[str, Any] | None]] = {}
    draw_date = fmt_date(r.date, ds)
    b = cv.text(cols["date"][0], y, draw_date, size=7.5)
    cells["date"] = (r.date.isoformat(), draw_date, b)
    if "vdate" in cols:
        cv.text(cols["vdate"][0], y, draw_date, size=7.5)
    width = spec["desc_right"] - cols["desc"][0]
    size = fit_font(r.description, width)
    b = cv.text(cols["desc"][0], y, r.description, size=size)
    cells["description"] = (r.description, r.description, b)
    debit = credit = None
    if "amount" in cols:  # uob: signed by suffix
        if r.debit is not None:
            raw = f"{fmt_amount(r.debit)} DR"
            b = cv.text(cols["amount"][0], y, raw, size=7.5, align="r")
            debit = (money_str(r.debit), raw, b)
        elif r.credit is not None:
            raw = f"{fmt_amount(r.credit)} CR"
            b = cv.text(cols["amount"][0], y, raw, size=7.5, align="r")
            credit = (money_str(r.credit), raw, b)
    else:
        if r.debit is not None:
            raw = fmt_amount(r.debit)
            b = cv.text(cols["debit"][0], y, raw, size=7.5, align="r")
            debit = (money_str(r.debit), raw, b)
        if r.credit is not None:
            raw = fmt_amount(r.credit)
            b = cv.text(cols["credit"][0], y, raw, size=7.5, align="r")
            credit = (money_str(r.credit), raw, b)
    cells["debit"] = debit or (None, None, None)
    cells["credit"] = credit or (None, None, None)
    raw = _bal_text(layout, r.balance)
    b = cv.text(cols["balance"][0], y, raw, size=7.5, align="r")
    cells["balance"] = (money_str(r.balance), raw, b)
    return cells


def _draw_totals(
    cv: DocCanvas, spec: dict[str, Any], st: Statement, truth: DocTruth, y: float, page: int
) -> None:
    if spec["totals"] != "row":
        return
    cols = {k: (x, a) for k, _h, x, a in spec["cols"]}
    cv.line(LEFT, y + 2, RIGHT, y + 2, 0.6)
    cv.text(cols["desc"][0], y + 6, "TOTAL", size=8, font="Helvetica-Bold")
    b = cv.text(
        cols["debit"][0],
        y + 6,
        fmt_amount(st.total_debits),
        size=7.5,
        align="r",
        font="Helvetica-Bold",
    )
    truth.set_field(
        "total_debits", money_str(st.total_debits), fmt_amount(st.total_debits), page, b
    )
    b = cv.text(
        cols["credit"][0],
        y + 6,
        fmt_amount(st.total_credits),
        size=7.5,
        align="r",
        font="Helvetica-Bold",
    )
    truth.set_field(
        "total_credits", money_str(st.total_credits), fmt_amount(st.total_credits), page, b
    )


__all__ = ["LAYOUT_SPEC", "A4_H", "A4_W", "render_statement"]
