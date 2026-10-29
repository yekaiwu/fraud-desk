"""Shareholder register (register of members) renderer (data-model 6.9)."""

from __future__ import annotations

from typing import Any

from .kyc_draw import LEFT, PRODUCER_PORTAL, RIGHT, finish, kv, row_box, title_block
from .kyc_view import view_of
from .render_common import DocCanvas, DocTruth, PageLabel, RenderedDoc, fmt_amount, fmt_date
from .world import World

COLS = [
    ("holder_name", "Member", LEFT, "l"),
    ("id_or_uen", "ID / UEN", 238, "l"),
    ("shares", "Shares", 372, "r"),
    ("percent", "Percent", 430, "r"),
    ("share_class", "Class", 440, "l"),
    ("date_acquired", "Acquired", RIGHT, "r"),
]


def render_register(w: World, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = "shareholder_register:-"
    truth = DocTruth(key=key, doc_type="shareholder_register")
    v = view_of(w)
    c = w.company
    cv = DocCanvas()
    ds = "dmy_mon"
    title_block(
        cv, "REGISTER OF MEMBERS", "Shareholder register - mock layout, not an official extract"
    )
    y = 92.0
    y = kv(cv, truth, y, "Company", "company_name", c.party.name, c.party.name)
    y = kv(cv, truth, y, "UEN", "uen", c.party.uen, c.party.uen)
    reg_raw = fmt_date(w.registry_as_of, ds)
    y = kv(cv, truth, y, "Register as at", "register_date", w.registry_as_of.isoformat(), reg_raw)
    ts_raw = f"{v.total_shares:,}"
    y = kv(cv, truth, y, "Total issued shares", "total_shares", str(v.total_shares), ts_raw)
    cap_raw = f"S$ {fmt_amount(c.paid_up_capital)}"
    y = kv(
        cv, truth, y, "Issued capital", "issued_capital", format(c.paid_up_capital, "f"), cap_raw
    )
    truth.set_field("currency", "SGD", "SGD", 1, None)
    y += 14
    truth.table(
        "shareholders",
        ["holder_name", "id_or_uen", "shares", "percent", "share_class", "date_acquired"],
    )
    for name, head, x, a in COLS:
        del name
        cv.text(x, y, head, size=8, font="Helvetica-Bold", align=a)
    cv.line(LEFT, y + 13, RIGHT, y + 13, 0.6)
    y += 22
    for r in v.register:
        sh_raw = f"{r.shares:,}"
        pc_raw = f"{r.percent:.2f}%"
        acq_raw = fmt_date(r.acquired, ds) if r.acquired else None
        cells = {
            "holder_name": (r.name, r.name, cv.text(LEFT, y, r.name, size=8.5)),
            "id_or_uen": (r.id_or_uen, r.id_or_uen, cv.text(238, y, r.id_or_uen or "", size=8.5)),
            "shares": (str(r.shares), sh_raw, cv.text(372, y, sh_raw, size=8.5, align="r")),
            "percent": (
                format(r.percent, "f"),
                pc_raw,
                cv.text(430, y, pc_raw, size=8.5, align="r"),
            ),
            "share_class": (r.share_class, r.share_class, cv.text(440, y, r.share_class, size=8.5)),
            "date_acquired": (
                r.acquired.isoformat() if r.acquired else None,
                acq_raw,
                cv.text(RIGHT, y, acq_raw, size=8.5, align="r") if acq_raw else None,
            ),
        }
        truth.add_row("shareholders", cells, 1, row_box(cells))
        y += 17
    cv.footer("Page 1 of 1")
    pdf = finish(
        cv,
        producer=PRODUCER_PORTAL,
        title="Register of Members",
        doc_date=w.registry_as_of,
        overrides=overrides,
    )
    return RenderedDoc(
        key=key,
        doc_type="shareholder_register",
        stem="shareholder_register",
        pdf=pdf,
        page_labels=[PageLabel("shareholder_register", key)],
        truth=truth,
        doc_date=w.registry_as_of,
        page_start=1,
        page_end=1,
    )
