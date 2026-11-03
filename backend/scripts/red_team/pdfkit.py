"""PDF editing primitives for the red team (independent of the in-house tamper code).

Everything here works on a PDF's page content streams with pikepdf, the way a forger who knows
the file format would: find the ``(text) Tj`` operators, rewrite the string, and move the
text origin so that right-aligned figures stay right-aligned. Nothing here imports the
generator or the checks. All documents are fictitious.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from decimal import Decimal

import pikepdf
from pikepdf import Operator, String
from reportlab.pdfbase.pdfmetrics import stringWidth

MONEY = re.compile(r"^\(?-?[\d,]+\.\d{2}\)?$")


def fmt_money(v: Decimal | float | int | str) -> str:
    return f"{Decimal(str(v)):,.2f}"


def money(s: str) -> Decimal:
    t = s.strip()
    neg = t.endswith(" DR")
    t = t.removesuffix(" DR").removesuffix(" CR")
    v = Decimal(t.replace(",", "").replace("(", "-").replace(")", ""))
    return -v if neg else v


@dataclass
class Item:
    page: int
    op_index: int  # index of the Tj operator in the page's parsed stream
    tm_index: int  # index of the Tm operator that positions it
    x: float
    y: float
    text: str
    font: str  # resource name, e.g. F1
    base_font: str  # e.g. Helvetica
    size: float

    @property
    def width(self) -> float:
        return stringWidth(self.text, self.base_font, self.size)

    @property
    def right(self) -> float:
        return self.x + self.width


class PdfEdit:
    """Load a PDF, expose its text items, rewrite them, and save it again."""

    def __init__(self, data: bytes):
        self.pdf = pikepdf.open(io.BytesIO(data))
        self.dirty = False
        self._ops: list[list] = []
        self._items: list[list[Item]] = []
        for pno, page in enumerate(self.pdf.pages):
            ops = self._parse(page)
            self._ops.append(ops)
            self._items.append(self._scan(pno, page, ops))

    @staticmethod
    def _parse(page) -> list[list]:
        return [[list(ins.operands), ins.operator] for ins in pikepdf.parse_content_stream(page)]

    @property
    def n_pages(self) -> int:
        return len(self.pdf.pages)

    def _scan(self, pno: int, page, ops) -> list[Item]:
        fonts = {}
        res = page.get("/Resources")
        if res is not None and "/Font" in res:
            for k, v in res.Font.items():
                fonts[str(k)[1:]] = str(v.get("/BaseFont", "/Helvetica"))[1:]
        font, size, tm = "F1", 10.0, (0.0, 0.0, 0)
        items = []
        for i, (operands, op) in enumerate(ops):
            name = str(op)
            if name == "Tf":
                font, size = str(operands[0])[1:], float(operands[1])
            elif name == "Tm":
                tm = (float(operands[4]), float(operands[5]), i)
            elif name == "Tj":
                text = bytes(operands[0]).decode("latin-1")
                base = fonts.get(font, "Helvetica")
                items.append(Item(pno, i, tm[2], tm[0], tm[1], text, font, base, size))
        return items

    def items(self, page: int = 0) -> list[Item]:
        return self._items[page]

    def set_text(self, item: Item, text: str, *, align: str = "right") -> None:
        """Rewrite the string; ``right`` keeps the right edge, ``left`` keeps the left edge."""
        if item.text == text:
            return
        self.dirty = True
        ops = self._ops[item.page]
        old_right = item.right
        item.text = text
        ops[item.op_index][0][0] = String(text.encode("latin-1"))
        if align == "right":
            item.x = old_right - item.width
            tm = ops[item.tm_index][0]
            tm[4] = round(item.x, 4)

    def move(self, item: Item, dx: float = 0.0, dy: float = 0.0) -> None:
        if not dx and not dy:
            return
        self.dirty = True
        ops = self._ops[item.page]
        item.x += dx
        item.y += dy
        tm = ops[item.tm_index][0]
        tm[4], tm[5] = round(item.x, 4), round(item.y, 4)

    def remove(self, item: Item) -> None:
        """Delete the text operator (the glyphs vanish from stream and render)."""
        self.dirty = True
        self._ops[item.page][item.op_index] = [[], Operator("n")]

    def raw_replace(self, page: int, old: bytes, new: bytes) -> None:
        """Byte-level replacement in a page's content stream (before ``dump``)."""
        self.dirty = True
        self._flush(page)
        stream = self.pdf.pages[page].Contents
        data = stream.read_bytes()
        if old not in data:
            raise ValueError(f"{old!r} not in page {page}")
        stream.write(data.replace(old, new, 1))
        self._ops[page] = self._parse(self.pdf.pages[page])
        self._items[page] = self._scan(page, self.pdf.pages[page], self._ops[page])

    def wrap(self, items: list[Item], before: list, after: list) -> None:
        """Insert operators before/after each item's Tj (``before``/``after`` are (operands, op))."""
        self.dirty = True
        by_page: dict[int, list[Item]] = {}
        for it in items:
            by_page.setdefault(it.page, []).append(it)
        for page, its in by_page.items():
            ops = self._ops[page]
            for it in sorted(its, key=lambda i: -i.op_index):
                ops[it.op_index + 1 : it.op_index + 1] = [[list(o), op] for o, op in after]
                ops[it.op_index : it.op_index] = [[list(o), op] for o, op in before]
            self._items[page] = self._scan(page, self.pdf.pages[page], ops)

    def _flush(self, page: int) -> None:
        stream = pikepdf.unparse_content_stream([(o, op) for o, op in self._ops[page]])
        self.pdf.pages[page].Contents = self.pdf.make_stream(stream)

    def dump(self, *, keep_docinfo: bool = True) -> bytes:
        for p in range(self.n_pages):
            self._flush(p)
        buf = io.BytesIO()
        self.pdf.save(buf)
        return buf.getvalue()


def find(items: list[Item], text: str, nth: int = 0) -> Item:
    hits = [i for i in items if i.text == text]
    if len(hits) <= nth:
        raise KeyError(f"{text!r} (#{nth}) not found")
    return hits[nth]


def rows_by_y(items: list[Item], tol: float = 3.0) -> list[list[Item]]:
    """Group items that share a baseline; rows top to bottom, items left to right."""
    rows: list[list[Item]] = []
    for it in sorted(items, key=lambda i: (-i.y, i.x)):
        if rows and abs(rows[-1][0].y - it.y) <= tol:
            rows[-1].append(it)
        else:
            rows.append([it])
    for r in rows:
        r.sort(key=lambda i: i.x)
    return rows
