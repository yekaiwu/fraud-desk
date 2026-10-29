import json
from pathlib import Path

import pytest
from stdnum.sg import uen as sg_uen

from app.core.models import Applicant, FileRef, Word
from app.core.registry import MockRegistry, load_registry
from app.core.testing import FIXED_TODAY, TEST_UENS, make_ctx, make_doc

REGISTRY = {
    "schema_version": "1",
    "generated_by": "make_bundle.py",
    "as_of": "2026-09-30",
    "entities": [
        {
            "uen": "201912345R",
            "entity_name": "Example Logistics Pte. Ltd.",
            "entity_type": "Private Company Limited by Shares",
            "status": "Live",
            "incorporation_date": "2019-04-12",
            "registered_address": "1 Fictitious Road, Singapore 000001",
            "principal_activity": "Freight forwarding and logistics",
            "ssic_code": "52291",
            "paid_up_capital": "500000.00",
            "currency": "SGD",
            "officers": [
                {
                    "name": "Tan Wei Ming",
                    "id_number": "S1234567D",
                    "nationality": "SG",
                    "position": "Director",
                    "appointment_date": "2019-04-12",
                    "cessation_date": None,
                }
            ],
            "shareholders": [
                {
                    "name": "Tan Wei Ming",
                    "id_or_uen": "S1234567D",
                    "holder_type": "individual",
                    "shares": 300000,
                    "percent": "60.00",
                    "share_class": "Ordinary",
                }
            ],
        }
    ],
}


def test_test_uens_are_valid() -> None:
    for u in TEST_UENS:
        assert sg_uen.is_valid(u), u
    assert not sg_uen.is_valid("201912345K")  # the earlier invalid example


def test_load_registry_missing_malformed_and_good(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        load_registry(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema_version": "1"}))
    with pytest.raises(ValueError):
        load_registry(wrong)
    good = tmp_path / "ok.json"
    good.write_text(json.dumps(REGISTRY))
    reg = load_registry(good)
    assert isinstance(reg, MockRegistry) and reg.entities[0].officers[0].cessation_date is None


def test_lookup_is_normalised_exact() -> None:
    reg = MockRegistry.model_validate(REGISTRY)
    assert reg.lookup(" 2019 12345r ") is not None
    assert reg.lookup("201912345H") is None
    assert reg.lookup(None) is None and reg.lookup("") is None
    assert len(reg.find_by_name("EXAMPLE LOGISTICS PTE LTD")) == 1
    assert reg.find_by_name("Example Logistic Pte Ltd") == []  # exact only, no fuzzy
    assert reg.find_by_name("") == []


def test_unknown_registry_keys_rejected() -> None:
    bad = json.loads(json.dumps(REGISTRY))
    bad["entities"][0]["surprise"] = 1
    with pytest.raises(ValueError):
        MockRegistry.model_validate(bad)


def test_context_helpers() -> None:
    d10 = make_doc("invoice", id="d10")
    d2 = make_doc("bank_statement", id="d2")
    d9 = make_doc("invoice", id="d9", file_id="f2")
    words = {
        ("f1", 1): [
            Word(page=1, text="Hello", x=0.1, y=0.1, w=0.05, h=0.01),
            Word(page=1, text="World", x=0.155, y=0.1, w=0.05, h=0.01),
        ]
    }
    ctx = make_ctx(
        [d10, d2, d9],
        use_case="kyc",
        applicant=Applicant(name="A", uen=TEST_UENS[0]),
        page_words=words,
    )
    assert [d.id for d in ctx.docs()] == ["d2", "d9", "d10"]  # numeric id order
    assert [d.id for d in ctx.docs("invoice")] == ["d9", "d10"]
    assert [d.id for d in ctx.docs("invoice", "bank_statement")] == ["d2", "d9", "d10"]
    assert ctx.doc("d9").file_id == "f2"
    with pytest.raises(KeyError):
        ctx.doc("d99")
    with pytest.raises(KeyError):
        ctx.file("f9")
    assert ctx.use_case == "kyc" and ctx.applicant.uen == TEST_UENS[0]
    assert ctx.today == FIXED_TODAY == ctx.case.today
    assert ctx.page_text("f1", 1) == "Hello World"
    assert ctx.page_text("f1", 2) == ""


def test_make_ctx_writes_real_files_content_addressed() -> None:
    import hashlib

    data = b"%PDF-1.4 fake pdf bytes"
    ref = FileRef(id="f1", name="a.pdf", sha256="0" * 64, size_bytes=0, mime="application/pdf")
    ctx = make_ctx(files=[(ref, data)])
    f = ctx.file("f1")
    assert f.sha256 == hashlib.sha256(data).hexdigest() and f.size_bytes == len(data)
    path = ctx.file_path("f1")
    assert path.read_bytes() == data
    assert path.parent.name == f.sha256[:2]
    assert ctx.fingerprints.lookup("file_sha256", f.sha256) == []
    assert ctx.llm.model_for("cheap") == "fake"  # type: ignore[attr-defined]


def test_make_ctx_routes_case_fields() -> None:
    ctx = make_ctx(status="open", partial=True, reference="R1")
    assert ctx.case.status == "open" and ctx.case.partial and ctx.case.reference == "R1"


def test_make_doc_fills_every_key_and_rejects_unknown() -> None:
    doc = make_doc(
        "invoice", fields={"invoice_number": "INV-1"}, tables={"lines": [{"amount": "1.00"}]}
    )
    from app.core.schemas import DOC_SCHEMAS

    assert set(doc.fields) == set(DOC_SCHEMAS["invoice"].fields)
    assert doc.fields["total"].value is None and doc.fields["total"].kind == "money"
    assert doc.tables["lines"].rows[0].cells["amount"].value == "1.00"
    assert set(doc.tables["lines"].rows[0].cells) == set(
        DOC_SCHEMAS["invoice"].tables["lines"].columns
    )
    with pytest.raises(KeyError):
        make_doc("invoice", fields={"nope": "x"})
    with pytest.raises(KeyError):
        make_doc("invoice", tables={"nope": []})
    shareholders = make_doc("shareholder_register", tables={"shareholders": [{"share_class": "A"}]})
    assert "share_class" in shareholders.tables["shareholders"].rows[0].cells  # right schema chosen
