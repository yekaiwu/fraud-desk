"""Contract renderer (two pages) with signature images re-jittered per use."""

from __future__ import annotations

import io
import math
import random
from typing import Any

from PIL import Image, ImageDraw

from .render_common import (
    A4_W,
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    finalize_pdf,
    fmt_amount,
    fmt_date,
    money_str,
)
from .world_types import Contract

LEFT, RIGHT = 54.0, A4_W - 54.0


def signature_png(seed: int, use_key: str | None, size: tuple[int, int] = (360, 120)) -> bytes:
    """A drawn signature. The stroke path depends on ``seed``; with ``use_key`` set, rotation
    (+-2 deg), scale (+-3%) and stroke jitter are drawn from ``(seed, use_key)`` so honest
    repeats are not pixel-identical. ``use_key=None`` gives the canonical, un-jittered image
    (used by ``reuse_signature``: identical crops appear only through tamper T34)."""
    base = random.Random(f"sig:{seed}")
    jit = random.Random(f"sigjit:{seed}:{use_key}") if use_key is not None else None
    scale_up = 3
    w, h = size[0] * scale_up, size[1] * scale_up
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    dr = ImageDraw.Draw(img)
    a = [base.uniform(0.15, 0.4) for _ in range(3)]
    f = [base.uniform(2.0, 4.5) for _ in range(3)]
    ph = [base.uniform(0, 6.28) for _ in range(3)]
    pts = []
    n = 220
    for i in range(n):
        t = i / (n - 1)
        x = 0.08 + 0.84 * t
        y = 0.5 + sum(a[k] * math.sin(f[k] * t * 6.28 + ph[k]) for k in range(3)) * (
            0.6 + 0.4 * math.sin(t * 9)
        )
        if jit is not None:
            y += jit.uniform(-0.012, 0.012)
            x += jit.uniform(-0.004, 0.004)
        pts.append((x * w, min(max(y, 0.08), 0.92) * h))
    width = 7 if jit is None else int(6 + jit.randrange(0, 3))
    dr.line(pts, fill=(20, 30, 90, 255), width=width, joint="curve")
    # a flourish underline
    dr.line([(0.1 * w, 0.88 * h), (0.9 * w, 0.84 * h)], fill=(20, 30, 90, 255), width=4)
    if jit is not None:
        img = img.rotate(jit.uniform(-2.0, 2.0), resample=Image.Resampling.BICUBIC, expand=False)
        s = jit.uniform(0.97, 1.03)
        nw, nh = int(w * s), int(h * s)
        scaled = img.resize((nw, nh), Image.Resampling.BICUBIC)
        canvas = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        canvas.paste(scaled, ((w - nw) // 2, (h - nh) // 2), scaled)
        img = canvas
    img = img.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def render_contract(
    c: Contract,
    sig_lookup: dict[str, Contract] | None = None,
    overrides: dict[str, Any] | None = None,
) -> RenderedDoc:
    key = c.key
    truth = DocTruth(key=key, doc_type="contract")
    f = truth.set_field
    truth.table("milestones", ["description", "due_date", "amount"])
    cv = DocCanvas()
    ds = "long"
    cv.text(A4_W / 2, 50, c.title.upper(), size=13, font="Helvetica-Bold", align="c")
    b = cv.text(A4_W / 2, 70, f"Contract No: {c.number}", size=10, align="c")
    f("contract_number", c.number, c.number, 1, b)
    f("title", c.title, c.title, 1, cv.text(LEFT, 98, c.title, size=9, color=(0.3, 0.3, 0.3)))
    b = cv.text(LEFT, 120, f"This Agreement is made on {fmt_date(c.contract_date, ds)}", size=9.5)
    f("contract_date", c.contract_date.isoformat(), fmt_date(c.contract_date, ds), 1, b)
    cv.text(LEFT, 146, "BETWEEN", size=9, font="Helvetica-Bold")
    b = cv.text(LEFT, 162, f"(1) {c.seller.name} (UEN {c.seller.uen}), the Seller;", size=9.5)
    f("seller_name", c.seller.name, c.seller.name, 1, b)
    f("seller_uen", c.seller.uen, c.seller.uen, 1, b)
    cv.text(LEFT, 180, "AND", size=9, font="Helvetica-Bold")
    b = cv.text(LEFT, 196, f"(2) {c.buyer.name} (UEN {c.buyer.uen}), the Buyer.", size=9.5)
    f("buyer_name", c.buyer.name, c.buyer.name, 1, b)
    f("buyer_uen", c.buyer.uen, c.buyer.uen, 1, b)
    y = 232.0
    cv.text(LEFT, y, "1. Scope", size=10, font="Helvetica-Bold")
    for i, line in enumerate(
        [
            "The Seller shall supply, and the Buyer shall purchase, industrial fasteners and related",
            "supplies on the terms of this Agreement, in quantities ordered by the Buyer by purchase order.",
        ]
    ):
        cv.text(LEFT, y + 16 + i * 13, line, size=9)
    y += 70
    cv.text(LEFT, y, "2. Contract value", size=10, font="Helvetica-Bold")
    b = cv.text(
        LEFT,
        y + 16,
        f"The total value of this Agreement is S$ {fmt_amount(c.value)} (exclusive of GST).",
        size=9.5,
    )
    f("contract_value", money_str(c.value), fmt_amount(c.value), 1, b)
    f("currency", "SGD", "S$", 1, b)
    y += 52
    cv.text(LEFT, y, "3. Term", size=10, font="Helvetica-Bold")
    b = cv.text(
        LEFT, y + 16, f"This Agreement takes effect on {fmt_date(c.effective, ds)}", size=9.5
    )
    f("effective_date", c.effective.isoformat(), fmt_date(c.effective, ds), 1, b)
    b = cv.text(LEFT, y + 30, f"and continues until {fmt_date(c.end, ds)}.", size=9.5)
    f("end_date", c.end.isoformat(), fmt_date(c.end, ds), 1, b)
    y += 66
    cv.text(LEFT, y, "4. Payment", size=10, font="Helvetica-Bold")
    b = cv.text(LEFT, y + 16, f"Payment terms: {c.payment_terms}.", size=9.5)
    f("payment_terms", c.payment_terms, c.payment_terms, 1, b)
    cv.footer("Page 1 of 2")
    cv.new_page()
    cv.text(LEFT, 50, c.title.upper() + " (continued)", size=11, font="Helvetica-Bold")
    y = 80.0
    clauses = [
        (
            "5. Delivery",
            "Goods are delivered to the Buyer's nominated address; risk passes on delivery.",
        ),
        ("6. Warranty", "The Seller warrants the goods are free from defects for twelve months."),
        (
            "7. Termination",
            "Either party may terminate on thirty days' written notice for material breach.",
        ),
        ("8. Governing law", "This fictitious Agreement is a specimen for software testing only."),
    ]
    for head, body in clauses:
        cv.text(LEFT, y, head, size=10, font="Helvetica-Bold")
        cv.text(LEFT, y + 15, body, size=9)
        y += 46
    y = max(y + 20, 330)
    cv.text(LEFT, y, "SIGNED by the parties:", size=9.5, font="Helvetica-Bold")
    y += 24
    reuse = None
    if c.sig_reuse_from and sig_lookup and c.sig_reuse_from in sig_lookup:
        reuse = sig_lookup[c.sig_reuse_from]
    s1_seed = reuse.sig_seller_seed if reuse else c.sig_seller_seed
    s2_seed = reuse.sig_buyer_seed if reuse else c.sig_buyer_seed
    # reuse_signature (tamper T34): the images of the source contract, jitter included, pasted
    # pixel-identical; honest uses re-jitter per ``(contract, role)``
    use_a = f"{(reuse.key if reuse else key)}:seller"
    use_b = f"{(reuse.key if reuse else key)}:buyer"
    cv.text(LEFT, y, "For the Seller", size=9, font="Helvetica-Bold")
    cv.text(310, y, "For the Buyer", size=9, font="Helvetica-Bold")
    bs1 = cv.image(signature_png(s1_seed, use_a), LEFT, y + 14, 170, 56)
    bs2 = cv.image(signature_png(s2_seed, use_b), 310, y + 14, 170, 56)
    cv.line(LEFT, y + 76, LEFT + 200, y + 76, 0.6)
    cv.line(310, y + 76, 510, y + 76, 0.6)
    b = cv.text(LEFT, y + 82, c.signatory_seller, size=9)
    f("signatory_seller", c.signatory_seller, c.signatory_seller, 2, b)
    b = cv.text(310, y + 82, c.signatory_buyer, size=9)
    f("signatory_buyer", c.signatory_buyer, c.signatory_buyer, 2, b)
    cv.text(LEFT, y + 96, "Director", size=8.5)
    cv.text(310, y + 96, "Authorised signatory", size=8.5)
    truth.extras["signature_boxes"] = {"seller": bs1, "buyer": bs2}
    cv.footer("Page 2 of 2")
    raw = cv.finish()
    ov = overrides or {}
    pdf = finalize_pdf(
        raw,
        producer=str(ov.get("producer", "Mock Ledger 5.0")),
        creator=str(ov.get("creator", "Mock Ledger Desktop")),
        title=f"Contract {c.number}",
        doc_date=c.contract_date,
    )
    return RenderedDoc(
        key=key,
        doc_type="contract",
        stem=f"contract_{c.n}",
        pdf=pdf,
        page_labels=[PageLabel("contract", key), PageLabel("contract", key)],
        truth=truth,
        doc_date=c.contract_date,
        page_start=1,
        page_end=2,
    )
