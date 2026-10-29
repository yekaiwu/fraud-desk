"""Company-name normalisation and matching (docs/checks-financial.md section 3.4 is normative).

This is company matching only. Person-name matching lives in ``app/names.py`` (rules-kyc).
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

COMPANY_MATCH_MIN = 0.92

LEGAL_FORM_TOKENS = frozenset(
    {
        "PTE",
        "LTD",
        "PRIVATE",
        "LIMITED",
        "LLP",
        "LP",
        "CO",
        "COMPANY",
        "INC",
        "CORP",
        "CORPORATION",
        "SDN",
        "BHD",
    }
)

_TA = re.compile(r"(?<![A-Z0-9])T/A(?![A-Z0-9])")
_PUNCT = re.compile(r"[.,'\"()\-/]")
_NONALNUM = re.compile(r"[^a-z0-9]")


def normalise_uen(uen: str | None) -> str:
    """Upper-case with all whitespace removed ('' for None)."""
    return "".join((uen or "").split()).upper()


def _key_of(part: str) -> str:
    text = part.replace("&", " AND ")
    text = _PUNCT.sub(" ", text)
    tokens = text.split()
    while len(tokens) > 1 and tokens[-1] in LEGAL_FORM_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def company_keys(name: str) -> list[str]:
    """All keys of a name: one per side of a ``T/A`` (trading-as) name; [] for an empty name."""
    text = unicodedata.normalize("NFKC", name or "").upper()
    text = " ".join(text.split())
    if not text:
        return []
    keys = [_key_of(part) for part in _TA.split(text)]
    return [k for k in keys if k]


def company_key(name: str) -> str:
    """Normalised key of the primary name (before any ``T/A``); '' for an empty name."""
    keys = company_keys(name)
    return keys[0] if keys else ""


def _digit_tokens(key: str) -> list[str]:
    return sorted(t for t in key.split() if any(c.isdigit() for c in t))


def _keys_match(ka: str, kb: str) -> bool:
    if ka == kb:
        return True
    if _digit_tokens(ka) != _digit_tokens(kb):
        return False
    return fuzz.token_sort_ratio(ka, kb) / 100 >= COMPANY_MATCH_MIN


def company_match(a: str, b: str) -> bool:
    """True when the names are the same company: equal keys, or a close fuzzy match whose
    digit-bearing tokens are equal (so ``ACME 1`` and ``ACME 2`` differ). Empty never matches."""
    keys_a, keys_b = company_keys(a), company_keys(b)
    if not keys_a or not keys_b:
        return False
    return any(_keys_match(ka, kb) for ka in keys_a for kb in keys_b)


def company_disc(name: str) -> str:
    """Discriminator form: sorted lower-case tokens of the key joined by ``_`` ([a-z0-9_] only)."""
    tokens = []
    for tok in company_key(name).lower().split():
        clean = _NONALNUM.sub("", tok)
        if clean:
            tokens.append(clean)
    return "_".join(sorted(tokens))


def uen_disc(uen: str) -> str:
    """``uen_`` + lower-cased alphanumerics of the UEN."""
    return "uen_" + _NONALNUM.sub("", (uen or "").lower())
