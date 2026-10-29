"""Passport (biographical page, mock) renderer with a TD3 machine-readable zone (data-model 6.12)."""

from __future__ import annotations

from datetime import date
from typing import Any

from .kyc_draw import GREY, PRODUCER_ID, finish
from .kyc_view import passport_mrz, view_of
from .render_common import (
    A4_W,
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    fmt_date,
    union_box,
)
from .render_id import avatar_png
from .world import World
from .world_types import Person

X0 = 70.0
CARD_W = A4_W - 2 * 54.0


def issue_date_of(p: Person) -> date:
    """Passports are valid for ten years in this mock."""
    assert p.passport_expiry is not None
    return date(p.passport_expiry.year - 10, p.passport_expiry.month, p.passport_expiry.day)


def render_passport(
    w: World, p: Person, n: int, overrides: dict[str, Any] | None = None
) -> RenderedDoc:
    key = f"passport:{n}"
    truth = DocTruth(key=key, doc_type="passport")
    f = truth.set_field
    over = view_of(w).passport_over
    assert p.passport_expiry is not None
    cv = DocCanvas()
    ds = "dmy_mon"
    cv.text(X0, 70, "PASSPORT BIOGRAPHICAL PAGE (specimen)", size=9, color=GREY)
    cv.rect(54, 92, CARD_W, 470, fill=(0.96, 0.95, 0.9), width=1.2)
    cv.text(
        X0,
        106,
        "PASSPORT / PASAPORTE (MOCK)",
        size=13,
        font="Helvetica-Bold",
        color=(0.5, 0.1, 0.1),
    )
    cv.image(avatar_png(p.sig_seed, (150, 190)), X0, 140, 112, 142)
    country = p.nationality  # ICAO 3-letter code of the issuing state
    x = X0 + 135
    y = 140.0

    def field(label: str, name: str, value: str | None, raw: str | None, size: float = 10) -> None:
        nonlocal y
        cv.text(x, y, label, size=7, color=GREY)
        box = cv.text(x, y + 9, raw, size=size, font="Helvetica-Bold") if raw else None
        f(name, value, raw, 1, box)
        y += 27

    cv.text(x, y, "Type", size=7, color=GREY)
    cv.text(x, y + 9, "P", size=10, font="Helvetica-Bold")
    cv.text(x + 70, y, "Country code", size=7, color=GREY)
    cc_box = cv.text(x + 70, y + 9, country, size=10, font="Helvetica-Bold")
    f("issuing_country", country, country, 1, cc_box)
    y += 27
    pno = over.get("passport_number", p.id_number)
    field("Passport No.", "passport_number", pno, pno)
    sur_raw = p.surname.upper()
    giv_raw = p.given_names.upper()
    cv.text(x, y, "Surname", size=7, color=GREY)
    sur_box = cv.text(x, y + 9, sur_raw, size=10, font="Helvetica-Bold")
    f("surname", p.surname, sur_raw, 1, sur_box)
    y += 27
    cv.text(x, y, "Given names", size=7, color=GREY)
    giv_box = cv.text(x, y + 9, giv_raw, size=10, font="Helvetica-Bold")
    f("given_names", p.given_names, giv_raw, 1, giv_box)
    y += 27
    full = f"{p.given_names} {p.surname}"
    f("holder_name", full, f"{giv_raw} {sur_raw}", 1, union_box([giv_box, sur_box]))
    dob = date.fromisoformat(over["date_of_birth"]) if "date_of_birth" in over else p.birth
    field("Nationality", "nationality", country, country)
    field("Date of birth", "date_of_birth", dob.isoformat(), fmt_date(dob, ds))
    field("Sex", "sex", p.sex, p.sex)
    issue = issue_date_of(p)
    field("Date of issue", "date_of_issue", issue.isoformat(), fmt_date(issue, ds))
    field(
        "Date of expiry",
        "date_of_expiry",
        p.passport_expiry.isoformat(),
        fmt_date(p.passport_expiry, ds),
    )
    l1, l2 = passport_mrz(w, p)
    my = 470.0
    b1 = cv.text(X0, my, l1, size=11.5, font="Courier-Bold")
    b2 = cv.text(X0, my + 16, l2, size=11.5, font="Courier-Bold")
    f("mrz_line1", l1, l1, 1, b1)
    f("mrz_line2", l2, l2, 1, b2)
    cv.footer("Page 1 of 1")
    pdf = finish(cv, producer=PRODUCER_ID, title="Passport", doc_date=issue, overrides=overrides)
    return RenderedDoc(
        key=key,
        doc_type="passport",
        stem=f"passport_{n}",
        pdf=pdf,
        page_labels=[PageLabel("passport", key)],
        truth=truth,
        doc_date=issue,
        page_start=1,
        page_end=1,
    )
