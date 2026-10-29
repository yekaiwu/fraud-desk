"""Hardening found by the pre-publication security review: render size cap, no API docs behind the gate,
and the launcher refusing to start without a proxy secret."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.pack import MAX_RENDER_PIXELS, render_page_png

SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
H = {"X-Gldb-Proxy-Secret": SECRET}
ROOT = Path(__file__).resolve().parents[2]


def _pdf(tmp_path: Path, w: float, h: float) -> Path:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=w, height=h)
    path = tmp_path / "p.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_an_ordinary_page_still_renders(tmp_path) -> None:
    png = render_page_png(_pdf(tmp_path, 595, 842), "application/pdf", 1, 2000)
    assert png.startswith(b"\x89PNG") and MAX_RENDER_PIXELS >= 2000 * 2829


def test_a_very_tall_pdf_page_is_refused_not_rendered(tmp_path) -> None:
    # 1 x 14400 pt at width 2000 would be 2000 x 28.8 million pixels
    with pytest.raises(ValueError, match="too large"):
        render_page_png(_pdf(tmp_path, 1, 14400), "application/pdf", 1, 2000)


def test_a_very_tall_image_is_refused_not_resized(tmp_path) -> None:
    from PIL import Image

    path = tmp_path / "tall.png"
    Image.new("L", (1, 30000), 255).save(path)
    with pytest.raises(ValueError, match="too large"):
        render_page_png(path, "image/png", 1, 2000)


def test_api_docs_are_off_behind_the_gate_and_on_without_it(
    settings: Settings, monkeypatch
) -> None:
    dev = main.create_app(settings)
    assert (dev.docs_url, dev.redoc_url, dev.openapi_url) == ("/docs", "/redoc", "/openapi.json")
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    prod = main.create_app(settings)
    assert (prod.docs_url, prod.redoc_url, prod.openapi_url) == (None, None, None)
    c = TestClient(prod, client=("203.0.113.9", 50000))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path, headers=H).status_code == 404


@pytest.mark.parametrize("secret", ["", "short"])
def test_the_launcher_refuses_to_start_without_a_real_proxy_secret(tmp_path, secret) -> None:
    # Copy the launcher into a fake install tree whose env file has a missing or short secret; it must stop
    # before it ever reaches `uv run`.
    (tmp_path / "deploy").mkdir()
    script = tmp_path / "deploy" / "run-backend.sh"
    script.write_text((ROOT / "deploy" / "run-backend.sh").read_text())
    (tmp_path / "deploy" / ".env.production").write_text(f"GLDB_PROXY_SECRET={secret}\n")
    r = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=20)
    assert r.returncode == 78
    assert "at least 16" in r.stderr and "refusing to start" in r.stderr
