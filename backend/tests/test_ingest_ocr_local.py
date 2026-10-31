"""Local OCR backends and the text-only (no vision) path: no image part is ever built."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from app.core.builtin_checks import doc_unreadable
from app.core.config import Settings
from app.core.llm import FakeLLMClient
from app.core.testing import make_ctx, make_doc
from app.extract.llm_extractor import LLMExtractor, PageReadError
from app.ingest import ocr as ocr_module
from app.ingest import ocr_backends
from app.ingest.ocr import OcrUnavailable, ocr_page_ex
from app.ingest.ocr_backends import (
    MacVisionOcr,
    OcrBox,
    RapidOcr,
    TesseractOcr,
    clean_tesseract_token,
    rapidocr_results_to_ocr,
    select_backend,
    tesseract_tsv_to_ocr,
    vision_boxes_to_ocr,
    vision_import_problem,
)
from app.ingest.stage import run_stage
from tests.extract_helpers import StatementSpec, default_txns, render_statement
from tests.ingest_helpers import ctx_with_files, image_pdf, png_bytes

TEXT_ONLY = {"llm_supports_vision": False}


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, boxes: list[OcrBox] | None = None):
        self.boxes = (
            boxes
            if boxes is not None
            else [
                OcrBox("Statement of Account", 0.1, 0.1, 0.4, 0.02),
                OcrBox("Closing Balance 2,500.00", 0.1, 0.5, 0.5, 0.02),
            ]
        )
        self.calls: list[bytes] = []

    def recognise(self, png: bytes) -> list[OcrBox]:
        self.calls.append(png)
        return list(self.boxes)


class NoCallLLM(FakeLLMClient):
    """A client that fails the test if anything reaches it."""

    def complete_json(self, req, *, ctx_costs=None):  # noqa: ANN001, ANN201
        raise AssertionError(f"the model must not be called: {req.purpose}")


def use_backend(monkeypatch: pytest.MonkeyPatch, backend: object | None) -> None:
    monkeypatch.setattr(ocr_module, "select_backend", lambda settings: backend)


# ----------------------------------------------------------------------------- conversion


def test_vision_observations_flip_to_top_left_origin() -> None:
    boxes = vision_boxes_to_ocr(
        [
            ("TOP LINE", 1.0, [0.06, 0.93, 0.30, 0.02]),
            ("bottom line", 0.9, (0.1, 0.02, 0.2, 0.01)),
            ("   ", 1.0, [0.1, 0.1, 0.1, 0.1]),
            ("broken", 1.0),
            (7, 1.0, [0, 0, 1, 1]),
        ]
    )
    assert [b.text for b in boxes] == ["TOP LINE", "bottom line"]
    assert boxes[0].y == pytest.approx(1.0 - 0.95) and boxes[0].y < boxes[1].y
    assert boxes[1].y == pytest.approx(0.97)
    assert all(0 <= b.x <= 1 and 0 <= b.y <= 1 and b.y + b.h <= 1.0 + 1e-9 for b in boxes)


class FakeOcrmacModule:
    """Stands in for ``ocrmac.ocrmac``: records the image it was given."""

    seen: list[tuple[tuple[int, int], str]] = []

    class OCR:
        def __init__(self, image, recognition_level="accurate", **_kw):  # noqa: ANN001, ANN003
            FakeOcrmacModule.seen.append((image.size, recognition_level))

        def recognize(self):  # noqa: ANN201
            return [("Hello World", 1.0, [0.1, 0.8, 0.3, 0.05])]


def test_mac_vision_backend_drives_ocrmac_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ocr_backends, "_import_ocrmac", lambda: FakeOcrmacModule)
    backend = MacVisionOcr()
    (box,) = backend.recognise(png_bytes(120, 80))
    assert FakeOcrmacModule.seen[-1] == ((120, 80), "accurate")
    assert box.text == "Hello World" and box.y == pytest.approx(0.15)


# ----------------------------------------------------------------------------- selection


def settings(**kw) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, llm_backend="fake", **kw)


def test_select_none_is_none() -> None:
    assert select_backend(settings(ocr_backend="none")) is None


def test_select_auto_without_ocrmac_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> None:
        raise ImportError("no ocrmac")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ocr_backends, "_import_ocrmac", missing)
    assert select_backend(settings(ocr_backend="auto")) is None
    assert "uv sync --extra ocr" in (vision_import_problem() or "")


def test_select_auto_off_darwin_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ocr_backends, "_import_ocrmac", lambda: FakeOcrmacModule)
    assert select_backend(settings(ocr_backend="auto")) is None
    assert "needs macOS" in (vision_import_problem() or "")


def test_select_auto_uses_vision_when_importable_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ocr_backends, "_import_ocrmac", lambda: FakeOcrmacModule)
    assert isinstance(select_backend(settings(ocr_backend="auto")), MacVisionOcr)
    assert isinstance(select_backend(settings(ocr_backend="vision")), MacVisionOcr)


def test_select_vision_explicit_but_missing_says_how_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(OcrUnavailable, match="macOS"):
        select_backend(settings(ocr_backend="vision"))


def test_a_broken_ocrmac_install_is_a_reason_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("pyobjc exploded")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ocr_backends, "_import_ocrmac", boom)
    assert "RuntimeError" in (vision_import_problem() or "")
    assert select_backend(settings(ocr_backend="auto")) is None


# ----------------------------------------------------------------------------- ocr_page_ex


def test_text_only_model_reads_a_scan_with_the_local_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeOcr()
    use_backend(monkeypatch, fake)
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=NoCallLLM(), **TEXT_ONLY)
    with ctx.costs.scope(stage="ingest"):
        words, warnings = ocr_page_ex(ctx, "f1", 1)
    assert warnings == []
    assert [w.text for w in words][:3] == ["Statement", "of", "Account"]
    # fractions of the PAGE (the render crop is undone), inside [0, 1]
    assert all(0 <= w.x <= 1 and 0 <= w.y <= 1 and w.x + w.w <= 1.0001 for w in words)
    assert len(fake.calls) == 1 and fake.calls[0].startswith(b"\x89PNG")
    assert ctx.costs.costs() == []  # local OCR is not a model call


def test_stage_with_local_ocr_gives_words_and_no_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_backend(monkeypatch, FakeOcr())
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=NoCallLLM(), **TEXT_ONLY)
    outcome = run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.is_scan and page.error is None and page.word_count > 0
    assert ctx.page_words[("f1", 1)] and outcome.partial is False


def test_no_reader_is_an_unavailable_page_with_a_reason_and_an_info_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_backend(monkeypatch, None)
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=NoCallLLM(), **TEXT_ONLY)
    outcome = run_stage(ctx)  # does not raise
    page = ctx.case.files[0].pages[0]
    assert page.error is not None and page.error.startswith("ocr_unavailable: ")
    assert "text-only" in page.error
    assert outcome.partial is True and outcome.detail["unreadable_pages"] == 1
    doc = make_doc("bank_statement", id="d1", page_end=1)
    flag_ctx = make_ctx([doc], files_extra=[ctx.case.files[0]])
    (flag,) = doc_unreadable(flag_ctx)
    assert flag.severity == "info" and "ocr_unavailable" in flag.evidence


def test_ocr_none_with_text_only_model_names_the_setting(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=NoCallLLM(), ocr_backend="none", **TEXT_ONLY)
    run_stage(ctx)
    assert "GLDB_OCR_BACKEND=none" in ctx.case.files[0].pages[0].error


def test_vision_model_keeps_using_model_ocr_by_default(tmp_path: Path) -> None:
    fixture = {"lines": [{"text": "FROM MODEL", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.02}]}
    llm = FakeLLMClient(fixtures={"ocr:page": fixture})
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=llm)  # vision on, ocr_backend=auto
    run_stage(ctx)
    assert [w.text for w in ctx.page_words[("f1", 1)]] == ["FROM", "MODEL"]
    assert len(llm.calls) == 1 and len(llm.calls[0].images) == 1


def test_ocr_backend_vision_overrides_the_model_even_when_it_has_vision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_backend(monkeypatch, FakeOcr())
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=NoCallLLM(), ocr_backend="vision")
    run_stage(ctx)
    assert ctx.page_words[("f1", 1)]


# ----------------------------------------------------------------------------- extraction


def test_text_only_extraction_of_a_scan_sends_ocr_text_and_no_image(tmp_path: Path) -> None:
    from tests.extract_helpers import empty_page, make_case

    seen: list = []

    def answer(req):  # noqa: ANN001, ANN202
        seen.append(req)
        return empty_page("bank_statement")

    llm = FakeLLMClient({"extract:bank_statement": answer})
    spec = StatementSpec(txns=default_txns(5))
    settings_ = Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="fake",
        extractor_mode="llm",
        **TEXT_ONLY,
    )
    ctx, doc, sha = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=settings_
    )  # is_scan with words present == a scan that local OCR has read
    reading = LLMExtractor().read_page(doc, ctx, 1)
    (req,) = seen
    assert req.images == []
    assert req.cache_extra == f"{sha}:1:ocr_text" and reading.mode == "ocr_text"
    assert "Balance Brought Forward" in req.user  # the OCR text is what the model reads


def test_text_only_extraction_of_a_scan_without_ocr_words_is_unreadable_not_imaged(
    tmp_path: Path,
) -> None:
    from tests.extract_helpers import make_case

    llm = NoCallLLM()
    settings_ = Settings(
        _env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake", **TEXT_ONLY
    )
    ctx, doc, _ = make_case(
        render_statement(StatementSpec(txns=default_txns(3))),
        "bank_statement",
        llm=llm,
        is_scan=True,
        keep_words=False,
        settings=settings_,
    )
    rendered: list = []
    with pytest.raises(PageReadError) as err:
        LLMExtractor(render=lambda *a, **k: rendered.append(a) or b"png").read_page(doc, ctx, 1)
    assert err.value.kind == "no_text" and rendered == []  # nothing was even rendered
    result = LLMExtractor().extract(doc, ctx)
    assert "unreadable: page 1: no_text" in result.warnings


# ----------------------------------------------------------------------------- real macOS Vision

try:  # the optional 'ocr' extra
    HAVE_OCRMAC = vision_import_problem() is None
except Exception:  # noqa: BLE001
    HAVE_OCRMAC = False


def _sample_statement_pdf(tmp_path: Path) -> bytes:
    """A generated DBS-style statement: from samples/ when present, else generated into tmp."""
    existing = Path(__file__).resolve().parents[2] / "samples/bundles/loan-clean/digital"
    candidates = sorted(existing.glob("bank_statement_*.pdf"))
    if candidates:
        return candidates[0].read_bytes()
    backend_dir = str(Path(__file__).resolve().parents[1])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from scripts.make_bundle import BundleSpec, build_bundle

    out = tmp_path / "loan-clean"
    build_bundle(BundleSpec(scenario="loan", variant="clean"), out)
    return sorted((out / "digital").glob("bank_statement_*.pdf"))[0].read_bytes()


@pytest.mark.skipif(not HAVE_OCRMAC, reason="ocrmac / macOS Vision not available")
def test_real_macos_vision_reads_a_rendered_sample_page(tmp_path: Path) -> None:
    import pymupdf
    from PIL import Image

    pdf = _sample_statement_pdf(tmp_path)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    ctx = ctx_with_files(
        tmp_path, [buf.getvalue()], llm=NoCallLLM(), ocr_backend="vision", **TEXT_ONLY
    )
    outcome = run_stage(ctx)
    page = ctx.case.files[0].pages[0]
    assert page.is_scan and page.error is None, outcome.warnings
    words = ctx.page_words[("f1", 1)]
    texts = " ".join(w.text for w in words)
    assert "Statement" in texts and "Account" in texts
    assert all(0 <= w.x <= 1 and 0 <= w.y <= 1 and w.w > 0 and w.h > 0 for w in words)
    title = next(w for w in words if w.text == "Statement")
    assert title.y < 0.25  # the heading is near the top of the page (top-left origin)
    assert max(w.y for w in words) > 0.5  # and text continues down the page


# ----------------------------------------------------------------------------- Tesseract

TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def tsv_word(
    block: int, line: int, word: int, left: int, top: int, w: int, h: int, conf: float, text: str
) -> str:
    return f"5\t1\t{block}\t1\t{line}\t{word}\t{left}\t{top}\t{w}\t{h}\t{conf}\t{text}\n"


def test_tesseract_tsv_joins_words_into_lines_with_fractional_boxes() -> None:
    tsv = (
        TSV_HEAD
        + "4\t1\t1\t1\t1\t0\t10\t20\t200\t20\t-1\t\n"  # a line row, not a word
        + tsv_word(1, 1, 2, 110, 20, 90, 20, 95.0, "World")
        + tsv_word(1, 1, 1, 10, 20, 80, 20, 96.0, "Hello")
        + tsv_word(1, 2, 1, 10, 60, 50, 20, 91.0, "1,234.50")
    )
    boxes = tesseract_tsv_to_ocr(tsv, 400, 200)
    assert [b.text for b in boxes] == ["Hello World", "1,234.50"]
    first = boxes[0]
    assert (first.x, first.y, first.w, first.h) == pytest.approx(
        (10 / 400, 20 / 200, 190 / 400, 20 / 200)
    )


def test_tesseract_tsv_drops_empty_words_and_negative_confidence() -> None:
    tsv = (
        TSV_HEAD
        + tsv_word(1, 1, 1, 10, 20, 50, 20, -1.0, "ghost")
        + tsv_word(1, 1, 2, 70, 20, 50, 20, 90.0, "   ")
        + tsv_word(1, 1, 3, 130, 20, 50, 20, 90.0, "kept")
    )
    assert [b.text for b in tesseract_tsv_to_ocr(tsv, 400, 200)] == ["kept"]
    assert tesseract_tsv_to_ocr(TSV_HEAD, 0, 0) == []


def test_select_tesseract_without_the_program_says_how_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr_backends, "find_tesseract", lambda: None)
    with pytest.raises(OcrUnavailable, match="brew install tesseract"):
        select_backend(settings(ocr_backend="tesseract"))


def test_select_tesseract_with_the_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_backends, "find_tesseract", lambda: "/fake/tesseract")
    assert isinstance(select_backend(settings(ocr_backend="tesseract")), TesseractOcr)


def test_tesseract_recognise_runs_the_program_and_reads_its_tsv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from PIL import Image

    seen: dict = {}

    def fake_run(argv, **kw):  # noqa: ANN001, ANN003, ANN202
        seen["argv"], seen["input"] = argv, kw["input"]
        out = TSV_HEAD + tsv_word(1, 1, 1, 80, 20, 160, 40, 93.0, "Balance")
        return subprocess.CompletedProcess(argv, 0, stdout=out.encode(), stderr=b"")

    monkeypatch.setattr(ocr_backends, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(ocr_backends.subprocess, "run", fake_run)
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buf, "PNG")
    boxes = TesseractOcr().recognise(buf.getvalue())
    assert (
        seen["argv"][0] == "/fake/tesseract"
        and seen["argv"][-1] == "tsv"
        and seen["input"]  # the page is sent on stdin (upscaled)
    )
    assert [b.text for b in boxes] == ["Balance"] and boxes[0].x == pytest.approx(0.1)


def test_tesseract_failure_is_an_unavailable_page_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from PIL import Image

    monkeypatch.setattr(ocr_backends, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(
        ocr_backends.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"boom"),
    )
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, "PNG")
    with pytest.raises(OcrUnavailable, match="exit 1"):
        TesseractOcr().recognise(buf.getvalue())


# ----------------------------------------------------------------------------- RapidOCR


def test_rapidocr_results_become_fractional_top_left_boxes() -> None:
    results = [
        [[[40, 100], [240, 100], [240, 120], [40, 120]], "Closing balance", 0.98],
        [[[40, 20], [140, 20], [140, 40], [40, 40]], "Statement", 0.99],
        [[[0, 0], [10, 0], [10, 10], [0, 10]], "   ", 0.9],  # blank text is dropped
        "not a result",  # malformed items are skipped
    ]
    boxes = rapidocr_results_to_ocr(results, 400, 200)
    assert [b.text for b in boxes] == ["Statement", "Closing balance"]  # reading order
    assert (boxes[1].x, boxes[1].y, boxes[1].w, boxes[1].h) == pytest.approx((0.1, 0.5, 0.5, 0.1))
    assert (
        rapidocr_results_to_ocr(None, 400, 200) == []
        and rapidocr_results_to_ocr(results, 0, 0) == []
    )


def test_select_rapidocr_without_the_package_says_how_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> object:
        raise ImportError("no rapidocr")

    monkeypatch.setattr(ocr_backends, "_import_rapidocr", missing)
    with pytest.raises(OcrUnavailable, match="uv sync --extra ocr"):
        select_backend(settings(ocr_backend="rapidocr"))


def test_rapidocr_recognise_passes_a_bgr_array_and_reads_the_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    seen: dict = {}

    class FakeEngine:
        def __call__(self, array):  # noqa: ANN001, ANN204
            seen["shape"], seen["red_in_blue_channel"] = array.shape, int(array[0, 0, 0])
            return [[[[10, 10], [110, 10], [110, 30], [10, 30]], "Hello", 0.9]], 0.01

    class FakeModule:
        RapidOCR = FakeEngine

    monkeypatch.setattr(ocr_backends, "_import_rapidocr", lambda: FakeModule)
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), (255, 0, 0)).save(buf, "PNG")
    boxes = select_backend(settings(ocr_backend="rapidocr")).recognise(buf.getvalue())
    assert isinstance(select_backend(settings(ocr_backend="rapidocr")), RapidOcr)
    assert (
        seen["shape"] == (100, 200, 3) and seen["red_in_blue_channel"] == 0
    )  # red pixel is BGR (0, 0, 255)
    assert [b.text for b in boxes] == ["Hello"] and boxes[0].w == pytest.approx(0.5)


def test_tesseract_tsv_splits_a_table_row_at_wide_gaps() -> None:
    row = (
        tsv_word(1, 1, 1, 10, 20, 100, 20, 95.0, "01-Apr-2026")
        + tsv_word(1, 1, 2, 120, 20, 60, 20, 95.0, "RENT")
        + tsv_word(1, 1, 3, 190, 20, 60, 20, 95.0, "PAYMENT")
        + tsv_word(1, 1, 4, 500, 20, 90, 20, 95.0, "6,100.00")
        + tsv_word(1, 1, 5, 700, 20, 90, 20, 95.0, "83,421.88")
    )
    boxes = tesseract_tsv_to_ocr(TSV_HEAD + row, 1000, 200)
    assert [b.text for b in boxes] == ["01-Apr-2026 RENT PAYMENT", "6,100.00", "83,421.88"]
    assert boxes[1].x == pytest.approx(0.5) and boxes[2].x == pytest.approx(0.7)


def test_tesseract_token_cleanup_only_touches_specks_and_glued_marks() -> None:
    assert clean_tesseract_token("207,282.43,") == "207,282.43"
    assert clean_tesseract_token("5.39CR") == "5.39 CR"
    assert clean_tesseract_token("6,100.00DR") == "6,100.00 DR"
    assert clean_tesseract_token("1,175.12") == "1,175.12"
    assert clean_tesseract_token("$****854E") == "S****854E"
    assert clean_tesseract_token("$1234567A") == "S1234567A"
    assert (
        clean_tesseract_token("§****037B") == "S****037B"
        and clean_tesseract_token("§1102037B") == "S1102037B"
    )
    assert clean_tesseract_token("$5.00") == "$5.00" and clean_tesseract_token("$100") == "$100"
    assert clean_tesseract_token("No.") == "No." and clean_tesseract_token("12,") == "12,"


def test_tesseract_upscales_a_small_page_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from PIL import Image

    seen: dict = {}

    def fake_run(argv, **kw):  # noqa: ANN001, ANN003, ANN202
        with Image.open(io.BytesIO(kw["input"])) as sent:
            seen["size"] = sent.size
        out = TSV_HEAD + tsv_word(1, 1, 1, 1000, 500, 400, 100, 90.0, "Total")
        return subprocess.CompletedProcess(argv, 0, stdout=out.encode(), stderr=b"")

    monkeypatch.setattr(ocr_backends, "find_tesseract", lambda: "/fake/tesseract")
    monkeypatch.setattr(ocr_backends.subprocess, "run", fake_run)
    buf = io.BytesIO()
    Image.new("L", (1240, 1754), 255).save(buf, "PNG")
    boxes = TesseractOcr().recognise(buf.getvalue())
    assert seen["size"] == (round(1240 * 2600 / 1754), 2600)  # scaled to the target long side
    assert boxes[0].x == pytest.approx(1000 / seen["size"][0])  # fractions use the scaled size


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("$31290441", "S3129044I"),  # S read as $, check letter I read as 1
        ("M430707420", "M43070742O"),  # FIN: check letter O read as 0
        ("$****7491", "S****749I"),  # masked ID card number
        ("M32552516l", "M32552516I"),  # lower-case l for I
        ("201741243k),", "201741243K),"),
        ("167.289.02", "167,289.02"),  # separators read as full stops
        ("1.234.567.89", "1,234,567.89"),
        ("8916269856", "8916269856"),  # an account number is not an ID
        ("$1,872.21", "$1,872.21"),  # a dollar amount stays
        ("12.345", "12.345"),
        ("S3129044I", "S3129044I"),
        ("S1-2026-0112,", "SI-2026-0112,"),  # the I of an invoice number
        ("S!-2026-0110", "SI-2026-0110"),
        ("S1-2026", "S1-2026"),
    ],
)
def test_token_repairs_follow_the_shape_of_ids_and_amounts(raw: str, want: str) -> None:
    assert clean_tesseract_token(raw) == want


@pytest.mark.parametrize(
    ("line", "want"),
    [
        ("PAYMENT TO ACME PTE LID", "PAYMENT TO ACME PTE LTD"),
        ("X ETE LTD", "X PTE LTD"),
        ("X PTELTD", "X PTE LTD"),
        ("BLUE PIE LTD", "BLUE PIE LTD"),  # a real word next to an intact LTD is left alone
        ("BLUE PIE LID", "BLUE PTE LTD"),
        ("UEN 2021839802", "UEN 202183980Z"),
        ("Account 8916269856", "Account 8916269856"),
        ("Acme Pte Ltd", "Acme Pte Ltd"),
    ],
)
def test_line_repairs_need_context(line: str, want: str) -> None:
    assert " ".join(ocr_backends.repair_line_tokens(line.split())) == want


def _tsv(*words: tuple[int, int, int, int, float, str]) -> str:
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        f"5\t1\t1\t1\t{1 + i}\t1\t{x}\t{y}\t{w}\t{h}\t{conf}\t{text}"
        for i, (x, y, w, h, conf, text) in enumerate(words)
    ]
    return "\n".join([head, *rows])


def _words(tsv: str) -> list[str]:
    return [r.split("\t")[11] for r in tsv.splitlines()[1:]]


def _bare_ocr() -> ocr_backends.TesseractOcr:
    ocr = object.__new__(ocr_backends.TesseractOcr)  # the passes under test never start the program
    return ocr


def test_sparse_pass_completes_a_clipped_header_value() -> None:
    page = _tsv(
        (100, 50, 120, 20, 92.0, "nvoice"),
        (240, 50, 30, 20, 96.0, "No:"),
        (300, 50, 140, 22, 90.0, "-2025-010"),
    )
    sparse = _tsv(
        (100, 50, 140, 20, 96.0, "Invoice"),
        (260, 50, 30, 20, 93.0, "No:"),
        (300, 50, 200, 22, 89.0, "INV-2025-0107"),
    )
    assert "INV-2025-0107" in _words(_bare_ocr()._complete_truncated_words(page, sparse))


def test_sparse_pass_leaves_a_correct_word_and_unrelated_differences_alone() -> None:
    page = _tsv((100, 50, 140, 20, 96.0, "Total"), (300, 50, 140, 22, 90.0, "1,234.50"))
    sparse = _tsv((100, 50, 140, 20, 96.0, "Total"), (300, 50, 140, 22, 90.0, "1,284.50"))
    # the page pass is complete and confident, so a different sparse reading does not replace it
    assert _words(_bare_ocr()._complete_truncated_words(page, sparse)) == ["Total", "1,234.50"]


def test_sparse_pass_ignores_a_low_confidence_sparse_word() -> None:
    page = _tsv((300, 50, 140, 22, 90.0, "-2025-010"))
    sparse = _tsv((300, 50, 200, 22, 40.0, "INV-2025-0107"))
    assert _words(_bare_ocr()._complete_truncated_words(page, sparse)) == ["-2025-010"]


def test_id_check_letter_read_as_a_bang_and_noise_words() -> None:
    assert clean_tesseract_token("$7665258!") == "S7665258I"
    assert clean_tesseract_token("S4966384|") == "S4966384I"
    assert ocr_backends._is_noise("ee", 12.0)
    assert ocr_backends._is_noise("————", 90.0)
    assert not ocr_backends._is_noise("ee", 80.0)  # Tesseract is sure of it
    assert not ocr_backends._is_noise("123", 5.0)  # digits stay whatever the confidence
    assert not ocr_backends._is_noise("Acme", 10.0)  # longer words stay
