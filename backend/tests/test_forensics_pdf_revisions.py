"""Unsigned incremental updates that replace text (red-team attack R36, docs/red-team.md).

A forger can keep the genuine file bytes and append the edited page as a new revision, with the
creation and modification dates left equal. ``pdf_modified`` now compares the text of the earlier
revisions with the final file; text that was replaced is a high flag, text that was only added
(OCR, form filling) is not. All data is fictitious.
"""

from __future__ import annotations

import io

import pikepdf
import pymupdf

from app.checks import forensics_pdf as fp
from scripts.red_team.attacks.fam4_meta import incremental_replace_stream
from tests.forensics_pdf_factory import ctx_for, statement_pdf


def _page_content(data: bytes) -> bytes:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return pdf.pages[0].Contents.read_bytes()


def _first_balance(data: bytes) -> str:
    words = pymupdf.open(stream=data, filetype="pdf")[0].get_text("words")
    return next(w[4] for w in words if "." in w[4] and w[4][0].isdigit() and "," in w[4])


def _revised(base: bytes, *, replace: bool) -> bytes:
    content = _page_content(base)
    old = _first_balance(base).encode()
    if replace:
        new = content.replace(b"(" + old + b")", b"(" + old.replace(b"1", b"9", 1) + b")", 1)
        assert new != content
    else:
        new = content + b"\nBT /F1 7 Tf 1 0 0 1 40 20 Tm (Recognised text layer) Tj ET\n"
    return incremental_replace_stream(base, 0, new)


def _flags(data: bytes):
    return [f for f in fp.pdf_modified(ctx_for([("f1", data)])) if "revision" in f.evidence]


def test_single_revision_statement_is_silent():
    assert not _flags(statement_pdf())


def test_replaced_text_in_a_later_revision_is_high():
    base = statement_pdf()
    forged = _revised(base, replace=True)
    assert forged.count(b"%%EOF") == 2
    flags = _flags(forged)
    assert len(flags) == 1
    f = flags[0]
    assert f.rule == "pdf_modified" and f.severity == "high"
    assert _first_balance(base) in f.evidence


def test_text_only_added_later_is_not_flagged():
    assert not _flags(_revised(statement_pdf(), replace=False))
