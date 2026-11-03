"""Offline form checks of bank details printed on invoices and bank statements (no network, no source).

* IBAN: country length and the ISO 7064 mod-97 check (python-stdnum does the check). An IBAN-shaped string
  counts when its country is one of the IBAN countries and its length is that country's; a string that is
  labelled "IBAN" is checked whatever it looks like.
* BIC/SWIFT: 8 or 11 characters, a real country code in positions 5 and 6, when it is labelled "BIC" or
  "SWIFT".
* A BIC and an IBAN printed together must name the same country.

Singapore accounts are not IBANs and Singapore bank and branch codes are not checked here: no authoritative
public table was verified for this build (docs/verification-sources.md).

Severity is low: a typo in a printed account number is the commonest cause, and a transposed digit is also
what a pasted-over account number looks like. A form check can only say the number cannot be right.
"""

from __future__ import annotations

import re

from stdnum import bic as stdnum_bic
from stdnum import iban as stdnum_iban
from stdnum.exceptions import ValidationError

from app.checks.registry import check, make_flag
from app.core.context import CaseContext
from app.core.models import Document, Flag
from app.names import mask_id, safe_text

_LENGTHS = (
    "AD24 AE23 AL28 AT20 AZ28 BA20 BE16 BG22 BH22 BR29 BY28 CH21 CR22 CY28 CZ24 DE22 DK18 DO28 "
    "EE20 EG29 ES24 FI18 FO18 FR27 GB22 GE22 GI23 GL18 GR27 GT28 HR21 HU28 IE22 IL23 IQ23 IS26 "
    "IT27 JO30 KW30 KZ20 LB28 LC32 LI21 LT20 LU20 LV21 MC27 MD24 ME22 MK19 MR27 MT31 MU30 NL18 "
    "NO15 PK24 PL28 PS29 PT25 QA29 RO24 RS22 SA24 SC31 SE24 SI19 SK24 SM27 ST25 SV28 TL23 TN24 "
    "TR26 UA29 VA22 VG24 XK20"
)
IBAN_LENGTHS: dict[str, int] = {pair[:2]: int(pair[2:]) for pair in _LENGTHS.split()}
_IBAN_SHAPE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?)(?![A-Z0-9])"
)
_IBAN_LABELLED = re.compile(r"\bIBAN\b[\s:.#-]*([A-Z0-9][A-Z0-9 ]{10,40})", re.I)
_BIC_LABELLED = re.compile(
    r"\b(?:BIC|SWIFT)(?:\s*(?:/|-)\s*(?:BIC|SWIFT))?(?:\s*code)?\b[\s:.#-]*([A-Z0-9]{8}(?:[A-Z0-9]{3})?)\b",
    re.I,
)
MAX_FLAGS_PER_DOC = 4


def _text(doc: Document) -> str:
    return " ".join(w.text for w in doc.words)


def _iban_problem(raw: str, labelled: bool) -> str | None:
    """Why ``raw`` is not a valid IBAN, or None if it is one (or not one we judge)."""
    iban = re.sub(r"\s+", "", raw).upper()
    country = iban[:2]
    expected = IBAN_LENGTHS.get(country)
    if expected is None:
        return "its country code is not an IBAN country" if labelled else None
    if len(iban) != expected:
        # a string that merely looks like an IBAN and has the wrong length is more likely a reference
        return (
            f"{country} IBANs have {expected} characters, this has {len(iban)}"
            if labelled
            else None
        )
    try:
        stdnum_iban.validate(iban)
    except ValidationError:
        return "its check digits do not work out (mod 97)"
    return None


def _bic_problem(raw: str) -> str | None:
    try:
        stdnum_bic.validate(raw.upper())
    except ValidationError:
        return "it is not 8 or 11 characters of a bank code, a real country and a location"
    return None


def _finds(text: str) -> tuple[list[tuple[str, bool]], list[str]]:
    """(IBAN-shaped strings with whether they are labelled, labelled BICs)."""
    ibans: dict[str, bool] = {}
    for m in _IBAN_LABELLED.finditer(text):
        cand = _IBAN_SHAPE.search(m.group(1).upper()) or None
        ibans[
            re.sub(r"\s+", "", (cand.group(1) if cand else m.group(1).split("  ")[0])).upper()
        ] = True
    for m in _IBAN_SHAPE.finditer(text.upper()):
        ibans.setdefault(re.sub(r"\s+", "", m.group(1)), False)
    return list(ibans.items()), [m.group(1).upper() for m in _BIC_LABELLED.finditer(text)]


@check(
    rule="inv_bank_details_valid",
    applies_to={"invoice", "bank_statement"},
    stage="rules",
    severity="low",
    use_cases=frozenset(),  # listed only when an invoice or a statement is present
    description="IBAN check digits and country length, and BIC/SWIFT form, on the bank details printed",
    tamper="account number or IBAN altered on the page",
)
def inv_bank_details_valid(ctx: CaseContext) -> list[Flag]:
    """Nothing printed that this check can judge is a pass (it ran), not a skip."""
    flags: list[Flag] = []
    for doc in ctx.docs("invoice", "bank_statement"):
        if doc.status not in ("ok", "partial"):
            continue
        text = _text(doc)
        acct = doc.fields.get("account_number")
        if acct is not None and acct.value:
            text += f" IBAN {acct.value}" if re.match(r"[A-Za-z]{2}\d{2}", acct.value) else ""
        ibans, bics = _finds(text)
        countries = set()
        doc_flags = 0
        for iban, labelled in ibans:
            problem = _iban_problem(iban, labelled)
            if problem is None:
                countries.add(iban[:2])
                continue
            if doc_flags < MAX_FLAGS_PER_DOC:
                doc_flags += 1
                flags.append(
                    make_flag(
                        "inv_bank_details_valid",
                        docs=[doc],
                        discriminator=f"iban_{iban[-6:]}",
                        evidence=f"IBAN {mask_id(iban)}: {problem}",
                        explanation=(
                            "The IBAN printed on this document cannot be right: "
                            f"{safe_text(problem)}. Check the account number with the payee."
                        ),
                    )
                )
        for code in bics:
            problem = _bic_problem(code)
            if problem and doc_flags < MAX_FLAGS_PER_DOC:
                doc_flags += 1
                flags.append(
                    make_flag(
                        "inv_bank_details_valid",
                        docs=[doc],
                        discriminator=f"bic_{code}",
                        evidence=f"BIC/SWIFT {safe_text(code, 12)}: {problem}",
                        explanation="The BIC/SWIFT code printed on this document is not a well-formed code.",
                    )
                )
            elif (
                not problem
                and countries
                and code[4:6] not in countries
                and doc_flags < MAX_FLAGS_PER_DOC
            ):
                doc_flags += 1
                flags.append(
                    make_flag(
                        "inv_bank_details_valid",
                        docs=[doc],
                        discriminator=f"country_{code}",
                        evidence=f"BIC country {code[4:6]}; IBAN country {', '.join(sorted(countries))}",
                        explanation="The BIC and the IBAN printed together name different countries.",
                    )
                )
    return flags
