"""Company profile (ACRA BizFile-style) text-layer extractor (ingest-and-extraction.md 6.8, data-model 6.6).

Labelled fields (``Entity name  Sembrook Freight Pte. Ltd.`` with the value to the right of, or
below, its label) and two header-anchored tables: ``Directors`` (Name, ID number, Nationality,
Position, Appointed) and ``Shareholders`` (Name, ID / UEN, Shares, Percent). Percent cells keep the
printed percentage points. A profile whose labels or both tables cannot be found raises
``LayoutNotRecognised`` and the cascade reads it with the model instead.

Honest limit: the layout vocabulary is the generator's plus a few common synonyms; a real BizFile
print with other labels goes to the LLM path.
"""

from __future__ import annotations

import re

from app.core.models import Cell, Table
from app.extract.base import LayoutNotRecognised
from app.extract.kyc_common import (
    SECTION_STOP,
    Form,
    currency_cell,
    fill_rows,
    make_validator,
    run_table,
)
from app.extract.registry import register_extractor, register_prompt_hints
from app.extract.text_layer import ColDef, DocView, TextLayerExtractor, words_text

DOC_TYPE = "company_profile"

LABELS = {
    "company_name": [
        r"^Entity\s+Name",
        r"^Company\s+Name",
        r"^Business\s+Name",
        r"^Name\s+of\s+(?:Entity|Company|Business)",
    ],
    "uen": [r"^UEN", r"^Co\.?\s*Reg(?:istration)?\.?\s*No\.?", r"^Registration\s+(?:No\.?|Number)"],
    "entity_type": [r"^Entity\s+Type", r"^Company\s+Type", r"^Type\s+of\s+(?:Entity|Company)"],
    "status": [r"^(?:Entity\s+|Company\s+)?Status"],
    "incorporation_date": [
        r"^(?:Date\s+of\s+)?Incorporation(?:\s+Date)?",
        r"^Date\s+Incorporated",
        r"^Registration\s+Date",
    ],
    "registered_address": [r"^Registered\s+(?:Office\s+)?Address", r"^Address"],
    "principal_activity": [
        r"^Principal\s+Activit(?:y|ies)",
        r"^Primary\s+Activity",
        r"^Business\s+Activity",
        r"^Nature\s+of\s+Business",
    ],
    "ssic_code": [r"^SSIC(?:\s+Code)?", r"^Primary\s+SSIC"],
    "paid_up_capital": [r"^Paid[-\s]?up\s+(?:Share\s+)?Capital"],
    "currency": [r"^Currency"],
    "retrieved_date": [
        r"^Information\s+as\s+at",
        r"^Retrieved(?:\s+(?:on|Date))?",
        r"^Extract(?:ed)?\s+(?:on|Date)",
        r"^As\s+at",
    ],
}
# headings that are not labels but must end a value
EXTRA_KNOWN = [r"^Directors?", r"^Shareholders?", r"^Name$"]

_ID_HDR = (
    r"id(?:\s*(?:number|no\.?))?|nric(?:\s*/\s*fin)?(?:\s*(?:number|no\.?))?|fin|"
    r"id\s*/\s*passport(?:\s*no\.?)?|nric\s*/\s*passport"
)
DIRECTOR_COLS = (
    ColDef("director_name", r"(?:director'?s?\s+)?(?:full\s+)?name|director"),
    ColDef("id_number", _ID_HDR),
    ColDef("nationality", r"nationality|citizenship"),
    ColDef("position", r"position|designation|role|office"),
    ColDef(
        "appointment_date",
        r"appointed(?:\s+on)?|appointment(?:\s+date)?|date\s+(?:of\s+)?appoint(?:ed|ment)",
    ),
)


SHARE_COLS = (
    ColDef(
        "holder_name",
        r"(?:share)?holder(?:'?s)?(?:\s+name)?|name|member(?:'?s)?(?:\s+name)?",
    ),
    ColDef(
        "id_or_uen",
        r"id\s*/\s*uen|uen\s*/\s*id|id\s*/\s*nric\s*/\s*uen|nric\s*/\s*uen|uen|"
        r"id(?:\s*(?:number|no\.?))?",
    ),
    ColDef(
        "shares",
        r"(?:no\.?\s+of\s+|number\s+of\s+)?shares(?:\s+held)?",
    ),
    ColDef(
        "percent",
        r"%(?:\s*held)?|percent(?:age)?(?:\s+held)?|holding\s*%|%\s*holding|ownership\s*%",
    ),
)


_INT = re.compile(r"\d[\d,]*")


def _is_director_row(cols: dict) -> bool:
    return bool(cols.get("director_name")) and any(
        cols.get(k) for k in ("id_number", "position", "appointment_date", "nationality")
    )


def _is_holder_row(cols: dict) -> bool:
    if not cols.get("holder_name"):
        return False
    shares = words_text(cols.get("shares", []))
    pct = words_text(cols.get("percent", []))
    return bool(_INT.fullmatch(shares.strip()) or "%" in pct or re.fullmatch(r"\d+(?:\.\d+)?", pct))


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class CompanyProfileTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, DOC_TYPE, LABELS, extra_known=EXTRA_KNOWN)

        # tables first: they claim their lines so labels never read a table row
        directors = run_table(
            view,
            DIRECTOR_COLS,
            accept=lambda k: (
                "director_name" in k
                and len(k & {"id_number", "position", "appointment_date", "nationality"}) >= 2
            ),
            is_row_start=_is_director_row,
            wrap_key="director_name",
            stop=lambda ln, cols: bool(SECTION_STOP.match(ln.text.strip())),
        )
        holders = run_table(
            view,
            SHARE_COLS,
            accept=lambda k: "holder_name" in k and "shares" in k and "percent" in k,
            is_row_start=_is_holder_row,
            wrap_key="holder_name",
            special=lambda ln, cols: (
                "total" if re.match(r"Total\b", ln.text.strip(), re.I) else None
            ),
            stop=lambda ln, cols: bool(SECTION_STOP.match(ln.text.strip())),
        )
        fill_rows(view, tables, DOC_TYPE, "directors", directors)
        fill_rows(view, tables, DOC_TYPE, "shareholders", holders)

        form.put_all(
            fields,
            [
                "company_name",
                "uen",
                "entity_type",
                "status",
                "incorporation_date",
                "principal_activity",
                "ssic_code",
                "currency",
                "retrieved_date",
            ],
            wrap=("principal_activity",),
        )
        form.put(fields, "registered_address", wrap=True)
        cap = form.put(fields, "paid_up_capital")
        if cap is not None:
            currency_cell(view, fields, cap.value_words)

        found = sum(
            1
            for k in (
                "company_name",
                "uen",
                "entity_type",
                "status",
                "incorporation_date",
                "principal_activity",
            )
            if fields[k].raw is not None
        )
        if found < 3:
            raise LayoutNotRecognised("fewer than three labelled company profile fields found")
        if not directors.header_pages and not holders.header_pages:
            raise LayoutNotRecognised("no directors or shareholders table header")


make_validator(DOC_TYPE)

register_prompt_hints(
    DOC_TYPE,
    "This is a company registry profile (BizFile style). Copy `company_name`, `uen`, "
    "`entity_type`, `status`, `incorporation_date`, `registered_address`, `principal_activity` "
    "(the declared business, exactly as printed), `ssic_code` and `paid_up_capital` from their "
    "labels. `directors` has one row per listed director or officer (`id_number` is the NRIC/FIN or "
    "passport number as printed, `appointment_date` the printed date). `shareholders` has one row "
    "per listed shareholder: `shares` is the printed share count and `percent` the printed "
    "percentage, for example `45.00%`; do not recompute either, and do not put a director in "
    "`shareholders` or the reverse. Never invent a row that is not printed.",
)
