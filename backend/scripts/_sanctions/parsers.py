"""Parsers of the official sanctions lists into one entry shape (docs/verification-sources.md).

Pure functions from the bytes of a downloaded file to a list of entries; nothing here touches the
network. The files are untrusted data: XML with a DTD or an entity declaration is refused (the official
feeds have none), and parsing is incremental so a large file is not held twice.

An entry::

    {"id": "un:6907993", "list": "un", "ref": "CDi.001", "kind": "person"|"entity"|"vessel"|"aircraft",
     "names": [primary, strong aliases ...], "dob": ["1971", "1936-07-01", "1947/1950"],
     "nationality": ["iraq"], "ids": [{"type": "Passport", "number": "..."}],
     "programs": ["Iraq"], "listed_on": "2003-06-27" | None}

Weak aliases (OFAC "weak", EU ``strong="false"``, UN and UK "low quality") are counted but not kept: a
weak alias is a common word more often than a name, and the matcher is set for precision.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_YEAR = re.compile(r"(?<!\d)(1[89]\d\d|20[0-2]\d)(?!\d)")
# id types that are not an identifier of the listed party (OFAC keeps notes and contact data there)
_NOT_AN_ID = re.compile(
    r"gender|secondary sanctions|website|email|phone|digital currency|vessel|aircraft|imo\b|mmsi|"
    r"organi[sz]ation|target type|executive order|additional sanctions|transactions prohibited|"
    r"caatsa|swift|directive|flag|tonnage|call sign|operator|date|model|manufactur|tail|"
    r"construction|build|length|owner",
    re.I,
)


class UnsafeXml(ValueError):
    """The file declares a DTD or an entity: not an official feed, not parsed."""


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def guard_xml(data: bytes) -> None:
    head = data[:16384].lower()
    if b"<!entity" in head or b"<!doctype" in head:
        raise UnsafeXml("the file declares a DTD or an entity")


def iter_elements(data: bytes, wanted: str) -> Iterator[ET.Element]:
    """Each complete ``wanted`` element, then freed."""
    guard_xml(data)
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if local(el.tag) == wanted:
            yield el
            el.clear()


def child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if local(c.tag) == name]


def text(el: ET.Element | None, name: str | None = None) -> str:
    node = child(el, name) if (el is not None and name) else el
    return " ".join((node.text or "").split()) if node is not None else ""


def descend(el: ET.Element, *path: str) -> list[ET.Element]:
    nodes = [el]
    for name in path:
        nodes = [c for n in nodes for c in children(n, name)]
    return nodes


# ------------------------------------------------------------------------------------- dates


def parse_dob(raw: str) -> str | None:
    """A date of birth as "YYYY-MM-DD", "YYYY-MM", "YYYY" or "YYYY/YYYY" (a range); None if unreadable.
    Reads "19 Apr 1947", "Apr 1947", "1947", "circa 1954", "1947 to 1950", "12/03/1970", "dd/mm/1945",
    "1936-07-01"."""
    s = " ".join((raw or "").lower().replace(",", " ").split())
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:t.*)?", s)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}"
    m = re.fullmatch(r"(\d{2}|dd)/(\d{2}|mm)/(\d{4})", s)
    if m:
        d, mo, y = m.groups()
        if mo.isdigit():
            return f"{y}-{mo}-{d}" if d.isdigit() else f"{y}-{mo}"
        return y
    m = re.fullmatch(r"(?:circa )?(\d{1,2}) ([a-z]{3})[a-z]* (\d{4})", s)
    if m and m[2] in MONTHS:
        return f"{m[3]}-{MONTHS[m[2]]:02d}-{int(m[1]):02d}"
    m = re.fullmatch(r"(?:circa )?([a-z]{3})[a-z]* (\d{4})", s)
    if m and m[1] in MONTHS:
        return f"{m[2]}-{MONTHS[m[1]]:02d}"
    years = _YEAR.findall(s)
    if len(years) == 2 and re.search(r"\bto\b|-|between|and", s):
        a, b = sorted(years)
        return f"{a}/{b}"
    if len(years) == 1:
        return years[0]
    return None


def _entry(
    list_id: str,
    uid: str,
    ref: str,
    kind: str,
    names: list[str],
    *,
    dob: list[str] | None = None,
    nationality: list[str] | None = None,
    ids: list[dict[str, str]] | None = None,
    programs: list[str] | None = None,
    listed_on: str | None = None,
    weak: int = 0,
) -> dict[str, Any] | None:
    seen: set[str] = set()
    clean = []
    for n in names:
        n = " ".join(n.split())
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            clean.append(n)
    if not clean:
        return None
    return {
        "id": f"{list_id}:{uid}",
        "list": list_id,
        "ref": ref or uid,
        "kind": kind,
        "names": clean,
        "dob": sorted(set(dob or [])),
        "nationality": sorted({n.casefold() for n in (nationality or []) if n}),
        "ids": [i for i in (ids or []) if i.get("number")],
        "programs": sorted({p for p in (programs or []) if p}),
        "listed_on": listed_on or None,
        "weak_aliases": weak,
    }


# ------------------------------------------------------------------------------------- UN


def parse_un(data: bytes) -> list[dict[str, Any]]:
    """UN Security Council consolidated list (scsanctions.un.org/resources/xml/en/consolidated.xml)."""
    out = []
    for kind, tag in (("person", "INDIVIDUAL"), ("entity", "ENTITY")):
        for el in iter_elements(data, tag):
            parts = [
                text(el, k) for k in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")
            ]
            names = [" ".join(p for p in parts if p), text(el, "NAME_ORIGINAL_SCRIPT")]
            weak = 0
            for al in descend(el, f"{tag}_ALIAS"):
                name = text(al, "ALIAS_NAME")
                if not name:
                    continue
                if "low" in text(al, "QUALITY").lower():
                    weak += 1
                else:
                    names.append(name)
            dob = []
            for d in descend(el, "INDIVIDUAL_DATE_OF_BIRTH"):
                if text(d, "DATE"):
                    dob.append(parse_dob(text(d, "DATE")) or "")
                elif text(d, "YEAR"):
                    dob.append(text(d, "YEAR"))
                elif text(d, "FROM_YEAR") and text(d, "TO_YEAR"):
                    dob.append(f"{text(d, 'FROM_YEAR')}/{text(d, 'TO_YEAR')}")
            ids = [
                {"type": text(d, "TYPE_OF_DOCUMENT"), "number": text(d, "NUMBER")}
                for d in descend(el, "INDIVIDUAL_DOCUMENT")
            ]
            nat = [text(v) for v in descend(el, "NATIONALITY", "VALUE")]
            e = _entry(
                "un",
                text(el, "DATAID"),
                text(el, "REFERENCE_NUMBER"),
                kind,
                names,
                dob=[d for d in dob if d],
                nationality=nat,
                ids=ids,
                programs=[text(el, "UN_LIST_TYPE")],
                listed_on=text(el, "LISTED_ON"),
                weak=weak,
            )
            if e:
                out.append(e)
    return out


# ------------------------------------------------------------------------------------- OFAC


def parse_ofac(data: bytes) -> list[dict[str, Any]]:
    """OFAC Specially Designated Nationals list, XML (sanctionslistservice.ofac.treas.gov ... SDN.XML)."""
    kinds = {"individual": "person", "entity": "entity", "vessel": "vessel", "aircraft": "aircraft"}
    out = []
    for el in iter_elements(data, "sdnEntry"):
        kind = kinds.get(text(el, "sdnType").lower(), "entity")
        first, last = text(el, "firstName"), text(el, "lastName")
        names = [" ".join(p for p in (first, last) if p)]
        weak = 0
        for aka in descend(el, "akaList", "aka"):
            name = " ".join(p for p in (text(aka, "firstName"), text(aka, "lastName")) if p)
            if text(aka, "category").lower() == "weak":
                weak += 1
            else:
                names.append(name)
        dob = [
            d
            for d in (
                parse_dob(text(x))
                for x in descend(el, "dateOfBirthList", "dateOfBirthItem", "dateOfBirth")
            )
            if d
        ]
        ids = [
            {"type": text(i, "idType"), "number": text(i, "idNumber")}
            for i in descend(el, "idList", "id")
            if not _NOT_AN_ID.search(text(i, "idType"))
        ]
        e = _entry(
            "ofac",
            text(el, "uid"),
            text(el, "uid"),
            kind,
            names,
            dob=dob,
            nationality=[text(n, "country") for n in descend(el, "nationalityList", "nationality")],
            ids=ids,
            programs=[text(p) for p in descend(el, "programList", "program")],
            weak=weak,
        )
        if e:
            out.append(e)
    return out


# ------------------------------------------------------------------------------------- EU


def parse_eu(data: bytes) -> list[dict[str, Any]]:
    """EU consolidated financial sanctions list, XML 1.1 (webgate.ec.europa.eu/fsd/fsf)."""
    out = []
    for el in iter_elements(data, "sanctionEntity"):
        subject = child(el, "subjectType")
        code = (subject.get("code") if subject is not None else "") or ""
        kind = "person" if code == "person" else "entity"
        names: list[str] = []
        weak = 0
        for na in children(el, "nameAlias"):
            whole = (na.get("wholeName") or "").strip() or " ".join(
                p for p in (na.get("firstName"), na.get("middleName"), na.get("lastName")) if p
            )
            if (na.get("strong") or "true").lower() == "false":
                weak += 1
            else:
                names.append(whole)
        dob = []
        for b in children(el, "birthdate"):
            y, mo, d = b.get("year") or "", b.get("monthOfYear") or "", b.get("dayOfMonth") or ""
            if y and mo and d:
                dob.append(f"{y}-{int(mo):02d}-{int(d):02d}")
            elif y and mo:
                dob.append(f"{y}-{int(mo):02d}")
            elif y:
                dob.append(y)
        ids = [
            {"type": i.get("identificationTypeDescription") or "", "number": i.get("number") or ""}
            for i in children(el, "identification")
            if (i.get("knownFalse") or "false") != "true"
        ]
        regs = children(el, "regulation")
        e = _entry(
            "eu",
            el.get("logicalId") or "",
            el.get("euReferenceNumber") or "",
            kind,
            names,
            dob=dob,
            nationality=[c.get("countryDescription") or "" for c in children(el, "citizenship")],
            ids=ids,
            programs=[r.get("programme") or "" for r in regs],
            listed_on=min((r.get("publicationDate") or "" for r in regs), default="") or None,
            weak=weak,
        )
        if e:
            out.append(e)
    return out


# ------------------------------------------------------------------------------------- UK


def parse_uk(data: bytes) -> list[dict[str, Any]]:
    """UK Sanctions List, XML (sanctionslist.fcdo.gov.uk; it replaced the OFSI consolidated list)."""
    kinds = {"individual": "person", "entity": "entity", "ship": "vessel"}
    out = []
    for el in iter_elements(data, "Designation"):
        kind = kinds.get(text(el, "IndividualEntityShip").lower(), "entity")
        names: list[str] = []
        weak = 0
        for n in descend(el, "Names", "Name"):
            parts = [text(n, f"Name{i}") for i in (1, 2, 3, 4, 5, 6)]
            whole = " ".join(p for p in parts if p)
            if "low" in text(n, "AliasStrength").lower():
                weak += 1
            else:
                names.append(whole)
        names += [
            text(n, "NameNonLatinScript") for n in descend(el, "NonLatinNames", "NonLatinName")
        ]
        dob = [
            d
            for d in (
                parse_dob(text(x))
                for x in descend(el, "IndividualDetails", "Individual", "DOBs", "DOB")
            )
            if d
        ]
        ids = [
            {"type": "Passport", "number": text(p, "PassportNumber")}
            for p in descend(el, "IndividualDetails", "Individual", "PassportDetails", "Passport")
        ] + [
            {"type": "National ID", "number": text(p, "NationalIdentifierNumber")}
            for p in descend(
                el,
                "IndividualDetails",
                "Individual",
                "NationalIdentifierDetails",
                "NationalIdentifier",
            )
        ]
        # an entity's registration number is the text of the node
        ids += [
            {"type": "Business registration", "number": text(p)}
            for p in descend(
                el,
                "EntityDetails",
                "Entity",
                "BusinessRegistrationNumbers",
                "BusinessRegistrationNumber",
            )
        ]
        e = _entry(
            "uk",
            text(el, "UniqueID"),
            text(el, "UniqueID"),
            kind,
            names,
            dob=dob,
            nationality=[
                text(n)
                for n in descend(
                    el, "IndividualDetails", "Individual", "Nationalities", "Nationality"
                )
            ],
            ids=ids,
            programs=[text(el, "RegimeName")],
            listed_on=_uk_date(text(el, "DateDesignated")),
            weak=weak,
        )
        if e:
            out.append(e)
    return out


def _uk_date(raw: str) -> str | None:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", raw)
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


PARSERS = {"un": parse_un, "ofac": parse_ofac, "eu": parse_eu, "uk": parse_uk}
