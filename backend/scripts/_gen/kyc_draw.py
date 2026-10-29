"""Small drawing helpers shared by the KYC/AML document renderers."""

from __future__ import annotations

from datetime import date
from typing import Any

from reportlab.pdfbase.pdfmetrics import stringWidth

from .render_common import DocCanvas, DocTruth, RenderedDoc, finalize_pdf, union_box

PRODUCER_PORTAL = ("Mock Registry Portal 2.0", "Mock Registry Portal")
PRODUCER_ID = ("Mock ID Imager 1.0", "Mock ID Imager")
LEFT = 40.0
RIGHT = 555.0
GREY = (0.35, 0.35, 0.35)


def wrap(text: str, width: float, size: float = 9.5, font: str = "Helvetica") -> list[str]:
    """Greedy word wrap to ``width`` points."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and stringWidth(trial, font, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def title_block(cv: DocCanvas, title: str, subtitle: str) -> None:
    cv.text(LEFT, 42, title, size=14, font="Helvetica-Bold")
    cv.text(LEFT, 62, subtitle, size=8.5, color=(0.3, 0.3, 0.3))


def kv(
    cv: DocCanvas,
    truth: DocTruth,
    y: float,
    label: str,
    name: str,
    value: str | None,
    raw: str | None,
    *,
    x_label: float = LEFT,
    x_value: float = 200.0,
    size: float = 9.5,
) -> float:
    """``label  value`` row; records the field with its box; returns the next y."""
    cv.text(x_label, y, label, size=8.5, color=GREY)
    box = cv.text(x_value, y, raw, size=size) if raw else None
    truth.set_field(name, value, raw, 1, box)
    return y + 17


def finish(
    cv: DocCanvas,
    *,
    producer: tuple[str, str],
    title: str,
    doc_date: date,
    overrides: dict[str, Any] | None = None,
) -> bytes:
    raw = cv.finish()
    ov = overrides or {}
    return finalize_pdf(
        raw,
        producer=str(ov.get("producer", producer[0])),
        creator=str(ov.get("creator", producer[1])),
        title=title,
        doc_date=doc_date,
    )


def row_box(cells: dict[str, tuple[str | None, str | None, dict[str, Any] | None]]) -> Any:
    return union_box([c[2] for c in cells.values() if c[2]])


__all__ = [
    "LEFT",
    "RIGHT",
    "GREY",
    "PRODUCER_ID",
    "PRODUCER_PORTAL",
    "RenderedDoc",
    "finish",
    "kv",
    "row_box",
    "title_block",
    "wrap",
]
