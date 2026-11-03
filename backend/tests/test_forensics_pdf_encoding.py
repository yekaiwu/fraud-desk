"""A font whose ToUnicode map contradicts the glyphs it draws (red-team attack R23).

Found by the independent red team (docs/red-team.md): a forger keeps the genuine text layer and
makes the page show other digits, by drawing the forged figure with a font whose ToUnicode CMap
maps the shown digit back to the genuine one. ``pdf_text_vs_render`` now reads the font
encoding against the CMap for simple fonts. All data is fictitious.
"""

from __future__ import annotations

import io

import pikepdf
import pymupdf
from pikepdf import Dictionary, Name, Operator

from app.checks import forensics_pdf as fp
from tests.forensics_pdf_factory import ctx_for, statement_pdf


def _lying_pdf(data: bytes, shown: str, extracted: str) -> bytes:
    """Re-draw page 1's first balance figure with a font whose ToUnicode maps ``shown`` to
    ``extracted``; the glyph on the page is ``shown``."""
    pdf = pikepdf.open(io.BytesIO(data))
    page = pdf.pages[0]
    cmap = "\n".join(
        [
            "/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
            "1 begincodespacerange <00> <FF> endcodespacerange",
            "1 beginbfchar",
            f"<{ord(shown):02X}> <{ord(extracted):04X}>",
            "endbfchar endcmap end end",
        ]
    ).encode()
    font = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.Type1,
            BaseFont=Name.Helvetica,
            Encoding=Name.WinAnsiEncoding,
            ToUnicode=pdf.make_stream(cmap),
        )
    )
    page.Resources.Font[Name("/FL")] = font
    ops = list(pikepdf.parse_content_stream(page))
    ops.append(pikepdf.ContentStreamInstruction([], Operator("BT")))
    ops.append(pikepdf.ContentStreamInstruction([Name("/FL"), 9], Operator("Tf")))
    ops.append(pikepdf.ContentStreamInstruction([1, 0, 0, 1, 40, 30], Operator("Tm")))
    ops.append(pikepdf.ContentStreamInstruction([pikepdf.String(shown * 3)], Operator("Tj")))
    ops.append(pikepdf.ContentStreamInstruction([], Operator("ET")))
    page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _run(data: bytes):
    return fp.pdf_text_vs_render(ctx_for([("f1", data)]))


def test_clean_statement_has_no_encoding_flag():
    flags = _run(statement_pdf())
    assert not [f for f in flags if "text map" in f.evidence]


def test_to_unicode_that_contradicts_the_glyphs_is_flagged():
    forged = _lying_pdf(statement_pdf(), shown="9", extracted="5")
    # the page really extracts the lie
    text = pymupdf.open(stream=forged, filetype="pdf")[0].get_text()
    assert "555" in text
    flags = [f for f in _run(forged) if "text map" in f.evidence]
    assert len(flags) == 1
    f = flags[0]
    assert f.rule == "pdf_text_vs_render" and f.severity == "medium"
    assert "draws '9'" in f.evidence and "says '5'" in f.evidence


def test_identity_to_unicode_is_not_flagged():
    honest = _lying_pdf(statement_pdf(), shown="9", extracted="9")
    assert not [f for f in _run(honest) if "text map" in f.evidence]


def test_cmap_parser_reads_chars_and_ranges():
    cmap = b"1 beginbfchar <39> <0035> endbfchar 1 beginbfrange <30> <32> <0061> endbfrange"
    assert fp._tounicode_map(cmap) == {0x39: "5", 0x30: "a", 0x31: "b", 0x32: "c"}
