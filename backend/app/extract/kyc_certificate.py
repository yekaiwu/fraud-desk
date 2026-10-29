"""Certificate of incorporation text-layer extractor (ingest-and-extraction.md 6.8, data-model 6.7).

Two printed forms are read: labelled lines (``Date of incorporation  12 Mar 2019``) and the usual
certificate sentence (``This is to certify that SEMBROOK FREIGHT PTE. LTD. (UEN 201912345R) is
incorporated on 12 March 2019``). Labels win where both exist. Only what is printed is read; there
is no claim about the issuer's authority or the certificate's authenticity.
"""

from __future__ import annotations

import re

from app.core.models import Cell, Table
from app.extract.base import LayoutNotRecognised
from app.extract.kyc_common import Form, make_validator
from app.extract.normalise import DATE_FRAGMENT
from app.extract.registry import register_extractor, register_prompt_hints
from app.extract.text_layer import DocView, TextLayerExtractor, flat_text

DOC_TYPE = "certificate_of_incorporation"

LABELS = {
    "company_name": [
        r"^(?:Company|Entity)\s+Name",
        r"^Name\s+of\s+(?:the\s+)?(?:Company|Entity)",
        r"^Name(?!\s+of)",
    ],
    "uen": [
        r"^UEN",
        r"^(?:Company|Entity)\s+(?:Number|No\.?)(?:\s*\(UEN\))?",
        r"^(?:Company\s+|Entity\s+)?Reg(?:istration)?\.?\s*(?:No\.?|Number)",
        r"^Co\.?\s*Reg(?:istration)?\.?\s*No\.?",
    ],
    "entity_type": [r"^Entity\s+Type", r"^Company\s+Type", r"^Type\s+of\s+(?:Company|Entity)"],
    "incorporation_date": [
        r"^Date\s+of\s+Incorporation",
        r"^Incorporation\s+Date",
        r"^Date\s+Incorporated",
        r"^Incorporated\s+on",
    ],
    "certificate_number": [r"^Certificate\s+(?:No\.?|Number)", r"^Cert\.?\s+No\.?"],
    "issue_date": [r"^Date\s+of\s+Issue", r"^Issue\s+Date", r"^Date\s+Issued", r"^Issued\s+on"],
    "issuer": [r"^Issued\s+by", r"^Issuing\s+Authority", r"^Issuer"],
}

_DATE = "(?P<v>" + DATE_FRAGMENT.pattern + ")"
_NAME_PROSE = re.compile(
    r"certif(?:y|ies)\s+that\s+(?P<name>.+?)\s*"
    r"(?:\(\s*(?:UEN|Co\.?\s*Reg\.?\s*No\.?)\s*[:.]?\s*(?P<uen>[0-9A-Z]{9,10})\s*\)\s*)?,?\s*"
    r"(?:was|is|has\s+been)\s+(?:this\s+day\s+)?(?:duly\s+)?(?:incorporated|registered)",
    re.I,
)
_DATE_PROSE = re.compile(
    r"(?:incorporated|registered)\b[^.]{0,60}?\bon\s+(?:the\s+)?" + _DATE,
    re.I,
)
_TYPE_PROSE = re.compile(
    r"(?:incorporated|registered)\b.*?\bas\s+an?\s+(?P<v>(?:private|public|exempt)\s+company"
    r"(?:\s+limited\s+by\s+(?:shares|guarantee))?)",
    re.I,
)
_UEN_PROSE = re.compile(r"\bUEN\b[:.\s]*(?P<v>[0-9]{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])")


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class CertificateTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, DOC_TYPE, LABELS)
        form.put_all(fields, list(LABELS), wrap=("company_name",))
        self._prose(view, fields)
        found = sum(
            1 for k in ("company_name", "uen", "incorporation_date") if fields[k].raw is not None
        )
        if found < 2:
            raise LayoutNotRecognised("fewer than two certificate fields found")

    @staticmethod
    def _prose(view: DocView, fields: dict[str, Cell]) -> None:
        flat = flat_text(view)
        text = flat.text
        m = _NAME_PROSE.search(text)
        if m:
            name = m.group("name").strip(" ,;:")
            if fields["company_name"].raw is None and name:
                start = m.start("name") + m.group("name").find(name)
                fields["company_name"] = view.cell(
                    flat.words_in(start, start + len(name)),
                    "text",
                    "fields.company_name",
                    raw_override=name,
                )
            if fields["uen"].raw is None and m.group("uen"):
                fields["uen"] = view.cell(
                    flat.words_in(m.start("uen"), m.end("uen")),
                    "id",
                    "fields.uen",
                    raw_override=m.group("uen"),
                )
        if fields["uen"].raw is None:
            mu = _UEN_PROSE.search(text)
            if mu:
                fields["uen"] = view.cell(
                    flat.words_in(mu.start("v"), mu.end("v")),
                    "id",
                    "fields.uen",
                    raw_override=mu.group("v"),
                )
        if fields["incorporation_date"].raw is None:
            md = _DATE_PROSE.search(text)
            if md:
                fields["incorporation_date"] = view.cell(
                    flat.words_in(md.start("v"), md.end("v")),
                    "date",
                    "fields.incorporation_date",
                    raw_override=md.group("v"),
                )
        if fields["entity_type"].raw is None:
            mt = _TYPE_PROSE.search(text)
            if mt:
                fields["entity_type"] = view.cell(
                    flat.words_in(mt.start("v"), mt.end("v")),
                    "text",
                    "fields.entity_type",
                    raw_override=mt.group("v"),
                )


make_validator(DOC_TYPE)

register_prompt_hints(
    DOC_TYPE,
    "This is a certificate of incorporation. `company_name` and `uen` are the company named in "
    "the certificate (the sentence 'This is to certify that ... is incorporated' or the labelled "
    "lines), `incorporation_date` the date of incorporation and `issue_date` the date the "
    "certificate was issued (they can differ), both exactly as printed. `certificate_number` and "
    "`issuer` only if printed. Make no judgement about stamps, seals or signatures.",
)
