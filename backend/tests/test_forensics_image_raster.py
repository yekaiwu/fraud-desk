"""Pixel rules: ela_region, copy_move, noise_mismatch (docs/forensics.md section 6 tests
20 to 23, 29, 30 and the corroboration rule 4.1 (7)).

Images are synthesised in ``forensics_image_factory``; nothing here touches the network or the
wall clock.
"""

from __future__ import annotations

import time

import pytest
from PIL import Image

from app.checks import forensics_image as fi
from app.checks.registry import CheckSkipped, run_stage_checks
from app.core.models import Region
from app.core.testing import make_ctx, make_doc
from tests.forensics_image_factory import (
    CLONE_DST,
    CLONE_SRC,
    clone_only,
    ela_patch_page,
    file_ref,
    image_pdf_bytes,
    jpeg_bytes,
    jpeg_roundtrip,
    noise_patch_png,
    photo_page,
    png_bytes,
    render_pdf_page,
    scan_like,
    scan_pdf_ctx,
    statement_pdf_bytes,
    tamper_fresh_patch,
    upload_ctx,
)

RASTER = ("ela_region", "copy_move", "noise_mismatch")


def real(flags):  # noqa: ANN001, ANN201
    """Flags that are findings (not limit notices)."""
    return [f for f in flags if not f.id.endswith("~notice")]


@pytest.fixture(scope="module")
def original() -> Image.Image:
    return photo_page(sigma=5.0)


@pytest.fixture(scope="module")
def statement_render() -> Image.Image:
    """Case-1 style render: a 1241 x 1754 text page."""
    return render_pdf_page(statement_pdf_bytes())


def overlap_of(flag, other) -> float:  # noqa: ANN001
    return fi._overlap(flag.region, other.region)


# --------------------------------------------------------------------------------------
# ela_region (tests 20, 21)
# --------------------------------------------------------------------------------------


def test_ela_flags_a_fresh_patch_on_a_jpeg_scan() -> None:
    ctx = upload_ctx(jpeg_bytes(ela_patch_page(), 95), doc_type="bank_statement")
    flags = fi.ela_region(ctx)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.rule == "ela_region" and flag.severity == "low" and flag.stage == "forensics"
    assert flag.document_ids == ["d1"]
    assert flag.region is not None and flag.region.label == "ELA"
    # the patch was pasted at (703, 603) of a 1200 x 800 page
    assert flag.region.x == pytest.approx(703 / 1200, abs=0.06)
    assert flag.region.y == pytest.approx(603 / 800, abs=0.06)
    assert "blocks, mean ELA" in flag.evidence and "MAD" in flag.evidence
    assert flag.id == "ela_region.d1.-~f1p1r1"
    Region.model_validate(flag.region.model_dump())


def test_ela_png_of_the_same_pixels_is_skipped() -> None:
    ctx = upload_ctx(png_bytes(ela_patch_page()), "image/png")
    with pytest.raises(CheckSkipped) as skipped:
        fi.ela_region(ctx)
    assert skipped.value.reason == "no jpeg raster"


def test_ela_is_silent_on_a_clean_scan(original: Image.Image) -> None:
    for q in (95, 85, 70, 50):
        assert fi.ela_region(upload_ctx(jpeg_bytes(original, q))) == []


def test_ela_skipped_above_12_megapixels_emits_a_notice() -> None:
    big = Image.new("RGB", (4100, 3000), (230, 230, 230))  # 12.3 MP
    ctx = upload_ctx(jpeg_bytes(big, 80))
    flags = fi.ela_region(ctx)
    assert [f.severity for f in flags] == ["info"]
    assert flags[0].id == "ela_region.d1.-~notice"
    assert "ELA skipped" in flags[0].evidence and "12 MP" in flags[0].evidence


def test_ela_does_not_judge_a_file_with_fewer_than_20_textured_blocks() -> None:
    flat = Image.new("RGB", (600, 600), (240, 240, 240))
    assert fi.ela_region(upload_ctx(jpeg_bytes(flat, 90))) == []


# --------------------------------------------------------------------------------------
# copy_move (tests 22, 30)
# --------------------------------------------------------------------------------------


def test_copy_move_flags_a_cloned_patch_and_names_both_clones(original: Image.Image) -> None:
    ctx = upload_ctx(jpeg_bytes(clone_only(original), 90))
    flags = fi.copy_move(ctx)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.rule == "copy_move" and flag.severity == "low"  # lone: a hint, not a finding
    assert "inlier matches between two patches" in flag.evidence
    assert flag.region is not None and len(flag.extra_regions) == 1
    assert flag.extra_regions[0].label == "copy source"
    # both clones are where the factory put them (1200 x 800 page)
    sx, sy = CLONE_SRC[0] / 1200, CLONE_SRC[1] / 800
    dx, dy = CLONE_DST[0] / 1200, CLONE_DST[1] / 800
    boxes = [flag.region, flag.extra_regions[0]]
    assert any(abs(b.x - sx) < 0.12 and abs(b.y - sy) < 0.12 for b in boxes)
    assert any(abs(b.x - dx) < 0.12 and abs(b.y - dy) < 0.12 for b in boxes)
    # equal y: the later clone in reading order (the right-hand one) is primary
    assert flag.region.x > flag.extra_regions[0].x


def test_copy_move_shift_in_evidence_is_positive_for_a_horizontal_clone(
    original: Image.Image,
) -> None:
    flag = fi.copy_move(upload_ctx(jpeg_bytes(clone_only(original), 90)))[0]
    assert "shift (0.33, 0.0" in flag.evidence or "shift (0.34, 0.0" in flag.evidence


def test_copy_move_clone_with_ela_overlap_makes_both_high(original: Image.Image) -> None:
    data = jpeg_bytes(tamper_fresh_patch(original, clone=True), 95)
    ctx = upload_ctx(data)
    copy_flag = fi.copy_move(ctx)[0]
    ela_flag = fi.ela_region(ctx)[0]
    assert copy_flag.severity == "high" and ela_flag.severity == "high"
    assert "corroborated by" in copy_flag.evidence and "ela_region" in copy_flag.evidence
    assert "corroborated by" in ela_flag.evidence and "copy_move" in ela_flag.evidence
    # the primary copy_move region is the clone that overlaps the ELA region
    assert overlap_of(copy_flag, ela_flag) >= fi.OVERLAP_MIN
    assert copy_flag.region.x > copy_flag.extra_regions[0].x


def test_cloning_nothing_leaves_a_clean_photo_silent(original: Image.Image) -> None:
    for q in (95, 70, 50):
        assert fi.copy_move(upload_ctx(jpeg_bytes(original, q))) == []


def test_two_runs_give_identical_flags(original: Image.Image) -> None:
    data = jpeg_bytes(tamper_fresh_patch(original, clone=True), 95)
    runs = []
    for _ in range(2):
        fi._raster_candidates.cache_clear()
        ctx = upload_ctx(data, doc_type="bank_statement")
        run_stage_checks(ctx, "forensics")
        runs.append([f.model_dump_json() for f in ctx.case.flags if f.rule in RASTER])
    assert runs[0] and runs[0] == runs[1]


def test_result_does_not_depend_on_the_order_checks_run(original: Image.Image) -> None:
    data = jpeg_bytes(tamper_fresh_patch(original, clone=True), 95)
    forward, backward = {}, {}
    fi._raster_candidates.cache_clear()
    ctx = upload_ctx(data)
    for rule in RASTER:
        forward[rule] = [f.model_dump_json() for f in getattr(fi, rule)(ctx)]
    fi._raster_candidates.cache_clear()
    ctx = upload_ctx(data)
    for rule in reversed(RASTER):
        backward[rule] = [f.model_dump_json() for f in getattr(fi, rule)(ctx)]
    assert forward == backward


def test_three_checks_share_one_analysis(
    original: Image.Image, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    real_analyse = fi._analyse_raster

    def counting(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append(1)
        return real_analyse(*args, **kwargs)

    monkeypatch.setattr(fi, "_analyse_raster", counting)
    fi._raster_candidates.cache_clear()
    ctx = upload_ctx(jpeg_bytes(original, 90))
    for rule in RASTER:
        getattr(fi, rule)(ctx)
    assert len(calls) == 1


# --------------------------------------------------------------------------------------
# noise_mismatch
# --------------------------------------------------------------------------------------


def test_noise_mismatch_flags_a_patch_with_other_noise_and_alone_stays_medium() -> None:
    ctx = upload_ctx(noise_patch_png(), "image/png")
    flags = fi.noise_mismatch(ctx)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.rule == "noise_mismatch" and flag.severity == "medium"  # one noisy check alone
    assert flag.region is not None and flag.region.label == "noise"
    # patch pasted at (500, 300) .. (820, 520) of a 1200 x 800 page
    assert 0.3 < flag.region.x < 0.55 and 0.3 < flag.region.y < 0.45
    # the lossless file has nothing for ELA and nothing is cloned
    with pytest.raises(CheckSkipped):
        fi.ela_region(ctx)
    assert fi.copy_move(ctx) == []


def test_noise_mismatch_is_silent_on_clean_scans(original: Image.Image) -> None:
    for q in (95, 70, 50):
        assert fi.noise_mismatch(upload_ctx(jpeg_bytes(original, q))) == []
    assert fi.noise_mismatch(upload_ctx(png_bytes(original), "image/png")) == []


# --------------------------------------------------------------------------------------
# test 23: clean degraded copies of the case-1 render
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("quality", [95, 85, 70, 50])
def test_clean_degraded_statement_copies_raise_no_raster_flag(
    statement_render: Image.Image, quality: int
) -> None:
    ctx = upload_ctx(jpeg_bytes(statement_render, quality), doc_type="bank_statement")
    for rule in RASTER:
        assert getattr(fi, rule)(ctx) == [], rule


def test_clean_png_statement_is_silent_and_ela_is_skipped(statement_render: Image.Image) -> None:
    ctx = upload_ctx(png_bytes(statement_render), "image/png", doc_type="bank_statement")
    assert fi.copy_move(ctx) == [] and fi.noise_mismatch(ctx) == []
    with pytest.raises(CheckSkipped, match="no jpeg raster"):
        fi.ela_region(ctx)


def test_scanned_noisy_statement_copies_are_silent(statement_render: Image.Image) -> None:
    scanned = scan_like(statement_render, sigma=4.0)
    for q in (90, 70, 50):
        ctx = upload_ctx(jpeg_bytes(scanned, q), doc_type="bank_statement")
        for rule in RASTER:
            assert getattr(fi, rule)(ctx) == [], (rule, q)


# --------------------------------------------------------------------------------------
# Corroboration (forensics.md 4.1 (7)): the only severity change
# --------------------------------------------------------------------------------------


def cand(rule: str, x: float, y: float, w: float = 0.1, h: float = 0.1, page: int = 1) -> fi._Cand:
    region = Region(file_id="f1", page=page, x=x, y=y, w=w, h=h, label=rule)
    return fi._Cand(
        rule=rule, file_id="f1", page=page, region=region, extra=(), evidence="e", explanation="x"
    )


def test_two_different_rules_on_one_region_become_high() -> None:
    out = fi._corroborate([cand("ela_region", 0.2, 0.2), cand("noise_mismatch", 0.21, 0.2)])
    assert [c.severity for c in out] == ["high", "high"]
    assert out[0].evidence.endswith("corroborated by noise_mismatch")
    assert out[1].evidence.endswith("corroborated by ela_region")


def test_same_rule_twice_does_not_raise_severity() -> None:
    out = fi._corroborate([cand("ela_region", 0.2, 0.2), cand("ela_region", 0.2, 0.2)])
    assert [c.severity for c in out] == ["low", "low"]


def test_different_rules_on_different_regions_or_pages_do_not_raise_severity() -> None:
    far = fi._corroborate([cand("ela_region", 0.1, 0.1), cand("copy_move", 0.7, 0.7)])
    assert [c.severity for c in far] == ["low", "low"]
    pages = fi._corroborate([cand("ela_region", 0.2, 0.2), cand("copy_move", 0.2, 0.2, page=2)])
    assert [c.severity for c in pages] == ["low", "low"]


def test_overlap_threshold_is_intersection_over_the_smaller_area() -> None:
    small = cand("ela_region", 0.2, 0.2, 0.05, 0.05)
    big = cand("copy_move", 0.2, 0.2, 0.3, 0.3)  # contains the small one: overlap 1.0
    assert [c.severity for c in fi._corroborate([small, big])] == ["high", "high"]
    # half of the small box inside: exactly OVERLAP_MIN
    edge = cand("noise_mismatch", 0.225, 0.2, 0.05, 0.05)
    assert [c.severity for c in fi._corroborate([small, edge])] == ["high", "high"]
    barely = cand("noise_mismatch", 0.2251, 0.2, 0.05, 0.05)
    # a lone ELA region is low; noise_mismatch has no lone demotion
    assert [c.severity for c in fi._corroborate([small, barely])] == ["low", "medium"]


def test_a_third_rule_adds_its_name_once() -> None:
    out = fi._corroborate(
        [
            cand("ela_region", 0.2, 0.2),
            cand("copy_move", 0.2, 0.2),
            cand("noise_mismatch", 0.2, 0.2),
        ]
    )
    assert out[0].evidence.endswith("corroborated by copy_move, noise_mismatch")


# --------------------------------------------------------------------------------------
# Scanned PDF pages (sources, caps, mapping, gates) - test 29
# --------------------------------------------------------------------------------------


def small_scan(seed: int) -> bytes:
    return jpeg_bytes(
        scan_like(Image.new("RGB", (560, 400), (235, 235, 235)), sigma=3.0, seed=seed), 85
    )


def test_twelve_scan_pages_analyse_eight_and_emit_a_notice() -> None:
    ctx = scan_pdf_ctx([small_scan(i) for i in range(12)])
    assert fi.MAX_RASTER_PAGES == 8
    flags = fi.ela_region(ctx)
    notices = [f for f in flags if f.severity == "info"]
    assert len(notices) == 1
    assert "pages analysed 8 of 12" in notices[0].evidence
    assert notices[0].id == "ela_region.d1.-~notice"
    assert real(flags) == []  # clean pages: only the notice
    for rule in ("copy_move", "noise_mismatch"):
        # 560 x 400 is below the 0.25 MP gate for these two: nothing is eligible for them
        with pytest.raises(CheckSkipped):
            getattr(fi, rule)(ctx)


def test_cap_is_a_constant_not_a_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fi, "MAX_RASTER_PAGES", 2)
    ctx = scan_pdf_ctx([small_scan(i) for i in range(3)])
    flags = fi.ela_region(ctx)
    assert any("pages analysed 2 of 3" in f.evidence for f in flags)


def test_tampered_scan_page_gets_a_page_fraction_region_inside_the_image_box(
    original: Image.Image,
) -> None:
    # the 1200 x 800 image fills 80% x 80% of an A4 page, offset (10%, 10%)
    page_bytes = jpeg_bytes(ela_patch_page(), 95)
    from tests.forensics_image_factory import pdf_with_pictures

    pdf = pdf_with_pictures([(page_bytes, (59.5, 84.2, 476.0, 673.6))], pages=1)
    ref = file_ref("f1", "application/pdf", pages=1, scan=False)  # coverage 64% => raster page
    ctx = make_ctx([make_doc("bank_statement", id="d1", file_id="f1")], files=[(ref, pdf)])
    flags = real(fi.ela_region(ctx))
    assert len(flags) == 1 and flags[0].region is not None
    r = flags[0].region
    assert r.page == 1 and 0.10 <= r.x <= 0.90 and 0.10 <= r.y <= 0.90
    # sub-box (703/1200, 603/800) maps through the image box: x = 0.1 + 0.586 * 0.8
    assert r.x == pytest.approx(0.1 + 0.8 * 703 / 1200, abs=0.06)
    assert r.y == pytest.approx(0.1 + 0.8 * 603 / 800, abs=0.07)


def test_a_page_label_never_switches_the_raster_rules_off() -> None:
    """Plan 5.3: a classifier label must not reduce checking. A page labelled other or
    boilerplate (or one the classifier failed on) is analysed like any other."""
    for doc_type in ("boilerplate", "other", "bank_statement"):
        ctx = upload_ctx(jpeg_bytes(ela_patch_page(), 95), doc_type=doc_type)
        assert len(real(fi.ela_region(ctx))) == 1, doc_type


def test_small_images_are_below_the_gate() -> None:
    tiny = Image.new("RGB", (200, 200), (200, 200, 200))
    ctx = upload_ctx(jpeg_bytes(tiny, 85))
    for rule in RASTER:
        with pytest.raises(CheckSkipped):
            getattr(fi, rule)(ctx)


def test_text_pdf_without_a_page_sized_image_has_no_raster_source() -> None:
    ref = file_ref("f1", "application/pdf", pages=1, scan=False)
    ctx = make_ctx(
        [make_doc("bank_statement", id="d1", file_id="f1")], files=[(ref, statement_pdf_bytes())]
    )
    for rule in RASTER:
        with pytest.raises(CheckSkipped):
            getattr(fi, rule)(ctx)


def test_rotated_scan_page_is_skipped_with_a_notice() -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=jpeg_bytes(ela_patch_page(), 95))
    page.set_rotation(90)
    pdf = doc.tobytes()
    ref = file_ref("f1", "application/pdf", pages=1, scan=True)
    ctx = make_ctx([make_doc("bank_statement", id="d1", file_id="f1")], files=[(ref, pdf)])
    flags = fi.ela_region(ctx)
    assert [f.severity for f in flags] == ["info"]
    assert "rotated" in flags[0].evidence


def test_corrupt_pdf_raises_for_the_run_when_every_file_failed() -> None:
    ref = file_ref("f1", "application/pdf", pages=1, scan=True)
    ctx = make_ctx([make_doc("bank_statement", id="d1", file_id="f1")], files=[(ref, b"not a pdf")])
    with pytest.raises(RuntimeError, match="all candidate files unreadable"):
        fi.ela_region(ctx)


def test_corrupt_image_among_good_files_adds_a_notice(original: Image.Image) -> None:
    ctx = make_ctx(
        [
            make_doc("national_id", id="d1", file_id="f1"),
            make_doc("national_id", id="d2", file_id="f2"),
        ],
        files=[
            (file_ref("f1", "image/jpeg"), jpeg_bytes(ela_patch_page(), 95)),
            (file_ref("f2", "image/jpeg"), b"\xff\xd8\xff garbage"),
        ],
    )
    flags = fi.ela_region(ctx)
    assert len(real(flags)) == 1
    notices = [f for f in flags if f.severity == "info"]
    assert len(notices) == 1 and "f2" in notices[0].evidence


def test_flag_cap_per_page_and_file() -> None:
    assert fi.MAX_REGIONS_PER_PAGE == 3 and fi.MAX_FLAGS_PER_FILE == 10
    flags = [
        fi.make_flag(
            "ela_region",
            docs=["d1"],
            evidence=f"e{i}",
            explanation="x",
            discriminator=f"f1p1r{i}",
        )
        for i in range(14)
    ]
    kept = fi._cap_flags(flags)
    assert len(kept) == 10 and kept[-1].evidence.endswith("(+4 more)")


# --------------------------------------------------------------------------------------
# Acceptance: runtime ceiling and purity
# --------------------------------------------------------------------------------------


@pytest.mark.timeout(20)
def test_two_megapixel_jpeg_runs_all_raster_checks_within_the_loose_ceiling() -> None:
    big = scan_like(
        Image.fromarray(__import__("numpy").full((1100, 1800, 3), 235, dtype="uint8")), sigma=4.0
    )
    ctx = upload_ctx(jpeg_bytes(big, 85), doc_type="bank_statement")
    start = time.perf_counter()
    run_stage_checks(ctx, "forensics")
    assert time.perf_counter() - start < 20


def test_rerunning_the_stage_on_the_same_case_is_byte_identical(original: Image.Image) -> None:
    data = jpeg_bytes(tamper_fresh_patch(original, clone=True), 95)
    ctx = upload_ctx(data, doc_type="bank_statement")
    run_stage_checks(ctx, "forensics")
    first = [f.model_dump_json() for f in ctx.case.flags]
    run_stage_checks(ctx, "forensics")
    assert [f.model_dump_json() for f in ctx.case.flags] == first
    assert first  # the tampered page produced flags


def test_checks_never_write_the_input_file(original: Image.Image) -> None:
    ctx = upload_ctx(jpeg_bytes(original, 90))
    path = ctx.file_path("f1")
    before = path.read_bytes()
    for rule in RASTER:
        getattr(fi, rule)(ctx)
    assert path.read_bytes() == before


def test_unused_helpers_stay_importable() -> None:
    assert callable(jpeg_roundtrip) and callable(image_pdf_bytes)


def test_surplus_candidates_are_summarised_not_silent() -> None:
    cands = [cand("ela_region", 0.1 * i, 0.1) for i in range(3)]
    out = fi._mark_surplus(list(cands), 2)
    assert out[-1].evidence.endswith("(+2 more)") and out[0].evidence == "e"
    assert fi._mark_surplus(list(cands), 0)[-1].evidence == "e"


def test_stage_run_records_ran_and_skipped_for_each_image_rule(original: Image.Image) -> None:
    ctx = upload_ctx(jpeg_bytes(tamper_fresh_patch(original, clone=True), 95))
    run_stage_checks(ctx, "forensics")
    runs = {r.rule: r for r in ctx.case.check_runs}
    for rule in ("ela_region", "copy_move", "noise_mismatch", "image_modified"):
        assert runs[rule].status == "ran", rule
    assert runs["signature_reuse"].status == "skipped"  # no PDF in the case
    assert runs["ela_region"].flags >= 1 and runs["copy_move"].flags >= 1
    high = [f for f in ctx.case.flags if f.rule in RASTER and f.severity == "high"]
    assert high and all("corroborated by" in f.evidence for f in high)
    assert all(f.stage == "forensics" for f in ctx.case.flags)


def test_png_upload_run_marks_ela_skipped_with_the_documented_reason() -> None:
    ctx = upload_ctx(png_bytes(ela_patch_page()), "image/png")
    run_stage_checks(ctx, "forensics")
    runs = {r.rule: r for r in ctx.case.check_runs}
    assert runs["ela_region"].status == "skipped" and runs["ela_region"].reason == "no jpeg raster"
