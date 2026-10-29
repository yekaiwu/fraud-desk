"""Image-channel tampers: pixel and EXIF edits on the JPEG renders (scan_hq, scan_lq, photo).

T40a EXIF Software names an editor, T40b EXIF DateTime later than DateTimeOriginal, T41 photo swap on
an ID card, T42 clone-stamp inside a statement page, T43 white box with typed digits over an amount.
The edit is applied to every degraded render present in the bundle. Ground truth keeps what is
printed: T43 changes the amount cell, the other tampers change no printed value.
"""

from __future__ import annotations

import io
import struct
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

from scripts._eval.tamper_core import (
    Ctx,
    Plan,
    TamperNotApplicable,
    TmpBundle,
    cell,
    credit_rows,
    to_box,
)
from scripts._gen.render_id import avatar_png
from scripts._gen.schema import Target

D = Decimal
QUALITY = {"scan_hq": 90, "scan_lq": 60, "photo": 70}
EXIF_DT = "%Y:%m:%d %H:%M:%S"


def scan_renders(tb: TmpBundle, ctx: Ctx) -> list[str]:
    out = [r for r in ctx.renders if r in QUALITY and r in tb.manifest.upload_order]
    if not out:
        raise TamperNotApplicable(
            "no JPEG render in this bundle (image-channel tampers need scan_hq, scan_lq or photo)"
        )
    return out


def jpeg_file(tb: TmpBundle, render: str, doc_key: str, page: int = 1) -> str:
    """The JPEG of ``page`` of document ``doc_key`` in ``render`` (one JPEG per page)."""
    for f in tb.manifest.files:
        if f.render != render or f.mime != "image/jpeg" or doc_key not in f.doc_keys:
            continue
        if f.path.endswith(f"_p{page:02d}.jpg"):
            return f.path
    raise TamperNotApplicable(f"no {render} page {page} for {doc_key}")


# ---------------------------------------------------------------------------- JPEG helpers


def replace_exif(jpeg: bytes, exif_bytes: bytes) -> bytes:
    """Replace (or insert) the Exif APP1 segment without re-encoding the pixels."""
    assert jpeg[:2] == b"\xff\xd8"
    out = bytearray(jpeg[:2])
    i = 2
    inserted = False
    while i < len(jpeg):
        if jpeg[i] != 0xFF:
            out += jpeg[i:]
            break
        marker = jpeg[i + 1]
        if marker == 0xDA:  # start of scan: copy the rest
            if not inserted:
                out += b"\xff\xe1" + struct.pack(">H", len(exif_bytes) + 2) + exif_bytes
                inserted = True
            out += jpeg[i:]
            break
        (length,) = struct.unpack(">H", jpeg[i + 2 : i + 4])
        seg = jpeg[i : i + 2 + length]
        if marker == 0xE1 and seg[4:10] == b"Exif\x00\x00":
            if not inserted:
                out += b"\xff\xe1" + struct.pack(">H", len(exif_bytes) + 2) + exif_bytes
                inserted = True
        else:
            out += seg
            if not inserted and marker != 0xE0:
                pass
        i += 2 + length
    return bytes(out)


def edit_exif(jpeg: bytes, *, software: str | None = None, shift_days: int | None = None) -> bytes:
    im = Image.open(io.BytesIO(jpeg))
    exif = im.getexif()
    sub = exif.get_ifd(0x8769)
    if software is not None:
        exif[0x0131] = software
    if shift_days is not None:
        orig = sub.get(0x9003) or exif.get(0x0132)
        if not orig:
            raise TamperNotApplicable("JPEG has no DateTimeOriginal")
        exif[0x0132] = (
            datetime.strptime(str(orig), EXIF_DT) + timedelta(days=shift_days)
        ).strftime(EXIF_DT)
    return replace_exif(jpeg, exif.tobytes())


def _save(im: Image.Image, quality: int, like: Image.Image) -> bytes:
    buf = io.BytesIO()
    exif = like.info.get("exif")
    kw: dict[str, Any] = {"exif": exif} if exif else {}
    im.convert("RGB").save(buf, "JPEG", quality=quality, **kw)
    return buf.getvalue()


def _px(box: dict[str, Any], size: tuple[int, int]) -> tuple[int, int, int, int]:
    w, h = size
    return (
        int(box["x"] * w),
        int(box["y"] * h),
        int((box["x"] + box["w"]) * w),
        int((box["y"] + box["h"]) * h),
    )


def _frac(
    rect: tuple[int, int, int, int], size: tuple[int, int], file: str, page: int = 1
) -> dict[str, Any]:
    w, h = size
    return {
        "file": file,
        "page": page,
        "x": rect[0] / w,
        "y": rect[1] / h,
        "w": (rect[2] - rect[0]) / w,
        "h": (rect[3] - rect[1]) / h,
    }


def _box_str(b: dict[str, Any]) -> str:
    return f"{b['file']}|{b['page']}|{b['x']:.5f}|{b['y']:.5f}|{b['w']:.5f}|{b['h']:.5f}"


# ----------------------------------------------------------------------------- EXIF tampers


def _id_doc(ctx: Ctx) -> str:
    docs = [d for d in ctx.docs("national_id")]
    return ctx.pick(docs, "ID card")["key"]


def _t40(ctx: Ctx, *, software: str | None, shift: int | None, what: str) -> Plan:
    key = _id_doc(ctx)

    def edit(tb: TmpBundle) -> None:
        for render in scan_renders(tb, ctx):
            rel = jpeg_file(tb, render, key)
            tb.replace_file(
                rel, edit_exif((tb.root / rel).read_bytes(), software=software, shift_days=shift)
            )

    return Plan(
        edit=f"{key} JPEG: {what}",
        file_edit=edit,
        locate=lambda b, n: (Target(doc_key=key), {"exif": "honest"}, {"exif": what}),
    )


def t40a(ctx: Ctx) -> Plan:
    return _t40(
        ctx,
        software="Adobe Photoshop 25.1",
        shift=None,
        what="EXIF Software = Adobe Photoshop 25.1",
    )


def t40b(ctx: Ctx) -> Plan:
    return _t40(ctx, software=None, shift=2, what="EXIF DateTime = DateTimeOriginal + 2 days")


# ------------------------------------------------------------------------------ geometry


def _affine_from_fields(doc: dict[str, Any], render: str) -> np.ndarray | None:
    """Least-squares affine map digital page fractions -> scan page fractions, fitted on the
    centres of every field box that has a ``boxes[render]`` entry."""
    src, dst = [], []
    for f in doc["fields"].values():
        b, sb = f.get("box"), (f.get("boxes") or {}).get(render)
        if b and sb:
            src.append((b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, 1.0))
            dst.append((sb["x"] + sb["w"] / 2, sb["y"] + sb["h"] / 2))
    if len(src) < 3:
        return None
    m, *_ = np.linalg.lstsq(np.array(src), np.array(dst), rcond=None)
    return m


def _map_box(m: np.ndarray, b: dict[str, float]) -> tuple[float, float, float, float]:
    pts = [
        (b["x"], b["y"]),
        (b["x"] + b["w"], b["y"]),
        (b["x"], b["y"] + b["h"]),
        (b["x"] + b["w"], b["y"] + b["h"]),
    ]
    mapped = np.array([[x, y, 1.0] for x, y in pts]) @ m
    x0, y0 = mapped.min(axis=0)
    x1, y1 = mapped.max(axis=0)
    return float(x0), float(y0), float(x1 - x0), float(y1 - y0)


def _avatar_rect(ctx: Ctx, key: str) -> dict[str, Any]:
    path = next(d.digital_path for d in ctx.base.documents if d.key == key)
    with pymupdf.open(str(ctx.base_dir / path)) as doc:
        page = doc[0]
        for img in page.get_images(full=True):
            rects = page.get_image_rects(img[0])
            if rects:
                r = rects[0]
                w, h = page.rect.width, page.rect.height
                return {
                    "file": path,
                    "page": 1,
                    "x": r.x0 / w,
                    "y": r.y0 / h,
                    "w": r.width / w,
                    "h": r.height / h,
                }
    raise TamperNotApplicable("no photo on the ID card page")


def t41(ctx: Ctx) -> Plan:
    key = _id_doc(ctx)
    photo = _avatar_rect(ctx, key)
    doc = next(d for d in ctx.gt["documents"] if d["key"] == key)
    state: dict[str, str] = {}

    def edit(tb: TmpBundle) -> None:
        for render in scan_renders(tb, ctx):
            m = _affine_from_fields(doc, render)
            if m is None:
                raise TamperNotApplicable("cannot locate the photo on the scan")
            rel = jpeg_file(tb, render, key)
            im = Image.open(io.BytesIO((tb.root / rel).read_bytes()))
            im.load()
            x, y, w, h = _map_box(m, photo)
            rect = _px({"x": x, "y": y, "w": w, "h": h}, im.size)
            src = Image.open(io.BytesIO(avatar_png(ctx.rng.randrange(1, 10**6)))).convert("RGB")
            buf = io.BytesIO()
            src.save(buf, "JPEG", quality=95)  # the source is a JPEG of its own (q95)
            src = Image.open(io.BytesIO(buf.getvalue())).resize(
                (rect[2] - rect[0], rect[3] - rect[1])
            )
            out = im.convert("RGB").copy()
            out.paste(src, (rect[0], rect[1]))
            tb.replace_file(rel, _save(out, 90, im))
            state[f"box_{render}"] = _box_str(_frac(rect, im.size, rel))

    def locate(b, n):  # noqa: ANN001, ANN202
        return (
            Target(doc_key=key, field="photo", box=to_box(photo)),
            {"photo": "original"},
            {"photo": "swapped", **state},
        )

    return Plan(
        edit=f"{key}: photo swapped for another synthetic avatar (source q95, final save q90)",
        file_edit=edit,
        locate=locate,
    )


# --------------------------------------------------------------------------- statements


def _stmt_page1_rows(ctx: Ctx) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    st = ctx.pick(ctx.docs("bank_statement"), "statement")
    rows = [
        r for r in st["tables"]["transactions"]["rows"] if r.get("page") == 1 and r.get("boxes")
    ]
    if len(rows) < 12:
        raise TamperNotApplicable("too few rows with scan boxes on page 1")
    return st, rows


def t42(ctx: Ctx) -> Plan:
    st, rows = _stmt_page1_rows(ctx)
    key = st["key"]
    i1 = ctx.rng.randrange(1, 4)
    i2 = i1 + ctx.rng.randrange(6, 9)
    src_row, dst_row = rows[i1], rows[i2]
    state: dict[str, str] = {}

    def edit(tb: TmpBundle) -> None:
        for render in scan_renders(tb, ctx):
            sb, db_ = src_row["boxes"].get(render), dst_row["boxes"].get(render)
            if not sb or not db_:
                raise TamperNotApplicable("rows have no scan boxes")
            rel = jpeg_file(tb, render, key)
            im = Image.open(io.BytesIO((tb.root / rel).read_bytes()))
            im.load()
            img = im.convert("RGB")
            sx0, sy0, sx1, sy1 = _px(
                {
                    "x": sb["x"] + sb["w"] * 0.45,
                    "y": sb["y"],
                    "w": sb["w"] * 0.55,
                    "h": sb["h"] * 3,
                },
                im.size,
            )
            patch = img.crop((sx0, sy0, sx1, sy1))
            dx0, dy0 = int((db_["x"] + db_["w"] * 0.45) * im.size[0]), int(db_["y"] * im.size[1])
            img.paste(patch, (dx0, dy0))
            tb.replace_file(rel, _save(img, QUALITY[render], im))
            state[f"box_{render}"] = _box_str(
                _frac((dx0, dy0, dx0 + patch.size[0], dy0 + patch.size[1]), im.size, rel)
            )

    def locate(b, n):  # noqa: ANN001, ANN202
        return (
            Target(doc_key=key, box=to_box(dst_row["box"])),
            {"region": "original"},
            {"region": f"rows {i1}-{i1 + 2} copied over rows {i2}-{i2 + 2}", **state},
        )

    return Plan(
        edit=f"{key} page 1: the amount block of rows {i1}.. copied over rows {i2}..",
        file_edit=edit,
        locate=locate,
    )


def t43(ctx: Ctx) -> Plan:
    st = ctx.pick(ctx.docs("bank_statement"), "statement")
    key = st["key"]
    rows = [
        r
        for r in credit_rows(st)
        if r.get("page") == 1
        and (r["cells"]["credit"].get("boxes"))
        and D(cell(r, "credit")) >= D("500")
    ]
    r = ctx.pick(rows, "credit row on page 1")
    idx = r["index"]
    old = D(cell(r, "credit"))
    new = (old * D("1.30")).quantize(D("0.01"))
    raw_new = f"{new:,.2f}"
    state: dict[str, str] = {}

    def edit(tb: TmpBundle) -> None:
        for render in scan_renders(tb, ctx):
            b = r["cells"]["credit"]["boxes"].get(render)
            if not b:
                raise TamperNotApplicable("amount cell has no scan box")
            rel = jpeg_file(tb, render, key)
            im = Image.open(io.BytesIO((tb.root / rel).read_bytes()))
            im.load()
            img = im.convert("RGB")
            x0, y0, x1, y1 = _px(b, im.size)
            d = ImageDraw.Draw(img)
            d.rectangle((x0 - 2, y0 - 1, x1 + 2, y1 + 1), fill=(255, 255, 255))
            font = ImageFont.load_default(size=max(8, int((y1 - y0) * 1.05)))
            tw = d.textlength(raw_new, font=font)
            d.text((x1 - tw, y0 - 1), raw_new, fill=(20, 20, 20), font=font)
            tb.replace_file(rel, _save(img, QUALITY[render], im))
            state[f"box_{render}"] = _box_str(_frac((x0, y0, x1, y1), im.size, rel))
        # the printed amount changed: ground truth records what is printed (the oracle reads it)
        for d_ in tb.gt["documents"]:
            if d_["key"] == key:
                row = next(x for x in d_["tables"]["transactions"]["rows"] if x["index"] == idx)
                row["cells"]["credit"]["value"] = format(new, "f")
                row["cells"]["credit"]["raw"] = raw_new

    def locate(base_gt, new_gt):  # noqa: ANN001, ANN202
        t = Target(
            doc_key=key, table="transactions", row=idx, box=to_box(r["cells"]["credit"].get("box"))
        )
        return t, {"credit": format(old, "f")}, {"credit": format(new, "f"), **state}

    return Plan(
        edit=f"{key} row {idx}: white box and typed digits over the credit {old} -> {new} on the scan",
        file_edit=edit,
        locate=locate,
    )


IMAGE_BUILDERS = {"T40a": t40a, "T40b": t40b, "T41": t41, "T42": t42, "T43": t43}
