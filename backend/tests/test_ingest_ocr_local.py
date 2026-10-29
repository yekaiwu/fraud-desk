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
    select_backend,
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
