"""Checks that compare the documents with an outside source (docs/verification-sources.md).

* ``x_sanctions_screen``: applicant, directors, owners and named counterparties against a local snapshot
  of the official sanctions lists. A possible match for a person to adjudicate, never a decision.
* ``x_entity_vs_gleif``: legal name, status and address against the GLEIF record, parents as context.
* ``x_address_vs_registry``: the printed Singapore postal code and address against OneMap and the register.
* ``inv_gst_registered``: the supplier is on the IRAS GST register.
* ``inv_bank_details_valid``: IBAN and BIC/SWIFT form, offline (no source needed).

The first four need an outside source. The network is asked once per case by ``app.sources.runner``
before the rules run; these functions only read ``ctx.sources`` and never open a connection. A source that
is not configured, or that private mode blocks, makes the check a skip with the code ``not_configured`` or
``private_mode`` (outside coverage). A source that was configured but could not be reached is an ordinary
skip: it counts as not run and the case is marked partial. Neither ever becomes a flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.checks.kyc import NAME_PLACEHOLDERS
from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext
from app.core.models import Document, Flag, Region
from app.names import (
    cell_region,
    has_legal_token,
    labelled,
    mask_id,
    match_company,
    normalise_id,
    safe_text,
    sorted_rows,
)
from app.sources.runner import MAX_GST_LOOKUPS, postal_codes, source_status
from app.sources.sanctions import SOURCE_LABELS, SOURCE_ORDER, Hit, Screener, Subject

# --------------------------------------------------------------------------------------
# reading the outcome of a source
# --------------------------------------------------------------------------------------


def _outcome(ctx: CaseContext, source: str, key: str):  # noqa: ANN202
    """The answer of ``source`` for ``key``; raises CheckSkipped with the right code when there is none."""
    code, reason = source_status(ctx.settings, source)
    if code:
        raise CheckSkipped(reason, code)
    out = ctx.sources.get(key)
    if out is None:
        raise CheckSkipped("nothing to look up in the source for this case")
    if out.status != "ok":
        raise CheckSkipped(
            out.reason or f"{source} could not be asked",
            out.status if out.status in ("not_configured", "private_mode") else None,
        )
    return out


def _first_skip(skips: list[CheckSkipped]) -> CheckSkipped:
    return skips[0]


def _note_unasked(ctx: CaseContext, rule: str, skips: list[CheckSkipped], extra: str = "") -> None:
    """Say on the run which documents the source could not answer for, so flags found on the others
    are kept and the gap is still visible."""
    if not skips and not extra:
        return
    parts = [extra] if extra else []
    if skips:
        parts.append(f"{len(skips)} document(s) not looked up ({skips[0].reason[:120]})")
    ctx.run_notes[rule] = "; ".join(parts) + "."


def _cell(doc: Document, name: str) -> str:
    c = doc.fields.get(name)
    return (c.value or "").strip() if c is not None and c.value else ""


def _usable(ctx: CaseContext, *types: str) -> list[Document]:
    return [d for d in ctx.docs(*types) if d.status in ("ok", "partial")]


# --------------------------------------------------------------------------------------
# x_sanctions_screen
# --------------------------------------------------------------------------------------

MAX_SUBJECTS = 60
LEVEL_LABEL = {
    "exact": "exact name",
    "fuzzy": "name one letter off",
    "identifier": "identifier only (the name differs)",
}


@dataclass
class _Where:
    doc: Document
    table: str | None = None
    row: int | None = None
    field: str | None = None
    region: Region | None = None


@dataclass
class _Subject:
    name: str
    kind: str  # person | entity
    roles: list[str] = field(default_factory=list)
    where: list[_Where] = field(default_factory=list)
    dob: date | None = None
    nationality: str | None = None
    ids: list[str] = field(default_factory=list)


def _key(name: str, kind: str) -> str:
    from app.names import entity_key

    return f"{kind}:{entity_key(name, 'company' if kind == 'entity' else 'person')}"


def _kind_of(name: str) -> str:
    return "entity" if has_legal_token(name) else "person"


def _collect_subjects(ctx: CaseContext) -> list[_Subject]:
    found: dict[str, _Subject] = {}

    def add(
        name: str | None, kind: str | None, role: str, where: _Where, **extra: object
    ) -> _Subject | None:
        text = " ".join((name or "").split())
        if not text or text.casefold() in NAME_PLACEHOLDERS or len(text) < 3:
            return None
        kind = kind or _kind_of(text)
        s = found.setdefault(_key(text, kind), _Subject(name=text, kind=kind))
        if role not in s.roles:
            s.roles.append(role)
        s.where.append(where)
        if isinstance(extra.get("dob"), date) and s.dob is None:
            s.dob = extra["dob"]  # type: ignore[assignment]
        if extra.get("nationality") and not s.nationality:
            s.nationality = str(extra["nationality"])
        for ident in extra.get("ids", []) or []:  # type: ignore[union-attr]
            if ident and ident not in s.ids:
                s.ids.append(ident)
        return s

    def field_subject(
        doc: Document, fname: str, role: str, kind: str | None = None, **extra: object
    ) -> None:
        cell = doc.fields.get(fname)
        if cell is not None and cell.value:
            add(
                cell.value,
                kind,
                role,
                _Where(doc, field=fname, region=labelled(cell_region(cell), fname)),
                **extra,
            )

    app_name = ctx.applicant.name
    first = next(
        iter(_usable(ctx, "company_profile", "certificate_of_incorporation")), None
    ) or next(iter(_usable(ctx)), None)
    if app_name and first is not None:
        add(app_name, "entity", "applicant company", _Where(first), ids=[ctx.applicant.uen or ""])

    for doc in _usable(ctx, "company_profile"):
        field_subject(doc, "company_name", "company on the profile", "entity")
        for row in sorted_rows(doc.tables.get("directors")):
            c = row.cells
            name = c["director_name"].value if "director_name" in c else None
            add(
                name,
                "person",
                "director",
                _Where(
                    doc,
                    "directors",
                    row.index,
                    "director_name",
                    labelled(cell_region(c.get("director_name"), row), "director_name"),
                ),
                nationality=(c["nationality"].value if "nationality" in c else None),
                ids=[c["id_number"].value or ""] if "id_number" in c else [],
            )
        for row in sorted_rows(doc.tables.get("shareholders")):
            c = row.cells
            ident = c["id_or_uen"].value if "id_or_uen" in c else None
            add(
                c["holder_name"].value if "holder_name" in c else None,
                None,
                "shareholder",
                _Where(
                    doc,
                    "shareholders",
                    row.index,
                    "holder_name",
                    labelled(cell_region(c.get("holder_name"), row), "holder_name"),
                ),
                ids=[ident or ""],
            )
    for doc in _usable(ctx, "shareholder_register"):
        for row in sorted_rows(doc.tables.get("shareholders")):
            c = row.cells
            add(
                c["holder_name"].value if "holder_name" in c else None,
                None,
                "shareholder",
                _Where(
                    doc,
                    "shareholders",
                    row.index,
                    "holder_name",
                    labelled(cell_region(c.get("holder_name"), row), "holder_name"),
                ),
                ids=[c["id_or_uen"].value or ""] if "id_or_uen" in c else [],
            )
    for doc in _usable(ctx, "ownership_chart"):
        for row in sorted_rows(doc.tables.get("holders")):
            c = row.cells
            kind = (
                {"individual": "person", "company": "entity"}.get(
                    (c["holder_type"].value or "").lower()
                )
                if "holder_type" in c
                else None
            )
            for col, role in (
                ("holder_name", "owner in the chart"),
                ("parent_name", "parent in the chart"),
            ):
                if col in c:
                    add(
                        c[col].value,
                        kind if col == "holder_name" else None,
                        role,
                        _Where(
                            doc, "holders", row.index, col, labelled(cell_region(c[col], row), col)
                        ),
                        ids=[c["id_or_uen"].value or ""]
                        if col == "holder_name" and "id_or_uen" in c
                        else [],
                    )
    for doc in _usable(ctx, "passport", "national_id"):
        number = _cell(doc, "passport_number") or _cell(doc, "id_number")
        cell = doc.fields.get("date_of_birth")
        field_subject(
            doc,
            "holder_name",
            "ID holder",
            "person",
            dob=cell.date_() if cell is not None else None,
            nationality=_cell(doc, "nationality"),
            ids=[number],
        )
    for doc in _usable(ctx, "source_of_funds_declaration"):
        field_subject(doc, "declarant_name", "declarant of the source of funds", "person")
        field_subject(doc, "counterparty_name", "named counterparty")
    for doc in _usable(ctx, "invoice"):
        field_subject(doc, "supplier_name", "invoice supplier", ids=[_cell(doc, "supplier_uen")])
        field_subject(doc, "buyer_name", "invoice buyer", ids=[_cell(doc, "buyer_uen")])
    for doc in _usable(ctx, "purchase_order"):
        field_subject(doc, "supplier_name", "purchase-order supplier")
        field_subject(doc, "buyer_name", "purchase-order buyer")
    for doc in _usable(ctx, "contract"):
        field_subject(doc, "seller_name", "contract seller")
        field_subject(doc, "buyer_name", "contract buyer")
    for doc in _usable(ctx, "bank_statement"):
        field_subject(doc, "account_holder", "account holder")
    return list(found.values())[:MAX_SUBJECTS]


def _matched_on(hit: Hit) -> str:
    parts = [LEVEL_LABEL[hit.name_match]]
    if hit.id_match:
        parts.append("identifier number")
    if hit.dob == "strong":
        parts.append("date of birth")
    elif hit.dob == "weak":
        parts.append("year of birth (the list gives a year or range)")
    elif hit.dob == "conflict":
        parts.append("date of birth DIFFERS")
    if hit.nationality:
        parts.append("nationality")
    return ", ".join(parts)


def _listing_details(entry: dict) -> str:
    """What the list says about the entry besides its name (programme, country, date listed), so the person
    adjudicating can compare it with the document without opening the list."""
    bits = []
    programs = [safe_text(p, 40) for p in (entry.get("programs") or [])[:3]]
    if programs:
        bits.append("programme " + ", ".join(programs))
    countries = [safe_text(n, 30) for n in (entry.get("nationality") or [])[:2]]
    if countries:
        bits.append("country " + ", ".join(countries))
    if entry.get("listed_on"):
        bits.append("listed " + safe_text(entry["listed_on"], 12))
    return ("; the list gives: " + ", ".join(bits)) if bits else ""


def _sanctions_flag(subject: _Subject, hit: Hit) -> Flag:
    entry = hit.entry
    docs = list({w.doc.id: w.doc for w in subject.where}.values())
    primary = subject.where[0]
    list_name = SOURCE_LABELS.get(entry["list"], entry["list"])
    level = hit.level
    ref = f"{list_name} entry {safe_text(entry['ref'], 40)} ({entry['kind']})"
    shown_ids = ", ".join(mask_id(i) for i in subject.ids if i)[:60]
    evidence = (
        f"document: {safe_text(subject.name)} ({', '.join(subject.roles)}"
        + (f"; id {shown_ids}" if shown_ids else "")
        + f"); list: {ref}: {safe_text(hit.listed_name)}; matched on: {_matched_on(hit)}"
        + _listing_details(entry)
    )
    if level == "high":
        explanation = (
            f"{safe_text(subject.name)} matches {ref} on the name and on at least one other identifier. "
            "Check the list entry against the person before anything else; this tool makes no decision "
            "and a person must adjudicate."
        )
    elif level == "low":
        explanation = (
            f"The name {safe_text(subject.name)} matches {ref}, but the date of birth on the list differs "
            "from the document. Probably a different person with the same name; a person should confirm."
        )
    else:
        if entry["kind"] == "entity":
            care = (
                "A name alone does not identify a company: unrelated companies can share one. Compare the "
                "registration number, country and address of the list entry with this company before "
                "treating it as listed."
            )
        else:
            care = (
                "A name alone does not identify a person: many people share one. Compare the date of birth, "
                "nationality and ID number of the list entry with this person before treating them as listed."
            )
        explanation = (
            f"Possible match, a person must adjudicate: {safe_text(subject.name)} resembles {ref} "
            f"({_matched_on(hit)}). {care} Nothing here says it is the listed one."
        )
    return make_flag(
        "x_sanctions_screen",
        docs=docs,
        table=primary.table,
        row=primary.row,
        field=primary.field,
        region=primary.region,
        severity=level,
        discriminator=f"{entry['id']}~{subject.name}",
        evidence=evidence,
        explanation=explanation,
    )


@check(
    rule="x_sanctions_screen",
    applies_to={"*"},
    stage="crossdoc",
    severity="high",
    description="Applicant, directors, owners and named counterparties against the official sanctions lists",
)
def x_sanctions_screen(ctx: CaseContext) -> list[Flag]:
    """High: an exact name and an agreeing identifier. Medium: a possible match (exact name alone, or a
    name one letter off) for a person to adjudicate. Low: an exact name whose listed date of birth differs.
    A snapshot that is stale or lacks a list is not a clean result: with no hit the check is a skip."""
    out = _outcome(ctx, "sanctions", "sanctions")
    screener: Screener = out.data
    flags: list[Flag] = []
    subjects = _collect_subjects(ctx)
    for subject in subjects:
        hits = screener.screen(
            Subject(
                subject.name,
                subject.kind,  # type: ignore[arg-type]
                subject.dob,
                subject.nationality,
                tuple(i for i in subject.ids if i),
            )
        )
        flags += [_sanctions_flag(subject, h) for h in hits]
    if not flags and out.reason:
        raise CheckSkipped(
            f"no match in what was screened, but the snapshot is incomplete: {out.reason}"
        )
    snap = screener.snapshot
    lists = (
        ", ".join(sid.upper() for sid in SOURCE_ORDER if snap.source(sid) is not None) + " lists"
    )
    ctx.run_notes["x_sanctions_screen"] = (
        f"Screened {len(subjects)} name(s) against {lists} (snapshot of {snap.built_at.date().isoformat()}, "
        f"{len(snap.entries):,} entries): "
        + (f"{len(flags)} possible match(es) for a person to adjudicate." if flags else "no match.")
    )
    return flags


# --------------------------------------------------------------------------------------
# x_entity_vs_gleif
# --------------------------------------------------------------------------------------


def _postal(text: str | None) -> str | None:
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", text or "")
    return m[1] if m else None


@check(
    rule="x_entity_vs_gleif",
    applies_to={"company_profile", "certificate_of_incorporation"},
    stage="crossdoc",
    severity="medium",
    use_cases=frozenset(),  # listed only when a profile or certificate is present
    description="Company name, status and address match the GLEIF legal-entity record when one exists",
    tamper="company name edited against the GLEIF record",
)
def x_entity_vs_gleif(ctx: CaseContext) -> list[Flag]:
    """No LEI is normal (most companies have none) and is never a flag. A record that disagrees is: a
    different legal name (medium), an inactive entity (medium), a different postal code (low). A parent
    company is context (an info note, no score)."""
    rec = _outcome(ctx, "gleif", "gleif").data
    if rec is None:
        ctx.run_notes["x_entity_vs_gleif"] = (
            "Asked GLEIF: it holds no legal-entity record for this company, which is normal and not a flag."
        )
        return []
    docs = _usable(ctx, "company_profile", "certificate_of_incorporation")
    flags: list[Flag] = []
    for doc in docs:
        cell = doc.fields.get("company_name")
        if (
            doc.doc_type == "company_profile"
            and cell is not None
            and cell.value
            and match_company(cell.value, rec.legal_name).verdict == "different"
        ):
            flags.append(
                make_flag(
                    "x_entity_vs_gleif",
                    docs=[doc],
                    field="company_name",
                    region=labelled(cell_region(cell), "name"),
                    discriminator="name",
                    evidence=f"document: {safe_text(cell.value)}; GLEIF ({rec.lei}): {safe_text(rec.legal_name)}",
                    explanation=(
                        f"The company name on this document is not the legal name GLEIF holds for LEI {rec.lei}."
                    ),
                )
            )
        addr = doc.fields.get("registered_address")
        doc_postal = _postal(addr.value if addr is not None else None)
        if doc_postal and rec.postal_code and doc_postal != rec.postal_code.strip():
            flags.append(
                make_flag(
                    "x_entity_vs_gleif",
                    docs=[doc],
                    field="registered_address",
                    region=labelled(cell_region(addr), "address"),
                    severity="low",
                    discriminator="address",
                    evidence=f"document postal code {doc_postal}; GLEIF legal address {rec.postal_code}",
                    explanation=(
                        "The postal code in the registered address differs from the legal address GLEIF "
                        "holds. Companies move; check the date of the record."
                    ),
                )
            )
    if rec.entity_status == "INACTIVE" and docs:
        flags.append(
            make_flag(
                "x_entity_vs_gleif",
                docs=docs,
                discriminator="status",
                evidence=f"GLEIF ({rec.lei}) entity status: {rec.entity_status}",
                explanation="GLEIF lists this legal entity as inactive (it may have ceased to exist).",
            )
        )
    parents = [p for p in (rec.direct_parent, rec.ultimate_parent) if p is not None]
    if parents and docs:
        text = "; ".join(
            f"{label} {safe_text(p.name)} (LEI {p.lei})"
            for label, p in (
                ("direct parent", rec.direct_parent),
                ("ultimate parent", rec.ultimate_parent),
            )
            if p is not None
        )
        flags.append(
            make_flag(
                "x_entity_vs_gleif",
                docs=docs,
                severity="info",
                discriminator="parent",
                evidence=f"GLEIF ({rec.lei}): {text}",
                explanation="Context, not a finding: GLEIF reports the parent companies above. "
                "Check that the ownership documents account for them.",
            )
        )
    ctx.run_notes["x_entity_vs_gleif"] = (
        f"Asked GLEIF for {safe_text(rec.legal_name)} (LEI {rec.lei}): {rec.entity_status}. "
        + (
            "The documents agree."
            if not [f for f in flags if f.severity != "info"]
            else "The documents differ."
        )
    )
    return flags


# --------------------------------------------------------------------------------------
# x_address_vs_registry
# --------------------------------------------------------------------------------------

_ROAD_WORDS = {
    "rd": "road", "st": "street", "ave": "avenue", "av": "avenue", "dr": "drive", "ln": "lane",
    "cres": "crescent", "cl": "close", "pl": "place", "blvd": "boulevard", "ctr": "centre",
    "center": "centre", "jln": "jalan", "lor": "lorong", "upp": "upper", "nth": "north",
    "sth": "south", "bt": "bukit", "tg": "tanjong", "kg": "kampong", "ter": "terrace",
    "gdn": "garden", "gdns": "gardens", "hts": "heights", "pk": "park", "sq": "square",
    "mt": "mount", "bkt": "bukit",
}  # fmt: skip


def _road_key(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", text.casefold()).split()
    return " ".join(_ROAD_WORDS.get(w, w) for w in words)


def _block_and_road(address: str) -> tuple[str | None, str]:
    """(block number, road key) of the street part of a printed address: ``25 Airline Road, ...``."""
    first = address.split(",")[0].split("#")[0]  # the unit number (#01-48) is not part of the road
    m = re.match(r"\s*(?:blk\.?|block)?\s*(\d+[A-Za-z]?)\s+(.+)$", first, flags=re.I)
    if not m:
        return None, _road_key(first)
    return m[1].upper(), _road_key(m[2])


@check(
    rule="x_address_vs_registry",
    applies_to={"company_profile"},
    stage="crossdoc",
    severity="medium",
    use_cases=frozenset(),  # listed only when a company profile is present
    description="Printed Singapore postal code and address exist (OneMap) and match the registered address",
    tamper="registered address replaced by a different or invented one",
)
def x_address_vs_registry(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    skips: list[CheckSkipped] = []
    asked = False
    for doc in _usable(ctx, "company_profile"):
        cell = doc.fields.get("registered_address")
        address = cell.value if cell is not None else None
        postals = postal_codes(address)
        if not address or not postals:
            continue
        postal = postals[0]
        try:
            records = _outcome(ctx, "onemap", f"onemap:{postal}").data
        except CheckSkipped as skip:  # this postal code could not be asked; the others still count
            skips.append(skip)
            continue
        asked = True
        region = labelled(cell_region(cell), "address")
        if not records:
            flags.append(
                make_flag(
                    "x_address_vs_registry",
                    docs=[doc],
                    field="registered_address",
                    region=region,
                    discriminator="postal",
                    evidence=f"document address: {safe_text(address, 100)}; OneMap has no place with postal code {postal}",
                    explanation=(
                        f"Postal code {postal} does not exist in OneMap, so this registered address "
                        "may be invented or mistyped."
                    ),
                )
            )
            continue
        block, road = _block_and_road(address)
        fits = [
            r
            for r in records
            if (road and _road_key(r.road) == road) and (block is None or block == r.block.upper())
        ]
        if (block or road) and not fits and any(_road_key(r.road) for r in records):
            listed = "; ".join(f"{r.block} {r.road}" for r in records[:2])
            flags.append(
                make_flag(
                    "x_address_vs_registry",
                    docs=[doc],
                    field="registered_address",
                    region=region,
                    discriminator="street",
                    evidence=f"document: {safe_text(address, 100)}; OneMap for {postal}: {safe_text(listed, 80)}",
                    explanation=(
                        f"The block and road printed on this document are not the ones OneMap holds for "
                        f"postal code {postal}."
                    ),
                )
            )
        registry_address = None
        if ctx.registry is not None:
            ent = ctx.registry.lookup(normalise_id(ctx.applicant.uen))
            registry_address = ent.registered_address if ent is not None else None
        reg_postal = _postal(registry_address)
        if reg_postal and reg_postal != postal:
            flags.append(
                make_flag(
                    "x_address_vs_registry",
                    docs=[doc],
                    field="registered_address",
                    region=region,
                    severity="low",
                    discriminator="registry",
                    evidence=f"document postal code {postal}; registry {reg_postal}",
                    explanation=(
                        "The postal code on this document differs from the registered address in the "
                        "company register. The company may have moved; check the date of the document."
                    ),
                )
            )
    if skips and not asked and not flags:
        raise _first_skip(skips)
    if not asked and not flags:
        raise CheckSkipped("no registered address with a postal code on the profile")
    _note_unasked(ctx, "x_address_vs_registry", skips)
    return flags


# --------------------------------------------------------------------------------------
# inv_gst_registered
# --------------------------------------------------------------------------------------


def _beyond_cap(ctx: CaseContext) -> set[str]:
    """Supplier ids on the invoices that the runner did not ask for (it asks the first MAX_GST_LOOKUPS)."""
    ids = []
    for doc in _usable(ctx, "invoice"):
        ident = "".join(
            (_cell(doc, "supplier_gst_no") or _cell(doc, "supplier_uen")).split()
        ).upper()
        if ident and ident not in ids:
            ids.append(ident)
    return set(ids[MAX_GST_LOOKUPS:])


@check(
    rule="inv_gst_registered",
    applies_to={"invoice"},
    stage="rules",
    severity="medium",
    use_cases=frozenset(),  # listed only when an invoice is present
    description="The supplier that charges GST is on the IRAS GST register",
    tamper="invented GST registration number on an invoice",
)
def inv_gst_registered(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    skips: list[CheckSkipped] = []
    asked = False
    unlooked = 0
    beyond = _beyond_cap(ctx)
    for doc in _usable(ctx, "invoice"):
        ident = "".join(
            (_cell(doc, "supplier_gst_no") or _cell(doc, "supplier_uen")).split()
        ).upper()
        if not ident:
            continue
        try:
            status = _outcome(ctx, "iras", f"iras:{ident}").data
        except CheckSkipped as skip:  # this number could not be asked; the others still count
            if ident in beyond and not source_status(ctx.settings, "iras")[0]:
                unlooked += 1  # past MAX_GST_LOOKUPS: not asked, said so on the run
            else:
                skips.append(skip)
            continue
        asked = True
        gst_cell = doc.fields.get("supplier_gst_no") or doc.fields.get("supplier_uen")
        region = labelled(cell_region(gst_cell), "gst")
        gst_amount = doc.fields["gst_amount"].money() if "gst_amount" in doc.fields else None
        charges = gst_amount is not None and gst_amount > 0
        shown = mask_id(ident)
        inv_date = doc.fields["invoice_date"].date_() if "invoice_date" in doc.fields else None
        common = {"docs": [doc], "field": "supplier_gst_no", "region": region}
        if not status.registered:
            if charges or _cell(doc, "supplier_gst_no"):
                flags.append(
                    make_flag(
                        "inv_gst_registered",
                        discriminator="not_registered",
                        evidence=f"invoice supplier {shown}: the GST register says not registered"
                        + (f" (removed {status.removed_on})" if status.removed_on else ""),
                        explanation="This invoice shows GST, but the supplier is not on the GST register.",
                        **common,
                    )
                )
            continue
        if (
            status.name
            and _cell(doc, "supplier_name")
            and match_company(_cell(doc, "supplier_name"), status.name).verdict == "different"
        ):
            flags.append(
                make_flag(
                    "inv_gst_registered",
                    discriminator="name",
                    evidence=f"invoice supplier: {safe_text(_cell(doc, 'supplier_name'))}; GST register for {shown}: {safe_text(status.name)}",
                    explanation="The GST register holds this number under a different name than the supplier's.",
                    **common,
                )
            )
        if inv_date is not None and status.registered_from:
            try:
                if inv_date < date.fromisoformat(status.registered_from):
                    flags.append(
                        make_flag(
                            "inv_gst_registered",
                            severity="low",
                            discriminator="before_registration",
                            evidence=f"invoice dated {inv_date.isoformat()}; GST registration from {status.registered_from}",
                            explanation="The supplier charged GST before the date it was registered for GST.",
                            **common,
                        )
                    )
            except ValueError:
                pass
        if inv_date is not None and status.removed_on:
            try:
                if inv_date > date.fromisoformat(status.removed_on):
                    flags.append(
                        make_flag(
                            "inv_gst_registered",
                            discriminator="after_removal",
                            evidence=f"invoice dated {inv_date.isoformat()}; GST registration removed {status.removed_on}",
                            explanation="The supplier charged GST after its GST registration was removed.",
                            **common,
                        )
                    )
            except ValueError:
                pass
    if skips and not asked and not flags:
        raise _first_skip(skips)
    if not asked and not flags:
        if unlooked:
            raise CheckSkipped(
                f"{unlooked} supplier number(s) not looked up (limit of {MAX_GST_LOOKUPS} per case)"
            )
        raise CheckSkipped("no supplier GST number or UEN on the invoices")
    extra = (
        f"{unlooked} supplier number(s) not looked up (limit of {MAX_GST_LOOKUPS} per case)"
        if unlooked
        else ""
    )
    _note_unasked(ctx, "inv_gst_registered", skips, extra)
    return flags
