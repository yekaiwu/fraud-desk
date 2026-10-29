"""Company profile (BizFile-style mock) renderer."""

from __future__ import annotations

from typing import Any

from .render_common import (
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    finalize_pdf,
    fmt_amount,
    fmt_date,
    money_str,
    union_box,
)
from .world import World

LEFT = 40.0
NATIONALITY = {"SG": "Singaporean", "ESP": "Spanish", "ES": "Spanish"}


def render_profile(w: World, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = "company_profile:-"
    truth = DocTruth(key=key, doc_type="company_profile")
    f = truth.set_field
    c = w.company
    cv = DocCanvas()
    ds = "dmy_mon"
    cv.text(LEFT, 42, "BUSINESS PROFILE (BizFile-style mock)", size=14, font="Helvetica-Bold")
    cv.text(
        LEFT,
        62,
        "Company registry information - specimen layout, not an official extract",
        size=8.5,
        color=(0.3, 0.3, 0.3),
    )
    y = 92.0

    def kv(
        label: str, value: str, name: str, raw: str | None = None, canon: str | None = None
    ) -> None:
        nonlocal y
        cv.text(LEFT, y, label, size=8.5, color=(0.35, 0.35, 0.35))
        b = cv.text(200, y, raw if raw is not None else value, size=9.5)
        f(name, canon if canon is not None else value, raw if raw is not None else value, 1, b)
        y += 17

    kv("Entity name", c.party.name, "company_name")
    kv("UEN", c.party.uen, "uen")
    kv("Entity type", c.entity_type, "entity_type")
    kv("Status", c.status, "status")
    kv(
        "Incorporation date",
        c.incorporation.isoformat(),
        "incorporation_date",
        fmt_date(c.incorporation, ds),
    )
    kv("Registered address", c.party.address, "registered_address")
    kv("Principal activity", c.activity, "principal_activity")
    kv("SSIC code", c.ssic, "ssic_code")
    kv(
        "Paid-up capital",
        money_str(c.paid_up_capital) or "",
        "paid_up_capital",
        f"S$ {fmt_amount(c.paid_up_capital)}",
    )
    f("currency", "SGD", "SGD", 1, None)
    kv(
        "Information as at",
        w.registry_as_of.isoformat(),
        "retrieved_date",
        fmt_date(w.registry_as_of, ds),
    )
    y += 12
    cv.text(LEFT, y, "Directors", size=11, font="Helvetica-Bold")
    y += 20
    truth.table(
        "directors", ["director_name", "id_number", "nationality", "position", "appointment_date"]
    )
    for x, h in (
        (LEFT, "Name"),
        (250, "ID number"),
        (340, "Nationality"),
        (430, "Position"),
        (500, "Appointed"),
    ):
        cv.text(x, y, h, size=8, font="Helvetica-Bold")
    cv.line(LEFT, y + 13, 559, y + 13, 0.6)
    y += 20
    for p in w.people:
        if not (p.on_profile and p.is_director):
            continue
        nat = NATIONALITY.get(p.nationality, p.nationality)
        bn = cv.text(LEFT, y, p.name, size=8.5)
        bi = cv.text(250, y, p.id_number, size=8.5)
        bt = cv.text(340, y, nat, size=8.5)
        bp = cv.text(430, y, p.position, size=8.5)
        ba = cv.text(500, y, fmt_date(p.appointment, ds), size=8.5)
        cells = {
            "director_name": (p.name, p.name, bn),
            "id_number": (p.id_number, p.id_number, bi),
            "nationality": (nat, nat, bt),
            "position": (p.position, p.position, bp),
            "appointment_date": (p.appointment.isoformat(), fmt_date(p.appointment, ds), ba),
        }
        truth.add_row("directors", cells, 1, union_box([x[2] for x in cells.values() if x[2]]))
        y += 17
    y += 16
    cv.text(LEFT, y, "Shareholders", size=11, font="Helvetica-Bold")
    y += 20
    truth.table("shareholders", ["holder_name", "id_or_uen", "shares", "percent"])
    for x, h in ((LEFT, "Name"), (300, "ID / UEN"), (440, "Shares"), (559, "Percent")):
        cv.text(x, y, h, size=8, font="Helvetica-Bold", align="r" if h == "Percent" else "l")
    cv.line(LEFT, y + 13, 559, y + 13, 0.6)
    y += 20
    for h in w.holders:
        bn = cv.text(LEFT, y, h.name, size=8.5)
        bi = cv.text(300, y, h.id_or_uen or "", size=8.5)
        sh = f"{h.shares:,}"
        bs = cv.text(440, y, sh, size=8.5)
        pc = f"{h.percent:.2f}%"
        bp = cv.text(559, y, pc, size=8.5, align="r")
        cells = {
            "holder_name": (h.name, h.name, bn),
            "id_or_uen": (h.id_or_uen, h.id_or_uen, bi),
            "shares": (str(h.shares), sh, bs),
            "percent": (format(h.percent, "f"), pc, bp),
        }
        truth.add_row("shareholders", cells, 1, union_box([x[2] for x in cells.values() if x[2]]))
        y += 17
    cv.footer("Page 1 of 1")
    raw = cv.finish()
    ov = overrides or {}
    pdf = finalize_pdf(
        raw,
        producer=str(ov.get("producer", "Mock Registry Portal 2.0")),
        creator=str(ov.get("creator", "Mock Registry Portal")),
        title="Business Profile",
        doc_date=w.registry_as_of,
    )
    return RenderedDoc(
        key=key,
        doc_type="company_profile",
        stem="company_profile",
        pdf=pdf,
        page_labels=[PageLabel("company_profile", key)],
        truth=truth,
        doc_date=w.registry_as_of,
        page_start=1,
        page_end=1,
    )
