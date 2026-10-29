"""Certificate of incorporation (mock registry style) renderer."""

from __future__ import annotations

from typing import Any

from .kyc_draw import GREY, LEFT, PRODUCER_PORTAL, finish, kv
from .kyc_view import view_of
from .render_common import A4_W, DocCanvas, DocTruth, PageLabel, RenderedDoc, fmt_date
from .world import World

ISSUER = "Mock Registry of Companies (specimen)"


def certificate_number(uen: str) -> str:
    return f"COI-{uen}"


def render_certificate(w: World, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = "certificate_of_incorporation:-"
    truth = DocTruth(key=key, doc_type="certificate_of_incorporation")
    c = w.company
    cv = DocCanvas()
    over = view_of(w).cert_over
    cv.rect(LEFT - 12, 70, A4_W - 2 * (LEFT - 12), 600, width=1.4)
    cv.rect(LEFT - 6, 76, A4_W - 2 * (LEFT - 6), 588, width=0.5)
    cv.text(
        A4_W / 2, 112, "CERTIFICATE OF INCORPORATION", size=17, font="Helvetica-Bold", align="c"
    )
    cv.text(
        A4_W / 2, 136, "Companies Act (specimen) - issued by a mock registry", size=9,
        align="c", color=GREY,
    )  # fmt: skip
    cv.text(LEFT + 20, 190, "This is to certify that", size=10)
    name = over.get("company_name", c.party.name)
    box = cv.text(A4_W / 2, 214, name, size=15, font="Helvetica-Bold", align="c")
    truth.set_field("company_name", name, name, 1, box)
    cv.text(LEFT + 20, 252, "was incorporated in Singapore on the date shown below as a", size=10)
    cv.text(LEFT + 20, 268, "company of the type stated, and that it is in existence.", size=10)
    y = 318.0
    y = kv(cv, truth, y, "Company number (UEN)", "uen", c.party.uen, c.party.uen, x_value=230)
    y = kv(cv, truth, y, "Entity type", "entity_type", c.entity_type, c.entity_type, x_value=230)
    inc_raw = fmt_date(c.incorporation, "long")
    y = kv(
        cv, truth, y, "Date of incorporation", "incorporation_date", c.incorporation.isoformat(),
        inc_raw, x_value=230,
    )  # fmt: skip
    num = certificate_number(c.party.uen)
    y = kv(cv, truth, y, "Certificate number", "certificate_number", num, num, x_value=230)
    y = kv(
        cv, truth, y, "Date of issue", "issue_date", c.incorporation.isoformat(), inc_raw,
        x_value=230,
    )  # fmt: skip
    y = kv(cv, truth, y, "Issued by", "issuer", ISSUER, ISSUER, x_value=230)
    cv.line(LEFT + 20, y + 60, LEFT + 200, y + 60, 0.6)
    cv.text(LEFT + 20, y + 66, "Registrar (mock)", size=8, color=GREY)
    cv.footer("Page 1 of 1")
    pdf = finish(
        cv,
        producer=PRODUCER_PORTAL,
        title="Certificate of Incorporation",
        doc_date=w.registry_as_of,
        overrides=overrides,
    )
    return RenderedDoc(
        key=key,
        doc_type="certificate_of_incorporation",
        stem="certificate_of_incorporation",
        pdf=pdf,
        page_labels=[PageLabel("certificate_of_incorporation", key)],
        truth=truth,
        doc_date=w.registry_as_of,
        page_start=1,
        page_end=1,
    )
