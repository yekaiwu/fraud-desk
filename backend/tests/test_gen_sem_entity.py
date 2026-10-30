"""ENTITY evaluation data: same-entity and different-entity company-name pairs (docs 9.2).

No model is called. The labels are ours (see the module docstring for the convention); the tests
prove they are consistent under mechanical rules and that no base name crosses the split.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from scripts._gen import sem_entity as e
from scripts._gen.sem_common import SPLITS, edit_distance, split_of_key, stem_key


def _initials(name: str) -> str:
    m = re.match(r"([A-Za-z])[. ]?([A-Za-z])[. ]?([A-Za-z])(?=[. ])", name)
    assert m, name
    return "".join(m.groups()).upper()


def _descriptor(name: str) -> str:
    found = [d for d in e.ALL_DESCRIPTORS if d.casefold() in name.casefold()]
    return max(found, key=len)


@pytest.fixture(scope="module")
def sets() -> dict[str, list[dict]]:
    return {s: e.build_pairs(s) for s in SPLITS}


def test_counts_per_class_and_split(sets: dict[str, list[dict]]) -> None:
    for split, rows in sets.items():
        assert len(rows) == 16 * e.PER_CLASS
        by = Counter((p["label"], p["class"]) for p in rows)
        assert {c for lab, c in by if lab == "same"} == set(e.SAME_CLASSES)
        assert {c for lab, c in by if lab == "different"} == set(e.DIFFERENT_CLASSES)
        assert set(by.values()) == {e.PER_CLASS}, split
        assert Counter(p["label"] for p in rows) == {"same": 192, "different": 192}
        assert all(p["split"] == split for p in rows)


def test_reproducible_and_seed_dependent(sets: dict[str, list[dict]]) -> None:
    assert e.build_pairs("held_out") == sets["held_out"]
    assert e.build_pairs("dev", seed=2) != sets["dev"]


def test_no_duplicate_pair_inside_or_across_splits(sets: dict[str, list[dict]]) -> None:
    keys = [
        frozenset((p["a"].casefold(), p["b"].casefold())) for rows in sets.values() for p in rows
    ]
    assert len(keys) == len(set(keys))
    assert all(len(k) == 2 for k in keys)


def test_same_pairs_are_the_same_entity_under_mechanical_rules(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for p in rows:
            if p["label"] != "same" or p["class"] == "typo":
                continue
            assert e.core_key(p["a"]) == e.core_key(p["b"]), p
            fa, fb = e.legal_family(p["a"]), e.legal_family(p["b"])
            assert fa == fb or "" in (fa, fb), p  # a spelling of the form, or the form left off


def test_different_pairs_are_different_entities_under_the_same_rules(
    sets: dict[str, list[dict]],
) -> None:
    for rows in sets.values():
        for p in rows:
            if p["label"] == "different":
                assert e.full_key(p["a"]) != e.full_key(p["b"]), p


def test_typo_pairs_are_one_slip_and_never_another_brand(sets: dict[str, list[dict]]) -> None:
    brands = {w.casefold() for w in (*e.BRAND_WORDS, *(w for g in e.PREFIX_GROUPS for w in g))}
    for split, rows in sets.items():
        typos = [p for p in rows if p["class"] == "typo"]
        assert len(typos) == e.PER_CLASS
        for p in typos:
            wa = re.sub(r"[^a-z ]", "", p["a"].casefold()).split()
            wb = re.sub(r"[^a-z ]", "", p["b"].casefold()).split()
            diffs = [(x, y) for x, y in zip(wa, wb, strict=False) if x != y]
            assert len(diffs) == 1, p
            x, y = diffs[0]
            assert 1 <= edit_distance(x, y) <= 2, p
            slipped = x if x not in brands else y  # the side that is not a pool brand
            assert slipped in brands or y in brands or x in brands, p
            other = {b for b in brands if b not in (x, y)}
            assert all(edit_distance(slipped, b) > 1 for b in other), (split, p)


def test_no_base_name_or_brand_prefix_crosses_the_split(sets: dict[str, list[dict]]) -> None:
    tokens: dict[str, set[str]] = {s: set() for s in SPLITS}
    stems: dict[str, set[str]] = {s: set() for s in SPLITS}
    brandish = {w.casefold() for w in e.BRAND_WORDS} | {
        w.casefold() for g in e.PREFIX_GROUPS for w in g
    }
    for split, rows in sets.items():
        for p in rows:
            for side in ("a", "b"):
                for w in re.findall(r"[A-Za-z]+", p[side]):
                    if w.casefold() in brandish:
                        tokens[split].add(w.casefold())
                        stems[split].add(stem_key(w))
    assert tokens["dev"] and tokens["held_out"]
    assert not tokens["dev"] & tokens["held_out"]
    assert not stems["dev"] & stems["held_out"]  # not even a shared four-letter prefix
    # the founder and initialism bases follow their own key
    for split, rows in sets.items():
        for p in rows:
            if p["class"] in ("initialism", "initial_conflict"):
                for side in ("a", "b"):
                    letters = _initials(p[side])
                    assert split_of_key(letters) == split, p
    pat = re.compile(
        rf"({'|'.join(e.SURNAME_PINYIN)}) ({'|'.join(e.GIVEN_PINYIN)}) ({'|'.join(e.GIVEN_PINYIN)})\b"
    )
    checked = 0
    for split, rows in sets.items():
        for p in rows:
            for side in ("a", "b"):
                m = pat.match(p[side].title())
                if m and p["class"] != "transliteration":  # dialect-romanised founder names
                    assert split_of_key("".join(m.groups())) == split, p
                    checked += 1
    assert checked >= 60


def test_every_surface_variation_the_brief_asks_for_is_there(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        by_class: dict[str, list[dict]] = {}
        for p in rows:
            by_class.setdefault(p["class"], []).append(p)
        forms = {x for p in by_class["legal_suffix"] for x in (p["a"], p["b"])}
        assert len(forms) >= 40
        legal = {
            lf
            for p in by_class["legal_suffix"]
            for side in ("a", "b")
            for lf in sum(e.LEGAL.values(), ())
            if p[side].casefold().endswith(lf.casefold())
        }
        assert len(legal) >= 6
        assert any(
            e.legal_family(p["a"]) == "" or e.legal_family(p["b"]) == ""
            for p in by_class["legal_suffix"]
        )
        # 'A.B.C.' forms against 'ABC' and 'A B C'
        dotted = [p for p in by_class["initialism"] if re.search(r"\b\w\.\w\.\w", p["a"] + p["b"])]
        plain = [
            p for p in by_class["initialism"] if re.search(r"\b[A-Za-z]{3}\b", p["a"] + p["b"])
        ]
        assert dotted and plain
        assert any(" & " in p["a"] or " & " in p["b"] for p in by_class["word_order"])
        translit = by_class["transliteration"]
        pinyin = set(e.SURNAME_PINYIN.values())
        assert any(
            w in pinyin for p in translit for w in re.findall(r"[A-Z][a-z]+", p["a"] + p["b"])
        )
        assert any(m in (p["a"] + p["b"]) for p in translit for m in e.MALAY_WORD.values())
        assert any(
            r in (p["a"] + p["b"])
            for p in by_class["region_marker"]
            for r in ("(S)", "(Singapore)", "Singapore", "(M)", "(Malaysia)", "(HK)", "(Hong Kong)")
        )


def test_different_classes_do_what_their_names_say(sets: dict[str, list[dict]]) -> None:
    def words(name: str) -> list[str]:
        return re.findall(r"[a-z]+", name.casefold())

    seen = Counter()
    for rows in sets.values():
        for p in rows:
            if p["label"] != "different":
                continue
            wa, wb = words(p["a"]), words(p["b"])
            cls = p["class"]
            seen[cls] += 1
            if cls == "shared_brand":
                assert wa[0] == wb[0] and _descriptor(p["a"]) != _descriptor(p["b"]), p
                assert e.INDUSTRY_OF[_descriptor(p["a"])] != e.INDUSTRY_OF[_descriptor(p["b"])], p
            elif cls == "same_prefix":
                assert wa[0] != wb[0] and wa[0][:4] == wb[0][:4], p
                assert edit_distance(wa[0], wb[0]) >= 3, p
            elif cls == "shared_generic":
                assert _descriptor(p["a"]) == _descriptor(p["b"]), p
                assert edit_distance(wa[0], wb[0]) >= 4 and wa[0][:4] != wb[0][:4], p
            elif cls == "same_industry":
                da, db = _descriptor(p["a"]), _descriptor(p["b"])
                assert da != db and e.INDUSTRY_OF[da] == e.INDUSTRY_OF[db], p
                assert wa[0][:4] != wb[0][:4], p
            elif cls == "group_member":
                holding = {h.casefold() for h in e.HOLDING_WORDS}
                assert wa[0] == wb[0], p
                assert bool(holding & set(wa)) != bool(holding & set(wb)), p
            elif cls == "jurisdiction_form":
                assert (
                    wa[0] == wb[0]
                    and e.full_key(p["a"])[1] != e.full_key(p["b"])[1]
                    or (wa[0] == wb[0] and e.core_key(p["a"]) != e.core_key(p["b"]))
                ), p
            elif cls == "initial_conflict":
                ia, ib = _initials(p["a"]), _initials(p["b"])
                assert ia != ib and sorted(ia) != [] and len(ia) == len(ib) == 3, p
            elif cls == "given_name_differs":
                assert wa[0] == wb[0] and wa != wb, p
    assert set(seen) == set(e.DIFFERENT_CLASSES)


def test_surface_style_is_independent_of_the_label(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for label in ("same", "different"):
            sub = [p for p in rows if p["label"] == label]
            upper = sum(p["a"].isupper() for p in sub) + sum(p["b"].isupper() for p in sub)
            assert 0.15 < upper / (2 * len(sub)) < 0.5, (label, upper)


def test_hard_flag_marks_the_confusable_classes(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for p in rows:
            assert p["hard"] == (p["class"] in e.HARD_CLASSES)
        assert Counter(p["hard"] for p in rows)[True] >= 100
