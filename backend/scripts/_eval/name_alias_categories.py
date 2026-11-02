"""What a real same-person alias pair that the matcher does not accept actually is.

A pair is put in exactly one category, by the strings alone, with the FROZEN baseline normalisation
(``names_baseline``), so the categories do not move when ``app.names`` changes. Two buckets:

* ``a``: a name matcher could reasonably accept it. The two names are the same words up to spacing,
  order, an honorific, a documented surname variant, or initials that line up. No letter disagrees.
* ``b``: it needs other evidence (an ID number, a date of birth, a document). Either a letter-level
  disagreement (a spelling variant; a one-letter variant is the same string change as a deliberate
  near-spelled substitution), or a name part that is only on one side (a nickname, a married name,
  a different given or family name, a stage name, an initial with nothing to match), or rows the
  export itself carries that are not name variants at all.

The split is our reading of the strings, not a human label: ``pair_type`` in the export is a
heuristic and was not used. Some ``b`` pairs are not the same person at all (Wikidata alias
fields hold stage names and sometimes another person's name), which no matcher should accept.
"""

from __future__ import annotations

from collections import Counter

from rapidfuzz.distance import Levenshtein

from app.names import SURNAME_VARIANTS
from scripts._eval import names_baseline as nb

# Honorific and title words that are not also common given names or surnames, used only to
# classify (a longer list than the matcher's).
HONORIFIC_WORDS = frozenset(
    {
        "sir", "lord", "lady", "acharya", "swami", "pandit", "pt", "shri", "shree", "sri", "smt",
        "srimati", "begum", "tun", "puan", "datuk", "dato", "datin", "tengku", "tunku", "maharaja",
        "maharani", "nawab", "mirza", "maulana", "maulvi", "justice", "judge", "hon", "honourable",
        "rt", "rev", "reverend", "fr", "father", "bishop", "pastor", "captain", "capt", "col",
        "colonel", "maj", "lt", "brig", "admiral", "commodore", "dr", "prof", "professor", "mr",
        "mrs", "ms", "miss", "mdm", "haji", "hajjah", "hj", "hjh", "hajee", "sardar", "sardarni",
        "sahib", "saheb", "prince", "princess",
    }
)  # fmt: skip

A_CATEGORIES = ("spacing_or_order", "honorific_only", "documented_variant", "initials_aligned")
B_CATEGORIES = (
    "one_letter_variant",
    "spelling_variant",
    "initials_unaligned",
    "dropped_name_part",
    "shares_family_name_only",
    "shares_first_name_only",
    "other_name_parts",
    "excluded_by_filter",
)
CATEGORIES = (*A_CATEGORIES, *B_CATEGORIES)

_CANON = {m: g[0] for g in SURNAME_VARIANTS for m in g}


def bucket_of(category: str) -> str:
    return "a" if category in A_CATEGORIES else "b"


def _pairable(x: str, y: str) -> bool:
    """Two leftover tokens that look like spellings of one word."""
    d = Levenshtein.distance(x, y)
    if d <= 2:
        return True
    shorter = min(len(x), len(y))
    return shorter >= 5 and d <= 0.34 * max(len(x), len(y))


def categorise(a: str, b: str) -> str:
    """The category of a pair of names that are the same person in the data."""
    pa, pb = nb.normalise_person(a), nb.normalise_person(b)
    ta, tb = list(pa.tokens), list(pb.tokens)
    ca0, cb0 = Counter(ta), Counter(tb)
    one_a, one_b = sorted((ca0 - cb0).elements()), sorted((cb0 - ca0).elements())
    if nb.match_person(a, b).typo or (
        len(one_a) == 1
        and len(one_b) == 1
        and len(ta) > 1
        and len(one_a[0]) > 1
        and len(one_b[0]) > 1
        and Levenshtein.distance(one_a[0], one_b[0]) == 1
    ):  # one letter of one word differs, in a long word (a slip, flagged) or a short one
        return "one_letter_variant"
    if "".join(sorted(ta)) == "".join(sorted(tb)) or "".join(ta) == "".join(tb):
        return "spacing_or_order"
    ra = [t for t in ta if t not in HONORIFIC_WORDS] or ta
    rb = [t for t in tb if t not in HONORIFIC_WORDS] or tb
    if (ra != ta or rb != tb) and (set(ra) <= set(rb) or set(rb) <= set(ra)):
        return "honorific_only"
    ca, cb = Counter(ta), Counter(tb)
    left_a, left_b = sorted((ca - cb).elements()), sorted((cb - ca).elements())
    if sorted(_CANON.get(t, t) for t in ta) == sorted(_CANON.get(t, t) for t in tb):
        return "documented_variant"
    if not left_a or not left_b:
        return "dropped_name_part"  # one name inside the other, and not accepted: a lone token
    if "".join(left_a) == "".join(left_b) or "".join(sorted(left_a)) == "".join(sorted(left_b)):
        return "spacing_or_order"
    initials = [t for t in left_a + left_b if len(t) == 1]
    if initials:
        longs_a = [t for t in left_a if len(t) > 1]
        longs_b = [t for t in left_b if len(t) > 1]
        shorts_a = [t for t in left_a if len(t) == 1]
        shorts_b = [t for t in left_b if len(t) == 1]

        def covers(shorts: list[str], longs: list[str]) -> bool:
            have = Counter(t[0] for t in longs)
            return bool(shorts) and all(have[k] >= n for k, n in Counter(shorts).items())

        aligned = (not longs_a and covers(shorts_a, longs_b)) or (
            not longs_b and covers(shorts_b, longs_a)
        )
        return "initials_aligned" if aligned else "initials_unaligned"
    free = list(left_b)
    paired = True
    for x in left_a:
        hit = next((y for y in free if _pairable(x, y)), None)
        if hit is None:
            paired = False
            break
        free.remove(hit)
    if paired and not free:
        return "spelling_variant"
    if ta and tb:
        if ta[-1] == tb[-1] and not (set(ta[:-1]) & set(tb[:-1])):
            return "shares_family_name_only"
        if ta[0] == tb[0] and not (set(ta[1:]) & set(tb[1:])):
            return "shares_first_name_only"
    return "other_name_parts"
