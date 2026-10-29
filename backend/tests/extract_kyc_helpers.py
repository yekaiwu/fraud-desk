"""Renderers for the KYC extract tests (not collected). reportlab only: no dependency on the
generator (gen-kyc writes the real one concurrently) or on ``app.ingest``.

The layouts mimic the generator's style described in docs/test-data-and-eval.md: a grey label at the
left and its value at x=200 (profile), a label printed above its value (cards), header-anchored
tables, and a SPECIMEN footer on every page.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A4
FOOT = "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT"

Item = tuple  # (x, y_from_top, text[, align[, size[, bold[, font]]]])


def draw(pages: list[list[Item]], *, footer: bool = True) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for n, items in enumerate(pages, start=1):
        for item in items:
            if item[0] == "rect":
                _, x, y, w, h = item
                c.rect(x, PAGE_H - y - h, w, h)
                continue
            if item[0] == "line":
                _, x1, y1, x2, y2 = item
                c.line(x1, PAGE_H - y1, x2, PAGE_H - y2)
                continue
            x, y, text = item[:3]
            align = item[3] if len(item) > 3 else "l"
            size = item[4] if len(item) > 4 else 9
            bold = item[5] if len(item) > 5 else False
            font = item[6] if len(item) > 6 else ("Helvetica-Bold" if bold else "Helvetica")
            c.setFont(font, size)
            if align == "r":
                c.drawRightString(x, PAGE_H - y, text)
            elif align == "c":
                c.drawCentredString(x, PAGE_H - y, text)
            else:
                c.drawString(x, PAGE_H - y, text)
        if footer:
            c.setFont("Helvetica", 7)
            c.drawString(40, 30, FOOT)
            c.drawRightString(555, 30, f"Page {n} of {len(pages)}")
        c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------------- company profile


@dataclass
class ProfileSpec:
    name: str = "Sembrook Freight Pte. Ltd."
    uen: str = "201912345R"
    entity_type: str = "Exempt Private Company Limited by Shares"
    status: str = "Live"
    incorporated: str = "12 Mar 2019"
    address: str = "1 Raffles Quay, #12-01 One Raffles Quay"
    address2: str | None = None
    activity: str = "Freight forwarding and logistics services"
    ssic: str = "52291"
    capital: str = "S$ 100,000.00"
    as_at: str = "30 Sep 2026"
    directors: list[tuple[str, str, str, str, str]] = field(
        default_factory=lambda: [
            ("Tan Wei Ming", "S1234567D", "Singaporean", "Director", "12 Mar 2019"),
            ("Hafiz bin Abdullah", "S7654321A", "Singaporean", "Director", "01 Jun 2020"),
            ("Elena Vasquez", "X1234567", "Spanish", "Director", "15 Jan 2022"),
        ]
    )
    holders: list[tuple[str, str, str, str]] = field(
        default_factory=lambda: [
            ("Tan Wei Ming", "S1234567D", "45,000", "45.00%"),
            ("Hafiz bin Abdullah", "S7654321A", "30,000", "30.00%"),
            ("Kestrel Capital Holdings Pte. Ltd.", "201934567C", "25,000", "25.00%"),
        ]
    )
    right_numbers: bool = False


def render_profile(spec: ProfileSpec | None = None) -> bytes:
    s = spec or ProfileSpec()
    items: list[Item] = [
        (40, 42, "BUSINESS PROFILE (BizFile-style mock)", "l", 14, True),
        (
            40,
            62,
            "Company registry information - specimen layout, not an official extract",
            "l",
            8.5,
        ),
    ]
    y = 92.0
    rows = [
        ("Entity name", s.name),
        ("UEN", s.uen),
        ("Entity type", s.entity_type),
        ("Status", s.status),
        ("Incorporation date", s.incorporated),
        ("Registered address", s.address),
    ]
    for label, value in rows:
        items += [(40, y, label, "l", 8.5), (200, y, value, "l", 9.5)]
        y += 17
    if s.address2:
        items.append((200, y - 4, s.address2, "l", 9.5))
        y += 13
    for label, value in [
        ("Principal activity", s.activity),
        ("SSIC code", s.ssic),
        ("Paid-up capital", s.capital),
        ("Information as at", s.as_at),
    ]:
        items += [(40, y, label, "l", 8.5), (200, y, value, "l", 9.5)]
        y += 17
    y += 12
    items.append((40, y, "Directors", "l", 11, True))
    y += 20
    for x, h in (
        (40, "Name"),
        (250, "ID number"),
        (340, "Nationality"),
        (430, "Position"),
        (500, "Appointed"),
    ):
        items.append((x, y, h, "l", 8, True))
    y += 20
    for name, nid, nat, pos, appt in s.directors:
        for x, t in ((40, name), (250, nid), (340, nat), (430, pos), (500, appt)):
            items.append((x, y, t, "l", 8.5))
        y += 17
    y += 16
    items.append((40, y, "Shareholders", "l", 11, True))
    y += 20
    if s.right_numbers:
        hdr = [
            (40, "Name", "l"),
            (300, "ID / UEN", "l"),
            (480, "Shares", "r"),
            (559, "Percent", "r"),
        ]
    else:
        hdr = [
            (40, "Name", "l"),
            (300, "ID / UEN", "l"),
            (440, "Shares", "l"),
            (559, "Percent", "r"),
        ]
    for x, h, al in hdr:
        items.append((x, y, h, al, 8, True))
    y += 20
    for name, uid, shares, pct in s.holders:
        items.append((40, y, name, "l", 8.5))
        items.append((300, y, uid, "l", 8.5))
        if s.right_numbers:
            items.append((480, y, shares, "r", 8.5))
        else:
            items.append((440, y, shares, "l", 8.5))
        items.append((559, y, pct, "r", 8.5))
        y += 17
    return draw([items])


# ---------------------------------------------------------------------------------- certificate


def render_certificate(*, labelled: bool = True, name: str = "SEMBROOK FREIGHT PTE. LTD.") -> bytes:
    items: list[Item] = [(300, 90, "CERTIFICATE OF INCORPORATION", "c", 16, True)]
    if labelled:
        y = 150.0
        for label, value in [
            ("Company name", name),
            ("UEN", "201912345R"),
            ("Entity type", "Private Company Limited by Shares"),
            ("Date of incorporation", "12 March 2019"),
            ("Certificate number", "CERT-2019-000123"),
            ("Date of issue", "14 March 2019"),
            ("Issued by", "Registrar of Companies (mock)"),
        ]:
            items += [(60, y, label, "l", 9), (230, y, value, "l", 10)]
            y += 20
    else:
        lines = [
            f"This is to certify that {name} (UEN 201912345R),",
            "is incorporated in Singapore on 12 March 2019 as a private company",
            "limited by shares under the Companies Act (mock).",
        ]
        for i, line in enumerate(lines):
            items.append((60, 160 + 16 * i, line, "l", 10))
    return draw([items])


# ---------------------------------------------------------------------------------- register


def render_register(
    *,
    holders: list[tuple[str, str, str, str, str, str]] | None = None,
    total: str | None = "100,000",
    right_numbers: bool = True,
    pages: int = 1,
) -> bytes:
    hs = holders or [
        ("Tan Wei Ming", "S1234567D", "45,000", "45.00%", "Ordinary", "12 Mar 2019"),
        ("Hafiz bin Abdullah", "S7654321A", "30,000", "30.00%", "Ordinary", "01 Jun 2020"),
        (
            "Kestrel Capital Holdings Pte. Ltd.",
            "201934567C",
            "25,000",
            "25.00%",
            "Ordinary",
            "15 Jan 2022",
        ),
    ]
    out: list[list[Item]] = []
    chunks = [hs] if pages == 1 else [hs[:2], hs[2:]]
    for pno, chunk in enumerate(chunks, start=1):
        items: list[Item] = [(40, 42, "REGISTER OF MEMBERS (mock)", "l", 14, True)]
        y = 80.0
        if pno == 1:
            for label, value in [
                ("Company name", "Sembrook Freight Pte. Ltd."),
                ("UEN", "201912345R"),
                ("Register date", "30 Sep 2026"),
                ("Currency", "SGD"),
            ]:
                items += [(40, y, label, "l", 8.5), (200, y, value, "l", 9.5)]
                y += 17
            y += 12
        cols = (
            [
                (40, "Name", "l"),
                (230, "ID / UEN", "l"),
                (370, "No. of Shares", "r"),
                (420, "%", "r"),
                (440, "Class", "l"),
                (500, "Date Acquired", "l"),
            ]
            if right_numbers
            else [
                (40, "Name", "l"),
                (230, "ID / UEN", "l"),
                (300, "No. of Shares", "l"),
                (385, "%", "l"),
                (440, "Class", "l"),
                (500, "Date Acquired", "l"),
            ]
        )
        for x, h, al in cols:
            items.append((x, y, h, al, 8, True))
        y += 20
        for name, uid, shares, pct, cls, acq in chunk:
            if right_numbers:
                row = [
                    (40, name, "l"),
                    (230, uid, "l"),
                    (370, shares, "r"),
                    (420, pct, "r"),
                    (440, cls, "l"),
                    (500, acq, "l"),
                ]
            else:
                row = [
                    (40, name, "l"),
                    (230, uid, "l"),
                    (300, shares, "l"),
                    (385, pct, "l"),
                    (440, cls, "l"),
                    (500, acq, "l"),
                ]
            for x, t, al in row:
                items.append((x, y, t, al, 8.5))
            y += 17
        if pno == len(chunks) and total is not None:
            y += 6
            items.append((40, y, "Total", "l", 8.5, True))
            items.append(
                (370 if right_numbers else 300, y, total, "r" if right_numbers else "l", 8.5, True)
            )
            items.append(
                (
                    420 if right_numbers else 385,
                    y,
                    "100.00%",
                    "r" if right_numbers else "l",
                    8.5,
                    True,
                )
            )
        out.append(items)
    return draw(out)


# ---------------------------------------------------------------------------------- SoF declaration


def render_sof(*, labelled: bool = True) -> bytes:
    items: list[Item] = [(40, 60, "SOURCE OF FUNDS DECLARATION", "l", 14, True)]
    body = [
        "I, Tan Wei Ming, a director of Tanjong Ridge Trading Pte. Ltd., declare that the amount of",
        "S$1,000,000.00 represents sales proceeds from Orchid Bay Holdings Pte. Ltd. under invoice",
        "INV-2026-0117 received between 01 Jul 2026 and 31 Aug 2026 into account 123-456-789-0.",
    ]
    if labelled:
        y = 110.0
        for label, value in [
            ("Declarant", "Tan Wei Ming"),
            ("Company", "Tanjong Ridge Trading Pte. Ltd."),
            ("UEN", "201845678K"),
            ("Declaration date", "02 Sep 2026"),
            ("Declared amount", "S$ 1,000,000.00"),
            ("Source of funds", "Sales proceeds"),
            ("Counterparty", "Orchid Bay Holdings Pte. Ltd."),
            ("Invoice reference", "INV-2026-0117"),
            ("Period", "01 Jul 2026 to 31 Aug 2026"),
            ("Receiving account", "123-456-789-0"),
        ]:
            items += [(40, y, label, "l", 8.5), (200, y, value, "l", 9.5)]
            y += 17
        y += 14
    else:
        items.append((40, 100, "Date: 02 Sep 2026", "l", 9))
        y = 140.0
    for i, line in enumerate(body):
        items.append((40, y + 13 * i, line, "l", 9))
    return draw([items])


# ---------------------------------------------------------------------------------- IDs


def render_nric(
    *,
    id_number: str = "S1234567D",
    name: str = "TAN WEI MING",
    dob: str = "12 Mar 1985",
    expiry: str | None = None,
) -> bytes:
    items: list[Item] = [
        (90, 70, "IDENTITY CARD SCAN (specimen)", "l", 9),
        (90, 106, "SINGAPORE IDENTITY CARD (MOCK)", "l", 12, True),
    ]
    y = 140.0
    for label, value in [
        ("NRIC No.", id_number),
        ("Name", name),
        ("Date of birth", dob),
        ("Sex", "M"),
        ("Nationality", "SINGAPORE CITIZEN"),
    ]:
        items += [(90, y, label, "l", 7.5), (90, y + 10, value, "l", 10, True)]
        y += 30
    items += [
        (90, 326, "Date of issue", "l", 7.5),
        (90, 336, "13 Aug 2015", "l", 9, True),
        (240, 326, "Address", "l", 7.5),
        (240, 336, "10 Anson Road #05-01 Singapore 079903", "l", 8),
    ]
    if expiry:
        items += [(90, 360, "Expiry date", "l", 7.5), (90, 370, expiry, "l", 9, True)]
    return draw([items])


MRZ1 = "P<SGPTAN<<WEI<MING".ljust(44, "<")
MRZ2 = "K1234567<4SGP8503127M3109158".ljust(42, "<") + "06"


def render_passport(*, mrz1: str = MRZ1, mrz2: str = MRZ2, with_labels: bool = True) -> bytes:
    items: list[Item] = [(60, 70, "PASSPORT (specimen)", "l", 12, True)]
    if with_labels:
        y = 110.0
        for label, value in [
            ("Surname", "TAN"),
            ("Given names", "WEI MING"),
            ("Passport No.", "K1234567"),
            ("Nationality", "SGP"),
            ("Issuing country", "SGP"),
            ("Date of birth", "12 Mar 1985"),
            ("Sex", "M"),
            ("Date of issue", "16 Sep 2021"),
            ("Date of expiry", "15 Sep 2031"),
        ]:
            items += [(60, y, label, "l", 7.5), (60, y + 10, value, "l", 10, True)]
            y += 30
    items += [
        (60, 700, mrz1, "l", 11, False, "Courier"),
        (60, 716, mrz2, "l", 11, False, "Courier"),
    ]
    return draw([items])


# ---------------------------------------------------------------------------------- ownership chart


@dataclass
class Box:
    cx: float
    top: float
    lines: list[str]
    width: float = 160.0


def render_chart(
    boxes: list[Box], *, threshold: str | None = "UBO threshold: 25.00%", arrows: bool = True
) -> bytes:
    items: list[Item] = [(300, 50, "OWNERSHIP STRUCTURE CHART", "c", 13, True)]
    if threshold:
        items.append((40, 70, threshold, "l", 8))
    items.append((420, 70, "As at 30 Sep 2026", "l", 8))
    for b in boxes:
        items.append(("rect", b.cx - b.width / 2, b.top, b.width, 12 * len(b.lines) + 14))
        for i, line in enumerate(b.lines):
            items.append((b.cx, b.top + 14 + 12 * i, line, "c", 8.5, i == 0))
    if arrows:
        top = boxes[0]
        for b in boxes[1:]:
            items.append(("line", top.cx, top.top + 12 * len(top.lines) + 14, b.cx, b.top))
    return draw([items])


# ---------------------------------------------------------------------------------- shared test plumbing


def settings(tmp_path, mode: str = "cascade", **kw):  # noqa: ANN001, ANN003, ANN201
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="fake",
        extractor_mode=mode,
        **kw,
    )


def text_layer(doc_type: str):  # noqa: ANN201
    from app.extract.registry import discover, get_extractors

    discover()
    return get_extractors(doc_type)["text_layer"]


def parse(pdf: bytes, doc_type: str):  # noqa: ANN201
    """Text-layer extraction of a one-document PDF: ``(result, ctx, doc)``."""
    from tests.extract_helpers import make_case

    ctx, doc, _ = make_case(pdf, doc_type)
    return text_layer(doc_type).extract(doc, ctx), ctx, doc


def values(cells) -> dict:  # noqa: ANN001
    return {k: c.value for k, c in cells.items()}


def row_values(table) -> list[dict]:  # noqa: ANN001
    return [values(r.cells) for r in table.rows]


def assert_all_boxed_and_verified(result) -> None:  # noqa: ANN001
    """Every non-empty text-layer cell points at a box and is verified (the clean-case contract)."""
    cells = list(result.fields.values())
    cells += [c for t in result.tables.values() for r in t.rows for c in r.cells.values()]
    for c in cells:
        if c.value is not None or c.raw is not None:
            if c.source == "derived":
                continue
            assert c.box is not None and c.verified is True, c


# canonical boxes of the standard chart (importable by other test files)
APP_BOX = Box(300, 100, ["Sembrook Freight Pte. Ltd.", "UEN 201912345R"], 200)
TAN_BOX = Box(110, 230, ["Tan Wei Ming", "Individual", "45.00%", "ID S1234567D", "UBO"])
HAFIZ_BOX = Box(300, 230, ["Hafiz bin Abdullah", "30.00%"])


# ---------------------------------------------------------------------------------- generator-style layouts
# Replicas (coordinates and fonts) of the layouts gen-kyc's renderers draw, built here so the tests do
# not import the generator. If gen-kyc changes a layout, W3 reconciles it against these.


def render_gen_chart() -> bytes:
    """Boxes with name / type / ID label + id / big percentage with a UBO badge at the right / a
    ``Held in <parent>`` line; a threshold line, a 'Chart as at' line, a long subtitle and notes."""
    H = 84.0
    items: list[Item] = [
        (40, 42, "OWNERSHIP STRUCTURE CHART", "l", 14, True),
        (40, 62, "Beneficial ownership disclosure - mock layout, not an official form", "l", 8.5),
        (555, 88, "Chart as at 30 Sep 2026", "r", 9),
        (40, 88, "UBO threshold", "l", 8.5),
        (110, 88, "25.00%", "l", 9.5, True),
        ("rect", 195, 150, 220, 52),
        (297.6, 160, "Sembrook Freight Pte. Ltd.", "c", 11, True),
        (257.6, 180, "UEN", "c", 7),
        (299.6, 179, "201912345R", "l", 9),
    ]
    xs = [43.8, 213.8, 383.8]
    direct = [
        ("Tan Wei Ming", "Individual", "S1234567D", "45.00%", True, None),
        ("Hafiz bin Abdullah", "Individual", "S7654321A", "30.00%", False, None),
        ("Kestrel Capital Holdings Pte. Ltd.", "Company", "201934567C", "25.00%", True, None),
    ]
    for x, (name, typ, uid, pct, ubo, parent) in zip(xs, direct, strict=True):
        items += _gen_box(x, 290, H, name, typ, uid, pct, ubo, parent)
    items += _gen_box(
        383.8,
        450,
        98.0,
        "Lim Wei Jie",
        "Individual",
        "S2345678A",
        "100.00%",
        True,
        "Kestrel Capital Holdings Pte. Ltd.",
    )
    items += [
        (40, 620, "Percentages are shares held directly in the entity above the box.", "l", 8),
        (40, 634, "UBO = beneficial owner at or above the threshold shown.", "l", 8),
    ]
    return draw([items])


def _gen_box(x, y, h, name, typ, uid, pct, ubo, parent) -> list[Item]:  # noqa: ANN001
    out: list[Item] = [
        ("rect", x, y, 150.0, h),
        (x + 6, y + 14, name, "l", 9 if len(name) < 24 else 7.5, True),
        (x + 6, y + 28, typ, "l", 8),
        (x + 6, y + 40, "ID/UEN", "l", 6.5),
        (x + 40, y + 40, uid, "l", 8.5),
        (x + 6, y + 56, pct, "l", 11, True),
    ]
    if ubo:
        out += [("rect", x + 112, y + 53, 30, 14), (x + 127, y + 63, "UBO", "c", 8, True)]
    if parent:
        out += [(x + 6, y + 73, "Held in", "l", 6.5), (x + 36, y + 72, parent, "l", 7.5)]
    return out


def render_gen_register() -> bytes:
    cols = [
        ("Member", 40, "l"),
        ("ID / UEN", 238, "l"),
        ("Shares", 372, "r"),
        ("Percent", 430, "r"),
        ("Class", 440, "l"),
        ("Acquired", 555, "r"),
    ]
    items: list[Item] = [(40, 42, "REGISTER OF MEMBERS", "l", 14, True)]
    y = 92.0
    for label, value in [
        ("Company", "Sembrook Freight Pte. Ltd."),
        ("UEN", "201912345R"),
        ("Register as at", "30 Sep 2026"),
        ("Total issued shares", "100,000"),
        ("Issued capital", "S$ 500,000.00"),
    ]:
        items += [(40, y, label, "l", 8.5), (200, y, value, "l", 9.5)]
        y += 17
    y += 14
    for head, x, al in cols:
        items.append((x, y, head, al, 8, True))
    y += 22
    rows = [
        ("Tan Wei Ming", "S1234567D", "45,000", "45.00%", "Ordinary", "18 Jun 2016"),
        ("Hafiz bin Abdullah", "S7654321A", "30,000", "30.00%", "Ordinary", "18 Jun 2016"),
        (
            "Kestrel Capital Holdings Pte. Ltd.",
            "201934567C",
            "25,000",
            "25.00%",
            "Ordinary",
            "10 Jun 2018",
        ),
    ]
    for row in rows:
        for (_h, x, al), text in zip(cols, row, strict=True):
            items.append((x, y, text, al, 8.5))
        y += 17
    return draw([items])


def render_gen_certificate() -> bytes:
    items: list[Item] = [
        (297.6, 112, "CERTIFICATE OF INCORPORATION", "c", 17, True),
        (297.6, 136, "Companies Act (specimen) - issued by a mock registry", "c", 9),
        (60, 190, "This is to certify that", "l", 10),
        (297.6, 214, "Sembrook Freight Pte. Ltd.", "c", 15, True),
        (60, 252, "was incorporated in Singapore on the date shown below as a", "l", 10),
        (60, 268, "company of the type stated, and that it is in existence.", "l", 10),
    ]
    y = 318.0
    for label, value in [
        ("Company number (UEN)", "201912345R"),
        ("Entity type", "Private Company Limited by Shares"),
        ("Date of incorporation", "18 June 2016"),
        ("Certificate number", "COI-201912345R"),
        ("Date of issue", "18 June 2016"),
        ("Issued by", "Mock Registry of Companies (specimen)"),
    ]:
        items += [(40, y, label, "l", 8.5), (230, y, value, "l", 9.5)]
        y += 17
    return draw([items])


def render_gen_sof(bank: str = "UOB-STYLE MOCK BANK") -> bytes:
    desc = [
        "Proceeds from the sale of goods to Orchid Bay Holdings Pte. Ltd. under invoice",
        "INV-2026-0117, received between 01 Jul 2026 and 31 Aug 2026.",
    ]
    items: list[Item] = [
        (40, 42, "SOURCE OF FUNDS DECLARATION", "l", 14, True),
        (40, 62, "Customer declaration - mock layout, not an official form", "l", 8.5),
    ]
    y = 96.0
    for label, value in [
        ("Declarant", "Tan Wei Ming"),
        ("Company", "Tanjong Ridge Trading Pte. Ltd."),
        ("UEN", "201845678K"),
        ("Date of declaration", "02 Sep 2026"),
    ]:
        items += [(40, y, label, "l", 8.5), (210, y, value, "l", 9.5)]
        y += 18
    y += 20
    items += [(40, y, "Amount declared", "l", 8.5), (210, y, "S$ 1,000,000.00", "l", 12)]
    y += 18
    items += [(40, y, "Origin of funds", "l", 8.5), (210, y, "Sales proceeds", "l", 9.5)]
    y += 18
    items.append((40, y, "Description", "l", 8.5))
    for line in desc:
        items.append((210, y, line, "l", 9.5))
        y += 14
    y += 4
    for label, value in [
        ("Received from", "Orchid Bay Holdings Pte. Ltd."),
        ("Invoice reference", "INV-2026-0117"),
        ("Funds received from", "01 Jul 2026"),
        ("Funds received to", "31 Aug 2026"),
    ]:
        items += [(40, y, label, "l", 8.5), (210, y, value, "l", 9.5)]
        y += 18
    items += [
        (40, y, "Receiving account", "l", 8.5),
        (210, y, bank, "l", 9.5),
        (360, y, "6323451406", "l", 9.5),
        (40, y + 40, "I declare that the information above is true and complete.", "l", 9),
        (40, y + 108, "Tan Wei Ming", "l", 8.5),
    ]
    return draw([items])


def render_gen_passport() -> bytes:
    x = 70.0 + 135
    items: list[Item] = [
        (70, 70, "PASSPORT BIOGRAPHICAL PAGE (specimen)", "l", 9),
        (70, 106, "PASSPORT / PASAPORTE (MOCK)", "l", 13, True),
        (x, 140, "Type", "l", 7),
        (x, 149, "P", "l", 10, True),
        (x + 70, 140, "Country code", "l", 7),
        (x + 70, 149, "SGP", "l", 10, True),
    ]
    y = 167.0
    for label, value in [
        ("Passport No.", "K1234567"),
        ("Surname", "VASQUEZ"),
        ("Given names", "ELENA"),
        ("Nationality", "SGP"),
        ("Date of birth", "03 Feb 1988"),
        ("Sex", "F"),
        ("Date of issue", "16 Sep 2021"),
        ("Date of expiry", "15 Sep 2031"),
    ]:
        items += [(x, y, label, "l", 7), (x, y + 9, value, "l", 10, True)]
        y += 27
    items += [
        (70, 470, MRZ1, "l", 11.5, False, "Courier-Bold"),
        (70, 486, MRZ2, "l", 11.5, False, "Courier-Bold"),
    ]
    return draw([items])
