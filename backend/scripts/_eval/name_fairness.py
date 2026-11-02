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
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.names import match_person, normalise_person, same_identity
from scripts._eval import metrics as mx
from scripts._eval import name_alias_categories as ac
from scripts._eval import names_baseline as nb
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
# added with the 2026 alias work, reported on its own so the pooled rates of earlier tables keep their
# meaning: a sibling (same family name, other given names) written as initials
EXTRA_DIFF_CLASSES = ("initials_sibling",)
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


def accepts_baseline(a: str, b: str) -> bool:
    """The frozen matcher of 2026-10-29 with equal ID numbers (``names_baseline``)."""
    return nb.same_identity(a, _ID, b, _ID) == "match"


MATCHERS: dict[str, Callable[[str, str], bool]] = {
    "old": accepts_old,
    "baseline": accepts_baseline,
    "new": accepts_new,
    "name_only": accepts_name_only,
}


# ------------------------------------------------------------------------------- generation


# The pair sets must not depend on the matcher under test. Which real alias pairs are usable and which
# synthetic candidates are redrawn as true aliases are decided with the FROZEN baseline matcher
# (``names_baseline``, app/names.py of 2026-10-29), so a new alias or honorific rule in ``app.names``
# can neither shrink the usable set nor remove the fakes it would accept: those show up as a changed
# rate or as a wrong match.


def _alias_equal(a: str, b: str) -> bool:
    """True when two spellings are one person's under the frozen normalisation (a label error if
    the pair is meant to be two people)."""
    pa, pb = nb.normalise_person(a), nb.normalise_person(b)
    return sorted(pa.tokens) == sorted(pb.tokens)


def _nested(a: str, b: str) -> bool:
    """One name's tokens are contained in the other's (``Azlan bin Tahir`` inside ``Muhammad Azlan
    bin Tahir``): the documented way one person is written short and long, never two people."""
    ta, tb = set(nb.normalise_person(a).tokens), set(nb.normalise_person(b).tokens)
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


def _initials(group: str, given: str) -> str | None:
    """``Ravi Kumar `` -> ``R. K. `` (the marker of a patronymic name stays); None when there is
    nothing to abbreviate."""
    words = given.split()
    marker = ""
    if group in _MARKED:
        if len(words) < 2:
            return None
        words, marker = words[:-1], f" {words[-1]} "
    if not words or any(len(w) < 2 for w in words):
        return None
    return " ".join(f"{w[0].upper()}." for w in words) + marker


def build_pairs(split: str, *, seed: int = SEED) -> list[Pair]:
    """Deterministic pairs for one split: ``Random(f"{seed}:{split}:{group}")`` per group."""
    out: list[Pair] = []
    for group in GROUPS:
        rng = random.Random(f"{seed}:{split}:{group}")
        seen: set[str] = set()
        out_extra: list[Pair] = []

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
                if cls == "shared_family":  # the sibling written as initials: still another person
                    ini = _initials(group, og[0])
                    if ini is not None:
                        out_extra.append(
                            Pair(
                                group,
                                split,
                                "initials_sibling",
                                a,
                                _join(group, a, ini, family),
                                False,
                                cls,
                            )
                        )
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
        out.extend(out_extra)
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


BOOTSTRAP_B = 1000
BOOTSTRAP_SEED = 20261102


def disparity(groups: dict[str, list[tuple[int, int]]], *, b: int = BOOTSTRAP_B) -> dict[str, Any]:
    """Max minus min of the group rates, with a 95% percentile bootstrap interval.

    ``groups`` maps a group to its clusters ``(k, n)``: ``k`` of ``n`` pairs failed. Independent
    pairs are clusters of one pair; pairs of the same person (one Wikidata item has several
    aliases) are one cluster, so the interval is not narrower than the data allow. Deterministic:
    the resampling uses a fixed seed."""
    rates = {g: (sum(k for k, _ in cl) / sum(n for _, n in cl)) for g, cl in groups.items() if cl}
    point = max(rates.values()) - min(rates.values()) if rates else 0.0
    # with 0 failures in every group the bootstrap has no spread: the Wilson upper bound of the worst
    # group says what the data cannot exclude
    hi_max = max(
        (
            mx.wilson(sum(k for k, _ in cl), sum(n for _, n in cl))[1]
            for cl in groups.values()
            if cl
        ),
        default=0.0,
    )
    rng = random.Random(BOOTSTRAP_SEED)
    sims: list[float] = []
    for _ in range(b):
        boot: list[float] = []
        for cl in groups.values():
            if not cl:
                continue
            if all(n == 1 for _, n in cl):
                n_tot = len(cl)
                k_tot = rng.binomialvariate(n_tot, sum(k for k, _ in cl) / n_tot)
                boot.append(k_tot / n_tot)
            else:
                draw = rng.choices(cl, k=len(cl))
                boot.append(sum(k for k, _ in draw) / sum(n for _, n in draw))
        sims.append(max(boot) - min(boot) if boot else 0.0)
    sims.sort()
    return {
        "point": point,
        "lo": sims[int(0.025 * (b - 1))],
        "hi": sims[int(0.975 * (b - 1))],
        "max_wilson_hi": hi_max,
        "max_group": max(rates, key=lambda g: rates[g]) if rates else "",
        "min_group": min(rates, key=lambda g: rates[g]) if rates else "",
        "rates": rates,
    }


def synthetic_disparity(
    pairs: Iterable[Pair], matcher: Callable[[str, str], bool], classes: tuple[str, ...]
) -> dict[str, Any]:
    """Disparity of the same-person false-flag rate over the six groups for the given classes."""
    groups: dict[str, list[tuple[int, int]]] = {g: [] for g in GROUPS}
    for p in pairs:
        if p.cls in classes and p.same_person:
            groups[p.group].append((0 if matcher(p.a, p.b) else 1, 1))
    return disparity(groups)


def run_synthetic(*, seed: int = SEED) -> dict[str, Any]:
    result: dict[str, Any] = {"seed": seed, "splits": {}}
    for split in SPLITS:
        pairs = build_pairs(split, seed=seed)
        result["splits"][split] = {
            "n_pairs": len(pairs),
            "matchers": {name: score(pairs, fn) for name, fn in MATCHERS.items()},
            "disparity_surface_alias": {
                name: synthetic_disparity(pairs, fn, ("surface", "alias"))
                for name, fn in MATCHERS.items()
                if name in ("baseline", "new", "name_only")
            },
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
    pa, pb = nb.normalise_person(a), nb.normalise_person(b)
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
        toks = nb.normalise_person(label).tokens
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


def natural_collisions(rows: list[dict[str, str]], split: str) -> list[tuple[str, str]]:
    """Pairs of strings of two different people (distinct QIDs, same split) that share a word of
    three letters or more: the natural rate at which different people look alike. No near-spelling
    is needed: any two Kumars are in it. Words used by more than 60 strings are skipped."""
    names: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r["split"] == split:
            names[r["qid"]].update((r["label_en"], r["alt_en"]))
    by_token: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for qid, ns in names.items():
        for n in ns:
            toks = nb.normalise_person(n).tokens
            if len(toks) < 2 or any(c.isdigit() for c in n):
                continue
            for t in set(toks):
                if len(t) >= 3:
                    by_token[t].add((qid, n))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for members in by_token.values():
        if len(members) > 60:
            continue
        ordered = sorted(members)
        for i, (q1, n1) in enumerate(ordered):
            for q2, n2 in ordered[i + 1 :]:
                if q1 != q2 and (n1, n2) not in seen:
                    seen.add((n1, n2))
                    out.append((n1, n2))
    return out


def _alias_clusters(
    pos: list[dict[str, str]], failed: list[bool], country: str
) -> list[tuple[int, int]]:
    by_person: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r, f in zip(pos, failed, strict=True):
        if r["country"] == country:
            by_person[r["qid"]][0] += int(f)
            by_person[r["qid"]][1] += 1
    return [(k, n) for k, n in by_person.values()]


def run_wikidata(path: Path) -> dict[str, Any]:
    rows = read_wikidata(path)
    result: dict[str, Any] = {"source": path.name, "rows": len(rows), "splits": {}}
    for split in SPLITS:
        sub = [r for r in rows if r["split"] == split]
        pos = [r for r in sub if usable_positive(r)]
        excluded = [r for r in sub if not usable_positive(r)]
        neg = wikidata_negatives(rows, split)
        countries = sorted({r["country"] for r in pos})
        entry: dict[str, Any] = {
            "positives_total": len(sub),
            "positives_used": len(pos),
            "negatives": len(neg),
            "countries": countries,
            "matchers": {},
            "categories": {},
        }
        for name, fn in MATCHERS.items():
            accepted = [fn(r["label_en"], r["alt_en"]) for r in pos]
            failed = [not x for x in accepted]
            ff = [r for r, f in zip(pos, failed, strict=True) if f]
            wm = [n for n in neg if fn(n[0], n[1])]
            by_country = {
                c: _cell(
                    sum(1 for r, f in zip(pos, failed, strict=True) if f and r["country"] == c),
                    sum(1 for r in pos if r["country"] == c),
                )
                for c in countries
            }
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
            if name in ("baseline", "new", "name_only"):
                entry["matchers"][name]["disparity"] = disparity(
                    {c: _alias_clusters(pos, failed, c) for c in countries}
                )
                cats: dict[str, dict[str, int]] = {c: defaultdict(int) for c in countries}
                for r, f in zip(pos, failed, strict=True):
                    if f:
                        cats[r["country"]][ac.categorise(r["label_en"], r["alt_en"])] += 1
                entry["categories"][name] = {c: dict(v) for c, v in cats.items()}
                bucket = {
                    c: {
                        k: sum(v for cat, v in cats[c].items() if ac.bucket_of(cat) == k)
                        for k in ("a", "b")
                    }
                    for c in countries
                }
                entry["matchers"][name]["by_bucket"] = {
                    c: {
                        k: _cell(bucket[c][k], sum(1 for r in pos if r["country"] == c))
                        for k in ("a", "b")
                    }
                    for c in countries
                }
        # the rows the filter drops: counted, and judged by the same matchers
        entry["excluded"] = {
            "n": len(excluded),
            "by_pair_type": dict(Counter(r["pair_type"] for r in excluded)),
            "not_accepted": {
                name: sum(1 for r in excluded if not MATCHERS[name](r["label_en"], r["alt_en"]))
                for name in ("baseline", "new")
            },
            "all_rows_not_accepted": {
                name: _cell(
                    sum(1 for r in sub if not MATCHERS[name](r["label_en"], r["alt_en"])), len(sub)
                )
                for name in ("baseline", "new")
            },
        }
        coll = natural_collisions(rows, split)
        accepted_pairs = {
            name: [pr for pr in coll if MATCHERS[name](pr[0], pr[1])]
            for name in ("baseline", "new")
        }
        base_set = set(accepted_pairs["baseline"])
        entry["natural_collisions"] = {
            "pairs": len(coll),
            "accepted": {name: len(v) for name, v in accepted_pairs.items()},
            "newly_accepted": [pr for pr in accepted_pairs["new"] if pr not in base_set],
            "no_longer_accepted": len(base_set - set(accepted_pairs["new"])),
        }
        result["splits"][split] = entry
    return result


# ------------------------------------------------------------------------------- report


def _fmt(c: dict[str, Any]) -> str:
    if not c["n"]:
        return "n/a"
    return f"{c['k']}/{c['n']} = {c['rate']:.3f} [{c['lo']:.3f}-{c['hi']:.3f}]"


def _d(d: dict[str, Any]) -> str:
    return f"{d['point']:.3f} [{d['lo']:.3f}-{d['hi']:.3f}]"


def render_markdown(synthetic: dict[str, Any], wikidata: dict[str, Any] | None) -> str:
    lines = [
        "# Name fairness: larger DEV/TEST set (generated)",
        "",
        "Generated by `backend/scripts/name_fairness.py`. Fictitious names, our own generator, so these "
        "are upper bounds. Wilson 95% intervals. Tune on DEV only; TEST is what is reported.",
        "",
        "`wrong_match` = different people accepted as the same person (lower is better); `false_flag` = "
        "the same person not accepted (lower is better). Both sides carry the same ID number, as in the "
        "270-pair slice, so only `match` passes. `baseline` is the frozen matcher of 2026-10-29 "
        "(`names_baseline.py`), `new` is `app/names.py` now, `name only` is the verdict without the "
        "typo rule.",
        "",
    ]
    cols = ("baseline", "new", "name_only")
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
                "| group | baseline | new | name only |",
                "|---|---|---|---|",
            ]
            aggs = {m: aggregate(s["matchers"][m], cls_set) for m in cols}
            for g in GROUPS:
                lines.append(
                    f"| {g} | {_fmt(aggs['baseline'][g])} | {_fmt(aggs['new'][g])} | {_fmt(aggs['name_only'][g])} |"
                )
            lines.append("")
        if "disparity_surface_alias" in s:
            lines += [
                "### Disparity of the same-person false-flag rate over the six groups (surface forms and aliases)",
                "",
                "Max minus min of the group rates, with a 95% bootstrap interval (1,000 resamples, fixed seed).",
                "",
                "| matcher | max - min [95% interval] | highest group | lowest group | worst group, Wilson upper bound |",
                "|---|---|---|---|---|",
            ]
            for m in cols:
                d = s["disparity_surface_alias"][m]
                lines.append(
                    f"| {m} | {_d(d)} | {d['max_group']} | {d['min_group']} | {d['max_wilson_hi']:.3f} |"
                )
            lines.append("")
        hard = DIFF_CLASSES + EXTRA_DIFF_CLASSES
        lines += [
            "### Hard-negative classes, new matcher (wrong_match)",
            "",
            "`initials_sibling` is a sibling written as initials (`R. K. Nair` for `Ravi Kumar Nair`); it "
            "is not in the pooled rates above. The baseline matcher already accepts a few of them: an "
            "initial that lines up with another person's first name is `probable` by design.",
            "",
            "| group | " + " | ".join(hard) + " | initials_sibling, baseline |",
            "|---|" + "---|" * (len(hard) + 1),
        ]
        for g in GROUPS:
            cells = [
                _fmt(s["matchers"]["new"][f"{g}/{c}"])
                if f"{g}/{c}" in s["matchers"]["new"]
                else "n/a"
                for c in hard
            ]
            base = s["matchers"]["baseline"].get(f"{g}/initials_sibling")
            lines.append(f"| {g} | " + " | ".join(cells) + f" | {_fmt(base) if base else 'n/a'} |")
        lines.append("")
    if wikidata:
        lines += [
            "## Real Wikidata pairs (same person aliases; different people one or two letters apart)",
            "",
            "The group is the country of the Wikidata item, not a name style. Several aliases of one "
            "person are one cluster in the bootstrap.",
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
            for m in ("baseline", "new", "name_only"):
                if m in e["matchers"]:
                    lines.append(
                        f"| {m} | {_fmt(e['matchers'][m]['false_flag'])} | {_fmt(e['matchers'][m]['wrong_match'])} |"
                    )
            lines += [
                "",
                f"One-letter slips among the real alias pairs (flagged by design): {_fmt(e['slip_positives'])}",
                "",
                "Not accepted by group (country of the item), and split into (a) a name matcher could "
                "reasonably accept and (b) needs other evidence:",
                "",
                "| group | n | baseline not accepted | new not accepted | new (a) | new (b) |",
                "|---|---|---|---|---|---|",
            ]
            for c in e["countries"]:
                bb = e["matchers"]["baseline"]["false_flag_by_country"][c]
                nn = e["matchers"]["new"]["false_flag_by_country"][c]
                bk = e["matchers"]["new"]["by_bucket"][c]
                lines.append(
                    f"| {c} | {nn['n']} | {_fmt(bb)} | {_fmt(nn)} | {_fmt(bk['a'])} | {_fmt(bk['b'])} |"
                )
            lines += [
                "",
                "Max minus min of the group rates, 95% cluster-bootstrap interval: "
                + ", ".join(
                    f"{m} {_d(e['matchers'][m]['disparity'])}" for m in ("baseline", "new")
                ),
                "",
                "Categories of the pairs that are not accepted (count per group, new matcher; baseline in brackets):",
                "",
                "| bucket | category | " + " | ".join(e["countries"]) + " | all |",
                "|---|---|" + "---|" * (len(e["countries"]) + 1),
            ]
            for cat in ac.CATEGORIES:
                if cat == "excluded_by_filter":
                    continue
                n_cells = [e["categories"]["new"][c].get(cat, 0) for c in e["countries"]]
                b_cells = [e["categories"]["baseline"][c].get(cat, 0) for c in e["countries"]]
                lines.append(
                    f"| {ac.bucket_of(cat)} | {cat} | "
                    + " | ".join(f"{n} ({b})" for n, b in zip(n_cells, b_cells, strict=True))
                    + f" | {sum(n_cells)} ({sum(b_cells)}) |"
                )
            ex = e["excluded"]
            lines += [
                "",
                f"Rows the usable filter drops: {ex['n']} (by pair_type {ex['by_pair_type']}); of them "
                f"not accepted: baseline {ex['not_accepted']['baseline']}, new {ex['not_accepted']['new']}. "
                f"Over all {e['positives_total']} rows of the split, not accepted: baseline "
                f"{_fmt(ex['all_rows_not_accepted']['baseline'])}, new {_fmt(ex['all_rows_not_accepted']['new'])}.",
                "",
            ]
            nc = e["natural_collisions"]
            lines += [
                f"Different people who share a name word ({nc['pairs']} pairs of strings of distinct "
                f"Wikidata items): accepted as one person by baseline {nc['accepted']['baseline']}, by new "
                f"{nc['accepted']['new']}; newly accepted by new: {len(nc['newly_accepted'])}"
                + (
                    " (" + "; ".join(f"{a} / {b}" for a, b in nc["newly_accepted"][:8]) + ")"
                    if nc["newly_accepted"]
                    else ""
                )
                + ".",
                "",
            ]
    return "\n".join(lines) + "\n"
