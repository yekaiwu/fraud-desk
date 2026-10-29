"""signature_reuse and crop_fingerprints (docs/forensics.md 4.6; tests 27 and 28 of section 6).

PDFs are built by hand: a text page plus a PNG "signature image" at a chosen place. No
authenticity claim is ever tested for or made.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.checks import forensics_image as fi
from app.checks.registry import CheckSkipped
from app.core.fingerprints import MemoryFingerprintStore
from app.core.models import Applicant
from app.core.testing import make_ctx, make_doc
from tests.forensics_image_factory import (
    HEADER_LOGO,
    LOWER_RIGHT,
    classification,
    file_ref,
    pdf_with_pictures,
    png_bytes,
    signature_png,
)

SUPPLIER_A = "201912345R"
PARTY_B = "201855555Z"
PARTY_C = "202011111A"


def invoice(doc_id: str, file_id: str, supplier: str = SUPPLIER_A):  # noqa: ANN201
    return make_doc("invoice", id=doc_id, file_id=file_id, fields={"supplier_uen": supplier})


def contract(doc_id: str, file_id: str, seller: str = PARTY_B, buyer: str = PARTY_C):  # noqa: ANN201
    return make_doc(
        "contract", id=doc_id, file_id=file_id, fields={"seller_uen": seller, "buyer_uen": buyer}
    )


def statement(doc_id: str, file_id: str, issuer: str = "dbs"):  # noqa: ANN201
    return make_doc("bank_statement", id=doc_id, file_id=file_id, issuer=classification(issuer))


def pdf_file(file_id: str, pictures, **kw):  # noqa: ANN001, ANN003, ANN201
    pdf = pdf_with_pictures(pictures, **kw)
    return file_ref(file_id, "application/pdf", pages=kw.get("pages", 1), scan=False), pdf


def case(docs, files, **kw):  # noqa: ANN001, ANN003, ANN201
    return make_ctx(docs, files=files, **kw)


# --------------------------------------------------------------------------------------
# test 27: the same image on documents of different parties
# --------------------------------------------------------------------------------------


def test_same_signature_on_an_invoice_and_a_contract_of_other_parties_is_flagged() -> None:
    sig = signature_png(1)
    ctx = case(
        [invoice("d1", "f1"), contract("d2", "f2")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(sig, LOWER_RIGHT)])],
    )
    flags = fi.signature_reuse(ctx)
    assert len(flags) == 1
    flag = flags[0]
    assert (
        flag.rule == "signature_reuse" and flag.severity == "medium" and flag.stage == "forensics"
    )
    assert flag.document_ids == ["d1", "d2"]
    first = fi.crop_fingerprints(ctx)[0]
    assert flag.id == f"signature_reuse.d1+d2.-~{first.phash[:8]}"
    assert len(first.phash) == 64  # 256 bits as hex
    assert "same signature/stamp image on invoice d1 (supplier 201912345R)" in flag.evidence
    assert "contract d2 (parties 201855555Z, 202011111A)" in flag.evidence
    assert flag.evidence.endswith("exact pixel match")
    assert flag.region is not None and len(flag.extra_regions) == 1
    assert flag.region.label == "signature/stamp image"
    # lower part of the page (box at y = 640 of 842)
    assert flag.region.y == pytest.approx(640 / 842, abs=0.01)
    assert "genuine" in flag.explanation and "nothing about whether" in flag.explanation


SELLER_SIG = (60.0, 640.0, 120.0, 60.0)
BUYER_SIG = (380.0, 640.0, 120.0, 60.0)


def signed_contract(doc_id: str, file_id: str, buyer: str):  # noqa: ANN201
    """A contract whose signatory labels carry boxes under the seller and the buyer signature."""
    from app.core.models import Cell, Region

    doc = contract(doc_id, file_id, seller=PARTY_B, buyer=buyer)
    for name, x in (("signatory_seller", 60 / 595), ("signatory_buyer", 380 / 595)):
        box = Region(file_id=file_id, page=1, x=x, y=710 / 842, w=0.2, h=0.02)
        doc.fields[name] = Cell(value=name, raw=name, kind="text", box=box)
    return doc


def contract_file(file_id: str, seller_sig: bytes, buyer_sig: bytes):  # noqa: ANN201
    return pdf_file(file_id, [(seller_sig, SELLER_SIG), (buyer_sig, BUYER_SIG)])


def test_a_buyers_signature_pasted_onto_a_contract_of_another_buyer_is_flagged() -> None:
    """Every contract names the applicant as seller, so the party sets always intersect: the
    crop has to be attributed to the side that signed (T34)."""
    seller, buyer = signature_png(11), signature_png(12)
    ctx = case(
        [signed_contract("d1", "f1", PARTY_C), signed_contract("d2", "f2", SUPPLIER_A)],
        [contract_file("f1", seller, buyer), contract_file("f2", seller, buyer)],
    )
    flags = fi.signature_reuse(ctx)
    assert len(flags) == 1 and flags[0].document_ids == ["d1", "d2"]
    assert "exact pixel match" in flags[0].evidence


def test_the_applicants_own_repeated_signature_is_not_reuse() -> None:
    seller = signature_png(11)
    ctx = case(
        [signed_contract("d1", "f1", PARTY_C), signed_contract("d2", "f2", SUPPLIER_A)],
        [
            contract_file("f1", seller, signature_png(12)),
            contract_file("f2", seller, signature_png(13)),
        ],
    )
    assert fi.signature_reuse(ctx) == []


def test_the_same_buyer_signing_two_contracts_is_not_reuse() -> None:
    seller, buyer = signature_png(11), signature_png(12)
    ctx = case(
        [signed_contract("d1", "f1", PARTY_C), signed_contract("d2", "f2", PARTY_C)],
        [contract_file("f1", seller, buyer), contract_file("f2", seller, buyer)],
    )
    assert fi.signature_reuse(ctx) == []


def test_slightly_blurred_copy_is_flagged_as_near_identical() -> None:
    from PIL import ImageFilter

    sig = signature_png(2)
    blurred = png_bytes(
        Image.open(io.BytesIO(sig)).convert("RGBA").filter(ImageFilter.GaussianBlur(0.7))
    )
    ctx = case(
        [invoice("d1", "f1"), contract("d2", "f2")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(blurred, LOWER_RIGHT)])],
    )
    flags = fi.signature_reuse(ctx)
    assert len(flags) == 1
    assert "near-identical (hamming" in flags[0].evidence
    assert flags[0].evidence.endswith("/256)")


def test_different_signatures_are_not_a_match() -> None:
    ctx = case(
        [invoice("d1", "f1"), contract("d2", "f2")],
        [
            pdf_file("f1", [(signature_png(1), LOWER_RIGHT)]),
            pdf_file("f2", [(signature_png(9), LOWER_RIGHT)]),
        ],
    )
    assert fi.signature_reuse(ctx) == []


def test_one_image_at_two_places_on_a_contract_is_reuse() -> None:
    sig = signature_png(3)
    second = (60.0, 640.0, 120.0, 60.0)
    ctx = case([contract("d1", "f1")], [pdf_file("f1", [(sig, LOWER_RIGHT), (sig, second)])])
    flags = fi.signature_reuse(ctx)
    assert len(flags) == 1 and flags[0].document_ids == ["d1"]
    assert len(flags[0].extra_regions) == 1


# --------------------------------------------------------------------------------------
# test 28: honest quirks (filters and the party rule)
# --------------------------------------------------------------------------------------


def test_same_logo_in_the_page_header_is_not_a_candidate() -> None:
    logo = signature_png(4)
    ctx = case(
        [
            invoice("d1", "f1", "201900001A"),
            invoice("d2", "f2", "201900002B"),
            contract("d3", "f3"),
        ],
        [pdf_file(f"f{i}", [(logo, HEADER_LOGO)]) for i in (1, 2, 3)],
    )
    assert fi.crop_fingerprints(ctx) == []
    with pytest.raises(CheckSkipped):
        fi.signature_reuse(ctx)


def test_same_stamp_on_two_statements_of_one_bank_is_silent() -> None:
    stamp = signature_png(5)
    ctx = case(
        [statement("d1", "f1"), statement("d2", "f2")],
        [pdf_file("f1", [(stamp, LOWER_RIGHT)]), pdf_file("f2", [(stamp, LOWER_RIGHT)])],
    )
    assert fi.signature_reuse(ctx) == []


def test_same_stamp_on_statements_of_different_banks_is_still_not_reuse() -> None:
    # two bank statements are never compared with each other (the stamp of a bank group)
    stamp = signature_png(5)
    ctx = case(
        [statement("d1", "f1", "dbs"), statement("d2", "f2", "uob")],
        [pdf_file("f1", [(stamp, LOWER_RIGHT)]), pdf_file("f2", [(stamp, LOWER_RIGHT)])],
    )
    assert fi.signature_reuse(ctx) == []


def test_same_signature_on_two_invoices_of_one_supplier_is_silent() -> None:
    sig = signature_png(6)
    ctx = case(
        [invoice("d1", "f1"), invoice("d2", "f2")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(sig, LOWER_RIGHT)])],
    )
    assert fi.signature_reuse(ctx) == []


def test_shared_party_between_contract_and_invoice_is_silent() -> None:
    sig = signature_png(7)
    ctx = case(
        [invoice("d1", "f1", PARTY_B), contract("d2", "f2")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(sig, LOWER_RIGHT)])],
    )
    assert fi.signature_reuse(ctx) == []


def test_documents_of_unknown_parties_are_never_flagged() -> None:
    sig = signature_png(8)
    ctx = case(
        [
            make_doc("company_profile", id="d1", file_id="f1"),
            make_doc("other", id="d2", file_id="f2"),
        ],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(sig, LOWER_RIGHT)])],
    )
    assert fi.signature_reuse(ctx) == []


def test_invoice_with_no_supplier_uen_has_unknown_parties() -> None:
    sig = signature_png(8)
    ctx = case(
        [invoice("d1", "f1", ""), contract("d2", "f2")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(sig, LOWER_RIGHT)])],
    )
    assert fi.signature_reuse(ctx) == []


def test_candidate_filters_size_aspect_and_ink() -> None:
    sig = signature_png(1)
    tiny = (400.0, 700.0, 20.0, 10.0)  # area far below 0.2% of the page
    huge = (30.0, 330.0, 540.0, 480.0)  # above 15% of the page
    thin = (60.0, 700.0, 400.0, 20.0)  # aspect 20
    ctx = case([invoice("d1", "f1")], [pdf_file("f1", [(sig, tiny), (sig, huge), (sig, thin)])])
    assert fi.crop_fingerprints(ctx) == []
    blank = png_bytes(Image.new("RGBA", (120, 60), (255, 255, 255, 0)))
    ctx = case([invoice("d1", "f1")], [pdf_file("f1", [(blank, LOWER_RIGHT)])])
    assert fi.crop_fingerprints(ctx) == []  # no ink at all


def test_same_xref_on_several_pages_of_one_document_counts_once() -> None:
    import pymupdf

    doc = pymupdf.open()
    first = doc.new_page(width=595, height=842)
    first.insert_image(pymupdf.Rect(380, 640, 500, 700), stream=signature_png(3))
    xref = first.get_images()[0][0]
    second = doc.new_page(width=595, height=842)
    second.insert_image(pymupdf.Rect(380, 640, 500, 700), xref=xref)
    ref = file_ref("f1", "application/pdf", pages=2, scan=False)
    ctx = make_ctx(
        [make_doc("contract", id="d1", file_id="f1", page_end=2)], files=[(ref, doc.tobytes())]
    )
    assert len(fi.crop_fingerprints(ctx)) == 1
    assert fi.signature_reuse(ctx) == []  # the repeated page image is not self-reuse


# --------------------------------------------------------------------------------------
# Cross-case lookups and crop_fingerprints
# --------------------------------------------------------------------------------------


def test_crop_fingerprints_are_pure_and_repeatable() -> None:
    sig = signature_png(1)
    store = MemoryFingerprintStore()
    ctx = case(
        [invoice("d1", "f1"), contract("d2", "f2")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)]), pdf_file("f2", [(sig, LOWER_RIGHT)])],
        fingerprints=store,
    )
    first = fi.crop_fingerprints(ctx)
    again = fi.crop_fingerprints(ctx)
    assert first == again and len(first) == 2
    assert [c.document_id for c in first] == ["d1", "d2"]
    assert first[0].phash == first[1].phash and first[0].exact == first[1].exact
    assert first[0].file_id == "f1" and first[0].page == 1
    fi.signature_reuse(ctx)
    assert store.lookup("crop_phash", first[0].phash) == []  # the check never records


def test_cross_case_hit_from_another_applicant_is_medium() -> None:
    sig = signature_png(1)
    store = MemoryFingerprintStore()
    ctx = case(
        [invoice("d1", "f1")],
        [pdf_file("f1", [(sig, LOWER_RIGHT)])],
        fingerprints=store,
        applicant=Applicant(name="Acme", uen=SUPPLIER_A),
    )
    phash = fi.crop_fingerprints(ctx)[0].phash
    store.record(
        "crop_phash", phash, case_id="c_other", document_id="d9", meta={"applicant_uen": PARTY_B}
    )
    flags = fi.signature_reuse(ctx)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.severity == "medium" and flag.document_ids == ["d1"]
    assert flag.id == f"signature_reuse.d1.-~xcase_{phash[:8]}"
    assert "c_other" in flag.evidence and "d9" in flag.evidence
    assert flag.region is not None


def test_cross_case_hit_from_the_same_applicant_or_unknown_is_info() -> None:
    sig = signature_png(1)
    for meta in ({"applicant_uen": SUPPLIER_A}, {}):
        store = MemoryFingerprintStore()
        ctx = case(
            [invoice("d1", "f1")],
            [pdf_file("f1", [(sig, LOWER_RIGHT)])],
            fingerprints=store,
            applicant=Applicant(name="Acme", uen=SUPPLIER_A),
        )
        phash = fi.crop_fingerprints(ctx)[0].phash
        store.record("crop_phash", phash, case_id="c_other", document_id="d9", meta=meta)
        flags = fi.signature_reuse(ctx)
        assert [f.severity for f in flags] == ["info"], meta


def test_a_hit_in_the_same_case_is_not_a_cross_case_hit() -> None:
    sig = signature_png(1)
    store = MemoryFingerprintStore()
    ctx = case([invoice("d1", "f1")], [pdf_file("f1", [(sig, LOWER_RIGHT)])], fingerprints=store)
    phash = fi.crop_fingerprints(ctx)[0].phash
    store.record("crop_phash", phash, case_id=ctx.case.id, document_id="d1", meta={})
    assert fi.signature_reuse(ctx) == []


# --------------------------------------------------------------------------------------
# Skips and failures
# --------------------------------------------------------------------------------------


def test_skipped_without_any_pdf_or_candidate() -> None:
    with pytest.raises(CheckSkipped):
        fi.signature_reuse(make_ctx())
    ctx = case([invoice("d1", "f1")], [pdf_file("f1", [])])
    with pytest.raises(CheckSkipped):
        fi.signature_reuse(ctx)


def test_unreadable_pdf_is_an_error_for_the_run() -> None:
    ref = file_ref("f1", "application/pdf", pages=1, scan=False)
    ctx = make_ctx([invoice("d1", "f1")], files=[(ref, b"definitely not a pdf")])
    with pytest.raises(RuntimeError, match="all candidate files unreadable"):
        fi.signature_reuse(ctx)


def test_party_keys_table() -> None:
    assert fi.party_keys(invoice("d1", "f1")) == {SUPPLIER_A}
    assert fi.party_keys(contract("d1", "f1")) == {PARTY_B, PARTY_C}
    assert fi.party_keys(statement("d1", "f1", "ocbc")) == {"bank:ocbc"}
    po = make_doc("purchase_order", id="d1", fields={"buyer_uen": PARTY_C})
    assert fi.party_keys(po) == {PARTY_C}
    sof = make_doc("source_of_funds_declaration", id="d1", fields={"declarant_name": "Tan Ah Kow"})
    assert len(fi.party_keys(sof)) == 1
    assert fi.party_keys(make_doc("company_profile", id="d1")) == set()
