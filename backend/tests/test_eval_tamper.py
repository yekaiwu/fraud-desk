"""Tamper catalogue (docs 4.3, section 6 'Tamper'): every definition resolves, manifests record the
TamperRecord, only changed files are written, edited values appear in the ground truth, and the
channel contracts hold (clean metadata for world tampers, bytes after the signature for T31)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf
import pytest

from scripts._eval.common import ensure_bundle, load_manifest, read_json
from scripts._eval.tamper_core import TamperNotApplicable
from scripts._eval.tamper_defs import TAMPERS, registered_rules, unregistered_rules
from scripts._gen.render_common import signature_byte_range
from scripts.make_tampered import (
    BUILDERS,
    NOT_BUILT,
    apply_tamper,
    build_instances,
    format_catalogue,
    main,
)

EDITOR_STRINGS = re.compile(
    r"photoshop|ilovepdf|smallpdf|canva|sejda|midjourney|dall-e|stable diffusion", re.I
)


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("eval-samples")
    for sc in ("loan", "kyc", "aml"):
        ensure_bundle(root, sc, "clean", 0, renders=("digital",))
    return root


@pytest.fixture(scope="module")
def kyc_scan_samples(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("eval-samples-scan")
    ensure_bundle(root, "kyc", "clean", 0, renders=("digital", "scan_hq"))
    return root


def build(
    samples: Path,
    tid: str,
    scenario: str,
    *,
    instance: int = 0,
    renders=("digital",),
    out: Path | None = None,
):  # noqa: ANN001, ANN201
    bdir = samples / "bundles" / f"{scenario}-clean"
    base = load_manifest(bdir / "manifest.json")
    dest = out or samples / "tampered" / f"{tid}-{scenario}-i{instance}"
    man = apply_tamper(
        base,
        tid,
        seed=4002,
        instance=instance,
        out_dir=dest,
        render=renders[0],
        renders=list(renders),
        base_dir=bdir,
    )
    return man, dest, bdir


# --------------------------------------------------------------------------- catalogue


def test_catalogue_has_every_row_of_the_table() -> None:
    expected = [
        "T01",
        "T02a",
        "T02b",
        "T02c",
        "T03",
        "T04",
        "T05",
        "T06",
        "T07",
        "T08",
        "T09",
        "T10",
        "T11",
        "T12",
        "T13",
        "T14a",
        "T14b",
        "T15",
        "T16a",
        "T16b",
        "T16c",
        "T17",
        "T18a",
        "T18b",
        "T19",
        "T20a",
        "T20b",
        "T21",
        "T22",
        "T23",
        "T24",
        "T30a",
        "T30b",
        "T31",
        "T32a",
        "T32b",
        "T32c",
        "T33",
        "T34",
        "T35",
        "T40a",
        "T40b",
        "T41",
        "T42",
        "T43",
        "T50a",
        "T50b",
        "T51",
        "T52",
        "T53",
        "T54",
        "T55",
        "T56a",
        "T56b",
        "T57",
        "T58",
        "T59",
        "T60",
    ]
    assert list(TAMPERS) == expected


def test_flags_match_the_spec() -> None:
    assert {t.id for t in TAMPERS.values() if t.weak} == {"T08", "T09", "T17", "T35"}
    assert {t.id for t in TAMPERS.values() if t.known_miss} == {"T02c"}
    assert {t.id for t in TAMPERS.values() if t.hard} == {"T02b"}
    assert TAMPERS["T02c"].expected_any_of == []


def test_expected_rules_are_registered_or_reported_as_gaps() -> None:
    reg = registered_rules()
    assert reg, "the check registry must be importable"
    gaps = {
        t.id: unregistered_rules(t, reg) for t in TAMPERS.values() if unregistered_rules(t, reg)
    }
    # a gap is allowed (reported as "rule not implemented: gap"), a silent unknown id is not
    for rules in gaps.values():
        assert all(isinstance(r, str) for r in rules)
    text = format_catalogue()
    for rules in gaps.values():
        assert f"rule not implemented: gap {rules}" in text


def test_list_prints_the_catalogue(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert (
        all(tid in out for tid in TAMPERS)
        and "known_miss" in out
        and "hard" in out
        and "[no builder]" in out
    )


def test_only_t56b_has_no_builder_and_says_why() -> None:
    missing = sorted(set(TAMPERS) - set(BUILDERS))
    assert missing == ["T56b"] and "generator gap" in NOT_BUILT["T56b"]


# ------------------------------------------------------------------- world-channel tampers


def test_t01_manifest_records_the_edit_and_inherits_untouched_files(samples: Path) -> None:
    man, dest, bdir = build(samples, "T01", "loan")
    rec = man.tampers[0]
    assert (
        rec.tamper_id == "T01"
        and rec.channel == "world"
        and rec.expected_any_of == ["bs_running_balance"]
    )
    assert rec.original["credit"] != rec.edited["credit"]
    assert (
        rec.target.row is not None
        and rec.target.table == "transactions"
        and rec.target.box is not None
    )
    changed = [f for f in man.files if not f.inherited]
    assert len(changed) == 1 and changed[0].path.startswith("digital/bank_statement_")
    assert all(f.path.startswith("../../bundles/loan-clean/") for f in man.files if f.inherited)
    assert (dest / changed[0].path).exists() and not (
        dest / "digital" / "company_profile.pdf"
    ).exists()
    assert man.kind == "tampered" and man.base_bundle == "../../bundles/loan-clean/manifest.json"
    # the manifest is the only index: inherited paths resolve to real files
    for rel in man.upload_order["digital"]:
        assert (dest / rel).resolve().exists()
    gt = read_json(dest / "ground_truth.json")
    doc = next(d for d in gt["documents"] if d["key"] == rec.target.doc_key)
    cells = next(r for r in doc["tables"]["transactions"]["rows"] if r["index"] == rec.target.row)[
        "cells"
    ]
    assert cells["credit"]["value"] == rec.edited["credit"]


@pytest.mark.parametrize(
    ("tid", "scenario"),
    [
        ("T02a", "loan"),
        ("T02b", "loan"),
        ("T04", "kyc"),
        ("T05", "aml"),
        ("T07", "loan"),
        ("T10", "aml"),
        ("T11", "loan"),
        ("T12", "loan"),
        ("T13", "aml"),
        ("T14a", "loan"),
        ("T14b", "loan"),
        ("T15", "aml"),
        ("T18a", "loan"),
        ("T18b", "loan"),
        ("T20a", "loan"),
        ("T20b", "loan"),
        ("T21", "loan"),
        ("T22", "loan"),
        ("T23", "loan"),
        ("T24", "loan"),
        ("T50a", "kyc"),
        ("T50b", "loan"),
        ("T51", "kyc"),
        ("T52", "aml"),
        ("T53", "kyc"),
        ("T54", "kyc"),
        ("T55", "kyc"),
        ("T56a", "kyc"),
        ("T57", "kyc"),
        ("T58", "aml"),
        ("T60", "aml"),
    ],
)
def test_world_tampers_change_a_value_and_the_ground_truth_carries_it(
    samples: Path, tid: str, scenario: str
) -> None:
    man, dest, _bdir = build(samples, tid, scenario)
    rec = man.tampers[0]
    assert rec.original != rec.edited, f"{tid}: original equals edited"
    assert rec.tamper_id == tid and rec.expected_any_of == TAMPERS[tid].expected_any_of
    assert [f for f in man.files if not f.inherited], f"{tid}: no file changed"
    assert (dest / "ground_truth.json").exists() and (dest / "labels.json").exists()
    # world tampers keep clean metadata: no editor strings in any changed file's Info
    for f in man.files:
        if not f.inherited and f.mime == "application/pdf":
            with pymupdf.open(str(dest / f.path)) as doc:
                meta = " ".join(str(v) for v in (doc.metadata or {}).values())
            assert not EDITOR_STRINGS.search(meta)


def test_t02a_and_t08_never_pick_the_last_month_and_t03_neither_end(samples: Path) -> None:
    for scenario in ("loan", "kyc", "aml"):
        for k in range(4):
            for tid in ("T02a", "T08"):
                bdir = samples / "bundles" / f"{scenario}-clean"
                base = load_manifest(bdir / "manifest.json")
                months = sorted(d.key for d in base.documents if d.doc_type == "bank_statement")
                try:
                    man = apply_tamper(
                        base,
                        tid,
                        seed=4002,
                        instance=k,
                        out_dir=samples / "tampered" / f"x-{tid}-{scenario}-{k}",
                        render="digital",
                        base_dir=bdir,
                    )
                except TamperNotApplicable:
                    continue
                assert man.tampers[0].target.doc_key != months[-1], (tid, scenario, k)
            man = apply_tamper(
                base,
                "T03",
                seed=4002,
                instance=k,
                out_dir=samples / "tampered" / f"x-T03-{scenario}-{k}",
                render="digital",
                base_dir=bdir,
            )
            assert man.tampers[0].target.doc_key not in (months[0], months[-1])
            assert man.tampers[0].target.doc_key not in [d.key for d in man.documents]


def test_t02c_is_a_known_miss_with_no_expected_rule(samples: Path) -> None:
    man, _dest, _ = build(samples, "T02c", "loan")
    rec = man.tampers[0]
    assert rec.known_miss and rec.expected_any_of == [] and rec.original != rec.edited
    assert any("financial_statement" in f.path for f in man.files if not f.inherited)


def test_t02b_is_the_hard_case_with_a_consistent_chain(samples: Path) -> None:
    man, dest, _ = build(samples, "T02b", "loan")
    assert man.tampers[0].hard
    gt = read_json(dest / "ground_truth.json")
    from decimal import Decimal

    from scripts._eval.tamper_core import cell

    # every statement chain is internally consistent after the recomputation
    for d in gt["documents"]:
        if d["doc_type"] != "bank_statement":
            continue
        prev = None
        for r in d["tables"]["transactions"]["rows"]:
            bal = Decimal(cell(r, "balance"))
            if prev is not None:
                assert (
                    prev + Decimal(cell(r, "credit") or "0") - Decimal(cell(r, "debit") or "0")
                    == bal
                )
            prev = bal


def test_the_same_seed_and_instance_give_the_same_tamper(samples: Path) -> None:
    a, _, _ = build(samples, "T10", "loan", instance=3, out=samples / "tampered" / "a")
    b, _, _ = build(samples, "T10", "loan", instance=3, out=samples / "tampered" / "b")
    assert a.tampers[0].edit == b.tampers[0].edit and a.tampers[0].seed_key == b.tampers[0].seed_key
    assert [f.sha256 for f in a.files] == [f.sha256 for f in b.files]
    c, _, _ = build(samples, "T10", "loan", instance=4, out=samples / "tampered" / "c")
    assert c.tampers[0].edit != a.tampers[0].edit


def test_scenario_mismatch_is_not_applicable(samples: Path) -> None:
    with pytest.raises(TamperNotApplicable):
        build(samples, "T57", "loan")  # crypto rows only apply to the KYC company


# --------------------------------------------------------------------------- bundle channel


def test_t16a_duplicates_the_file_and_t16c_is_a_single_inherited_invoice(samples: Path) -> None:
    man, dest, _ = build(samples, "T16a", "loan")
    paths = man.upload_order["digital"]
    assert len(paths) == len({p for p in paths}) and any("_resubmitted" in p for p in paths)
    dup = next(f for f in man.files if "_resubmitted" in f.path)
    orig = next(f for f in man.files if f.path != dup.path and f.sha256 == dup.sha256)
    assert dup.sha256 == orig.sha256
    man, dest, _ = build(samples, "T16c", "aml")
    assert len(man.upload_order["digital"]) == 1 and man.files[0].inherited
    assert man.tampers[0].channel == "bundle"


def test_t16b_has_new_bytes_for_the_same_invoice(samples: Path) -> None:
    man, dest, _ = build(samples, "T16b", "loan")
    new = next(f for f in man.files if "_retypeset" in f.path)
    orig_path = new.path.replace("_retypeset", "")
    orig = next(f for f in man.files if f.path.endswith(orig_path.split("/")[-1]))
    assert new.sha256 != orig.sha256 and new.doc_keys == orig.doc_keys


def test_t06_drops_a_page_and_t17_two_invoices(samples: Path) -> None:
    man, dest, _ = build(samples, "T06", "loan")
    key = man.tampers[0].target.doc_key
    gt = read_json(dest / "ground_truth.json")
    doc = next(d for d in gt["documents"] if d["key"] == key)
    assert doc["tables"]["page_chain"]["rows"]
    man, _dest, bdir = build(samples, "T17", "loan")
    base = load_manifest(bdir / "manifest.json")
    assert (
        len([d for d in base.documents if d.doc_type == "invoice"])
        - len([d for d in man.documents if d.doc_type == "invoice"])
        == 2
    )


# ---------------------------------------------------------------- metadata and pdf_edit channels


def test_metadata_tampers_change_only_info_strings(samples: Path) -> None:
    man, dest, _ = build(samples, "T32a", "loan")
    changed = [f for f in man.files if not f.inherited]
    assert len(changed) == 1
    with pymupdf.open(str(dest / changed[0].path)) as doc:
        meta = doc.metadata
    assert EDITOR_STRINGS.search(meta["producer"] or "") or EDITOR_STRINGS.search(
        meta["creator"] or ""
    )
    assert meta["modDate"] != meta["creationDate"]
    man, dest, _ = build(samples, "T33", "kyc")
    f = next(f for f in man.files if not f.inherited)
    with pymupdf.open(str(dest / f.path)) as doc:
        assert re.search(r"midjourney|dall-e|stable diffusion", doc.metadata["creator"] or "", re.I)
    man, dest, _ = build(samples, "T32c", "aml")
    f = next(f for f in man.files if not f.inherited)
    with pymupdf.open(str(dest / f.path)) as doc:
        assert doc.metadata["producer"] == "Mock Word Processor 3.1"


def test_t30_leaves_visible_text_unchanged_except_at_the_target(samples: Path) -> None:
    man, dest, bdir = build(samples, "T30a", "loan")
    rec = man.tampers[0]
    f = next(f for f in man.files if not f.inherited)
    base_path = bdir / f.path
    box = rec.target.box
    assert box is not None
    with pymupdf.open(str(base_path)) as a, pymupdf.open(str(dest / f.path)) as b:
        for pno in range(len(a)):
            wa = {(w[4], round(w[0]), round(w[1])) for w in a[pno].get_text("words")}
            wb = {(w[4], round(w[0]), round(w[1])) for w in b[pno].get_text("words")}
            extra = wb - wa
            page = b[pno]
            w_pt, h_pt = page.rect.width, page.rect.height
            rect = pymupdf.Rect(
                box.x * w_pt - 4,
                box.y * h_pt - 4,
                (box.x + box.w) * w_pt + 4,
                (box.y + box.h) * h_pt + 4,
            )
            for text, x, y in extra:
                assert pno + 1 == box.page and rect.contains(pymupdf.Point(x, y)), (
                    f"unexpected new text {text!r}"
                )
    assert rec.original["credit"] != rec.edited["credit"]


def test_t31_has_bytes_after_the_signature_and_the_q09_control_does_not(samples: Path) -> None:
    man, dest, bdir = build(
        samples, "T31", "kyc"
    )  # ocbc layout: already carries the Q09 placeholder
    f = next(f for f in man.files if not f.inherited)
    data = (dest / f.path).read_bytes()
    rng = signature_byte_range(data)
    assert rng is not None and rng[2] + rng[3] < len(data)
    control = (bdir / f.path).read_bytes()
    crng = signature_byte_range(control)
    assert crng is not None and crng[2] + crng[3] == len(control)
    assert control.count(b"%%EOF") >= 2  # the honest incremental update before signing
    man, dest, _ = build(samples, "T31", "loan")  # dbs layout: placeholder added, then appended
    f = next(f for f in man.files if not f.inherited)
    data = (dest / f.path).read_bytes()
    rng = signature_byte_range(data)
    assert rng is not None and rng[2] + rng[3] < len(data)


def test_t35_adds_extra_fonts(samples: Path) -> None:
    man, dest, bdir = build(samples, "T35", "loan")
    f = next(f for f in man.files if not f.inherited)

    def fonts(p: Path) -> set[str]:
        with pymupdf.open(str(p)) as d:
            return {x[3] for x in d[0].get_fonts()}

    assert len(fonts(dest / f.path)) >= len(fonts(bdir / f.path)) + 2


def test_t34_reuses_a_signature(samples: Path) -> None:
    man, _dest, _ = build(samples, "T34", "loan")
    assert man.tampers[0].channel == "pdf_edit" and man.tampers[0].original != man.tampers[0].edited
    assert any(m.op == "reuse_signature" for m in man.spec.mutations)


# ------------------------------------------------------------------------- image channel


def test_t40_edits_exif_without_touching_pixels(kyc_scan_samples: Path) -> None:
    from PIL import Image

    man, dest, bdir = build(kyc_scan_samples, "T40a", "kyc", renders=("scan_hq",))
    f = next(f for f in man.files if not f.inherited)
    new = Image.open(dest / f.path)
    assert "Photoshop" in str(new.getexif().get(0x0131))
    old = Image.open(bdir / f.path)
    from PIL import ImageChops

    assert old.getexif().get(0x0131) is None
    assert ImageChops.difference(new.convert("RGB"), old.convert("RGB")).getbbox() is None
    man, dest, bdir = build(kyc_scan_samples, "T40b", "kyc", renders=("scan_hq",))
    f = next(f for f in man.files if not f.inherited)
    ex = Image.open(dest / f.path).getexif()
    assert ex.get(0x0132) != ex.get_ifd(0x8769).get(0x9003)


def test_t41_t42_t43_change_pixels_and_record_boxes_per_render(kyc_scan_samples: Path) -> None:
    from PIL import Image, ImageChops

    for tid in ("T41", "T42", "T43"):
        man, dest, bdir = build(kyc_scan_samples, tid, "kyc", renders=("scan_hq",))
        rec = man.tampers[0]
        f = next(f for f in man.files if not f.inherited and f.render == "scan_hq")
        diff = ImageChops.difference(
            Image.open(dest / f.path).convert("RGB"), Image.open(bdir / f.path).convert("RGB")
        )
        assert diff.getbbox() is not None, tid
        assert rec.edited.get("box_scan_hq"), tid
        assert rec.channel == "image" and rec.original != rec.edited
    gt = read_json(
        dest / "ground_truth.json"
    )  # T43: the printed amount in the truth is the edited one
    doc = next(d for d in gt["documents"] if d["key"] == rec.target.doc_key)
    row = next(r for r in doc["tables"]["transactions"]["rows"] if r["index"] == rec.target.row)
    assert row["cells"]["credit"]["value"] == rec.edited["credit"]


def test_image_tamper_on_a_digital_only_base_is_not_applicable(samples: Path) -> None:
    with pytest.raises(TamperNotApplicable):
        build(samples, "T40a", "kyc", renders=("scan_hq",))


def test_cli_builds_instances_and_prints_the_count(
    samples: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    got = build_instances(
        "T11", n=2, seed=4002, samples=samples, out_root=tmp_path / "t", renders=["digital"]
    )
    assert {sc for _k, sc, _d in got} == {"loan", "aml"} and len(got) == 4
    for _k, _sc, d in got:
        assert json.loads((d / "manifest.json").read_text())["tampers"][0]["tamper_id"] == "T11"


def test_pdf_edit_tampers_are_byte_deterministic(samples: Path) -> None:
    for tid in ("T30a", "T30b", "T31", "T35"):
        a, _, _ = build(samples, tid, "loan", out=samples / "tampered" / f"det-a-{tid}")
        b, _, _ = build(samples, tid, "loan", out=samples / "tampered" / f"det-b-{tid}")
        assert [f.sha256 for f in a.files] == [f.sha256 for f in b.files], tid


def test_pin_trailer_id_replaces_only_the_final_trailer_id() -> None:
    from scripts._eval.tamper_pdf import pin_trailer_id

    data = b"%PDF-1.7\n/ID[<AA><BB>]\nxref\ntrailer\n<</Size 3/ID [(a\\)b<)<CC>]>>\nstartxref\n9\n%%EOF\n"
    out = pin_trailer_id(data)
    assert out.startswith(b"%PDF-1.7\n/ID[<AA><BB>]") and b"(a\\)b<)" not in out
    assert out.endswith(b">>\nstartxref\n9\n%%EOF\n") and pin_trailer_id(out) == out
    assert pin_trailer_id(b"no id here") == b"no id here"
