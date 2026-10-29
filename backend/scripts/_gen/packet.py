"""``--packet`` (C2 stretch): concatenate every document of a bundle into one PDF."""

from __future__ import annotations

from datetime import date

import pymupdf

from .render_common import RenderedDoc, finalize_pdf


def build_packet(docs: list[RenderedDoc], doc_date: date) -> tuple[bytes, list[dict]]:
    """Returns the packet bytes and one page record per packet page:
    ``{"page", "doc_key", "c1", "segment_start", "layout"}`` (pages numbered across the packet)."""
    out = pymupdf.open()
    records: list[dict] = []
    page_no = 0
    for d in docs:
        with pymupdf.open(stream=d.pdf, filetype="pdf") as src:
            out.insert_pdf(src)
        for i, pl in enumerate(d.page_labels):
            page_no += 1
            records.append(
                {
                    "page": page_no,
                    "doc_key": pl.doc_key,
                    "c1": pl.c1,
                    "segment_start": i == 0 or pl.c1 == "boilerplate",
                    "layout": pl.layout,
                }
            )
    raw = out.tobytes(deflate=True, garbage=3)
    out.close()
    pdf = finalize_pdf(
        raw,
        producer="Mock Composer 9.1",
        creator="Mock Packet Tool",
        title="Document packet",
        doc_date=doc_date,
    )
    return pdf, records
