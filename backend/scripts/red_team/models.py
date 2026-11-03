"""Document models for the red team: just enough structure to forge consistently.

A forger who edits a bank statement has to move the running balance of every later row, the
closing balance, the printed totals and the opening balance of every later statement. These
helpers do that on top of :mod:`pdfkit` so an attack can say "make this credit larger and keep
everything on the statements consistent". All figures are fictitious.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .pdfkit import Item, PdfEdit, fmt_money, money, rows_by_y

DATE_RE = re.compile(r"^\d{2}([ /-])(?:[A-Za-z]{3}|\d{2})\1\d{4}$")


@dataclass
class Row:
    stmt: Statement
    page: int
    kind: str  # bf | wd | dep
    date: Item
    desc: Item
    amt: Item | None
    bal: Item

    @property
    def amount(self) -> Decimal:
        return money(self.amt.text.split()[0]) if self.amt else Decimal("0")

    @property
    def balance(self) -> Decimal:
        return money(self.bal.text)


class Statement:
    """A bank statement in any of the three layouts of the base bundles (DBS-, OCBC-, UOB-style)."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self.edit = PdfEdit(data)
        self.rows: list[Row] = []
        self.opening = self.closing = self.total_wd = self.total_dep = None
        self.wd_edge = self.dep_edge = None
        self.suffix = False  # UOB style: "123.45 DR"
        self._parse()

    def _parse(self) -> None:
        for p in range(self.edit.n_pages):
            for r in rows_by_y(self.edit.items(p)):
                texts = [i.text for i in r]
                if "Opening balance" in texts:
                    self.opening = r[texts.index("Opening balance") + 1]
                if "Closing balance" in texts:
                    self.closing = r[texts.index("Closing balance") + 1]
                if "Total debits" in texts:
                    self.total_wd = r[texts.index("Total debits") + 1]
                if "Total credits" in texts:
                    self.total_dep = r[texts.index("Total credits") + 1]
                if any(t.startswith("Withdraw") for t in texts):
                    self.wd_edge = [i for i in r if i.text.startswith("Withdraw")][0].right
                    self.dep_edge = [i for i in r if i.text.startswith("Deposit")][0].right
                if "Amount" in texts and "Transaction" in texts:
                    self.suffix = True
                if texts[0] == "TOTAL" and len(r) == 3:
                    self.total_wd, self.total_dep = r[1], r[2]
                    continue
                if not DATE_RE.match(texts[0]) or len(r) < 3:
                    continue
                bal = r[-1]
                if "BALANCE BROUGHT FORWARD" in texts:
                    self.rows.append(
                        Row(
                            self,
                            p,
                            "bf",
                            r[0],
                            r[texts.index("BALANCE BROUGHT FORWARD")],
                            None,
                            bal,
                        )
                    )
                    continue
                desc = max(r[1:-2], key=lambda i: len(i.text)) if len(r) > 4 else r[1]
                amt = r[-2]
                if self.suffix:
                    kind = "wd" if amt.text.endswith("DR") else "dep"
                else:
                    kind = (
                        "wd"
                        if abs(amt.right - self.wd_edge) < abs(amt.right - self.dep_edge)
                        else "dep"
                    )
                self.rows.append(Row(self, p, kind, r[0], desc, amt, bal))

    def txns(self) -> list[Row]:
        return [r for r in self.rows if r.kind != "bf"]

    def _fmt(self, value: Decimal, kind: str) -> str:
        t = fmt_money(value)
        return f"{t} {'DR' if kind == 'wd' else 'CR'}" if self.suffix else t

    def _bal(self, v: Decimal) -> str:
        return f"{fmt_money(-v)} DR" if (self.suffix and v < 0) else fmt_money(v)

    def set_amount(self, row: Row, value: Decimal) -> None:
        self.edit.set_text(row.amt, self._fmt(value, row.kind))

    def retarget(self, row: Row, desc: str, kind: str, value: Decimal) -> None:
        """Turn a row into another transaction (description, column and amount)."""
        self.edit.set_text(row.desc, desc, align="left")
        if kind != row.kind and not self.suffix:
            dx = (self.dep_edge - self.wd_edge) * (1 if kind == "dep" else -1)
            self.edit.set_text(row.amt, self._fmt(value, kind))
            self.edit.move(row.amt, dx=dx)
        else:
            self.edit.set_text(row.amt, self._fmt(value, kind))
        row.kind = kind

    def drop_row(self, row: Row) -> None:
        """Delete the row's text and close the gap by moving the rows below it up."""
        idx = self.rows.index(row)
        nxt = self.rows[idx + 1] if idx + 1 < len(self.rows) else None
        gap = row.date.y - nxt.date.y if nxt is not None and nxt.page == row.page else None
        for it in {
            id(i): i for i in self.edit.items(row.page) if abs(i.y - row.date.y) < 3
        }.values():
            self.edit.remove(it)
        if gap:
            for r in [r for r in self.rows[idx + 1 :] if r.page == row.page]:
                for it in {
                    id(i): i for i in self.edit.items(r.page) if abs(i.y - r.date.y) < 3
                }.values():
                    self.edit.move(it, dy=gap)
        self.rows.remove(row)

    def recompute(self, opening: Decimal | None = None) -> Decimal:
        """Rewrite every balance, the totals and the closing balance from the (edited) rows."""
        run = opening if opening is not None else money(self.opening.text)
        if opening is not None:
            self.edit.set_text(self.opening, self._bal(opening))
        wd = dep = Decimal("0")
        for r in self.rows:
            if r.kind == "bf":
                self.edit.set_text(r.bal, self._bal(run))
                continue
            amt = r.amount
            if r.kind == "wd":
                run -= amt
                wd += amt
            else:
                run += amt
                dep += amt
            self.edit.set_text(r.bal, self._bal(run))
        if self.total_wd is not None:
            self.edit.set_text(self.total_wd, fmt_money(wd))
            self.edit.set_text(self.total_dep, fmt_money(dep))
        self.edit.set_text(self.closing, self._bal(run))
        return run

    def dump(self) -> bytes:
        return self.edit.dump()


@dataclass
class StatementSet:
    stmts: list[Statement] = field(default_factory=list)

    @classmethod
    def from_files(cls, files: dict[str, bytes]) -> StatementSet:
        names = sorted(n for n in files if re.search(r"bank_statement_\d{4}-\d{2}", n))
        return cls([Statement(n, files[n]) for n in names])

    def recompute_from(self, k: int) -> Decimal:
        """Recompute statement k and every later one so that the chain stays consistent."""
        closing = None
        for i in range(k, len(self.stmts)):
            closing = self.stmts[i].recompute(opening=closing)
        return closing

    def write_back(self, files: dict[str, bytes]) -> None:
        """Only files that were really edited are rewritten (a forger leaves the others alone)."""
        for s in self.stmts:
            if s.edit.dirty:
                files[s.name] = s.dump()


# ----------------------------------------------------------------------------- invoices


def round2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"))


class Invoice:
    """A tax invoice laid out as the base bundles do: line rows, then Subtotal / GST / Total.

    Two layouts exist: ``desc qty unit amount`` and ``desc qty unit gst amount`` (GST per line).
    """

    def __init__(self, name: str, data: bytes):
        self.name = name
        self.edit = PdfEdit(data)
        self.lines: list[tuple] = []  # (desc, qty, unit, gst_item_or_None, amount)
        self.subtotal = self.gst = self.total = None
        for r in rows_by_y(self.edit.items(0)):
            texts = [i.text for i in r]
            if texts[0] == "Subtotal":
                self.subtotal = r[1]
            elif texts[0].startswith("GST ("):
                self.gst = r[1]
            elif texts[0] in ("Total payable", "Total"):
                self.total = r[1]
            elif (
                len(r) in (4, 5) and texts[1].isdigit() and re.match(r"^[\d,]+\.\d{2}$", texts[-1])
            ):
                self.lines.append((r[0], r[1], r[2], r[3] if len(r) == 5 else None, r[-1]))

    def item(self, text_prefix: str) -> Item:
        for it in self.edit.items(0):
            if it.text.startswith(text_prefix):
                return it
        raise KeyError(text_prefix)

    def recompute(self, rate: Decimal = Decimal("0.09")) -> tuple[Decimal, Decimal, Decimal]:
        sub = gst_lines = Decimal("0")
        per_line = False
        for _, qty, unit, g, amt in self.lines:
            line = round2(Decimal(qty.text) * money(unit.text))
            self.edit.set_text(amt, fmt_money(line))
            sub += line
            if g is not None:
                per_line = True
                lg = round2(line * rate)
                self.edit.set_text(g, fmt_money(lg))
                gst_lines += lg
        gst = gst_lines if per_line else round2(sub * rate)
        self.edit.set_text(self.subtotal, fmt_money(sub))
        self.edit.set_text(self.gst, fmt_money(gst))
        self.edit.set_text(self.total, fmt_money(sub + gst))
        return sub, gst, sub + gst

    def set_qty(self, k: int, qty: int) -> None:
        self.edit.set_text(self.lines[k][1], str(qty), align="right")

    def dump(self) -> bytes:
        return self.edit.dump()


# ----------------------------------------------------------------------------- financial statements


class FinStmt:
    """Three-page financial statements: label -> the (FY, prior) item pair per page."""

    def __init__(self, name: str, data: bytes):
        self.name = name
        self.edit = PdfEdit(data)
        self.rows: dict[str, list[tuple[int, Item, Item]]] = {}
        for p in range(self.edit.n_pages):
            for r in rows_by_y(self.edit.items(p)):
                if len(r) in (2, 3) and re.match(r"^\(?[\d,]+\)?$", r[1].text):
                    self.rows.setdefault(r[0].text, []).append((p, *r[1:]))

    def get(self, label: str, col: int = 0, nth: int = 0) -> int:
        it = self.rows[label][nth][1 + col]
        return int(it.text.replace(",", "").replace("(", "-").replace(")", ""))

    def put(self, label: str, value: int, col: int = 0, nth: int = 0) -> None:
        it = self.rows[label][nth][1 + col]
        txt = f"({abs(value):,})" if value < 0 else f"{value:,}"
        self.edit.set_text(it, txt)

    def dump(self) -> bytes:
        return self.edit.dump()
