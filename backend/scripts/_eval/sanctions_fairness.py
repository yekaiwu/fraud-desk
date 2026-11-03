"""False-positive rate of sanctions screening by name group (offline once a snapshot exists).

Sanctions screening is a name-matching task, so it inherits the fairness question of ``name_fairness``:
which groups of people are told "possible match" about when they are not on any list? Two kinds of
pseudo-customer are screened against the real snapshot, with no date of birth or ID (so every hit is a
false positive unless the person really shares a listed name):

* fictitious customers: every distinct name the dev/test generator draws for the six groups of
  ``kyc_fairness.GROUPS`` (western order, Chinese order, Malay and Indian patronymic, mononym, titled);
* real draws: random persons of the lists themselves, screened against the snapshot WITHOUT their own
  record and without any other record that carries the same name (the lists repeat each other), grouped by
  the nationality the list gives (groups of at least ``MIN_GROUP`` draws; the rest are "other"). A hit
  then means two different listed people whose names the matcher cannot tell apart.

Rates carry Wilson 95% intervals; the gap between the highest and lowest group is bootstrapped by
``name_fairness.disparity``. Groups are the ones our generator and the lists give us. They are not a
validated taxonomy and not protected attributes: the bank defines those.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Any

from app.sources.sanctions import Screener, Snapshot, Subject, _person_words
from scripts._eval import metrics as mx
from scripts._eval import name_fairness as nf
from scripts._gen.kyc_fairness import GROUPS

REAL_DRAWS = 3000
MIN_GROUP = 150
SEED = 7101


def fictitious_customers(split: str = "test", seed: int = nf.SEED) -> dict[str, list[str]]:
    """Distinct names per generator group (both sides of every pair)."""
    out: dict[str, set[str]] = {g: set() for g in GROUPS}
    for p in nf.build_pairs(split, seed=seed):
        out[p.group].update((p.a, p.b))
    return {g: sorted(v) for g, v in out.items()}


def cell(k: int, n: int) -> dict[str, Any]:
    lo, hi = mx.wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "lo": lo, "hi": hi}


def screen_groups(screener: Screener, groups: dict[str, list[str]]) -> dict[str, Any]:
    """Per group: customers with any hit, with a medium-or-high hit, and the pooled disparity."""
    table: dict[str, Any] = {}
    clusters: dict[str, list[tuple[int, int]]] = {}
    for g, names in groups.items():
        any_hit = med = 0
        flagged = []
        for name in names:
            hits = screener.screen(Subject(name, "person"))
            any_hit += bool(hits)
            med += any(h.level in ("medium", "high") for h in hits)
            if hits and len(flagged) < 5:
                flagged.append(f"{name} -> {hits[0].entry['id']} {hits[0].listed_name}")
        table[g] = {
            "any": cell(any_hit, len(names)),
            "medium_up": cell(med, len(names)),
            "examples": flagged,
        }
        clusters[g] = [(1 if i < med else 0, 1) for i in range(len(names))]
    return {"groups": table, "disparity": nf.disparity(clusters) if len(clusters) > 1 else None}


def real_draws(
    snapshot: Snapshot, n: int = REAL_DRAWS, seed: int = SEED
) -> dict[str, list[tuple[str, set[int], dict[str, Any]]]]:
    """``group -> [(name, indexes of the records to leave out)]``."""
    rng = random.Random(seed)
    by_name: dict[tuple[str, ...], set[str]] = defaultdict(set)
    people = []
    for i, e in enumerate(snapshot.entries):
        if e["kind"] != "person":
            continue
        for name in e["names"]:
            words = _person_words(name)
            if words:
                by_name[tuple(sorted(words))].add(e["id"])
        words = _person_words(e["names"][0])
        if words:
            people.append(i)
    chosen = rng.sample(people, min(n, len(people)))
    nat_counts = Counter((snapshot.entries[i]["nationality"] or ["unknown"])[0] for i in chosen)
    big = {k for k, v in nat_counts.items() if v >= MIN_GROUP and k != "unknown"}
    out: dict[str, list[tuple[str, set[int]]]] = defaultdict(list)
    for i in chosen:
        e = snapshot.entries[i]
        nat = (e["nationality"] or ["unknown"])[0]
        group = nat if nat in big else "other_or_unstated"
        words = _person_words(e["names"][0])
        out[group].append((e["names"][0], {e["id"]} | by_name[tuple(sorted(words or ()))], e))
    return out


def screen_real(
    screener: Screener, draws: dict[str, list[tuple[str, set[str], dict[str, Any]]]]
) -> dict[str, Any]:
    table: dict[str, Any] = {}
    clusters: dict[str, list[tuple[int, int]]] = {}
    for g, items in sorted(draws.items()):
        med = 0
        examples = []
        for name, skip, drawn in items:
            hits = hits_without(screener, name, skip, drawn)
            ok = any(h.level in ("medium", "high") for h in hits)
            med += ok
            if ok and len(examples) < 5:
                examples.append(f"{name} -> {hits[0].entry['id']} {hits[0].listed_name}")
        table[g] = {"medium_up": cell(med, len(items)), "examples": examples}
        clusters[g] = [(1 if i < med else 0, 1) for i in range(len(items))]
    return {"groups": table, "disparity": nf.disparity(clusters) if len(clusters) > 1 else None}


def _years(entry: dict[str, Any]) -> set[str]:
    out = set()
    for d in entry.get("dob", []):
        out.update(d.split("/")) if "/" in d else out.add(d[:4])
    return out


def _same_listed_person(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two list records that are probably one person listed twice (by two lists, in two spellings): they
    share an identifier or a year of birth. Not a false positive, so not counted as one."""
    ids_a = {
        i["number"].replace(" ", "").upper() for i in a.get("ids", []) if len(i["number"]) >= 6
    }
    ids_b = {
        i["number"].replace(" ", "").upper() for i in b.get("ids", []) if len(i["number"]) >= 6
    }
    return bool(ids_a & ids_b) or bool(_years(a) & _years(b))


def hits_without(screener: Screener, name: str, skip: set[str], drawn: dict[str, Any]) -> list[Any]:
    """Hits for ``name`` other than the records in ``skip`` and other listings of the same person."""
    return [
        h
        for h in screener.screen(Subject(name, "person"), limit=50)
        if h.entry["id"] not in skip and not _same_listed_person(drawn, h.entry)
    ]


def recall_probe(screener: Screener, seed: int = SEED, n: int = 300) -> dict[str, Any]:
    """Sanity: the matcher still finds a listed person when the customer writes the name in another
    order (exact) or with one letter slipped (fuzzy). Not a fairness number."""
    rng = random.Random(seed)
    persons = [
        e
        for e in screener.snapshot.entries
        if e["kind"] == "person" and _person_words(e["names"][0])
    ]
    sample = rng.sample(persons, min(n, len(persons)))
    reorder = slip = 0
    slip_n = 0
    for e in sample:
        words = e["names"][0].split()
        if len(words) >= 2:
            reordered = " ".join(reversed(words))
            reorder += any(
                h.entry["id"] == e["id"]
                for h in screener.screen(Subject(reordered, "person"), limit=50)
            )
        longest = max(range(len(words)), key=lambda j: len(words[j]))
        w = words[longest]
        if len(w) >= 7 and w.isascii() and w.isalpha():
            slip_n += 1
            bad = list(words)
            bad[longest] = w[:3] + ("x" if w[3] != "x" else "y") + w[4:]
            slip += any(
                h.entry["id"] == e["id"]
                for h in screener.screen(Subject(" ".join(bad), "person"), limit=50)
            )
    return {"reordered": cell(reorder, len(sample)), "one_letter_slip": cell(slip, slip_n)}


# ----------------------------------------------------------------------------------- fixture

FIXTURE_VERSION = 1
FIXTURE_NOTE = (
    "Offline regression fixture for tests/test_sanctions_fairness.py. entries: a sample of public official "
    "sanctions-list records (UN, OFAC, EU, UK Sanctions List; OGL v3.0 for the UK data) kept to the fields "
    "the matcher reads; customers: fictitious generator names per group; draws: persons drawn from the "
    "entries with the ids to leave out. Rebuild with backend/scripts/sanctions_fairness.py --write-fixture."
)
FIXTURE_FIELDS = ("id", "list", "ref", "kind", "names", "dob", "nationality", "ids")


def build_fixture(
    snapshot: Snapshot, *, background: int = 600, per_group: int = 150, draws: int = 600
) -> dict[str, Any]:
    """A small, deterministic extract of ``snapshot`` plus the pseudo-customers to screen against it."""
    rng = random.Random(SEED)
    screener = Screener(snapshot)
    by_id = {e["id"]: e for e in snapshot.entries}
    drawn = real_draws(snapshot, n=REAL_DRAWS, seed=SEED)
    flat = [(g, item) for g, items in drawn.items() for item in items]
    rng.shuffle(flat)
    flat = flat[:draws]
    keep: dict[str, dict[str, Any]] = {}
    out_draws = []
    for g, (name, skip, rec) in flat:
        keep[rec["id"]] = rec
        for h in screener.screen(
            Subject(name, "person"), limit=50
        ):  # every record the draw could touch
            keep[h.entry["id"]] = h.entry
        out_draws.append({"group": g, "name": name, "skip": sorted(skip), "drawn": rec["id"]})
        for sid in skip:
            if sid in by_id:
                keep[sid] = by_id[sid]
    cust = {
        g: sorted(rng.sample(v, min(per_group, len(v))))
        for g, v in fictitious_customers("test").items()
    }
    for name in (n for v in cust.values() for n in v):
        for h in screener.screen(Subject(name, "person"), limit=50):
            keep[h.entry["id"]] = h.entry
    pool = [e for e in snapshot.entries if e["id"] not in keep]
    for e in rng.sample(pool, min(background, len(pool))):
        keep[e["id"]] = e
    entries = [
        {k: e.get(k) for k in FIXTURE_FIELDS} for e in sorted(keep.values(), key=lambda e: e["id"])
    ]
    return {
        "version": FIXTURE_VERSION,
        "note": FIXTURE_NOTE,
        "entries": entries,
        "customers": cust,
        "draws": out_draws,
    }


def fixture_snapshot(fixture: dict[str, Any]) -> Snapshot:
    from datetime import UTC, datetime
    from pathlib import Path

    now = datetime.now(UTC)
    return Snapshot(
        Path("fixture.json"), now, [], [{**e, "weak_aliases": 0} for e in fixture["entries"]]
    )


def measure_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """The numbers the regression test pins: per-group false positives for the fictitious customers and for
    the real draws, and the recall probe."""
    snap = fixture_snapshot(fixture)
    sc = Screener(snap)
    by_id = {e["id"]: e for e in snap.entries}
    customers = {
        g: sum(
            any(h.level in ("medium", "high") for h in sc.screen(Subject(n, "person")))
            for n in names
        )
        for g, names in fixture["customers"].items()
    }
    real: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in fixture["draws"]:
        cell_ = real[d["group"]]
        cell_[1] += 1
        hits = hits_without(sc, d["name"], set(d["skip"]), by_id[d["drawn"]])
        cell_[0] += any(h.level in ("medium", "high") for h in hits)
    return {
        "customers": {g: [k, len(fixture["customers"][g])] for g, k in customers.items()},
        "draws": dict(real),
        "recall": recall_probe(sc, n=300),
    }
