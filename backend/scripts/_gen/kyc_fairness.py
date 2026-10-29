"""Fairness slice set (docs 4.7, plan 7.2): name pairs in six fixed groups, one tiny KYC case each.

``build_fairness(out, seed=, today=)`` writes ``<out>/fairness/pairs.json``, ``registry.json`` and
``cases/<pair_id>/`` (profile with one director, that director's NRIC card, register and chart at
100%). Name ``a`` is printed on the documents, name ``b`` is the registry's form. Same-person pairs
differ only in surface form; different-person pairs are near-spelled people (edit distance 1 to 2)
who share the ID number, so a tool must separate them on the *name*, not the ID.

Everything is fictitious and deterministic: ``random.Random(f"{seed}:fairness:{group}")``.
"""

from __future__ import annotations

import random
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import ids as idm
from . import names_data as nd
from . import quirks as qk
from .bundle_common import write_bundle
from .kyc_chart import render_chart
from .kyc_register import render_register
from .kyc_view import view_of
from .registry_writer import write_registry
from .render_id import render_id
from .render_profile import render_profile
from .schema import (
    FIXED_TODAY,
    BundleSpec,
    Expected,
    NamePair,
    Planted,
    RuleExpectation,
    write_json,
)
from .world import World
from .world_types import Company, Holder, Party, Person

D = Decimal
GROUPS: tuple[str, ...] = (
    "western_order",
    "chinese_order",
    "malay_patronymic",
    "indian_patronymic",
    "mononym",
    "titles",
)
SAME_PER_GROUP = 30
DIFF_PER_GROUP = 15
FIRST_COMPANY_INDEX = 1000
NAME_RULES = ["x_directors", "x_ownership_vs_registry", "x_entity"]
_NEAR = {
    "a": "e", "e": "a", "i": "y", "o": "u", "u": "o", "y": "i", "l": "r", "r": "l", "m": "n",
    "n": "m", "k": "g", "g": "k", "s": "z", "z": "s", "b": "p", "p": "b", "d": "t", "t": "d",
    "c": "k", "j": "g", "w": "v", "v": "w", "f": "h", "h": "f", "x": "s", "q": "k",
}  # fmt: skip
_KEEP = {
    "bin",
    "binte",
    "binti",
    "bte",
    "s/o",
    "d/o",
    "dr",
    "mdm",
    "mr",
    "ms",
    "prof",
    "datuk",
    "seri",
    "datin",
}
_MOHD = {"muhammad", "mohd", "mohamed", "muhd", "mohammad"}
_MONO_A = ("Sur", "Han", "Wid", "Bud", "Sar", "Tri", "Yos", "Dar", "Kus", "Rah", "Sum", "Win")
_MONO_B = ("yadi", "ianto", "anto", "ati", "ono", "ani", "ika", "wati", "mono", "ardi")


# ------------------------------------------------------------------------------ name forms


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def near_spell(name: str, rng: random.Random) -> str:
    """A different person's spelling: one (sometimes two) letters of one name token changed."""
    toks = name.split()
    elig = [i for i, t in enumerate(toks) if t.isalpha() and len(t) >= 3 and t.lower() not in _KEEP]
    for _ in range(50):
        i = rng.choice(elig)
        t = toks[i]
        pos = rng.sample(range(1, len(t)), 2 if (len(t) > 3 and rng.random() < 0.3) else 1)
        chars = list(t)
        for p in pos:
            sub = _NEAR.get(chars[p].lower(), "e")
            chars[p] = sub.upper() if chars[p].isupper() else sub
        new = list(toks)
        new[i] = "".join(chars)
        out = " ".join(new)
        if 1 <= edit_distance(name, out) <= 2:
            return out
    raise RuntimeError(f"no near spelling for {name!r}")


def _split(name: str) -> tuple[str, str]:
    t, bare = nd.split_title(name)
    return t, bare


def forms_for(group: str, name: str) -> list[tuple[str, str]]:
    """Same-person registry forms of ``name`` as ``(label, form)``; the first is identical."""
    title, bare = _split(name)
    toks = bare.split()
    forms: list[tuple[str, str]] = [("printed", name), ("upper", name.upper())]
    if group == "western_order":
        if len(toks) == 2:
            forms += [
                ("reordered", f"{toks[1]} {toks[0]}"),
                ("comma_order", f"{toks[1].upper()}, {toks[0]}"),
            ]
        elif len(toks) == 3:
            forms += [("middle_omitted", f"{toks[0]} {toks[2]}")]
    elif group == "chinese_order":
        forms += [
            ("given_first", f"{' '.join(toks[1:])} {toks[0]}"),
            ("surname_upper", f"{toks[0].upper()} {' '.join(toks[1:])}"),
            ("given_joined", f"{toks[0]} {''.join(t.lower() for t in toks[1:]).capitalize()}"),
            ("given_hyphen", f"{'-'.join(toks[1:])} {toks[0]}"),
        ]
    elif group == "malay_patronymic":
        marker = next((t for t in toks if t.lower() in ("bin", "binte", "binti")), None)
        if marker:
            i = toks.index(marker)
            given, father = toks[:i], toks[i + 1 :]
            forms += [
                ("patronymic_dropped", " ".join(given + father)),
                (
                    "patronymic_abbrev",
                    " ".join([*given, "b." if marker == "bin" else "bte", *father]),
                ),
            ]
            if marker == "bin" and len(given) >= 2 and given[0].lower() in _MOHD:
                forms.append(("first_given_dropped", " ".join([*given[1:], marker, *father])))
                forms.append(("mohd_abbrev", " ".join(["Mohd", *given[1:], "b.", *father])))
    elif group == "indian_patronymic":
        marker = next((t for t in toks if t.lower() in ("s/o", "d/o")), None)
        if marker:
            i = toks.index(marker)
            forms += [
                ("patronymic_dropped", " ".join(toks[:i] + toks[i + 1 :])),
                ("reordered", " ".join(toks[i + 1 :] + toks[:i])),
            ]
    elif group == "mononym":
        forms += [("padded", f" {name} ")]
    elif group == "titles":
        forms += [
            ("title_dropped", bare),
            ("title_changed", f"{'Mdm' if title != 'Mdm' else 'Mr'} {bare}"),
            ("title_datuk", f"Datuk Seri {bare}"),
        ]
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for lab, f in forms:
        if f not in seen:
            seen.add(f)
            out.append((lab, f))
    return out


def base_name(group: str, rng: random.Random, idx: int, sex: str) -> str:
    if group == "western_order":
        given = nd.WESTERN_GIVEN_F if sex == "F" else nd.WESTERN_GIVEN_M
        name = f"{rng.choice(given)} {rng.choice(nd.WESTERN_SURNAMES)}"
        if idx % 5 == 4:  # a middle name that the registry form drops
            g, s = name.split()
            middles = [m for m in nd.WESTERN_GIVEN_M + nd.WESTERN_GIVEN_F if m != g]
            return f"{g} {rng.choice(middles)} {s}"
        return name
    if group == "mononym":
        return (
            rng.choice(nd.MONONYMS)
            if idx < len(nd.MONONYMS)
            else (rng.choice(_MONO_A) + rng.choice(_MONO_B))
        )
    return nd.make_name(rng, group, sex)


# ----------------------------------------------------------------------- tiny world and docs


def _tiny_world(
    pair_id: str, idx: int, name_a: str, group: str, sex: str, seed: int, today: date
) -> World:
    rng = random.Random(f"{seed}:fairness:{pair_id}:world")
    by = rng.randint(1965, 1992)
    uen = idm.make_uen(2020, 20000 + idx)
    inc = date(2020, 3, 2)
    party = Party(
        name=f"Fairness {group.split('_')[0].title()} {idx:03d} Pte. Ltd.",
        uen=uen,
        gst_no=None,
        address=nd.address(rng),
    )
    w = World(
        scenario="kyc",
        company_index=FIRST_COMPANY_INDEX + idx,
        seed=seed,
        today=today,
        quirks=[],
        layout="ocbc_style",
    )
    w.company = Company(
        party=party,
        incorporation=inc,
        activity="Freight forwarding and logistics",
        ssic="52291",
        paid_up_capital=D("100000.00"),
    )
    person = Person(
        key="p0",
        name=name_a,
        group=group,
        sex=sex,
        birth=date(by, rng.randint(1, 12), rng.randint(1, 28)),
        id_number=idm.random_nric(rng, by),
        appointment=inc,
        registry_name=name_a.upper(),
        address=nd.address(rng),
        issue_date=date(by + 18, 6, 1),
        sig_seed=rng.randrange(1, 10**6),
    )
    w.people = [person]
    w.holders = [
        Holder(name_a, "individual", person.id_number, 100000, D("100.00"), "", "p0", is_ubo=True)
    ]
    view_of(w)
    return w


def _registry_entity(w: World, name_b: str) -> dict[str, Any]:
    p = w.people[0]
    c = w.company
    return {
        "uen": c.party.uen,
        "entity_name": c.party.name,
        "entity_type": c.entity_type,
        "status": "Live",
        "incorporation_date": c.incorporation.isoformat(),
        "registered_address": c.party.address,
        "principal_activity": c.activity,
        "ssic_code": c.ssic,
        "paid_up_capital": format(c.paid_up_capital, "f"),
        "currency": "SGD",
        "officers": [
            {
                "name": name_b,
                "id_number": p.id_number,
                "nationality": "SG",
                "position": "Director",
                "appointment_date": p.appointment.isoformat(),
                "cessation_date": None,
            }
        ],
        "shareholders": [
            {
                "name": name_b,
                "id_or_uen": p.id_number,
                "holder_type": "individual",
                "shares": 100000,
                "percent": "100.00",
                "share_class": "Ordinary",
            }
        ],
    }


def _pairs(seed: int, same_n: int, diff_n: int) -> list[tuple[NamePair, str]]:
    """``[(pair, sex)]`` for every group, deterministic."""
    out: list[tuple[NamePair, str]] = []
    for group in GROUPS:
        rng = random.Random(f"{seed}:fairness:{group}")
        for k in range(same_n):
            sex = "F" if k % 3 == 1 else "M"
            name_a = base_name(group, rng, k, sex)
            forms = forms_for(group, name_a)
            lab, name_b = forms[k % len(forms)]
            out.append(
                (
                    NamePair(
                        pair_id=f"{group}-s{k:02d}",
                        group=group,  # type: ignore[arg-type]
                        same_person=True,
                        name_a=name_a,
                        name_b=name_b,
                        form_a="printed",
                        form_b=lab,
                    ),
                    sex,
                )
            )
        for k in range(diff_n):
            sex = "F" if k % 3 == 1 else "M"
            name_a = base_name(group, rng, k + same_n, sex)
            name_b = near_spell(name_a, rng)
            out.append(
                (
                    NamePair(
                        pair_id=f"{group}-d{k:02d}",
                        group=group,  # type: ignore[arg-type]
                        same_person=False,
                        name_a=name_a,
                        name_b=name_b,
                        form_a="printed",
                        form_b=f"near_spelling_{edit_distance(name_a, name_b)}",
                    ),
                    sex,
                )
            )
    return out


def build_fairness(
    out: Path,
    *,
    seed: int = 4002,
    today: date = FIXED_TODAY,
    same_per_group: int = SAME_PER_GROUP,
    diff_per_group: int = DIFF_PER_GROUP,
) -> list[NamePair]:
    """Write ``<out>/fairness/`` and return the pairs."""
    root = Path(out) / "fairness"
    if root.exists():
        shutil.rmtree(root)
    (root / "cases").mkdir(parents=True)
    pairs = _pairs(seed, same_per_group, diff_per_group)
    entities: list[dict[str, Any]] = []
    for n, (pair, sex) in enumerate(pairs):
        w = _tiny_world(pair.pair_id, n, pair.name_a, pair.group, sex, seed, today)
        entities.append(_registry_entity(w, pair.name_b))
        docs = [
            render_profile(w),
            render_id(w.people[0], 1),
            render_register(w),
            render_chart(w),
        ]
        spec = BundleSpec(
            scenario="kyc",
            variant="clean",
            company_index=FIRST_COMPANY_INDEX + n,
            seed=seed,
            today=today,
            quirks=qk.default_quirks(0),
        )
        if pair.same_person:
            expected = Expected(
                lane_any_of=["fast", "edd"], band_max="low", must_not_fire=list(NAME_RULES)
            )
            planted: list[Planted] = []
        else:
            expected = Expected(
                lane="edd",
                must_fire=[RuleExpectation(rule=list(NAME_RULES), min_severity="high")],
            )
            planted = [
                Planted(
                    id="P1",
                    kind="near_spelled_registry_name",
                    description=f"registry holds '{pair.name_b}', documents print '{pair.name_a}'",
                    deck_slide=None,
                    target=None,
                    facts={"name_a": pair.name_a, "name_b": pair.name_b, "group": pair.group},
                )
            ]
        write_bundle(
            spec,
            root / "cases" / pair.pair_id,
            docs,
            applicant={"name": w.company.party.name, "uen": w.company.party.uen},
            expected=expected,
            planted=planted,
            kind="fairness_case",
            bid=pair.pair_id,
            registry_path="../../registry.json",
            doc_date=today,
        )
    write_registry(root / "registry.json", entities)
    write_json(
        root / "pairs.json",
        {"schema_version": "1", "pairs": [p.model_dump(mode="json") for p, _ in pairs]},
    )
    return [p for p, _ in pairs]
