"""Renderers: statement layouts, text layers, producers, Q09, signatures (T-gen-loan-05/06/18)."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import date
from decimal import Decimal

import imagehash
import pikepdf
import pymupdf
import pytest
from PIL import Image

from app.core.textlayout import page_marker
from scripts._gen import render_common as rc
from scripts._gen import world
from scripts._gen.render_contract import render_contract, signature_png
from scripts._gen.render_fs import render_fs
from scripts._gen.render_id import render_id
from scripts._gen.render_invoice import render_invoice, render_po
from scripts._gen.render_profile import render_profile
from scripts._gen.render_statement import LAYOUT_SPEC, render_statement
from scripts._gen.schema import LAYOUT_C3, LAYOUT_PRODUCER

LAYOUTS = ["dbs_style", "ocbc_style", "uob_style", "software"]
DATE_RE = {
    "dbs_style": r"\b\d{2} [A-Z][a-z]{2} \d{4}\b",
    "ocbc_style": r"\b\d{2}/\d{2}/\d{4}\b",
    "uob_style": r"\b\d{2}-[A-Z][a-z]{2}-\d{4}\b",
    "software": r"\b\d{4}-\d{2}-\d{2}\b",
}
D = Decimal


def _render(layout: str, idx: int = 4):  # noqa: ANN202
    w = world.build_world("loan", layout=layout)
    return w, render_statement(w.statements[idx], w.quirks)


def _text(pdf: bytes, page: int = 0) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as d:
        return d[page].get_text()


@pytest.mark.parametrize("layout", LAYOUTS)
def test_layouts_have_a_text_layer_and_their_own_look(layout: str) -> None:
    w, r = _render(layout)
    text = _text(r.pdf)
    assert len(text.split()) > 80
    assert LAYOUT_SPEC[layout]["title"] in text
    assert re.search(DATE_RE[layout], text)
    for other, pat in DATE_RE.items():
        if other != layout and other != "software":
            assert not re.search(pat, text) or layout == "software", (layout, other)
    assert "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT" in text
    assert r.issuer == LAYOUT_C3[layout]
    assert page_marker(text) == (1, r.page_end)
    assert w.company.account_number in text


def test_the_four_layouts_differ_in_header_columns_and_dates() -> None:
    titles = {lay: LAYOUT_SPEC[lay]["title"] for lay in LAYOUTS}
    assert len(set(titles.values())) == 4
    assert len({len(LAYOUT_SPEC[lay]["cols"]) for lay in LAYOUTS}) >= 3
    assert len({LAYOUT_SPEC[lay]["date_style"] for lay in LAYOUTS}) == 4
    assert LAYOUT_SPEC["uob_style"]["marker"].startswith("{m} pages")
    assert [c[1] for c in LAYOUT_SPEC["ocbc_style"]["cols"]][:3] == [
        "Date",
        "Value Date",
        "Description",
    ]
    assert [c[1] for c in LAYOUT_SPEC["dbs_style"]["cols"]][2:4] == ["Withdrawals", "Deposits"]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_producer_creator_and_dates(layout: str) -> None:
    w, r = _render(layout)
    st = w.statements[4]
    with pymupdf.open(stream=r.pdf, filetype="pdf") as d:
        md = d.metadata
    assert (md["producer"], md["creator"]) == LAYOUT_PRODUCER[layout]
    assert (
        md["creationDate"] == md["modDate"] == "D:20251201090000+08'00'"
    )  # 30 Nov + 1 day, 09:00 +08:00
    assert st.period_end == date(2025, 11, 30)
    with pikepdf.open(io.BytesIO(r.pdf)) as p:
        assert set(p.docinfo.keys()) >= {
            "/Producer",
            "/Creator",
            "/CreationDate",
            "/ModDate",
            "/Title",
        }


def test_render_twice_gives_identical_bytes() -> None:
    for layout in LAYOUTS:
        _, a = _render(layout)
        _, b = _render(layout)
        assert hashlib.sha256(a.pdf).hexdigest() == hashlib.sha256(b.pdf).hexdigest()


def test_deterministic_pdf_id_via_pikepdf() -> None:
    _, a = _render("dbs_style")
    with pikepdf.open(io.BytesIO(a.pdf)) as p:
        ident = bytes(p.trailer["/ID"][0])
    assert len(ident) == 16


def test_dbs_prints_brought_forward_row_and_totals() -> None:
    w, r = _render("dbs_style")
    first = r.truth.tables["transactions"]["rows"][0]["cells"]
    assert (
        first["description"]["value"] == "BALANCE BROUGHT FORWARD"
        and first["debit"]["value"] is None
        and first["credit"]["value"] is None
    )
    assert first["balance"]["value"] == format(w.statements[4].opening, "f")
    assert r.truth.fields["total_debits"]["value"] == format(w.statements[4].total_debits, "f")


def test_other_layouts_print_or_omit_totals_as_specified() -> None:
    _, ocbc = _render("ocbc_style")
    _, uob = _render("uob_style")
    _, soft = _render("software")
    assert (
        ocbc.truth.fields["total_debits"]["value"] is None
        and soft.truth.fields["total_debits"]["value"] is None
    )
    assert uob.truth.fields["total_credits"]["value"] is not None
    assert (
        soft.truth.fields["bank_name"]["value"] is None
        and soft.truth.fields["uen"]["value"] is not None
    )


def test_uob_amount_suffix_and_overdraft_balance_text() -> None:
    w = world.build_world("loan", layout="uob_style")
    st = next(s for s in w.statements if any(r.balance < 0 for r in s.rows))
    r = render_statement(st, w.quirks)
    text = "\n".join(_text(r.pdf, i) for i in range(r.page_end))
    assert " DR" in text and " CR" in text
    neg = next(
        x for x in r.truth.tables["transactions"]["rows"] if D(x["cells"]["balance"]["value"]) < 0
    )
    assert neg["cells"]["balance"]["raw"].endswith(" DR")
    from app.core.money import parse_money

    assert parse_money(neg["cells"]["balance"]["raw"]) == D(neg["cells"]["balance"]["value"])


def test_each_page_has_marker_account_and_chain_rows() -> None:
    w, r = _render("dbs_style")
    chain = r.truth.tables["page_chain"]["rows"]
    n = r.page_end
    assert n in (2, 3) and len(chain) == n
    for i, row in enumerate(chain, start=1):
        assert row["cells"]["page_x"]["value"] == str(i) and row["cells"]["page_y"]["value"] == str(
            n
        )
        assert w.company.account_number in _text(r.pdf, i - 1)
    assert r.truth.fields["page_x_of_y"]["value"] == ",".join(f"{i}/{n}" for i in range(1, n + 1))


def test_q09_placeholder_signature_has_several_eof_and_bytes_reach_eof() -> None:
    _, r = _render("ocbc_style")
    assert r.pdf.count(b"%%EOF") >= 3
    br = rc.signature_byte_range(r.pdf)
    assert br is not None and br[0] == 0 and br[2] + br[3] == len(r.pdf)  # second range ends at EOF
    assert (
        r.pdf[br[1] : br[1] + 1] == b"<" and r.pdf[br[2] - 1 : br[2]] == b">"
    )  # the /Contents placeholder is the gap
    with pymupdf.open(stream=r.pdf, filetype="pdf") as d:
        assert len(d) == 3
    for lay in ("dbs_style", "uob_style", "software"):
        _, other = _render(lay)
        assert other.pdf.count(b"%%EOF") == 1 and rc.signature_byte_range(other.pdf) is None


def test_q12_tnc_page_after_ocbc_statements_is_boilerplate() -> None:
    _, r = _render("ocbc_style")
    assert [pl.c1 for pl in r.page_labels][-1] == "boilerplate" and r.page_labels[
        -1
    ].doc_key is None
    assert "Terms and Conditions" in _text(r.pdf, r.page_count - 1)
    assert r.page_end == r.page_count - 1  # the document proper excludes the T&C page


def test_quirk_switches_remove_q09_and_q12() -> None:
    w = world.build_world("loan", layout="ocbc_style", quirks=["Q01"])
    r = render_statement(w.statements[0], w.quirks)
    assert r.pdf.count(b"%%EOF") == 1 and r.page_count == r.page_end


# ------------------------------------------------------------------------ other documents


def test_invoice_layouts_and_paid_stamp() -> None:
    w = world.build_world("loan")
    a = next(i for i in w.invoices if i.layout == "A" and i.status == "paid")
    b = next(i for i in w.invoices if i.layout == "B")
    ra, rb = render_invoice(a), render_invoice(b)
    assert "GST (S$)" not in _text(ra.pdf) and "GST (S$)" in _text(rb.pdf)
    assert "PAID" in _text(ra.pdf) and ra.truth.fields["payment_status"]["value"] == "paid"
    assert ra.truth.fields["paid_date"]["value"] == a.paid_date.isoformat()
    assert all(r["cells"]["gst_amount"]["value"] is None for r in ra.truth.tables["lines"]["rows"])
    assert all(
        r["cells"]["gst_amount"]["value"] is not None for r in rb.truth.tables["lines"]["rows"]
    )
    with pymupdf.open(stream=ra.pdf, filetype="pdf") as d:
        assert d.metadata["producer"] == "Mock Ledger 5.0" and len(d) == 1
    assert ra.truth.fields["supplier_gst_no"]["value"] == w.company.party.gst_no
    blank = next(i for i in w.invoices if i.blank_page)
    rblank = render_invoice(blank)
    assert rblank.page_count == 2 and rblank.page_labels[1].c1 == "boilerplate"


def test_purchase_order_matches_its_invoice() -> None:
    w = world.build_world("loan")
    po = w.pos[0]
    r = render_po(po)
    assert r.truth.fields["total"]["value"] == format(
        next(i for i in w.invoices if i.po_ref == po.number).total, "f"
    )
    assert "PURCHASE ORDER" in _text(r.pdf)


def test_financial_statement_pages_scale_and_basis() -> None:
    w = world.build_world("loan")
    r25, r24 = render_fs(w.fs["FY2025"]), render_fs(w.fs["FY2024"])
    assert r25.page_count == 3 and r24.page_count == 3
    assert "Statement of Financial Position" in _text(r25.pdf, 1) and "Cash Flows" in _text(
        r25.pdf, 2
    )
    assert "S$'000" in _text(r24.pdf) and r24.truth.fields["scale_printed"]["value"] == "1000"
    assert r25.truth.fields["auditor"]["value"] == "Harbourview Assurance LLP"
    rev = r25.truth.tables["income_statement"]["rows"][0]["cells"]
    assert (
        rev["current"]["raw"] == "900,000"
        and rev["current"]["value"] == "900000.00"
        and rev["prior"]["value"] is not None
    )
    cos = r25.truth.tables["income_statement"]["rows"][1]["cells"]
    assert cos["current"]["raw"].startswith("(") and not cos["current"]["value"].startswith("-")
    un = world.build_world("loan", 1)
    ru = render_fs(un.fs["FY2025"])
    assert "Management Accounts (Unaudited)" in _text(ru.pdf)
    with pymupdf.open(stream=r25.pdf, filetype="pdf") as d:
        assert d.metadata["producer"] == "Mock Word Processor 3.1"


def test_contract_two_pages_and_signature_images() -> None:
    w = world.build_world("loan")
    r = render_contract(w.contracts[0])
    assert r.page_count == 2
    with pymupdf.open(stream=r.pdf, filetype="pdf") as d:
        assert len(d[1].get_images(full=True)) == 2 and d.metadata["producer"] == "Mock Ledger 5.0"
    assert r.truth.fields["contract_value"]["value"] == format(w.contracts[0].value, "f")
    assert set(r.truth.extras["signature_boxes"]) == {"seller", "buyer"}


def test_honest_signature_repeats_are_not_pixel_identical() -> None:
    a, b = signature_png(77, "contract:1:seller"), signature_png(77, "contract:2:seller")
    assert a != b
    ha = imagehash.phash(Image.open(io.BytesIO(a)).convert("L"), hash_size=16)
    hb = imagehash.phash(Image.open(io.BytesIO(b)).convert("L"), hash_size=16)
    assert ha - hb > 0
    assert signature_png(77, "contract:1:seller") == a  # deterministic per use
    assert signature_png(77, None) == signature_png(77, None)


def test_profile_and_national_id_render_with_text_layer() -> None:
    w = world.build_world("loan")
    prof = render_profile(w)
    assert "BUSINESS PROFILE" in _text(prof.pdf) and w.company.party.uen in _text(prof.pdf)
    assert (
        len(prof.truth.tables["directors"]["rows"]) == 2
        and len(prof.truth.tables["shareholders"]["rows"]) == 2
    )
    assert prof.truth.fields["principal_activity"]["value"].startswith("Wholesale of fasteners")
    with pymupdf.open(stream=prof.pdf, filetype="pdf") as d:
        assert d.metadata["producer"] == "Mock Registry Portal 2.0"
    idr = render_id(w.people[0], 1)
    assert w.people[0].id_number in _text(idr.pdf)
    assert (
        idr.truth.fields["id_number"]["value"] == w.people[0].id_number
        and idr.truth.fields["expiry_date"]["value"] is None
    )
    with pymupdf.open(stream=idr.pdf, filetype="pdf") as d:
        assert d.metadata["producer"] == "Mock ID Imager 1.0" and len(d[0].get_images()) == 1


def test_printed_lines_cover_every_page_with_boxes_in_range() -> None:
    _, r = _render("dbs_style")
    pages = rc.printed_lines(r.pdf)
    assert [p["page"] for p in pages] == list(range(1, r.page_end + 1))
    for p in pages:
        for ln in p["lines"]:
            assert (
                0 <= ln["x"] <= 1
                and 0 <= ln["y"] <= 1
                and ln["x"] + ln["w"] <= 1.001
                and ln["y"] + ln["h"] <= 1.001
            )
    assert any("Statement of Account" in ln["text"] for ln in pages[0]["lines"])


def test_truth_boxes_are_fractions_inside_the_page() -> None:
    w = world.build_world("loan")
    r = render_fs(w.fs["FY2025"])
    for t in r.truth.tables.values():
        for row in t["rows"]:
            for c in row["cells"].values():
                b = c["box"]
                if b:
                    assert (
                        0 <= b["x"] <= 1
                        and b["x"] + b["w"] <= 1.001
                        and b["y"] >= 0
                        and b["y"] + b["h"] <= 1.001
                    )
