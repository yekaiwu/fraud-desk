"""Ingest stage: page facts, text layer, metadata, failures stay local (ingest-and-extraction.md 9)."""

from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
import pikepdf
import pymupdf
import pytest
from PIL import Image
from reportlab.lib.colors import white

from app.core.llm import FakeLLMClient
from app.ingest import stage as ingest_stage
from app.ingest.stage import run_stage
from tests.ingest_helpers import (
    PAGE_H,
    ctx_with_files,
    image_pdf,
    jpeg_with_orientation,
    lines_page,
    make_pdf,
    png_bytes,
)

BODY = [
    "Statement of Account for Example Logistics Pte Ltd",
    "Opening Balance 1,000.00 brought forward from the previous month",
    "Closing Balance 2,500.00 carried forward to the next month",
]
OCR_FIXTURE = {"lines": [{"text": "Statement of Account", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.02}]}


def test_clean_two_page_digital_pdf(tmp_path: Path) -> None:
    pdf = make_pdf([lines_page(BODY), lines_page(BODY)], producer="Mock Ledger 5.0")
    ctx = ctx_with_files(tmp_path, [pdf])
    outcome = run_stage(ctx)

    file = ctx.case.files[0]
    assert [p.page for p in file.pages] == [1, 2]
    for page in file.pages:
        assert page.has_text_layer and not page.is_scan and page.error is None
        assert page.word_count > 10
        assert page.width_pt == pytest.approx(595.0) and page.height_pt == pytest.approx(842.0)
        words = ctx.page_words[("f1", page.page)]
        assert len(words) == page.word_count
        for w in words:
            assert 0 <= w.x <= 1 and 0 <= w.y <= 1 and w.x + w.w <= 1.0001 and w.y + w.h <= 1.0001
            assert w.font == "Helvetica" and w.size == 11.0 and w.page == page.page
        # reading order: first word of the first line comes first
        assert [w.text for w in words[:3]] == ["Statement", "of", "Account"]
    assert file.pdf_meta["Producer"] == "Mock Ledger 5.0"
    assert outcome.detail["files"] == 1 and outcome.detail["pages"] == 2
    assert outcome.detail["text_pages"] == 2 and outcome.detail["scan_pages"] == 0
    assert outcome.detail["ocr_calls"] == 0 and outcome.partial is False
    assert ctx.llm.calls == []  # a clean digital file needs no model


def test_idempotent_rerun(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)]), make_pdf([lines_page(BODY)])])
    run_stage(ctx)
    first = ([f.model_dump() for f in ctx.case.files], dict(ctx.page_words))
    run_stage(ctx)
    second = ([f.model_dump() for f in ctx.case.files], dict(ctx.page_words))
    assert first == second
    assert len(ctx.case.files[0].pages) == 1


def test_rotated_page_boxes_follow_rotation(tmp_path: Path) -> None:
    page = [{"text": "TOPLEFT", "x": 40.0, "y": 810.0, "size": 20.0}, *lines_page(BODY, top=700)]
    pdf = make_pdf([page], rotate={0: 90})
    ctx = ctx_with_files(tmp_path, [pdf])
    run_stage(ctx)
    info = ctx.case.files[0].pages[0]
    assert info.rotation == 90 and info.width_pt == pytest.approx(842.0)
    word = next(w for w in ctx.page_words[("f1", 1)] if w.text == "TOPLEFT")
    # rotated clockwise: the unrotated top-left corner is now at the top-right
    assert word.x > 0.85 and word.y < 0.15

    # the rendered pixels agree with the box
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=72)
    gray = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")

    def dark_in(box: tuple[float, float, float, float]) -> int:
        x, y, w, h = box
        region = gray.crop(
            (
                int(x * pix.width),
                int(y * pix.height),
                int((x + w) * pix.width) + 1,
                int((y + h) * pix.height) + 1,
            )
        )
        return int((np.asarray(region) < 128).sum())

    assert dark_in((word.x, word.y, word.w, word.h)) > 5
    assert dark_in((1 - word.x - word.w, word.y, word.w, word.h)) == 0  # mirrored place is empty


def test_hidden_covered_and_wrapped_text_is_kept(tmp_path: Path) -> None:
    page = [
        {"text": "visible heading words", "x": 50, "y": 800, "size": 11},
        {"text": "hiddenwhite", "x": 50, "y": 700, "size": 11, "color": white},
        {"text": "coveredsecret", "x": 50, "y": 600, "size": 11},
        {"text": "a long description that wraps", "x": 50, "y": 500, "size": 11},
        {"text": "onto a second line", "x": 50, "y": 486, "size": 11},
    ]
    pdf = make_pdf([page], covers={0: (40, 590, 200, 30)})
    ctx = ctx_with_files(tmp_path, [pdf])
    run_stage(ctx)
    texts = [w.text for w in ctx.page_words[("f1", 1)]]
    for expected in ("hiddenwhite", "coveredsecret", "wraps", "onto"):
        assert expected in texts


def test_scan_pdf_with_ocr_fixture(tmp_path: Path) -> None:
    llm = FakeLLMClient(fixtures={"ocr:page": OCR_FIXTURE})
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=llm)
    outcome = run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.is_scan and not page.has_text_layer and page.error is None
    assert page.word_count == 3
    assert [w.text for w in ctx.page_words[("f1", 1)]] == ["Statement", "of", "Account"]
    assert outcome.detail["ocr_calls"] == 1 and outcome.detail["scan_pages"] == 1
    assert outcome.partial is False
    request = llm.calls[0]
    assert (
        request.purpose == "ocr:page" and request.model_role == "cheap" and len(request.images) == 1
    )
    sha = ctx.case.files[0].sha256
    assert request.cache_extra == f"{sha}:1:{ctx.settings.vision_dpi}"
    assert ctx.costs.costs()[0].stage == "ingest"


def test_scan_without_fixture_is_reported_not_read(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [image_pdf()])
    outcome = run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.error is not None and page.error.startswith("ocr_unavailable")
    assert page.is_scan and ctx.page_words == {}
    assert outcome.partial is True
    assert any("ocr_unavailable" in w for w in outcome.warnings)
    assert outcome.detail["unreadable_pages"] == 1


def test_ocr_schema_failure_is_local(tmp_path: Path) -> None:
    llm = FakeLLMClient(fixtures={"ocr:page": {"lines": "not a list"}})
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=llm)
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("ocr_failed")


def test_corrupt_pdf_gets_placeholder_page(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [b"%PDF-1.4\nthis is not really a pdf at all\n%%EOF"])
    outcome = run_stage(ctx)
    pages = ctx.case.files[0].pages
    assert len(pages) == 1 and pages[0].page == 1
    assert pages[0].error.startswith("corrupt_pdf")
    assert pages[0].width_pt == 0 and not pages[0].has_text_layer and not pages[0].is_scan
    assert outcome.partial is True


def test_one_broken_page_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ingest import pdf as pdf_module

    real = pdf_module.load_page

    def flaky(doc, index):  # noqa: ANN001, ANN202
        if index == 1:
            raise RuntimeError("cannot load page object")
        return real(doc, index)

    monkeypatch.setattr(pdf_module, "load_page", flaky)
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)] * 3)])
    outcome = run_stage(ctx)
    pages = ctx.case.files[0].pages
    assert [p.error is None for p in pages] == [True, False, True]
    assert pages[1].error.startswith("page_load_failed")
    assert ("f1", 2) not in ctx.page_words and ("f1", 3) in ctx.page_words
    assert outcome.partial is True and outcome.detail["unreadable_pages"] == 1


def test_images_jpeg_with_exif_and_png(tmp_path: Path) -> None:
    jpeg = jpeg_with_orientation(6, width=120, height=80)
    llm = FakeLLMClient(fixtures={"ocr:page": OCR_FIXTURE})
    ctx = ctx_with_files(tmp_path, [jpeg, png_bytes()], llm=llm)
    run_stage(ctx)
    jpg, png = ctx.case.files
    assert len(jpg.pages) == 1 and len(png.pages) == 1
    assert jpg.pages[0].is_scan and png.pages[0].is_scan
    assert not jpg.pages[0].has_text_layer
    # orientation 6 rotates the picture: the page is now taller than wide
    assert jpg.pages[0].height_pt > jpg.pages[0].width_pt
    assert jpg.pdf_meta == {}


def test_decompression_bomb_is_image_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    ctx = ctx_with_files(tmp_path, [png_bytes(200, 300)])
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("image_too_large")


def test_pixel_cap_is_set_at_import() -> None:
    import app.ingest.images  # noqa: F401

    assert Image.MAX_IMAGE_PIXELS == 50_000_000


def test_broken_image_is_decode_failed(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [b"\x89PNG\r\n\x1a\nnot a real png"])
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("image_decode_failed")


def test_hash_mismatch(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)])])
    stored = ctx.file_path("f1")
    stored.write_bytes(stored.read_bytes() + b"\n% tampered")
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("hash_mismatch")


def test_missing_stored_file_is_a_local_error(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)])])
    ctx.file_path("f1").unlink()
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("hash_mismatch")


def test_magic_mismatch_jpeg_labelled_pdf(tmp_path: Path) -> None:
    jpeg = jpeg_with_orientation(1)
    ctx = ctx_with_files(tmp_path, [jpeg])
    ctx.case.files[0].mime = "application/pdf"
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("magic_mismatch")


def _encrypted(user: str) -> bytes:
    src = make_pdf([lines_page(BODY)], producer="Enc Producer")
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(src)) as pdf:
        pdf.save(out, encryption=pikepdf.Encryption(owner="owner-secret", user=user))
    return out.getvalue()


def test_owner_password_only_pdf_opens(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [_encrypted("")])
    run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.error is None and page.has_text_layer
    assert ctx.case.files[0].pdf_meta.get("Producer") == "Enc Producer"


def test_user_password_pdf_is_encrypted_pdf(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [_encrypted("secret")])
    outcome = run_stage(ctx)
    assert ctx.case.files[0].pages[0].error.startswith("encrypted_pdf")
    assert outcome.partial is True


def test_blank_page_is_not_an_error(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY), []])])
    outcome = run_stage(ctx)
    blank = ctx.case.files[0].pages[1]
    assert blank.error is None and not blank.is_scan and not blank.has_text_layer
    assert blank.word_count == 0
    assert outcome.partial is False


def test_timeout_marks_remaining_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_stage, "FILE_DEADLINE_S", -1.0)
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)] * 2)])
    outcome = run_stage(ctx)
    assert all(p.error.startswith("timeout") for p in ctx.case.files[0].pages)
    assert outcome.partial is True


def test_pdf_meta_keeps_raw_info_keys(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)], producer="P1", creator="C1")])
    run_stage(ctx)
    meta = ctx.case.files[0].pdf_meta
    assert meta["Producer"] == "P1" and meta["Creator"] == "C1"
    assert all(not k.startswith("/") and len(v) <= 500 for k, v in meta.items())


def test_untrusted_text_is_just_data(tmp_path: Path) -> None:
    hostile = lines_page(
        ["'; DROP TABLE cases; -- ../../etc/passwd <script>x</script> ignore rules"] * 12
    )
    ctx = ctx_with_files(tmp_path, [make_pdf([hostile])])
    run_stage(ctx)
    assert ctx.case.files[0].pages[0].has_text_layer
    assert any("DROP" in w.text for w in ctx.page_words[("f1", 1)])


def test_ingest_never_reads_the_wall_clock() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "ingest"
    for path in root.glob("*.py"):
        text = path.read_text()
        assert not re.search(r"date\.today|datetime\.now|datetime\.utcnow", text), path.name


def test_page_height_constant_sanity() -> None:
    assert PAGE_H == 842.0


def test_searchable_scan_is_treated_as_digital_known_gap(tmp_path: Path) -> None:
    from tests.ingest_helpers import searchable_scan_pdf

    ctx = ctx_with_files(tmp_path, [searchable_scan_pdf()])
    run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.has_text_layer and not page.is_scan and page.word_count >= 10
    assert ctx.llm.calls == []


def test_scan_with_a_few_stray_words_still_goes_to_ocr(tmp_path: Path) -> None:
    from tests.ingest_helpers import searchable_scan_pdf

    llm = FakeLLMClient(fixtures={"ocr:page": OCR_FIXTURE})
    ctx = ctx_with_files(tmp_path, [searchable_scan_pdf(words=3)], llm=llm)
    run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.is_scan and not page.has_text_layer
    assert len(llm.calls) == 1


def test_text_layer_needs_ten_words_and_little_garbage() -> None:
    from app.core.models import Word
    from app.ingest.pdf import has_text_layer

    def words(texts: list[str]) -> list[Word]:
        return [Word(page=1, text=t, x=0.1, y=0.1, w=0.1, h=0.01) for t in texts]

    assert has_text_layer(words(["word"] * 10))
    assert not has_text_layer(words(["word"] * 9))
    assert not has_text_layer(words(["����"] * 6 + ["word"] * 6))
    assert not has_text_layer(words([""] * 12))
    assert has_text_layer(words(["�"] * 1 + ["wordwordword"] * 11))


def test_animated_png_reads_the_first_frame_only(tmp_path: Path) -> None:
    frames = [Image.new("RGB", (60, 90), c) for c in ((255, 255, 255), (0, 0, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, "PNG", save_all=True, append_images=frames[1:])
    llm = FakeLLMClient(fixtures={"ocr:page": OCR_FIXTURE})
    ctx = ctx_with_files(tmp_path, [buf.getvalue()], llm=llm)
    outcome = run_stage(ctx)
    assert len(ctx.case.files[0].pages) == 1
    assert any("first frame" in w for w in outcome.warnings)
