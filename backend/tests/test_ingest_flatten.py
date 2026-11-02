"""Page finding and straightening for phone photos (app/ingest/flatten.py)."""

from __future__ import annotations

import cv2
import numpy as np

from app.ingest.flatten import flatten

W, H = 1000, 1400


def page_png() -> bytes:
    img = np.full((H, W, 3), 245, np.uint8)
    for i in range(12):  # lines of "text"
        cv2.rectangle(img, (80, 100 + i * 100), (700 - i * 20, 130 + i * 100), (20, 20, 20), -1)
    cv2.rectangle(img, (800, 60), (920, 180), (30, 30, 200), -1)  # a marker at the top right
    return cv2.imencode(".png", img)[1].tobytes()


def photo_of(page: bytes, quad: list[tuple[float, float]], size=(1600, 1900)) -> bytes:
    src = cv2.imdecode(np.frombuffer(page, np.uint8), cv2.IMREAD_COLOR)
    dst = np.float32(quad)
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [W, 0], [W, H], [0, H]]), dst)
    desk = np.full((size[1], size[0], 3), 35, np.uint8)
    warped = cv2.warpPerspective(src, m, size, dst=desk, borderMode=cv2.BORDER_TRANSPARENT)
    return cv2.imencode(".png", warped)[1].tobytes()


def test_a_tilted_page_on_a_dark_desk_is_found_and_straightened() -> None:
    quad = [(260, 140), (1380, 220), (1330, 1760), (190, 1640)]
    out = flatten(photo_of(page_png(), quad))
    assert out.changed
    assert abs(out.height_px / out.width_px - 1.4) < 0.12  # the page's own shape, not A4 by decree
    img = cv2.imdecode(np.frombuffer(out.png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert img[: out.height_px // 5, : out.width_px // 3].min() < 120  # text sits at the top left
    assert np.median(img) > 200  # the desk is gone


def test_a_box_read_on_the_straightened_page_maps_back_onto_the_photo() -> None:
    quad = [(260, 140), (1380, 220), (1330, 1760), (190, 1640)]
    out = flatten(photo_of(page_png(), quad))
    # the red marker is at (800..920, 60..180) of the page, i.e. the same fractions of the straightened page
    x, y, w, h = out.to_original(0.80, 0.043, 0.12, 0.086)
    cx, cy = (x + w / 2) * 1600, (y + h / 2) * 1900
    # expected: page point (860, 120) through the same perspective
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [W, 0], [W, H], [0, H]]), np.float32(quad))
    ex, ey = cv2.perspectiveTransform(np.float32([[[860, 120]]]), m)[0][0]
    assert abs(cx - ex) < 25 and abs(cy - ey) < 25


def test_an_upright_flat_page_is_left_alone() -> None:
    out = flatten(page_png())
    assert not out.changed and out.to_original(0.1, 0.2, 0.3, 0.4) == (0.1, 0.2, 0.3, 0.4)


def test_no_page_outline_means_no_change() -> None:
    noise = np.random.default_rng(1).integers(0, 255, (400, 300, 3), np.uint8)
    out = flatten(cv2.imencode(".png", noise)[1].tobytes())
    assert not out.changed


def test_garbage_bytes_do_not_raise() -> None:
    assert not flatten(b"not an image").changed


def test_a_bright_region_that_is_not_page_shaped_is_left_alone() -> None:
    img = np.full((1000, 1000, 3), 30, np.uint8)
    cv2.rectangle(
        img, (100, 100), (900, 560), (240, 240, 240), -1
    )  # 800 x 460: a table, not a page
    cv2.putText(img, "x", (300, 300), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 5)
    assert not flatten(cv2.imencode(".png", img)[1].tobytes()).changed
