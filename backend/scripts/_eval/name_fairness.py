"""Name-matching fairness on a larger seeded DEV/TEST set, plus real Wikidata alias pairs.

Companion to ``fairness.py`` (the 270-pair pipeline slice, which is kept as it is). This module runs
the matcher itself, with the ID rule the pipeline applies, on far more pairs:

* Six name groups (``kyc_fairness.GROUPS``), >= 100 different-person pairs per group and split.
  Names come from the extended pools in ``names_data`` (tokens hashed into ``dev`` or ``test``, so no
  base name and no given-name or patronym token crosses the split). Tune on DEV only; the numbers
  that go in a report are TEST.
* Different-person classes: ``near_spelling`` (one or two letters changed, as in the slice, but a
  candidate whose normalised tokens are equal is a true alias and is re-drawn), ``shared_family``
  (siblings: same surname or patronym, other given name), ``shared_given`` and ``father_son``.
* Same-person classes: ``surface`` (the slice's registry forms), ``alias`` (Mohd / Muhd / Abd / Noor)
  and ``typo`` (a one-letter slip, reported on its own: a typo and a near-spelled fake are the same
  string, so this is the price of flagging the fake).
* ``--wikidata``: real alias pairs of the same person, and different people with names one or two
  letters apart, from the public Wikidata export (data files only, parsed with ``csv``).

"Flagged" is what the pipeline does when both sides carry the same ID number: only an ``identity``
of ``match`` passes (``match_name_conflict`` raises ``x_ownership_vs_registry`` "name differs"). The
old matcher is emulated, not re-run: it differed only in that a one-letter slip inside a name with
a patronymic marker (``bin``, ``binte``, ``s/o``, ``d/o``) was not seen as a slip.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.names import match_person, normalise_person, same_identity
from scripts._eval import metrics as mx
from scripts._gen import names_data as nd
from scripts._gen.kyc_fairness import GROUPS, edit_distance, forms_for, near_spell

SPLITS = ("dev", "test")
SEED = 4002
N_NEAR = 120
N_HARD = 40  # per hard-negative class
N_SAME = 120
N_ALIAS = 40
_ID = "S1234567D"
DIFF_CLASSES = ("near_spelling", "shared_family", "shared_given", "father_son")
SAME_CLASSES = ("surface", "alias", "typo")
_MARKED = {"malay_patronymic", "indian_patronymic"}
_ALIAS_SWAPS = {
    "muhammad": ("Mohd", "Muhd", "Mohamed", "Mohamad", "Muhamad", "Mohammad"),
    "abdul": ("Abd", "Abdull"),
    "nur": ("Noor",),
    "siti": ("Siti",),
}


@dataclass(frozen=True)
class Pair:
    group: str
    split: str
    cls: str
    a: str
    b: str
    same_person: bool
    form: str = ""


# ------------------------------------------------------------------------------- matchers


def _is_marked(a: str, b: str) -> bool:
    pa, pb = normalise_person(a), normalise_person(b)
    return bool(pa.given and pa.patronym and pb.given and pb.patronym)


def accepts_new(a: str, b: str) -> bool:
    """The current matcher with equal ID numbers on both sides: only ``match`` passes."""
    return same_identity(a, _ID, b, _ID) == "match"


def accepts_old(a: str, b: str) -> bool:
    """The matcher before the fix, with equal ID numbers: a slip was only seen when no patronymic
    marker sat on both sides (verified against the old source in tests)."""
    m = match_person(a, b)
    old_typo = m.typo and not _is_marked(a, b)
    return m.verdict == "same" or (m.verdict == "probable" and not old_typo)


def accepts_name_only(a: str, b: str) -> bool:
    """The matcher verdict alone, the way a plain similarity check would read it."""
    return match_person(a, b).verdict in ("same", "probable")


def load_legacy(path: Path) -> Callable[[str, str], bool]:
    """The real old matcher from a copy of the old ``names.py`` (our own file from git history,
    e.g. ``git show <rev>:backend/app/names.py > old_names.py``), same equal-ID rule."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("legacy_names", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)

    def accepts(a: str, b: str) -> bool:
        return module.same_identity(a, _ID, b, _ID) == "match"

    return accepts


MATCHERS: dict[str, Callable[[str, str], bool]] = {
    "old": accepts_old,
    "new": accepts_new,
    "name_only": accepts_name_only,
}


# ------------------------------------------------------------------------------- generation


def _alias_equal(a: str, b: str) -> bool:
    """True when two spellings are one person's after normalisation (a label error if the pair is
    meant to be two people)."""
    pa, pb = normalise_person(a), normalise_person(b)
    return sorted(pa.tokens) == sorted(pb.tokens)


def _nested(a: str, b: str) -> bool:
    """One name's tokens are contained in the other's (``Azlan bin Tahir`` inside ``Muhammad Azlan
    bin Tahir``): the documented way one person is written short and long, never two people."""
    ta, tb = set(normalise_person(a).tokens), set(normalise_person(b).tokens)
    return ta <= tb or tb <= ta


def _near(rng: random.Random, name: str) -> str | None:
    for _ in range(20):
        try:
            cand = near_spell(name, rng)
        except RuntimeError:
            return None
        if not _alias_equal(name, cand):
            return cand
    return None


def _sex(k: int) -> str:
    return "F" if k % 3 == 1 else "M"


def _family_given(group: str, name: str) -> tuple[str, str] | None:
    """``(given part, family part)`` of a printed name, or None when the group has no such split."""
    if group in ("malay_patronymic", "indian_patronymic"):
        for marker in (" binte ", " binti ", " bin ", " s/o ", " d/o "):
            if marker in name:
                given, family = name.split(marker, 1)
                return f"{given}{marker}", family
        return None
    _, bare = nd.split_title(name)
    toks = bare.split()
    if group in ("chinese_order", "titles") and len(toks) >= 3:
        return " ".join(toks[1:]), toks[0]
    if group == "western_order" and len(toks) == 2:
        return toks[0], toks[1]
    return None


def _join(group: str, name: str, given: str, family: str) -> str:
    title, _ = nd.split_title(name)
    if group in ("malay_patronymic", "indian_patronymic"):
        return f"{given}{family}"
    if group in ("chinese_order", "titles"):
        return f"{title + ' ' if title else ''}{family} {given}"
    return f"{given} {family}"


def build_pairs(split: str, *, seed: int = SEED) -> list[Pair]:
    """Deterministic pairs for one split: ``Random(f"{seed}:{split}:{group}")`` per group."""
    out: list[Pair] = []
    for group in GROUPS:
        rng = random.Random(f"{seed}:{split}:{group}")
        seen: set[str] = set()

        def fresh(
            k: int,
            group: str = group,
            rng: random.Random = rng,
            seen: set[str] = seen,
        ) -> str:
            for _ in range(200):
                name = nd.make_name_ext(rng, group, _sex(k), split)
                if name not in seen:
                    seen.add(name)
                    return name
            return nd.make_name_ext(rng, group, _sex(k), split)  # pool exhausted: allow a repeat

        # near-spelled different people (the slice's class)
        for k in range(N_NEAR):
            a = fresh(k)
            b = _near(rng, a)
            if b is not None:
                out.append(
                    Pair(group, split, "near_spelling", a, b, False, f"edit_{edit_distance(a, b)}")
                )
        # hard negatives: siblings, shared given name, father and son
        for cls in ("shared_family", "shared_given", "father_son"):
            made = 0
            for k in range(N_HARD * 4):
                if made >= N_HARD:
                    break
                a = fresh(k)
                gf = _family_given(group, a)
                if gf is None or (cls == "father_son" and group not in _MARKED):
                    break
                given, family = gf
                other = nd.make_name_ext(rng, group, _sex(k), split)
                og = _family_given(group, other)
                if og is None:
                    continue
                if cls == "shared_family":
                    if edit_distance(given.lower(), og[0].lower()) < 3:
                        continue
                    b = _join(group, a, og[0], family)
                elif cls == "shared_given":
                    if edit_distance(family.lower(), og[1].lower()) < 3:
                        continue
                    b = _join(group, a, given, og[1])
                else:  # father and son: the last given name and the patronym swap places
                    words = given.split()
                    b = f"{family} {words[-1]} {words[-2]}"
                if cls == "father_son":
                    if words[-2].lower() == family.lower():
                        continue
                elif _alias_equal(a, b) or _nested(a, b):
                    continue
                out.append(Pair(group, split, cls, a, b, False, cls))
                made += 1
        # same person: registry surface forms
        for k in range(N_SAME):
            a = fresh(k + N_NEAR)
            forms = forms_for(group, a)
            lab, b = forms[rng.randrange(len(forms))]
            out.append(Pair(group, split, "surface", a, b, True, lab))
        # same person: spelling aliases of Muhammad / Abdul / Nur
        if group == "malay_patronymic":
            made = 0
            for k in range(N_ALIAS * 6):
                if made >= N_ALIAS:
                    break
                a = fresh(k)
                toks = a.split()
                hit = [i for i, t in enumerate(toks) if t.lower() in _ALIAS_SWAPS]
                if not hit:
                    continue
                i = hit[0]
                alt = rng.choice(_ALIAS_SWAPS[toks[i].lower()])
                b = " ".join([*toks[:i], alt, *toks[i + 1 :]])
                if b != a:
                    out.append(Pair(group, split, "alias", a, b, True, f"{toks[i]}>{alt}"))
                    made += 1
        # same person with a one-letter slip (reported on its own)
        for k in range(N_NEAR):
            a = fresh(k + 2 * N_NEAR)
            b = _near(rng, a)
            if b is not None and edit_distance(a, b) == 1:
                out.append(Pair(group, split, "typo", a, b, True, "edit_1"))
    return out


# ------------------------------------------------------------------------------- scoring


def _cell(k: int, n: int) -> dict[str, Any]:
    lo, hi = mx.wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "lo": lo, "hi": hi}


def score(pairs: Iterable[Pair], matcher: Callable[[str, str], bool]) -> dict[str, dict[str, Any]]:
    """Per ``group/class``: ``wrong_match`` (different people accepted) for different-person
    classes, ``false_flag`` (same person not accepted) for same-person classes."""
    buckets: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for p in pairs:
        buckets[(p.group, p.cls)].append(matcher(p.a, p.b))
    out: dict[str, dict[str, Any]] = {}
    for (group, cls), accepted in buckets.items():
        same = cls in SAME_CLASSES
        k = sum(1 for a in accepted if not a) if same else sum(accepted)
        out[f"{group}/{cls}"] = {
            "group": group,
            "cls": cls,
            "same_person": same,
            "metric": "false_flag" if same else "wrong_match",
            **_cell(k, len(accepted)),
        }
    return out


def aggregate(table: dict[str, dict[str, Any]], classes: tuple[str, ...]) -> dict[str, Any]:
    """Pool the classes in ``classes`` per group."""
    out: dict[str, Any] = {}
    for g in GROUPS:
        rows = [table[f"{g}/{c}"] for c in classes if f"{g}/{c}" in table]
        k, n = sum(r["k"] for r in rows), sum(r["n"] for r in rows)
        out[g] = _cell(k, n)
    return out


def run_synthetic(*, seed: int = SEED) -> dict[str, Any]:
    result: dict[str, Any] = {"seed": seed, "splits": {}}
    for split in SPLITS:
        pairs = build_pairs(split, seed=seed)
        result["splits"][split] = {
            "n_pairs": len(pairs),
            "matchers": {name: score(pairs, fn) for name, fn in MATCHERS.items()},
        }
    return result


# ------------------------------------------------------------------------------- Wikidata

_DESCRIPTION_WORDS = frozenset(
    [
        "cricketer",
        "footballer",
        "politician",
        "actor",
        "actress",
        "singer",
        "film",
        "director",
        "writer",
        "author",
        "player",
        "coach",
        "minister",
        "member",
        "judge",
        "general",
        "doctor",
        "professor",
        "businessman",
    ]  # fmt: skip
)


def read_wikidata(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def usable_positive(row: dict[str, str]) -> bool:
    """Same filter for every run: both names have >= 2 tokens and no digits, they share a token,
    and neither looks like a description. Stage names and pinyin-versus-English names that share no
    token are dropped, not hidden: the count is reported."""
    a, b = row["label_en"], row["alt_en"]
    if any(c.isdigit() for c in a + b) or row.get("pair_type") == "unrelated_token_set":
        return False
    pa, pb = normalise_person(a), normalise_person(b)
    if len(pa.tokens) < 2 or len(pb.tokens) < 2 or len(pa.tokens) > 6 or len(pb.tokens) > 6:
        return False
    if _DESCRIPTION_WORDS & (set(pa.tokens) | set(pb.tokens)):
        return False
    return bool(set(pa.tokens) & set(pb.tokens))


def wikidata_negatives(rows: list[dict[str, str]], split: str) -> list[tuple[str, str, str]]:
    """Different people (distinct QIDs, same split) whose labels share all tokens but one and
    differ in that token by one or two letters. ``[(a, b, country)]``."""
    labels: dict[str, tuple[str, str]] = {}
    for r in rows:
        if r["split"] == split:
            labels.setdefault(r["qid"], (r["label_en"], r["country"]))
    blocks: dict[tuple[str, ...], list[tuple[str, str, str, str]]] = defaultdict(list)
    for qid, (label, country) in labels.items():
        toks = normalise_person(label).tokens
        if len(toks) < 2 or any(c.isdigit() for c in label):
            continue
        for i, t in enumerate(toks):
            blocks[(*sorted(toks[:i] + toks[i + 1 :]),)].append((qid, label, country, t))
    seen: set[frozenset[str]] = set()
    out: list[tuple[str, str, str]] = []
    for members in blocks.values():
        for i, (q1, l1, c1, t1) in enumerate(members):
            for q2, l2, _, t2 in members[i + 1 :]:
                key = frozenset((q1, q2))
                if q1 == q2 or key in seen or t1 == t2:
                    continue
                if edit_distance(t1, t2) <= 2:
                    seen.add(key)
                    out.append((l1, l2, c1))
    return out


def run_wikidata(path: Path) -> dict[str, Any]:
    rows = read_wikidata(path)
    result: dict[str, Any] = {"source": path.name, "rows": len(rows), "splits": {}}
    for split in SPLITS:
        sub = [r for r in rows if r["split"] == split]
        pos = [r for r in sub if usable_positive(r)]
        neg = wikidata_negatives(rows, split)
        entry: dict[str, Any] = {
            "positives_total": len(sub),
            "positives_used": len(pos),
            "negatives": len(neg),
            "matchers": {},
        }
        for name, fn in MATCHERS.items():
            ff = [r for r in pos if not fn(r["label_en"], r["alt_en"])]
            wm = [n for n in neg if fn(n[0], n[1])]
            by_country = {}
            for c in sorted({r["country"] for r in pos}):
                rs = [r for r in pos if r["country"] == c]
                by_country[c] = _cell(
                    sum(1 for r in rs if not fn(r["label_en"], r["alt_en"])), len(rs)
                )
            slips = [r for r in pos if match_person(r["label_en"], r["alt_en"]).typo]
            entry["matchers"][name] = {
                "false_flag": _cell(len(ff), len(pos)),
                "false_flag_by_country": by_country,
                "wrong_match": _cell(len(wm), len(neg)),
                "false_flag_examples": [(r["label_en"], r["alt_en"]) for r in ff[:12]],
                "wrong_match_examples": [(n[0], n[1]) for n in wm[:12]],
            }
            if name == "new":
                entry["slip_positives"] = _cell(len(slips), len(pos))
                entry["slip_examples"] = [(r["label_en"], r["alt_en"]) for r in slips[:12]]
        result["splits"][split] = entry
    return result


# ------------------------------------------------------------------------------- report


def _fmt(c: dict[str, Any]) -> str:
    if not c["n"]:
        return "n/a"
    return f"{c['k']}/{c['n']} = {c['rate']:.3f} [{c['lo']:.3f}-{c['hi']:.3f}]"


def render_markdown(synthetic: dict[str, Any], wikidata: dict[str, Any] | None) -> str:
    lines = [
        "# Name fairness: larger DEV/TEST set (generated)",
        "",
        "Generated by `backend/scripts/name_fairness.py`. Fictitious names, our own generator, so these "
        "are upper bounds. Wilson 95% intervals. Tune on DEV only; TEST is what is reported.",
        "",
        "`wrong_match` = different people accepted as the same person (lower is better); `false_flag` = "
        "the same person not accepted (lower is better). Both sides carry the same ID number, as in the "
        "270-pair slice, so only `match` passes.",
        "",
    ]
    for split in SPLITS:
        s = synthetic["splits"][split]
        lines += [f"## Synthetic {split.upper()} ({s['n_pairs']} pairs)", ""]
        for cls_set, title, metric in (
            (("near_spelling",), "Near-spelled different people", "wrong_match"),
            (DIFF_CLASSES, "All different-person classes pooled", "wrong_match"),
            (("surface", "alias"), "Same person, surface forms and aliases", "false_flag"),
            (("typo",), "Same person with a one-letter typo (flagged by design)", "false_flag"),
        ):
            lines += [
                f"### {title} ({metric})",
                "",
                "| group | old matcher | new matcher | name only |",
                "|---|---|---|---|",
            ]
            aggs = {m: aggregate(s["matchers"][m], cls_set) for m in ("old", "new", "name_only")}
            for g in GROUPS:
                lines.append(
                    f"| {g} | {_fmt(aggs['old'][g])} | {_fmt(aggs['new'][g])} | {_fmt(aggs['name_only'][g])} |"
                )
            lines.append("")
        lines += [
            "### Hard-negative classes, new matcher (wrong_match)",
            "",
            "| group | " + " | ".join(DIFF_CLASSES) + " |",
            "|---|" + "---|" * len(DIFF_CLASSES),
        ]
        for g in GROUPS:
            cells = [
                _fmt(s["matchers"]["new"][f"{g}/{c}"])
                if f"{g}/{c}" in s["matchers"]["new"]
                else "n/a"
                for c in DIFF_CLASSES
            ]
            lines.append(f"| {g} | " + " | ".join(cells) + " |")
        lines.append("")
    if wikidata:
        lines += [
            "## Real Wikidata pairs (same person aliases; different people one or two letters apart)",
            "",
        ]
        for split in SPLITS:
            e = wikidata["splits"][split]
            lines += [
                f"### {split.upper()}: {e['positives_used']} usable alias pairs of {e['positives_total']}, "
                f"{e['negatives']} different-person pairs",
                "",
                "| matcher | false_flag (same person) | wrong_match (different people) |",
                "|---|---|---|",
            ]
            for m in ("old", "new", "name_only"):
                lines.append(
                    f"| {m} | {_fmt(e['matchers'][m]['false_flag'])} | {_fmt(e['matchers'][m]['wrong_match'])} |"
                )
            lines += [
                "",
                f"One-letter slips among the real alias pairs (flagged by design): {_fmt(e['slip_positives'])}",
                "",
            ]
    return "\n".join(lines) + "\n"
