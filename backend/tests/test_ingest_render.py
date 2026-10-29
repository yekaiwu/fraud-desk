"""Rendering: UI render cache and the small model render (cost lever 6)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.core.storage import sha256_bytes
from app.ingest.render import RenderedImage, render_for_model, render_page_png
from tests.ingest_helpers import jpeg_with_orientation, lines_page, make_pdf, png_bytes

BODY = ["Statement of Account for Example Logistics Pte Ltd", "Opening Balance 1,000.00 forward"]


def _stored(tmp_path: Path, data: bytes, name: str) -> tuple[Path, str]:
    path = tmp_path / name
    path.write_bytes(data)
    return path, sha256_bytes(data)


def test_model_render_is_grayscale_small_and_deterministic(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    first = render_for_model(path, "application/pdf", 1, dpi=150)
    second = render_for_model(path, "application/pdf", 1, dpi=150)
    assert first.png == second.png
    img = Image.open(io.BytesIO(first.png))
    assert img.mode == "L"
    assert max(img.size) <= 2200
    # smaller than a colour render of the same page at the same dpi
    import pymupdf

    doc = pymupdf.open(path)
    colour = doc[0].get_pixmap(dpi=150, alpha=False).tobytes("png")
    uncropped = render_for_model(path, "application/pdf", 1, dpi=150, crop_margins=False)
    assert len(uncropped.png) < len(colour)
    assert len(first.png) <= len(uncropped.png)


def test_model_render_caps_the_longest_side(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    big = render_for_model(path, "application/pdf", 1, dpi=600, crop_margins=False)
    assert max(big.width_px, big.height_px) <= 2200


def test_margin_crop_and_to_page_round_trip(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    full = render_for_model(path, "application/pdf", 1, dpi=100, crop_margins=False)
    cropped = render_for_model(path, "application/pdf", 1, dpi=100)
    assert full.crop == (0.0, 0.0, 1.0, 1.0)
    assert cropped.crop != (0.0, 0.0, 1.0, 1.0)
    cx, cy, cw, ch = cropped.crop
    assert cw * ch <= 0.9
    x, y, w, h = cropped.to_page(0.0, 0.0, 1.0, 1.0)
    assert (x, y, w, h) == pytest.approx((cx, cy, cw, ch))
    x2, y2, w2, h2 = cropped.to_page(0.5, 0.5, 0.1, 0.1)
    assert x2 == pytest.approx(cx + 0.5 * cw) and w2 == pytest.approx(0.1 * cw)
    assert cropped.width_px / full.width_px == pytest.approx(cw, abs=0.02)


def test_to_page_is_identity_without_crop() -> None:
    img = RenderedImage(png=b"x", width_px=1, height_px=1)
    assert img.to_page(0.1, 0.2, 0.3, 0.4) == (0.1, 0.2, 0.3, 0.4)


def test_blank_page_is_not_cropped(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, make_pdf([[]]), "blank.pdf")
    rendered = render_for_model(path, "application/pdf", 1, dpi=72)
    assert rendered.crop == (0.0, 0.0, 1.0, 1.0)


def test_image_upload_render_applies_exif_orientation(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, jpeg_with_orientation(6, width=120, height=80), "a.jpg")
    rendered = render_for_model(path, "image/jpeg", 1, dpi=150, crop_margins=False)
    assert rendered.height_px > rendered.width_px  # transposed: 80 x 120
    assert render_for_model(path, "image/jpeg", 1, dpi=150, crop_margins=False).png == rendered.png


def test_image_is_never_upscaled(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, png_bytes(200, 300), "a.png")
    rendered = render_for_model(path, "image/png", 1, dpi=300, crop_margins=False)
    assert (rendered.width_px, rendered.height_px) == (200, 300)


def test_bad_page_number_is_a_value_error(tmp_path: Path) -> None:
    path, _ = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    with pytest.raises(ValueError):
        render_for_model(path, "application/pdf", 0, dpi=100)


def test_ui_render_is_cached_and_atomic(tmp_path: Path) -> None:
    path, sha = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    renders = tmp_path / "renders"
    first = render_page_png(path, sha, "application/pdf", 1, width_px=600, renders_dir=renders)
    assert first == renders / sha / "1_600.png"
    assert Image.open(first).width == 600
    mtime = first.stat().st_mtime_ns
    second = render_page_png(path, sha, "application/pdf", 1, width_px=600, renders_dir=renders)
    assert second == first and second.stat().st_mtime_ns == mtime
    assert not list((renders / sha).glob("*.tmp")) and not list((renders / sha).glob(".*"))


def test_ui_render_default_width_comes_from_dpi(tmp_path: Path) -> None:
    path, sha = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    out = render_page_png(
        path, sha, "application/pdf", 1, renders_dir=tmp_path / "r", render_dpi=100
    )
    assert out.name == f"1_{round(595 / 72 * 100)}.png"


def test_ui_render_width_is_clamped_and_image_works(tmp_path: Path) -> None:
    path, sha = _stored(tmp_path, png_bytes(300, 400), "a.png")
    out = render_page_png(path, sha, "image/png", 1, width_px=50, renders_dir=tmp_path / "r")
    assert out.name == "1_200.png" and Image.open(out).width == 200


def test_ui_render_rejects_bad_hash_and_page(tmp_path: Path) -> None:
    path, sha = _stored(tmp_path, make_pdf([lines_page(BODY)]), "a.pdf")
    with pytest.raises(ValueError):
        render_page_png(path, "../../etc", "application/pdf", 1, renders_dir=tmp_path / "r")
    with pytest.raises(ValueError):
        render_page_png(path, sha, "application/pdf", 0, renders_dir=tmp_path / "r")


def test_model_render_of_a_scan_with_a_bogus_72_dpi_density_still_follows_the_setting(
    tmp_path: Path,
) -> None:
    """A 1240 x 1754 scan that declares 72 dpi is an A4 page at 150 dpi, not a 17 inch sheet: a
    lower ``vision_dpi`` must make it smaller instead of being ignored (cost lever 6)."""
    buf = io.BytesIO()
    Image.new("RGB", (1240, 1754), (255, 255, 255)).save(buf, "JPEG", dpi=(72, 72))
    path, _ = _stored(tmp_path, buf.getvalue(), "scan.jpg")
    low = render_for_model(path, "image/jpeg", 1, dpi=72, crop_margins=False)
    high = render_for_model(path, "image/jpeg", 1, dpi=150, crop_margins=False)
    assert (low.width_px, low.height_px) == (596, 842)
    assert (high.width_px, high.height_px) == (1240, 1754)
