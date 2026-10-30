"""Shared pieces of the semantic evaluation data (docs/test-data-and-eval.md section 9).

Everything here is hand-written catalogues and plain code: no model is called to make or label
any row. The data is ours, fictitious and reproducible from the seed, so every number measured on
it is an upper bound.

* Two splits with fixed seeds: ``dev`` (5101, prompts are tuned on this one only) and ``held_out``
  (7001, run once with frozen prompts).
* A *split key* decides which split a token or a base name belongs to. It is a hash of the key, so
  the split is stable, does not depend on pool order, and a prompt written while looking at the dev
  rows never saw a held-out token.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SEED_DEV = 5101
SEED_HELD_OUT = 7001
SPLITS: tuple[str, ...] = ("dev", "held_out")
SEEDS: dict[str, int] = {"dev": SEED_DEV, "held_out": SEED_HELD_OUT}
DATA_VERSION = "sem-data-1"

D = Decimal

UPPER_NOTE = "Hand-written by us from fictitious catalogues; every number measured on this set is an upper bound."


def stream(seed: int, *parts: object) -> random.Random:
    """An independent, reproducible stream per purpose (the generator's own convention)."""
    return random.Random(":".join(str(p) for p in (seed, *parts)))


def split_of_key(key: str) -> str:
    """``"dev"`` or ``"held_out"`` for a key: a hash, stable across runs and pool order."""
    digest = hashlib.sha256(("sem-split:" + key.casefold()).encode()).digest()
    return "dev" if digest[0] % 2 == 0 else "held_out"


def stem_key(word: str, n: int = 4) -> str:
    """Split key of a brand-like word: its first ``n`` letters. Words that share a prefix
    (``Crestview``, ``Crestmont``) therefore always land in the same split, so a prompt cannot
    learn a prefix from dev and meet it again in held-out."""
    letters = "".join(ch for ch in word.casefold() if ch.isalnum())
    return letters[:n]


def in_split(words: tuple[str, ...], split: str, n: int = 4) -> tuple[str, ...]:
    return tuple(w for w in words if split_of_key(stem_key(w, n)) == split)


def nd_split(split: str) -> str:
    """The split name ``names_data.make_name_ext`` uses (``dev`` / ``test``) for ours."""
    return "dev" if check_split(split) == "dev" else "test"


def other_split(split: str) -> str:
    return "held_out" if split == "dev" else "dev"


def check_split(split: str) -> str:
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    return split


def dump_json(obj: Any) -> str:
    """Byte-stable JSON: UTF-8, sorted keys, 2-space indent, trailing newline."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------ number formats


def q2(v: Decimal) -> Decimal:
    return v.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(v: Decimal) -> str:
    """Canonical amount string: two decimals, no grouping."""
    return format(q2(v), "f")


def fmt_amount(v: Decimal) -> str:
    """Printed amount ``1,234.50``."""
    return f"{q2(v):,.2f}"


_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(d: date) -> str:
    """Printed date ``14 Jul 2025``."""
    return f"{d.day:02d} {_MON[d.month - 1]} {d.year}"


def weekday_on_or_before(d: date) -> date:
    while d.weekday() >= 5:
        d = date.fromordinal(d.toordinal() - 1)
    return d


def pick_weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = sorted(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings only)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
