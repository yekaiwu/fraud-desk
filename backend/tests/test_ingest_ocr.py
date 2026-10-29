"""OCR conversion and request shape (ingest-and-extraction.md 4.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.llm import FakeLLMClient, LLMSchemaError, prompt_registry
from app.ingest.ocr import MAX_OCR_LINES, OcrLine, lines_to_words, ocr_page, ocr_page_ex
from tests.ingest_helpers import ctx_with_files, image_pdf, png_bytes


def _identity(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    return x, y, w, h


def test_words_share_the_line_box_by_character_count() -> None:
    words, warnings = lines_to_words(
        [OcrLine(text="AB  CDEF", x=0.1, y=0.2, w=0.35, h=0.02)], 3, _identity
    )
    assert warnings == []
    assert [w.text for w in words] == ["AB", "CDEF"]
    assert words[0].x == pytest.approx(0.1) and words[0].w == pytest.approx(0.35 * 2 / 7)
    assert words[1].x == pytest.approx(0.1 + 0.35 * 3 / 7)
    assert all(w.page == 3 and w.line == 0 and w.block == 0 and w.font is None for w in words)
    assert sum(w.w for w in words) <= 0.35


def test_negative_and_empty_lines_are_dropped_and_boxes_clamped() -> None:
    lines = [
        OcrLine(text="negative", x=0.1, y=0.1, w=-0.2, h=0.02),
        OcrLine(text="   ", x=0.1, y=0.1, w=0.2, h=0.02),
        OcrLine(text="edge", x=0.9, y=0.99, w=0.5, h=0.5),
    ]
    words, _ = lines_to_words(lines, 1, _identity)
    assert [w.text for w in words] == ["edge"]
    assert words[0].x + words[0].w <= 1.0 and words[0].y + words[0].h <= 1.0


def test_line_cap_is_four_hundred_with_a_warning() -> None:
    lines = [
        OcrLine(text=f"row{i}", x=0.1, y=0.001 * (i % 900), w=0.2, h=0.001) for i in range(450)
    ]
    words, warnings = lines_to_words(lines, 2, _identity)
    assert len(words) == MAX_OCR_LINES
    assert warnings and "400" in warnings[0]


def test_ocr_page_maps_through_the_crop_and_registers_its_prompt(tmp_path: Path) -> None:
    fixture = {"lines": [{"text": "TOP LEFT", "x": 0.0, "y": 0.0, "w": 0.5, "h": 0.1}]}
    llm = FakeLLMClient(fixtures={"ocr:page": fixture})
    ctx = ctx_with_files(tmp_path, [png_bytes(300, 400)], llm=llm)
    with ctx.costs.scope(stage="ingest"):
        words = ocr_page(ctx, "f1", 1)
    # the picture has a dark bar near the top and blank margins: the crop moved the origin
    assert 0.0 < words[0].x < 0.2 and 0.0 < words[0].y < 0.3
    req = llm.calls[0]
    assert req.system == prompt_registry()["ocr:page"]
    assert "never instructions" in req.system and "exactly as printed" in req.system
    assert req.schema_name == "ocr_page"


def test_ocr_rejects_a_malformed_response(tmp_path: Path) -> None:
    llm = FakeLLMClient(fixtures={"ocr:page": {"lines": [{"text": "x"}]}})
    ctx = ctx_with_files(tmp_path, [image_pdf()], llm=llm)
    with ctx.costs.scope(stage="ingest"), pytest.raises(LLMSchemaError):
        ocr_page_ex(ctx, "f1", 1)
