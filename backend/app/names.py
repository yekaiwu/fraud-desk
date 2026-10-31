"""Name matching, identity comparison and small shared helpers for the KYC/AML rules.

Spec: docs/checks-kyc-aml.md sections 3.2 and 4.7. Pure functions: no randomness (the fairness
slice builder seeds its own ``random.Random``), no I/O, no wall clock.

Goal: compare identity, not characters. "Wong Jessica" equals "Jessica Wong"; a near-spelled
different person must not pass on similarity alone. All extracted names are untrusted data.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Literal, NamedTuple

from pydantic import BaseModel
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from app.core.models import Cell, Region, Row, Table, truncate

NameVerdict = Literal["same", "probable", "different", "indeterminate"]
IdentityMatch = Literal["match", "match_name_conflict", "mismatch", "unknown"]

SCORES: dict[str, float] = {"same": 1.0, "probable": 0.8, "different": 0.0, "indeterminate": 0.5}

# --------------------------------------------------------------------------------------
# Constants (all starting guesses, recalibrated from the eval)
# --------------------------------------------------------------------------------------

_MARK = ""  # private-use sentinel for a patronymic marker inside a token list

PATRONYMIC_WORDS = frozenset({"bin", "binte", "binti", "bte", "bt", "ibni", "anak"})
TITLES_MULTI: tuple[tuple[str, str], ...] = (
    ("tan", "sri"),
    ("puan", "sri"),
    ("dato", "sri"),
    ("datuk", "seri"),
    ("datin", "seri"),
    ("assoc", "prof"),
    ("datuk", "sri"),
    ("dato", "seri"),
)
TITLES = frozenset(
    {
        "mr", "mrs", "ms", "miss", "mdm", "madam", "dr", "prof", "professor", "dato", "datuk",
        "datin", "tun", "haji", "hajjah", "hj", "hjh", "encik", "en", "cik", "puan", "sir",
        "rev", "ir", "er",
    }
)  # fmt: skip
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "phd"})
TOKEN_ALIASES: dict[str, str] = {
    **dict.fromkeys(
        (
            "mohd",
            "mohamed",
            "mohammad",
            "mohamad",
            "muhammad",
            "muhamad",
            "mohammed",
            "muhd",
            "mhd",
            "muhamed",
            "mohmmad",
            "mohamod",
            "mohammod",
        ),
        "muhammad",
    ),
    **dict.fromkeys(("abd", "abdul", "abdull"), "abdul"),
    **dict.fromkeys(("nur", "noor"), "nur"),
    **dict.fromkeys(("siti", "st"), "siti"),
}

LEGAL_TOKENS = frozenset(
    {
        "pte", "ltd", "limited", "private", "llp", "lp", "llc", "inc", "incorporated", "corp",
        "corporation", "co", "company", "sdn", "bhd", "berhad", "plc", "pty",
    }
)  # fmt: skip
GENERIC_COMPANY_WORDS = frozenset(
    {
        "holdings", "group", "enterprise", "enterprises", "international", "global", "services",
        "solutions", "industries", "resources",
    }
)  # fmt: skip

TRUNCATION_MIN_CHARS = 12
INITIAL_MIN_EDIT_LEN = 6
INITIALS_MAX = 4  # most initials expanded at once ("S. R. Ramaswamy"); 0 switches it off
MERGE_MAX_TOKEN_LEN = 6
MERGE_MAX_MERGES = 4
_MERGE_VARIANT_CAP = 200

_APOSTROPHES = dict.fromkeys(map(ord, "'’‘`´ʼʻ"), None)
_CJK = re.compile("[㐀-䶿一-鿿豈-﫿]")
_MARKER_SLASH = re.compile(r"(?<![a-z0-9])(?:s/o|d/o|a/l|a/p)(?![a-z0-9])")
_MARKER_PHRASE = re.compile(r"(?<![a-z])(?:son of|daughter of)(?![a-z])")
_B_DOT = re.compile(r"(?<=[a-z]{3})\s+b\.\s+(?=[a-z]{3})")
_BRACKETS = re.compile(r"\(([^()]*)\)")
_PUNCT = re.compile(rf"[^\w{_MARK}]|_")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")
_NRIC = re.compile(r"^[STFGM]\d{7}[A-Z]$")


def safe_text(value: object, limit: int = 80) -> str:
    """Untrusted text for evidence: control characters stripped, whitespace collapsed, truncated."""
    text = _CONTROL.sub(" ", str(value if value is not None else ""))
    text = " ".join(text.split())
    return truncate(text, limit)


def _fold(s: str) -> str:
    """Drop diacritics (NFKD then remove combining marks)."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# --------------------------------------------------------------------------------------
# Person names
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonName:
    raw: str
    tokens: tuple[str, ...]  # cleaned, alias-mapped, original order, patronymic marker removed
    given: tuple[str, ...]
    patronym: tuple[str, ...]
    optional: tuple[str, ...]  # tokens that were in brackets: "Wong Mei Ling (Jessica)"
    script: Literal["latin", "cjk", "mixed", "empty"]


@dataclass(frozen=True)
class NameMatch:
    verdict: NameVerdict
    score: float  # 1.0 same, 0.8 probable, 0.0 different, 0.5 indeterminate
    reason: str  # short, goes into evidence
    a: str  # canonical forms compared
    b: str
    slip: bool = False  # a ``probable`` that rests on a one-letter spelling difference
    slip_kind: str = ""  # "double_letter", "vowel" or "other" when ``slip``

    @property
    def typo(self) -> bool:
        """A ``probable`` that rests only on a one-letter spelling difference, in the whole name or
        in the given names or the patronym of a marked name (``bin``, ``s/o``). That is what a
        near-spelled fake looks like, so identity checks do not accept it on the name alone. With
        the same ID on both sides a typo and a deliberate substitution look identical."""
        return self.verdict == "probable" and self.slip


def _latin_tokens(s: str) -> list[str]:
    s = s.translate(_APOSTROPHES)
    s = _fold(s)
    s = _MARKER_SLASH.sub(f" {_MARK} ", s)
    s = _MARKER_PHRASE.sub(f" {_MARK} ", s)
    s = _B_DOT.sub(f" {_MARK} ", s)
    s = _PUNCT.sub(" ", s)
    return s.split()


def _strip_titles(tokens: list[str]) -> list[str]:
    out = list(tokens)
    while len(out) > 1:
        if len(out) > 2 and tuple(out[:2]) in TITLES_MULTI:
            out = out[2:]
        elif out[0] in TITLES:
            out = out[1:]
        else:
            break
    while len(out) > 1 and out[-1] in SUFFIXES:
        out.pop()
    return out


def _mark_patronymics(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for i, tok in enumerate(out):
        if tok in PATRONYMIC_WORDS and 0 < i < len(out) - 1:
            out[i] = _MARK
    return out


def _alias(tok: str) -> str:
    return TOKEN_ALIASES.get(tok, tok)


@lru_cache(maxsize=8192)
def _normalise(name: str) -> PersonName:
    raw = name.strip()
    if not raw:
        return PersonName(raw, (), (), (), (), "empty")
    s = unicodedata.normalize("NFKC", raw).casefold()
    letters = [c for c in s if c.isalpha()]
    cjk = [c for c in s if _CJK.match(c)]
    if cjk and len(cjk) == len(letters):
        chars = tuple(cjk)
        return PersonName(raw, chars, chars, (), (), "cjk")
    script: Literal["latin", "cjk", "mixed", "empty"] = "mixed" if cjk else "latin"
    if cjk:
        s = _CJK.sub(" ", s)
    optional_parts = _BRACKETS.findall(s)
    core = _BRACKETS.sub(" ", s)
    tokens = _mark_patronymics(_strip_titles(_latin_tokens(core)))
    given: list[str] = []
    patronym: list[str] = []
    seen_marker = False
    flat: list[str] = []
    for tok in tokens:
        if tok == _MARK:
            seen_marker = True
            continue
        tok = _alias(tok)
        flat.append(tok)
        (patronym if seen_marker else given).append(tok)
    optional: list[str] = []
    for part in optional_parts:
        optional.extend(_alias(t) for t in _latin_tokens(part) if t != _MARK)
    if cjk:
        flat.append("".join(cjk))
        given.append("".join(cjk))
    if not flat and not optional:
        return PersonName(raw, (), (), (), (), "empty")
    return PersonName(raw, tuple(flat), tuple(given), tuple(patronym), tuple(optional), script)


def normalise_person(name: str | None) -> PersonName:
    return _normalise(name or "")


def _merge_variants(tokens: tuple[str, ...]) -> set[tuple[str, ...]]:
    """The list itself plus every list obtained by merging adjacent short tokens (<= 4 merges)."""
    seen: set[tuple[str, ...]] = {tokens}
    frontier = [tokens]
    for _ in range(MERGE_MAX_MERGES):
        nxt: list[tuple[str, ...]] = []
        for t in frontier:
            for i in range(len(t) - 1):
                if len(t[i]) <= MERGE_MAX_TOKEN_LEN and len(t[i + 1]) <= MERGE_MAX_TOKEN_LEN:
                    merged = (*t[:i], t[i] + t[i + 1], *t[i + 2 :])
                    if merged not in seen:
                        seen.add(merged)
                        nxt.append(merged)
        frontier = nxt
        if len(seen) > _MERGE_VARIANT_CAP or not frontier:
            break
    return seen


def _is_initial(x: str, y: str) -> bool:
    if len(x) == 1 and len(y) > 1 and y.startswith(x):
        return True
    return len(y) == 1 and len(x) > 1 and x.startswith(y)


def _initials_match(only_a: list[str], only_b: list[str]) -> bool:
    """One side's leftover tokens are all single letters and the other side has longer names whose
    first letters cover those letters (``h l`` for ``hemant laxman``; ``p u`` for ``pudukkottai
    ulaganathan pillai``): the written-out name may carry more given names than the initials."""
    if not 1 <= min(len(only_a), len(only_b)) <= INITIALS_MAX:
        return False
    for short, long_ in ((only_a, only_b), (only_b, only_a)):
        if all(len(t) == 1 for t in short) and all(len(t) > 1 for t in long_):
            have = Counter(t[0] for t in long_)
            return all(have[k] >= n for k, n in Counter(short).items())
    return False


def _edit_one(x: str, y: str) -> bool:
    return (
        min(len(x), len(y)) >= INITIAL_MIN_EDIT_LEN and x != y and Levenshtein.distance(x, y) == 1
    )


_TYPO_REASON = "one letter differs"
_AB_FORMS = {"ab", "abdul"}  # "Ab" is a Malay short form of Abdul but "AB" may be initials
_SLIP_TOKENS = re.compile(r"([^\s:/]+)/([^\s:/]+)$")
_VOWELS = frozenset("aeiouy")  # y stands in for i (Aisyah / Aysyah)
_RANK = {"same": 3, "probable": 2, "different": 1, "indeterminate": 0}


def _compare_tokens(
    ta: tuple[str, ...], tb: tuple[str, ...], *, part: bool = False
) -> tuple[NameVerdict, str]:
    """``part``: the lists are one side of a patronymic marker (given names or the patronym)."""
    if not ta or not tb:
        return "indeterminate", "empty name"
    if sorted(ta) == sorted(tb):
        return "same", "same tokens, order-insensitive"
    # merge in WRITTEN order, compare as sorted lists
    va, vb = _merge_variants(ta), _merge_variants(tb)
    sorted_b = {tuple(sorted(v)) for v in vb}
    if any(tuple(sorted(v)) in sorted_b for v in va):
        return "same", "given names joined"
    ca, cb = Counter(ta), Counter(tb)
    only_a = sorted((ca - cb).elements())
    only_b = sorted((cb - ca).elements())
    if len(only_a) == 1 and len(only_b) == 1:
        x, y = only_a[0], only_b[0]
        if _is_initial(x, y):
            return "probable", f"initial: {x}/{y}"
        if _edit_one(x, y):
            return "probable", f"{_TYPO_REASON}: {x}/{y}"
        if {x, y} == _AB_FORMS:  # not an alias: "AB" is also a pair of initials
            return "probable", f"short form: {x}/{y}"
    if len(ta) > len(only_a) and _initials_match(only_a, only_b):  # a shared token anchors it
        return "probable", f"initials: {' '.join(only_a)}/{' '.join(only_b)}"
    if not only_a or not only_b:
        short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(short) >= 2:
            return "probable", "the shorter name is contained in the longer"
        # a lone token may stand for a mononym ("Suryadi" for "Suryadi Wibowo": the first token of
        # the longer name), never for the last token ("Kumar" is not "Rajesh Kumar")
        if len(short) == 1 and len(short[0]) >= 5 and (part or short[0] == long_[0]):
            return "probable", "single name matches a token of the longer name"
    return "different", f"only in a: {only_a}; only in b: {only_b}"


def slip_kind(x: str, y: str) -> str:
    """Kind of a one-letter difference between two tokens: ``double_letter`` (Kamarudin /
    Kamaruddin), ``vowel`` (Hussain / Hussein, Krishnan / Krishnen) or ``other`` (a consonant
    substituted, dropped or added, which is also how OCR and keyboard slips and fakes look)."""
    if len(x) > len(y):
        x, y = y, x
    if len(y) == len(x) + 1:
        for i in range(len(y)):
            if y[:i] + y[i + 1 :] == x:
                doubled = (i > 0 and y[i] == y[i - 1]) or (i + 1 < len(y) and y[i] == y[i + 1])
                if doubled:
                    return "double_letter"
                return "vowel" if y[i] in _VOWELS else "other"
        return "other"
    diff = [(p, q) for p, q in zip(x, y, strict=False) if p != q]
    if len(diff) == 1 and diff[0][0] in _VOWELS and diff[0][1] in _VOWELS:
        return "vowel"
    return "other"


def _slip_of(reason: str) -> tuple[bool, str]:
    if _TYPO_REASON not in reason:
        return False, ""
    m = _SLIP_TOKENS.search(reason)
    return True, slip_kind(m.group(1), m.group(2)) if m else "other"


def _variants_of(p: PersonName) -> list[tuple[str, ...]]:
    out = [p.tokens]
    if p.optional:
        out.append((*p.tokens, *p.optional))
    return out


def _compare_marked(pa: PersonName, pb: PersonName) -> tuple[NameVerdict, str]:
    """Both names carry a patronymic marker: the given names and the patronym are compared apart,
    so ``Ahmad bin Ismail`` and ``Ismail bin Ahmad`` (father and son) are different people. A
    one-letter slip in either part keeps its reason (``one letter differs in the patronym: ...``)
    so ``NameMatch.typo`` still sees it."""
    best: tuple[NameVerdict, str] = ("different", "")
    for ga in (pa.given, (*pa.given, *pa.optional)) if pa.optional else (pa.given,):
        for gb in (pb.given, (*pb.given, *pb.optional)) if pb.optional else (pb.given,):
            gv, gr = _compare_tokens(ga, gb, part=True)
            # the better verdict wins; at equal rank a variant that is not a slip wins
            if (
                not best[1]
                or _RANK[gv] > _RANK[best[0]]
                or (
                    _RANK[gv] == _RANK[best[0]]
                    and (_TYPO_REASON in best[1])
                    and (_TYPO_REASON not in gr)
                )
            ):
                best = (gv, gr)
    pv, pr = _compare_tokens(pa.patronym, pb.patronym, part=True)
    if best[0] not in ("same", "probable") or pv not in ("same", "probable"):
        if sorted(pa.tokens) == sorted(pb.tokens):
            return "different", "given name and patronym swapped"
        return "different", f"given names: {best[1]}; patronym: {pr}"
    if best[0] == "same" and pv == "same":
        return "same", "given names and patronym match"
    for part, (v, r) in (("given names", best), ("the patronym", (pv, pr))):
        if v == "probable" and _TYPO_REASON in r:
            return "probable", f"{_TYPO_REASON} in {part}: {r.rsplit(': ', 1)[-1]}"
    return "probable", f"given names {best[0]}, patronym {pv}"


def match_person(a: str | None, b: str | None) -> NameMatch:
    pa, pb = normalise_person(a), normalise_person(b)
    ca, cb = " ".join(pa.tokens), " ".join(pb.tokens)

    def result(verdict: NameVerdict, reason: str, x: str = ca, y: str = cb) -> NameMatch:
        slip, kind = _slip_of(reason) if verdict == "probable" else (False, "")
        return NameMatch(verdict, SCORES[verdict], reason, x, y, slip, kind)

    if pa.script == "empty" or pb.script == "empty":
        return result("indeterminate", "empty name")
    if pa.script == "mixed" or pb.script == "mixed" or (pa.script != pb.script):
        return result("indeterminate", "cannot compare scripts")
    if pa.script == "cjk":
        if pa.tokens == pb.tokens:
            return result("same", "same characters")
        return result("different", "different characters")
    if pa.given and pa.patronym and pb.given and pb.patronym:
        verdict, reason = _compare_marked(pa, pb)
        return result(verdict, reason)
    best: tuple[NameVerdict, str, str, str] | None = None
    for va in _variants_of(pa):
        for vb in _variants_of(pb):
            verdict, reason = _compare_tokens(va, vb)
            # the better verdict wins; at equal rank a variant that is not a slip wins
            if (
                best is None
                or _RANK[verdict] > _RANK[best[0]]
                or (
                    _RANK[verdict] == _RANK[best[0]]
                    and _TYPO_REASON in best[1]
                    and _TYPO_REASON not in reason
                )
            ):
                best = (verdict, reason, " ".join(va), " ".join(vb))
    assert best is not None
    return result(best[0], best[1], best[2], best[3])


# --------------------------------------------------------------------------------------
# Company names
# --------------------------------------------------------------------------------------


def _company_forms(name: str | None) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """(stripped tokens, full tokens, whether a legal token was stripped)."""
    s = unicodedata.normalize("NFKC", name or "").casefold()
    s = _fold(s.translate(_APOSTROPHES)).replace("&", " and ")
    full = _PUNCT.sub(" ", s).split()
    if len(full) > 1 and full[0] == "the":
        full = full[1:]
    stripped = list(full)
    while len(stripped) > 1 and stripped[-1] in LEGAL_TOKENS:
        stripped.pop()
    return tuple(stripped), tuple(full), len(stripped) != len(full)


def normalise_company(name: str | None) -> str:
    """Canonical company name used for comparison: lower-case, diacritics and punctuation folded,
    leading 'the' and trailing legal-form tokens (pte, ltd, ...) removed."""
    return " ".join(_company_forms(name)[0])


def is_truncation(a: str | None, b: str | None) -> bool:
    """One normalised name (>= 12 chars) is a strict prefix of the other (bank descriptions are
    cut at a fixed width, e.g. ``ACME TRADING PTE L``)."""
    _, fa, _ = _company_forms(a)
    _, fb, _ = _company_forms(b)
    sa, sb = " ".join(fa), " ".join(fb)
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(short) >= TRUNCATION_MIN_CHARS and short != long_ and long_.startswith(short)


def company_in_text(name: str | None, text: str | None) -> bool:
    """The company's name (without its legal form) appears, word for word, inside ``text``.

    A word may be one letter off when it has four letters or more. Meant for free text such as a bank
    description, where OCR noise around a correct name (``EROM VANTAGE TRADING PTE LTD St2026``) must
    not turn the right payer into a different one. A one-word name must be 6+ letters long."""
    wanted, _, _ = _company_forms(name)
    if not wanted or (len(wanted) == 1 and len(wanted[0]) < 6):
        return False
    _, words, _ = _company_forms(text)

    def same(a: str, b: str) -> bool:
        return a == b or (len(a) >= 4 and len(a) == len(b) and _edit_one(a, b))

    return any(
        all(same(words[i + j], w) for j, w in enumerate(wanted))
        for i in range(len(words) - len(wanted) + 1)
    )


def has_legal_token(name: str | None) -> bool:
    return _company_forms(name)[2]


def match_company(a: str | None, b: str | None, *, allow_truncation: bool = True) -> NameMatch:
    sa, fa, _ = _company_forms(a)
    sb, fb, _ = _company_forms(b)
    ca, cb = " ".join(sa), " ".join(sb)

    def result(verdict: NameVerdict, reason: str) -> NameMatch:
        return NameMatch(verdict, SCORES[verdict], reason, ca, cb)

    if not sa or not sb:
        return result("indeterminate", "empty name")
    if sa == sb or sorted(sa) == sorted(sb):
        return result("same", "same name tokens")
    if len(sa) == len(sb):
        diffs = [(x, y) for x, y in zip(sa, sb, strict=True) if x != y]
        if len(diffs) == 1 and _edit_one(*diffs[0]):
            return result("probable", f"one letter differs: {diffs[0][0]}/{diffs[0][1]}")
    core_a = sorted(t for t in sa if t not in GENERIC_COMPANY_WORDS)
    core_b = sorted(t for t in sb if t not in GENERIC_COMPANY_WORDS)
    if core_a and core_a == core_b:
        return result("probable", "only generic words differ")
    if allow_truncation:
        full_a, full_b = " ".join(fa), " ".join(fb)
        short, long_ = (full_a, full_b) if len(full_a) <= len(full_b) else (full_b, full_a)
        if len(short) >= TRUNCATION_MIN_CHARS and long_.startswith(short):
            return result("probable", "one name is a truncation of the other")
    return result(
        "different",
        f"only in a: {sorted(set(sa) - set(sb))}; only in b: {sorted(set(sb) - set(sa))}",
    )


# --------------------------------------------------------------------------------------
# Keys and identity
# --------------------------------------------------------------------------------------


def _key_token(tok: str) -> str:
    ascii_only = re.sub(r"[^a-z0-9]", "", tok)
    if ascii_only == tok:
        return tok
    return "u" + "".join(f"{ord(c):04x}" for c in tok if c.isalnum())


def entity_key(name: str | None, kind: Literal["person", "company", "auto"] = "auto") -> str:
    """THE discriminator form: lower-case tokens, sorted, joined by ``_``, ``[a-z0-9_]`` only."""
    if not (name or "").strip():
        return ""
    if kind == "auto":
        kind = "company" if has_legal_token(name) else "person"
    if kind == "company":
        tokens = list(_company_forms(name)[0])
        ordered = sorted(_key_token(t) for t in tokens if t)
    else:
        p = normalise_person(name)
        if p.script == "cjk":
            ordered = [_key_token(t) for t in p.tokens]  # written order matters in CJK
        else:
            ordered = sorted(_key_token(t) for t in p.tokens if t)
    return "_".join(t for t in ordered if t)


def normalise_id(s: str | None) -> str:
    """Upper-case, spaces and hyphens removed."""
    return re.sub(r"[\s\-]+", "", unicodedata.normalize("NFKC", s or "")).upper()


def mask_id(s: str) -> str:
    """``S8222222A`` -> ``S****222A`` (first char, ``****``, last 4). Ids shorter than 8 characters
    (too short to be an NRIC/FIN or passport number) are left as is."""
    s = normalise_id(s)
    if "*" in s or len(s) < 8:
        return s
    return s[0] + "****" + s[-4:]


def looks_like_nric(s: str | None) -> bool:
    return bool(_NRIC.match(normalise_id(s)))


def ids_conflict_or_equal(a: str | None, b: str | None) -> Literal["equal", "differ", "na"]:
    """Compare two ids; a masked id compares only its last 4 characters."""
    na, nb = normalise_id(a), normalise_id(b)
    if not na or not nb:
        return "na"
    if "*" in na or "*" in nb:
        return "equal" if na[-4:] == nb[-4:] else "differ"
    return "equal" if na == nb else "differ"


class IdentityStatus(NamedTuple):
    """``match`` is what ``same_identity`` returns. ``needs`` says what would settle a name that
    cannot decide alone: ``"id"`` (an NRIC/FIN is missing or only its last 4 characters are
    known) or ``"dob_or_document"`` (the ID is equal but the name differs by one letter, which is
    a typo and a deliberate substitution alike, so only another factor can tell them apart)."""

    match: IdentityMatch
    needs: Literal["", "id", "dob_or_document"]
    name: NameMatch


def identity_status(
    a_name: str | None,
    a_id: str | None,
    b_name: str | None,
    b_id: str | None,
    *,
    kind: Literal["person", "company"] = "person",
) -> IdentityStatus:
    na, nb = normalise_id(a_id), normalise_id(b_id)
    nm = match_company(a_name, b_name) if kind == "company" else match_person(a_name, b_name)
    verdict = nm.verdict
    # a one-letter slip between two person names is what a near-spelled fake looks like: it is
    # no longer a match on the name alone, so it is a conflict (with equal ids) or a mismatch
    typo = kind == "person" and nm.typo
    name_ok = verdict == "same" or (verdict == "probable" and not typo)
    conflict = verdict == "different" or typo
    soft = "id" if verdict == "probable" and not typo else ""  # accepted, but on the name alone
    if na and nb:
        if "*" in na or "*" in nb:  # a masked id compares its last 4 characters only
            if na[-4:] != nb[-4:]:
                return IdentityStatus("mismatch", "", nm)
            if name_ok:
                return IdentityStatus("match", soft, nm)
            if typo:
                return IdentityStatus("match_name_conflict", "dob_or_document", nm)
            return IdentityStatus("unknown", "", nm)
        if na == nb:
            if typo:
                return IdentityStatus("match_name_conflict", "dob_or_document", nm)
            return IdentityStatus("match_name_conflict" if conflict else "match", "", nm)
        return IdentityStatus("mismatch", "", nm)
    if name_ok:
        return IdentityStatus("match", soft, nm)
    if conflict:
        return IdentityStatus("mismatch", "id" if typo else "", nm)
    return IdentityStatus("unknown", "", nm)


def same_identity(
    a_name: str | None,
    a_id: str | None,
    b_name: str | None,
    b_id: str | None,
    *,
    kind: Literal["person", "company"] = "person",
) -> IdentityMatch:
    return identity_status(a_name, a_id, b_name, b_id, kind=kind).match


# --------------------------------------------------------------------------------------
# Percent tolerance, regions, rows
# --------------------------------------------------------------------------------------

_DECIMALS = re.compile(r"\d[\d,]*(?:\.(\d+))?")


def _half_unit(cell: Cell) -> Decimal:
    raw = cell.raw
    if raw is None:
        return Decimal("0.005")
    m = _DECIMALS.search(raw)
    if m is None:
        return Decimal("0.005")
    places = len(m.group(1) or "")
    return Decimal(5) * Decimal(10) ** -(places + 1)


def pct_tol(*cells: Cell) -> Decimal:
    """Rounding-aware percent tolerance: half a unit of each cell's last printed decimal + 0.001."""
    return sum((_half_unit(c) for c in cells), Decimal(0)) + Decimal("0.001")


def union_region(boxes: Sequence[Region | None]) -> Region | None:
    """Bounding box on the page of the first present box; label None."""
    present = [b for b in boxes if b is not None]
    if not present:
        return None
    first = present[0]
    same = [b for b in present if b.file_id == first.file_id and b.page == first.page]
    x0 = min(b.x for b in same)
    y0 = min(b.y for b in same)
    x1 = max(b.x + b.w for b in same)
    y1 = max(b.y + b.h for b in same)
    return Region(
        file_id=first.file_id,
        page=first.page,
        x=x0,
        y=y0,
        w=min(x1 - x0, 1.0 - x0) if x1 - x0 > 0 else first.w,
        h=min(y1 - y0, 1.0 - y0) if y1 - y0 > 0 else first.h,
        label=None,
    )


def cell_region(cell: Cell | None, row: Row | None = None) -> Region | None:
    """``cell.box``, else ``row.box``, else None."""
    if cell is not None and cell.box is not None:
        return cell.box
    if row is not None and row.box is not None:
        return row.box
    return None


def labelled(region: Region | None, label: str) -> Region | None:
    return region.model_copy(update={"label": label}) if region is not None else None


def sorted_rows(table: Table | None) -> list[Row]:
    """Rows in printed order (by ``Row.index``), whatever the in-memory order."""
    if table is None:
        return []
    return sorted(table.rows, key=lambda r: r.index)


def unverified_note(*cells: Cell | None) -> str:
    """Evidence suffix when any value used in a flag is not confirmed in the text layer."""
    if any(c is not None and c.verified is False for c in cells):
        return "; value not confirmed in the text layer"
    return ""


# --------------------------------------------------------------------------------------
# Fairness slice test (plan 7.2)
# --------------------------------------------------------------------------------------


class SlicePair(NamedTuple):
    group: str
    a: str
    b: str
    same_person: bool
    expect: tuple[NameVerdict, ...]


class SliceRow(BaseModel):
    group: str
    same_pairs: int
    false_flag_rate: float
    probable_rate: float
    indeterminate_rate: float
    baseline_false_flag_rate: float
    diff_pairs: int
    false_accept_rate: float
    baseline_false_accept_rate: float


SLICE_GROUPS: tuple[str, ...] = (
    "western_order",
    "chinese_order",
    "malay_patronymic",
    "indian_patronymic",
    "titles",
    "mononym",
    "diacritics_initials",
    "non_latin",
)
N_SAME = 24
N_DIFF = 12
BASELINE_RATIO_MIN = 85

_SAME: tuple[NameVerdict, ...] = ("same",)
_PROB: tuple[NameVerdict, ...] = ("probable",)
_DIFF: tuple[NameVerdict, ...] = ("different",)
_INDET: tuple[NameVerdict, ...] = ("indeterminate",)

# Fixed fictitious name pools. No real people: common given names and surnames combined at random.
_W_GIVEN = ["Jessica", "Daniel", "Priscilla", "Marcus", "Natalie", "Gregory", "Vanessa", "Howard",
            "Isabel", "Raymond", "Cynthia", "Dennis", "Felicia", "Walter", "Melissa", "Clarence"]  # fmt: skip
_W_SURNAME = ["Wong", "Smith", "Fernandez", "Goh", "Pereira", "Lau", "Hughes", "Ng", "Santos",
              "Ong", "Barker", "Quek"]  # fmt: skip
_C_SURNAME = ["Tan", "Lim", "Lee", "Wong", "Chua", "Ong", "Goh", "Teo", "Koh", "Ng", "Yeo", "Sim"]
_C_GIVEN = [("Wei", "Ming"), ("Mei", "Ling"), ("Siew", "Hoon"), ("Boon", "Heng"), ("Kok", "Wai"),
            ("Hui", "Min"), ("Jia", "Hao"), ("Xin", "Yi"), ("Zhi", "Wei"), ("Yong", "Kang"),
            ("Li", "Hua"), ("Shu", "Fen")]  # fmt: skip
_C_NICK = ["Jason", "Kelvin", "Alvin", "Eric", "Gary", "Ivan"]
_C_NEAR = [("Ming", "Ning"), ("Hoon", "Hong"), ("Heng", "Hong"), ("Kang", "Kong"), ("Wai", "Wei"),
           ("Hao", "Hau"), ("Min", "Mun"), ("Yi", "Yu"), ("Fen", "Fan"), ("Hua", "Hui"),
           ("Ling", "Ning"), ("Jie", "Jia")]  # fmt: skip
_M_GIVEN = ["Faris", "Hafiz", "Irfan", "Amirul", "Haikal", "Syafiq", "Zulkifli", "Razak", "Danial",
            "Hakim"]  # fmt: skip
_M_FATHER = ["Abdullah", "Ismail", "Hassan", "Yusof", "Rahman", "Salleh", "Osman", "Ibrahim"]
_M_FEMALE = ["Siti Aminah", "Nurul Huda", "Farah Dina", "Nur Aisyah", "Zarina Hanim",
             "Salmah Ahmad"]  # fmt: skip
_M_NEAR = [("Faris", "Farid"), ("Amir", "Amin"), ("Hakim", "Hakem"), ("Zaki", "Zaid"),
           ("Ayub", "Ayob")]  # fmt: skip
_I_GIVEN = ["Rajesh", "Suresh", "Ramesh", "Anand", "Vijay", "Mohan", "Arun", "Karthik", "Dinesh",
            "Naveen"]  # fmt: skip
_I_FATHER = ["Kumar", "Subramaniam", "Pillai", "Nair", "Krishnan", "Raman", "Gopal", "Singh",
             "Nathan", "Menon"]  # fmt: skip
_I_FEMALE = ["Priya", "Lakshmi", "Divya", "Anita", "Deepa", "Meena"]
_I_NEAR = [("Ravi", "Rani"), ("Arun", "Arul"), ("Vijay", "Vinay"), ("Karan", "Karun"),
           ("Dev", "Devi"), ("Anil", "Anit")]  # fmt: skip
_T_NAMES = ["Lim Boon Heng", "Chua Mei Ling", "Ahmad Zaki", "Lim Kok", "Tan Wei Ming",
            "Ong Hui Min", "Goh Kah Wee", "Teo Siew Hoon", "Ravi Kumar", "Yusof Ishak"]  # fmt: skip
_T_TITLES = ["Mr", "Dr", "Prof", "Mdm", "Mrs", "Ms", "Dato' Sri", "Tan Sri", "Datuk", "Encik",
             "Puan", "Miss"]  # fmt: skip
_T_NEAR = [("Lim Boon Heng", "Lim Boon Hong"), ("Chua Mei Ling", "Chua Mei Ning"),
           ("Tan Wei Ming", "Tan Wei Ning"), ("Goh Kah Wee", "Goh Kah Wei"),
           ("Ong Hui Min", "Ong Hui Mun"), ("Teo Siew Hoon", "Teo Siew Hong"),
           ("Ahmad Zaki", "Ahmad Zaid"), ("Ravi Kumar", "Rani Kumar"),
           ("Yusof Ishak", "Yusof Ishaq")]  # fmt: skip
_MONO = ["Suryadi", "Sukarno", "Wijaya", "Hartono", "Susilo", "Gunawan", "Budiman", "Santoso",
         "Prabowo", "Kurniawan", "Halimah", "Setiawan"]  # fmt: skip
_MONO_SURNAME = ["Wibowo", "Pratama", "Hidayat", "Saputra", "Lestari"]
_MONO_NEAR = [("Suryadi", "Suryanto"), ("Gunawan", "Gunadi"), ("Santoso", "Santika"),
              ("Wijaya", "Wijanto"), ("Hartono", "Hartini"), ("Susilo", "Susanto"),
              ("Budiman", "Budianto"), ("Prabowo", "Prasetyo"), ("Setiawan", "Setyadi"),
              ("Kurniawan", "Kurnia"), ("Sukarno", "Sukardi")]  # fmt: skip
_D_NAMES = [("José", "García"), ("Renée", "Dubois"), ("Zoë", "Müller"), ("François", "Lefèvre"),
            ("Chloé", "Martin"), ("Émile", "Laurent"), ("Anaïs", "Moreau"), ("Hélène", "Petit"),
            ("Jürgen", "Weber"), ("Noémie", "Laurent"), ("André", "Silva"),
            ("Sébastien", "Roux")]  # fmt: skip
_D_ACCENT_NEAR = [("José García", "Jorge García"), ("Renée Dubois", "René Dubois"),
                  ("Zoë Müller", "Zora Müller"), ("André Silva", "Andrea Silva")]  # fmt: skip
_CJK_SURNAME = "陈林黄王李张刘周吴郑"
_CJK_GIVEN = "伟明杰敏华强丽芳磊军婷"
_LATIN_FOR_CJK = ["Tan Wei Ming", "Lim Mei Ling", "Wong Jia Hao", "Lee Xin Yi", "Chen Wei Jie"]


def _plain(s: str) -> str:
    return _fold(unicodedata.normalize("NFKC", s))


def _far(rng: random.Random, pool: Sequence[str], avoid: str, min_dist: int = 2) -> str:
    for _ in range(200):
        pick = rng.choice(list(pool))
        if pick != avoid and Levenshtein.distance(pick.casefold(), avoid.casefold()) >= min_dist:
            return pick
    raise RuntimeError("name pool too small")  # pragma: no cover


def _western(rng: random.Random) -> list[SlicePair]:
    g = "western_order"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        given, sur = rng.choice(_W_GIVEN), rng.choice(_W_SURNAME)
        forms = [
            (f"{given} {sur}", f"{sur} {given}"),
            (f"{given} {sur}", f"{sur}, {given}"),
            (f"{given} {sur}", f"{sur} {given}".upper()),
            (f"  {given.lower()}   {sur.upper()} ", f"{sur} {given}"),
            (f"{sur}, {given}", f"{given} {sur}"),
            (f"{given} {sur}", f"{given}  {sur}"),
        ]
        a, b = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, _SAME))
    for _ in range(N_DIFF):
        g1 = rng.choice(_W_GIVEN)
        g2 = _far(rng, _W_GIVEN, g1)
        sur = rng.choice(_W_SURNAME)
        out.append(SlicePair(g, f"{g1} {sur}", f"{sur} {g2}", False, _DIFF))
    return out


def _chinese(rng: random.Random) -> list[SlicePair]:
    g = "chinese_order"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        sur = rng.choice(_C_SURNAME)
        g1, g2 = rng.choice(_C_GIVEN)
        nick = rng.choice(_C_NICK)
        full = f"{sur} {g1} {g2}"
        forms: list[tuple[str, str, tuple[NameVerdict, ...]]] = [
            (full, f"{g1} {g2} {sur}", _SAME),
            (full, f"{g2} {g1} {sur}", _SAME),
            (full, f"{sur} {g1}{g2.lower()}", _SAME),
            (full, f"{sur} {g1}-{g2}", _SAME),
            (f"{full} ({nick})", f"{nick} {sur}", _PROB),
            (f"{sur}, {g1} {g2}", f"{g1} {g2} {sur}", _SAME),
        ]
        a, b, exp = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, exp))
    for i in range(N_DIFF):
        sur = rng.choice(_C_SURNAME)
        g1 = rng.choice(_C_GIVEN)[0]
        x, y = _C_NEAR[i % len(_C_NEAR)]
        a = f"{sur} {g1} {x}"
        b = f"{sur} {g1} {y}" if i % 2 == 0 else f"{g1} {y} {sur}"
        out.append(SlicePair(g, a, b, False, _DIFF))
    return out


def _malay(rng: random.Random) -> list[SlicePair]:
    g = "malay_patronymic"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        given, father, female = rng.choice(_M_GIVEN), rng.choice(_M_FATHER), rng.choice(_M_FEMALE)
        forms: list[tuple[str, str, tuple[NameVerdict, ...]]] = [
            (f"Muhammad {given} bin {father}", f"Mohd {given} {father}", _SAME),
            (f"{given} bin {father}", f"{given} {father}", _SAME),
            (f"{female} binte {father}", f"{female} bte {father}", _SAME),
            (
                f"{female} binti {father}",
                f"{female.split()[1]} {father} {female.split()[0]}",
                _SAME,
            ),
            (f"Muhammad {given} bin {father}", f"{given} {father}", _PROB),
            (f"{given} b. {father}", f"{given} bin {father}", _SAME),
        ]
        a, b, exp = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, exp))
    for i in range(N_DIFF):
        father = rng.choice(_M_FATHER)
        if i < len(_M_NEAR):
            x, y = _M_NEAR[i]
            out.append(SlicePair(g, f"{x} bin {father}", f"{y} bin {father}", False, _DIFF))
        elif i < len(_M_NEAR) + 4:
            g1 = rng.choice(_M_GIVEN)
            g2 = _far(rng, _M_GIVEN, g1)
            out.append(SlicePair(g, f"{g1} bin {father}", f"{g2} bin {father}", False, _DIFF))
        else:
            f1 = rng.choice(_M_FEMALE)
            f2 = _far(rng, _M_FEMALE, f1, 4)
            out.append(SlicePair(g, f"{f1} binte {father}", f"{f2} binte {father}", False, _DIFF))
    for _ in range(2):  # father and son: the same two names, given name and patronym swapped
        a, b = rng.sample(_M_FATHER, 2)
        out.append(SlicePair(g, f"{a} bin {b}", f"{b} bin {a}", False, _DIFF))
    return out


def _indian(rng: random.Random) -> list[SlicePair]:
    g = "indian_patronymic"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        given, father, female = rng.choice(_I_GIVEN), rng.choice(_I_FATHER), rng.choice(_I_FEMALE)
        forms = [
            (f"{given} s/o {father}", f"{given} {father}"),
            (f"{given} s/o {father}", f"{father} {given}"),
            (f"{female} d/o {father}", f"{female} {father}"),
            (f"{given} S/O {father}", f"{given} son of {father}"),
            (f"{female} d/o {father}", f"{father} {female}"),
            (f"{given} a/l {father}", f"{given} s/o {father}"),
        ]
        a, b = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, _SAME))
    for i in range(N_DIFF):
        father = rng.choice(_I_FATHER)
        if i < len(_I_NEAR):
            x, y = _I_NEAR[i]
        else:
            x = rng.choice(_I_GIVEN)
            y = _far(rng, _I_GIVEN, x)
        out.append(SlicePair(g, f"{x} s/o {father}", f"{y} s/o {father}", False, _DIFF))
    for _ in range(2):  # father and son: the same two names, given name and patronym swapped
        a, b = rng.sample(_I_GIVEN, 2)
        out.append(SlicePair(g, f"{a} s/o {b}", f"{b} s/o {a}", False, _DIFF))
    return out


def _titles(rng: random.Random) -> list[SlicePair]:
    g = "titles"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        name = rng.choice(_T_NAMES)
        t1, t2 = rng.choice(_T_TITLES), rng.choice(_T_TITLES)
        forms = [
            (f"{t1} {name}", name),
            (name, f"{t1} {name}"),
            (f"{t1} {name}", f"{t2} {name}"),
            (f"{t1} {name}".upper(), name),
        ]
        a, b = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, _SAME))
    for i in range(N_DIFF):
        x, y = _T_NEAR[i % len(_T_NEAR)]
        title = _T_TITLES[i % len(_T_TITLES)]
        out.append(SlicePair(g, f"{title} {x}", f"{title} {y}", False, _DIFF))
    return out


def _mononym(rng: random.Random) -> list[SlicePair]:
    g = "mononym"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        m, sur = rng.choice(_MONO), rng.choice(_MONO_SURNAME)
        forms: list[tuple[str, str, tuple[NameVerdict, ...]]] = [
            (m, f"Mr {m}", _SAME),
            (m, f"Encik {m}", _SAME),
            (m, f"{m} {sur}", _PROB),
            (f"Dr {m}", m, _SAME),
        ]
        a, b, exp = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, exp))
    for i in range(N_DIFF):
        x, y = _MONO_NEAR[i % len(_MONO_NEAR)]
        out.append(SlicePair(g, x, y, False, _DIFF))
    return out


def _diacritics(rng: random.Random) -> list[SlicePair]:
    g = "diacritics_initials"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        given, sur = rng.choice(_D_NAMES)
        wg, ws = rng.choice(_W_GIVEN), rng.choice(_W_SURNAME)
        forms: list[tuple[str, str, tuple[NameVerdict, ...]]] = [
            (f"{given} {sur}", _plain(f"{given} {sur}"), _SAME),
            (f"{given} {sur}", f"{given} {sur}".upper(), _SAME),
            (f"{wg[0]}. {ws}", f"{wg} {ws}", _PROB),
            (f"{sur}, {given}", _plain(f"{given} {sur}"), _SAME),
        ]
        a, b, exp = forms[i % len(forms)]
        out.append(SlicePair(g, a, b, True, exp))
    for i in range(N_DIFF):
        if i < len(_D_ACCENT_NEAR):
            a, b = _D_ACCENT_NEAR[i]
        else:
            wg, ws = rng.choice(_W_GIVEN), rng.choice(_W_SURNAME)
            other = rng.choice([c for c in "ABCDEFGHKLMNPRSTVW" if c != wg[0].upper()])
            a, b = f"{wg[0]}. {ws}", f"{other}. {ws}"
        out.append(SlicePair(g, a, b, False, _DIFF))
    return out


def _non_latin(rng: random.Random) -> list[SlicePair]:
    g = "non_latin"
    out: list[SlicePair] = []
    for i in range(N_SAME):
        sur = rng.choice(_CJK_SURNAME)
        g1, g2 = rng.choice(_CJK_GIVEN), rng.choice(_CJK_GIVEN)
        full = sur + g1 + g2
        if i % 3 == 0:
            out.append(SlicePair(g, full, full, True, _SAME))
        elif i % 3 == 1:
            out.append(SlicePair(g, full, f"{sur} {g1}{g2}", True, _SAME))
        else:
            out.append(SlicePair(g, full, rng.choice(_LATIN_FOR_CJK), True, _INDET))
    for i in range(N_DIFF):
        sur = rng.choice(_CJK_SURNAME)
        g1, g2 = rng.choice(_CJK_GIVEN), rng.choice(_CJK_GIVEN)
        full = sur + g1 + g2
        if i % 2 == 0:
            other_last = rng.choice([c for c in _CJK_GIVEN if c != g2])
            other = sur + g1 + other_last
        else:
            other_sur = rng.choice([c for c in _CJK_SURNAME if c != sur])
            other = other_sur + g1 + g2
        out.append(SlicePair(g, full, other, False, _DIFF))
    return out


_BUILDERS: dict[str, Callable[[random.Random], list[SlicePair]]] = {
    "western_order": _western,
    "chinese_order": _chinese,
    "malay_patronymic": _malay,
    "indian_patronymic": _indian,
    "titles": _titles,
    "mononym": _mononym,
    "diacritics_initials": _diacritics,
    "non_latin": _non_latin,
}


def slice_pairs(seed: int = 4002) -> list[SlicePair]:
    """Deterministic fictitious pairs grouped by naming convention (>= 20 same, >= 10 different
    per group). Same seed, same pairs."""
    pairs: list[SlicePair] = []
    for index, group in enumerate(SLICE_GROUPS):
        pairs.extend(_BUILDERS[group](random.Random(seed * 1000 + index)))
    return pairs


def baseline_matcher(a: str, b: str) -> NameVerdict:
    """Character similarity only (what token-set matching is compared against)."""
    return "same" if fuzz.ratio(a.casefold(), b.casefold()) >= BASELINE_RATIO_MIN else "different"


def _default_matcher(a: str, b: str) -> NameVerdict:
    return match_person(a, b).verdict


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def run_slice_test(
    matcher: Callable[[str, str], NameVerdict] | None = None, seed: int = 4002
) -> list[SliceRow]:
    """One row per group. ``false_flag_rate`` = same-person pairs judged ``different`` (a wrong
    flag); ``false_accept_rate`` = different-person pairs judged ``same``. The baseline columns
    use plain character similarity (rapidfuzz ratio >= 85)."""
    match = matcher or _default_matcher
    pairs = slice_pairs(seed)
    rows: list[SliceRow] = []
    for group in SLICE_GROUPS:
        members = [p for p in pairs if p.group == group]
        same = [p for p in members if p.same_person]
        diff = [p for p in members if not p.same_person]
        verdicts = [match(p.a, p.b) for p in same]
        base_same = [baseline_matcher(p.a, p.b) for p in same]
        diff_verdicts = [match(p.a, p.b) for p in diff]
        base_diff = [baseline_matcher(p.a, p.b) for p in diff]
        rows.append(
            SliceRow(
                group=group,
                same_pairs=len(same),
                false_flag_rate=_rate(sum(v == "different" for v in verdicts), len(same)),
                probable_rate=_rate(sum(v == "probable" for v in verdicts), len(same)),
                indeterminate_rate=_rate(sum(v == "indeterminate" for v in verdicts), len(same)),
                baseline_false_flag_rate=_rate(sum(v == "different" for v in base_same), len(same)),
                diff_pairs=len(diff),
                false_accept_rate=_rate(sum(v == "same" for v in diff_verdicts), len(diff)),
                baseline_false_accept_rate=_rate(sum(v == "same" for v in base_diff), len(diff)),
            )
        )
    return rows
