"""Ownership chart renderer: boxes and arrows (data-model 6.8).

The applicant sits at the top, direct holders on the next tier, the holders of an intermediate
company on the tier below it. Every box prints the holder's percentage; a child's percentage is the
share held *in its parent* (CR-19), and the child box repeats its parent's name. ``UBO`` badges mark
beneficial owners; the threshold is printed ``25.00%``.
"""

from __future__ import annotations

from typing import Any

from reportlab.pdfbase.pdfmetrics import stringWidth

from .kyc_draw import GREY, LEFT, PRODUCER_PORTAL, RIGHT, finish, row_box
from .kyc_view import ChartRow, view_of
from .render_common import A4_W, DocCanvas, DocTruth, PageLabel, RenderedDoc, fmt_date
from .world import World

BOX_W, BOX_H, CHILD_H, GAP = 150.0, 84.0, 98.0, 20.0
Y_APPLICANT, Y_DIRECT, Y_CHILD = 150.0, 290.0, 450.0


def _fit(text: str, width: float, size: float, font: str = "Helvetica-Bold") -> float:
    while size > 5.5 and stringWidth(text, font, size) > width:
        size -= 0.25
    return size


def _arrow(cv: DocCanvas, x: float, y0: float, y1: float) -> None:
    cv.line(x, y0, x, y1, 0.9)
    cv.line(x - 3, y1 - 5, x, y1, 0.9)
    cv.line(x + 3, y1 - 5, x, y1, 0.9)


def _holder_box(
    cv: DocCanvas, truth: DocTruth, r: ChartRow, x: float, y: float, h: float
) -> dict[str, Any]:
    cv.rect(x, y, BOX_W, h, fill=(0.95, 0.97, 1.0) if r.holder_type == "company" else (1, 1, 1))
    inner = BOX_W - 12
    name_box = cv.text(x + 6, y + 7, r.name, size=_fit(r.name, inner, 9), font="Helvetica-Bold")
    type_raw = "Company" if r.holder_type == "company" else "Individual"
    type_box = cv.text(x + 6, y + 21, type_raw, size=8)
    id_box = None
    if r.id_or_uen:
        cv.text(x + 6, y + 33, "ID/UEN", size=6.5, color=GREY)
        id_box = cv.text(x + 40, y + 33, r.id_or_uen, size=8.5)
    pct_raw = f"{r.percent:.2f}%"
    pct_box = cv.text(x + 6, y + 47, pct_raw, size=11, font="Helvetica-Bold")
    ubo_box = None
    if r.is_ubo:
        cv.rect(x + BOX_W - 38, y + 46, 30, 14, fill=(0.85, 0.95, 0.85), width=0.8)
        ubo_box = cv.text(x + BOX_W - 23, y + 49, "UBO", size=8, font="Helvetica-Bold", align="c")
    parent_box = None
    if r.parent:
        cv.text(x + 6, y + 66, "Held in", size=6.5, color=GREY)
        parent_box = cv.text(
            x + 36, y + 65, r.parent, size=_fit(r.parent, BOX_W - 44, 7.5, "Helvetica")
        )
    cells = {
        "holder_name": (r.name, r.name, name_box),
        "holder_type": (r.holder_type, type_raw, type_box),
        "id_or_uen": (r.id_or_uen, r.id_or_uen, id_box),
        "percent": (format(r.percent, "f"), pct_raw, pct_box),
        "parent_name": (r.parent, r.parent, parent_box),
        "is_ubo": ("true" if r.is_ubo else "false", "UBO" if r.is_ubo else None, ubo_box),
    }
    truth.add_row("holders", cells, 1, row_box(cells))
    return {"x": x, "w": BOX_W}


def _xs(n: int, width: float = BOX_W) -> list[float]:
    total = n * width + (n - 1) * GAP
    x0 = (A4_W - total) / 2
    return [x0 + i * (width + GAP) for i in range(n)]


def render_chart(w: World, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = "ownership_chart:-"
    truth = DocTruth(key=key, doc_type="ownership_chart")
    v = view_of(w)
    c = w.company
    cv = DocCanvas()
    cv.text(LEFT, 42, "OWNERSHIP STRUCTURE CHART", size=14, font="Helvetica-Bold")
    cv.text(LEFT, 62, "Beneficial ownership disclosure - mock layout, not an official form",
            size=8.5, color=(0.3, 0.3, 0.3))  # fmt: skip
    chart_date = w.registry_as_of
    cd_raw = f"Chart as at {fmt_date(chart_date, 'dmy_mon')}"
    cd_box = cv.text(RIGHT, 88, cd_raw, size=9, align="r")
    truth.set_field("chart_date", chart_date.isoformat(), cd_raw, 1, cd_box)
    thr_raw = f"{v.chart_threshold:.2f}%"
    cv.text(LEFT, 88, "UBO threshold", size=8.5, color=GREY)
    thr_box = cv.text(LEFT + 70, 88, thr_raw, size=9.5, font="Helvetica-Bold")
    truth.set_field("ubo_threshold_percent", format(v.chart_threshold, "f"), thr_raw, 1, thr_box)
    # applicant box
    ax = (A4_W - 220) / 2
    cv.rect(ax, Y_APPLICANT, 220, 52, fill=(0.9, 0.93, 0.98), width=1.2)
    nm_box = cv.text(
        A4_W / 2, Y_APPLICANT + 10, c.party.name, size=_fit(c.party.name, 200, 11), font="Helvetica-Bold", align="c"
    )  # fmt: skip
    cv.text(A4_W / 2 - 40, Y_APPLICANT + 30, "UEN", size=7, color=GREY, align="c")
    uen_box = cv.text(A4_W / 2 + 2, Y_APPLICANT + 29, c.party.uen, size=9, align="l")
    truth.set_field("company_name", c.party.name, c.party.name, 1, nm_box)
    truth.set_field("uen", c.party.uen, c.party.uen, 1, uen_box)
    truth.table(
        "holders", ["holder_name", "holder_type", "id_or_uen", "percent", "parent_name", "is_ubo"]
    )
    direct = [r for r in v.chart if r.parent is None]
    xs = _xs(len(direct)) if direct else []
    mid_y = Y_APPLICANT + 52 + 36
    if direct:
        cv.line(A4_W / 2, Y_APPLICANT + 52, A4_W / 2, mid_y, 0.9)
        cv.line(xs[0] + BOX_W / 2, mid_y, xs[-1] + BOX_W / 2, mid_y, 0.9)
    pos: dict[str, float] = {}
    for r, x in zip(direct, xs, strict=True):
        _arrow(cv, x + BOX_W / 2, mid_y, Y_DIRECT)
        _holder_box(cv, truth, r, x, Y_DIRECT, BOX_H)
        pos[r.name] = x
    # intermediate tiers
    for parent_name, px in pos.items():
        kids = [r for r in v.chart if r.parent == parent_name]
        if not kids:
            continue
        span = len(kids) * BOX_W + (len(kids) - 1) * GAP
        start = min(px, A4_W - 20 - span)
        _arrow(cv, px + BOX_W / 2, Y_DIRECT + BOX_H, Y_CHILD)
        for j, r in enumerate(kids):
            _holder_box(cv, truth, r, start + j * (BOX_W + GAP), Y_CHILD, CHILD_H)
    cv.text(LEFT, 620, "Percentages are shares held directly in the entity above the box.",
            size=8, color=GREY)  # fmt: skip
    cv.text(
        LEFT, 634, "UBO = beneficial owner at or above the threshold shown.", size=8, color=GREY
    )
    cv.footer("Page 1 of 1")
    pdf = finish(
        cv,
        producer=PRODUCER_PORTAL,
        title="Ownership Chart",
        doc_date=w.registry_as_of,
        overrides=overrides,
    )
    return RenderedDoc(
        key=key,
        doc_type="ownership_chart",
        stem="ownership_chart",
        pdf=pdf,
        page_labels=[PageLabel("ownership_chart", key)],
        truth=truth,
        doc_date=w.registry_as_of,
        page_start=1,
        page_end=1,
    )
