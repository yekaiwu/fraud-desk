"""Helpers shared by the attack modules."""

from __future__ import annotations

import io
import random
import re
from decimal import Decimal

import pikepdf

from ..core import Bundle
from ..models import FinStmt, Invoice, Row, StatementSet


def stmts(b: Bundle) -> StatementSet:
    return StatementSet.from_files(b.files)


def credits(ss: StatementSet, k: int, minimum: Decimal = Decimal("1000")) -> list[Row]:
    return [r for r in ss.stmts[k].rows if r.kind == "dep" and r.amount >= minimum]


def invoice(b: Bundle, name: str) -> Invoice:
    return Invoice(name, b.files[name])


def inv_names(b: Bundle) -> list[str]:
    return sorted(n for n in b.files if n.startswith("invoice_"))


def fin(b: Bundle, year: int) -> FinStmt:
    return FinStmt(
        f"financial_statement_FY{year}.pdf", b.files[f"financial_statement_FY{year}.pdf"]
    )


def restore_docinfo(new: bytes, original: bytes) -> bytes:
    """Put the original document-info dictionary back (what a careful forger does)."""
    src = pikepdf.open(io.BytesIO(original))
    dst = pikepdf.open(io.BytesIO(new))
    for k in list(dst.docinfo.keys()):
        del dst.docinfo[k]
    for k, v in src.docinfo.items():
        dst.docinfo[k] = v
    out = io.BytesIO()
    dst.save(out)
    return out.getvalue()


def pick(rng: random.Random, seq):
    return seq[rng.randrange(len(seq))]


def money_str(v: Decimal) -> str:
    return f"{v:,.2f}"


def uen_check_letter(body: str) -> str:
    """Fresh valid business-registration UEN (nnnnnnnnX) using the public algorithm, found by search."""
    from stdnum.sg import uen

    for c in "ABCDEFGHJKLMNPQRSTUVWXYZ":
        if uen.is_valid(body + c):
            return c
    raise ValueError(body)


def valid_uen(rng: random.Random, year: int = 2019) -> str:
    """A UEN of the form yyyynnnnnX that passes the standard checksum (made up, fictitious)."""
    while True:
        body = f"{year}{rng.randrange(10**5):05d}"
        try:
            return body + uen_check_letter(body)
        except ValueError:
            continue


def strip_signatures(pdf: pikepdf.Pdf) -> bool:
    """Remove every signature dictionary and the AcroForm that holds the field (what a forger does
    before editing a signed file). Returns True if anything was removed."""
    removed = False
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary) and "/ByteRange" in obj:
            for k in (
                "/ByteRange",
                "/Contents",
                "/Filter",
                "/SubFilter",
                "/Type",
                "/M",
                "/Name",
                "/Reason",
            ):
                if k in obj:
                    del obj[k]
            removed = True
    if "/AcroForm" in pdf.Root:
        del pdf.Root["/AcroForm"]
        removed = True
    return removed


def sub_text_everywhere(data: bytes, old: str, new: str) -> bytes:
    """Replace a literal string in every page stream, keeping Info untouched."""
    pdf = pikepdf.open(io.BytesIO(data))
    strip_signatures(pdf)
    for page in pdf.pages:
        s = (
            page.Contents.read_bytes()
            if not isinstance(page.Contents, pikepdf.Array)
            else b"".join(c.read_bytes() for c in page.Contents)
        )
        o = re.escape(old.encode("latin-1"))
        s2 = re.sub(o, new.encode("latin-1").replace(b"\\", b"\\\\"), s)
        if s2 != s:
            page.Contents = pdf.make_stream(s2)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()
