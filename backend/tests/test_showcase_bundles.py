"""The live-demo showcase cases build, are valid documents and stay fictitious."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
from stdnum.sg import uen as sg_uen

from scripts import make_showcase_bundles as sb


def test_showcases_build_with_the_expected_files(tmp_path: Path) -> None:
    manifest = sb.build(tmp_path / "out")
    assert [m["reference"] for m in manifest] == [
        "showcase-1-sanctions-hit",
        "showcase-2-live-lookup-verified",
        "showcase-3-live-lookup-wrong-date",
        "showcase-4-ai-generated-image",
        "showcase-5-hidden-instruction",
        "showcase-6-receipt-does-not-add-up",
    ]
    on_disk = json.loads((tmp_path / "out" / "showcases.json").read_text())
    for entry in on_disk:
        assert entry["files"] and all(
            (tmp_path / "out" / f).stat().st_size > 0 for f in entry["files"]
        )
        assert entry["use_case"] in {"kyc", "aml", "loan"}


def test_invoice_party_numbers_are_well_formed() -> None:
    for party in (sb.BUYER, sb.SUPPLIER, sb.SANCTIONED):
        assert sg_uen.is_valid(party.uen)


def test_documents_say_what_the_case_is_meant_to_show(tmp_path: Path) -> None:
    sb.build(tmp_path / "out")
    inv = pymupdf.open(tmp_path / "out" / "sanctions" / "invoice_CMS-2026-0417.pdf")
    assert "Chonmyong Shipping Co" in inv[0].get_text()
    inj = pymupdf.open(tmp_path / "out" / "hidden-instruction" / "invoice_HMS-2026-1190.pdf")
    assert "ignore all previous instructions" in inj[0].get_text().lower()
    png = (tmp_path / "out" / "ai-image" / "invoice_HMS-2026-1204.png").read_bytes()
    assert b"Stable Diffusion" in png


def test_lookup_cases_carry_the_sponsor_identity_and_differ_only_in_the_date(
    tmp_path: Path,
) -> None:
    sb.build(tmp_path / "out")
    entries = {e["slug"]: e for e in json.loads((tmp_path / "out" / "showcases.json").read_text())}
    for slug in ("lookup-match", "lookup-mismatch"):
        assert entries[slug]["applicant_uen"] == sb.GLDB_UEN
        assert entries[slug]["applicant_name"] == sb.GLDB_NAME
        profile = pymupdf.open(tmp_path / "out" / slug / "company_profile.pdf")[0].get_text()
        assert sb.GLDB_UEN in profile
    real = pymupdf.open(tmp_path / "out" / "lookup-match" / "certificate_of_incorporation.pdf")[
        0
    ].get_text()
    other = pymupdf.open(tmp_path / "out" / "lookup-mismatch" / "certificate_of_incorporation.pdf")[
        0
    ].get_text()
    assert "May 2021" in real and "March 2020" not in real
    assert "March 2020" in other or "Mar 2020" in other
    assert real != other


def test_variant_one_shares_no_file_with_variant_zero(tmp_path: Path) -> None:
    sb.build(tmp_path / "a", 0)
    sb.build(tmp_path / "b", 1)

    def digests(root: Path) -> set[bytes]:
        import hashlib

        return {
            hashlib.sha256(p.read_bytes()).digest()
            for p in root.rglob("*")
            if p.is_file() and p.suffix in {".pdf", ".png"}
        }

    assert digests(tmp_path / "a") and not digests(tmp_path / "a") & digests(tmp_path / "b")


def test_extras_bill_the_bundle_company_so_cross_document_checks_stay_quiet(tmp_path: Path) -> None:
    listed = sb.demo_extras(tmp_path / "x", 1)
    assert set(listed) == {"receipt", "jurong-kiln", "gldb"}
    kiln = sb.DEMO_BUYERS["jurong-kiln"]
    for name in listed["jurong-kiln"]:
        path = tmp_path / "x" / "jurong-kiln" / name
        if path.suffix == ".pdf":
            assert kiln.name in pymupdf.open(path)[0].get_text()
    gldb = pymupdf.open(tmp_path / "x" / "gldb" / listed["gldb"][0])[0].get_text()
    assert sb.GLDB_NAME in gldb and "Chonmyong Shipping Co" in gldb
