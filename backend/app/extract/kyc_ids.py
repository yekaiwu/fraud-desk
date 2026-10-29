"""National ID (NRIC / FIN) and passport text-layer extractors (ingest-and-extraction.md 6.8,
data-model 6.11 and 6.12).

Identity documents are the best-evidence KYC inputs and the most sensitive, so the rules are strict:

* ``Cell.value`` carries the number exactly as printed (upper-cased, spaces removed by the ``id``
  rule). Nothing is masked here (masking is for flag evidence) and **nothing is repaired**: an NRIC
  with seven digits missing one, or a check letter that does not fit, is extracted as printed so
  ``id_format`` and ``id_checksum`` can see it. This module has no check-letter or check-digit logic.
* ``national_id.id_number`` is taken from its label; without a label, from the first word that has
  the NRIC/FIN shape (``[STFGM]`` + 7 digits + letter).
* ``passport.mrz_line1`` / ``mrz_line2`` are the last two lines of the document made only of
  ``A-Z 0-9 <`` (at least one ``<``), copied character for character with ``<`` preserved. A line
  that is not 44 characters long is kept as read and warned ``unparsed:`` (never padded or trimmed);
  ``passport_mrz`` raises the flag.
* A printed date that is not a real date (``31 Feb 1990``) gives ``value=None`` with ``raw`` kept.
* ``passport.holder_name`` is read from its label; if only ``Surname`` and ``Given names`` are
  printed it is composed from them and marked ``source="derived"`` (no box, ``verified=None``).
"""

from __future__ import annotations

import re

from app.core.models import Cell, Table, Word
from app.extract.base import LayoutNotRecognised
from app.extract.kyc_common import Form, make_validator
from app.extract.normalise import format_warning
from app.extract.registry import register_extractor, register_prompt_hints
from app.extract.text_layer import DocView, TextLayerExtractor

NRIC_SHAPE = re.compile(r"^[STFGM]\d{7}[A-Z]$")
_MRZ_TEXT = re.compile(r"^[A-Z0-9<]{36,52}$")
_MRZ_LINE1 = re.compile(r"^[A-Z]<")

# ------------------------------------------------------------------------------------ national id

NATIONAL_ID = "national_id"
ID_LABELS = {
    "holder_name": [r"^Name", r"^Full\s+Name", r"^Holder(?:'?s)?\s+Name"],
    "id_number": [
        r"^NRIC\s*/\s*FIN(?:\s*(?:No\.?|Number))?",
        r"^NRIC(?:\s*(?:No\.?|Number))?",
        r"^FIN(?:\s*(?:No\.?|Number))?",
        r"^ID\s*(?:No\.?|Number)",
        r"^Identity\s+(?:Card\s+)?(?:No\.?|Number)",
    ],
    "date_of_birth": [r"^Date\s+of\s+Birth", r"^D\.?O\.?B\.?", r"^Birth\s+Date"],
    "sex": [r"^Sex", r"^Gender"],
    "nationality": [r"^Nationality", r"^Citizenship"],
    "date_of_issue": [r"^Date\s+of\s+Issue", r"^Issue\s+Date", r"^Date\s+Issued", r"^Issued"],
    "expiry_date": [
        r"^(?:Date\s+of\s+)?Expiry(?:\s+Date)?",
        r"^Expiry",
        r"^Valid\s+(?:Until|To|Till)",
    ],
    "address": [r"^(?:Residential\s+|Registered\s+)?Address"],
}


@register_extractor(doc_types={NATIONAL_ID}, backend="text_layer")
class NationalIdTextLayer(TextLayerExtractor):
    doc_types = frozenset({NATIONAL_ID})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, NATIONAL_ID, ID_LABELS)
        form.put_all(
            fields,
            [
                "holder_name",
                "id_number",
                "date_of_birth",
                "sex",
                "nationality",
                "date_of_issue",
                "expiry_date",
            ],
        )
        form.put(fields, "address", wrap=True)
        if fields["id_number"].raw is None:  # no label: the first word shaped like an NRIC/FIN
            for ln in view.all_lines:
                hit = next(
                    (w for w in ln.words if NRIC_SHAPE.match(w.text.strip().strip(",.;:()"))), None
                )
                if hit is not None:
                    raw = hit.text.strip().strip(",.;:()")
                    fields["id_number"] = view.cell(
                        [hit], "id", "fields.id_number", raw_override=raw
                    )
                    break
        found = sum(
            1 for k in ("holder_name", "id_number", "date_of_birth") if fields[k].raw is not None
        )
        if fields["id_number"].raw is None or found < 2:
            raise LayoutNotRecognised("no ID number with a name or date of birth found")


make_validator(NATIONAL_ID)

register_prompt_hints(
    NATIONAL_ID,
    "This is a Singapore NRIC or FIN card (front). Copy `id_number` exactly as printed, character "
    "for character (letter, seven digits, letter); never correct, complete or mask it, even if it "
    "looks wrong. `holder_name` as printed (often upper case), dates exactly as printed "
    "(`date_of_birth`, `date_of_issue`, `expiry_date`), `sex` as the single letter printed. "
    "A date that is not a real date is still copied as printed.",
)

# ------------------------------------------------------------------------------------ passport

PASSPORT = "passport"
PASSPORT_LABELS = {
    "holder_name": [r"^Full\s+Name", r"^Holder(?:'?s)?\s+Name", r"^Name(?!\s+of)"],
    "surname": [r"^Surname", r"^Family\s+Name", r"^Last\s+Name"],
    "given_names": [r"^Given\s+Names?", r"^Forenames?", r"^First\s+Names?"],
    "passport_number": [
        r"^Passport\s*(?:No\.?|Number|#)",
        r"^Document\s*(?:No\.?|Number)",
    ],
    "nationality": [r"^Nationality"],
    "issuing_country": [
        r"^Issuing\s+(?:Country|State)",
        r"^Country\s+of\s+Issue",
        r"^Country\s+Code",
        r"^Code(?:\s+of\s+Issuing\s+State)?",
    ],
    "date_of_birth": [r"^Date\s+of\s+Birth", r"^D\.?O\.?B\.?", r"^Birth\s+Date"],
    "sex": [r"^Sex", r"^Gender"],
    "date_of_issue": [r"^Date\s+of\s+Issue", r"^Issue\s+Date", r"^Date\s+Issued"],
    "date_of_expiry": [
        r"^Date\s+of\s+Expiry",
        r"^Date\s+of\s+Expiration",
        r"^Expiry(?:\s+Date)?",
        r"^Valid\s+(?:Until|To|Till)",
    ],
}


def mrz_lines(view: DocView) -> list[tuple[str, list[Word]]]:
    """Machine-readable lines of the document in reading order: ``(text as printed, words)``."""
    out: list[tuple[str, list[Word]]] = []
    for ln in view.all_lines:
        text = "".join(w.text.strip() for w in ln.words)
        if "<" in text and _MRZ_TEXT.match(text):
            out.append((text, list(ln.words)))
    return out


@register_extractor(doc_types={PASSPORT}, backend="text_layer")
class PassportTextLayer(TextLayerExtractor):
    doc_types = frozenset({PASSPORT})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, PASSPORT, PASSPORT_LABELS)
        form.put_all(fields, [k for k in PASSPORT_LABELS if k != "holder_name"])
        form.put(fields, "holder_name")
        self._mrz(view, fields)
        if fields["holder_name"].raw is None and fields["holder_name"].value is None:
            given, sur = fields["given_names"], fields["surname"]
            if given.value and sur.value:
                fields["holder_name"] = Cell(
                    value=f"{given.value} {sur.value}",
                    kind="text",
                    source="derived",
                    verified=None,
                )
        labelled = sum(
            1 for k in PASSPORT_LABELS if k != "holder_name" and fields[k].raw is not None
        )
        both_mrz = fields["mrz_line1"].raw is not None and fields["mrz_line2"].raw is not None
        one_mrz = fields["mrz_line1"].raw is not None or fields["mrz_line2"].raw is not None
        if not both_mrz and labelled + (1 if one_mrz else 0) < 3:
            raise LayoutNotRecognised("fewer than three passport fields or both MRZ lines found")

    @staticmethod
    def _mrz(view: DocView, fields: dict[str, Cell]) -> None:
        lines = mrz_lines(view)[-2:]
        slots: dict[str, tuple[str, list[Word]]] = {}
        if len(lines) == 2:
            slots = {"mrz_line1": lines[0], "mrz_line2": lines[1]}
        elif len(lines) == 1:
            key = "mrz_line1" if _MRZ_LINE1.match(lines[0][0]) else "mrz_line2"
            slots = {key: lines[0]}
        for key, (text, words) in slots.items():
            fields[key] = view.cell(words, "text", f"fields.{key}", raw_override=text)
            if len(text) != 44:
                view.warn(format_warning("unparsed", f"fields.{key}", text))


make_validator(PASSPORT)

register_prompt_hints(
    PASSPORT,
    "This is a passport data page. Copy the visual fields (`surname`, `given_names`, "
    "`passport_number`, `nationality`, `issuing_country`, `date_of_birth`, `sex`, `date_of_issue`, "
    "`date_of_expiry`) exactly as printed. `mrz_line1` and `mrz_line2` are the two machine-readable "
    "lines at the bottom of the page: copy each character for character, keeping every `<` filler "
    "and every letter O versus digit 0 exactly as printed, with no spaces added or removed and no "
    "padding or trimming, even if a line looks too short or too long. Never compute or correct a "
    "check digit.",
)
