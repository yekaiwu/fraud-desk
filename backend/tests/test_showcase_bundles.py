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
        "showcase-2-live-registry-lookup",
        "showcase-3-ai-generated-image",
        "showcase-4-hidden-instruction",
        "showcase-5-receipt-does-not-add-up",
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
