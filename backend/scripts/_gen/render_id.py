"""National ID card (NRIC front, mock) renderer with a synthetic avatar."""

from __future__ import annotations

import io
import random
from typing import Any

from PIL import Image, ImageDraw

from .render_common import DocCanvas, DocTruth, PageLabel, RenderedDoc, finalize_pdf, fmt_date
from .world_types import Person

LEFT = 90.0


def avatar_png(seed: int, size: tuple[int, int] = (150, 190)) -> bytes:
    """A synthetic head-and-shoulders avatar (no real face)."""
    rng = random.Random(f"avatar:{seed}")
    w, h = size
    bg = tuple(rng.randint(170, 235) for _ in range(3))
    img = Image.new("RGB", size, bg)
    dr = ImageDraw.Draw(img)
    skin = rng.choice(
        [(235, 200, 170), (210, 165, 130), (190, 140, 105), (240, 210, 185), (165, 120, 90)]
    )
    hair = rng.choice([(25, 20, 18), (60, 40, 25), (90, 60, 40), (15, 15, 15), (110, 110, 110)])
    shirt = tuple(rng.randint(30, 200) for _ in range(3))
    dr.ellipse((w * 0.05, h * 0.68, w * 0.95, h * 1.25), fill=shirt)
    dr.rectangle((w * 0.42, h * 0.55, w * 0.58, h * 0.75), fill=skin)
    dr.ellipse((w * 0.25, h * 0.12, w * 0.75, h * 0.66), fill=skin)
    dr.pieslice((w * 0.22, h * 0.07, w * 0.78, h * 0.5), 180, 360, fill=hair)
    for ex in (0.38, 0.62):
        dr.ellipse((w * ex - 4, h * 0.36 - 3, w * ex + 4, h * 0.36 + 3), fill=(40, 30, 25))
    dr.arc((w * 0.4, h * 0.45, w * 0.6, h * 0.55), 20, 160, fill=(120, 60, 60), width=2)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def render_id(p: Person, n: int, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = f"national_id:{n}"
    truth = DocTruth(key=key, doc_type="national_id")
    f = truth.set_field
    cv = DocCanvas()
    ds = "dmy_mon"
    cv.text(LEFT, 70, "IDENTITY CARD SCAN (specimen)", size=9, color=(0.4, 0.4, 0.4))
    cv.rect(LEFT - 14, 92, 475, 300, fill=(0.93, 0.95, 0.98), width=1.2)
    cv.text(
        LEFT,
        106,
        "SINGAPORE IDENTITY CARD (MOCK)",
        size=12,
        font="Helvetica-Bold",
        color=(0.1, 0.2, 0.5),
    )
    cv.image(avatar_png(p.sig_seed), LEFT + 300, 135, 105, 133)
    y = 140.0

    def kv(label: str, name: str, canon: str | None, raw: str | None) -> None:
        nonlocal y
        cv.text(LEFT, y, label, size=7.5, color=(0.35, 0.35, 0.35))
        b = cv.text(LEFT, y + 10, raw or "", size=10, font="Helvetica-Bold")
        f(name, canon, raw, 1, b if raw else None)
        y += 30

    kv("NRIC No.", "id_number", p.id_number, p.id_number)
    kv("Name", "holder_name", p.name, p.name.upper())
    kv("Date of birth", "date_of_birth", p.birth.isoformat(), fmt_date(p.birth, ds))
    kv("Sex", "sex", p.sex, p.sex)
    kv("Nationality", "nationality", "Singaporean", "SINGAPORE CITIZEN")
    cv.text(LEFT, 326, "Date of issue", size=7.5, color=(0.35, 0.35, 0.35))
    b = cv.text(LEFT, 336, fmt_date(p.issue_date, ds), size=9, font="Helvetica-Bold")
    f("date_of_issue", p.issue_date.isoformat(), fmt_date(p.issue_date, ds), 1, b)
    cv.text(LEFT + 150, 326, "Address", size=7.5, color=(0.35, 0.35, 0.35))
    b = cv.text(LEFT + 150, 336, p.address[:44], size=8)
    f("address", p.address[:44], p.address[:44], 1, b)
    f("expiry_date", None, None, 1, None)
    cv.footer("Page 1 of 1")
    raw = cv.finish()
    ov = overrides or {}
    pdf = finalize_pdf(
        raw,
        producer=str(ov.get("producer", "Mock ID Imager 1.0")),
        creator=str(ov.get("creator", "Mock ID Imager")),
        title="Identity card",
        doc_date=p.issue_date,
    )
    return RenderedDoc(
        key=key,
        doc_type="national_id",
        stem=f"national_id_{n}",
        pdf=pdf,
        page_labels=[PageLabel("national_id", key)],
        truth=truth,
        doc_date=p.issue_date,
        page_start=1,
        page_end=1,
    )
