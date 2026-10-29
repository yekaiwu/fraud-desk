"""Fictitious name lists and person-name generators (own lists, no Faker).

Groups cover plan 4.6 / docs 4.7: western order, Chinese order (surname first), Malay and Indian
patronymics (``bin/binte/binti``, ``s/o``, ``d/o``), mononyms and titles.
"""

from __future__ import annotations

import random

NAME_GROUPS: tuple[str, ...] = (
    "western_order",
    "chinese_order",
    "malay_patronymic",
    "indian_patronymic",
    "mononym",
    "titles",
)

CHINESE_SURNAMES = (
    "Tan", "Lim", "Lee", "Wong", "Ng", "Goh", "Chua", "Koh", "Teo", "Ong", "Chan", "Yeo", "Low",
    "Sim", "Foo", "Chew", "Ho", "Seah", "Quek", "Toh", "Loh", "Heng", "Pang", "Soh", "Tay",
)  # fmt: skip
CHINESE_GIVEN_M = (
    "Wei Ming", "Jun Jie", "Kai Xiang", "Zhi Hao", "Boon Kiat", "Eng Hock", "Cheng Wei", "Jia Hao",
    "Yong Sheng", "Kok Leong", "Wen Jie", "Jian Hui",
)  # fmt: skip
CHINESE_GIVEN_F = (
    "Hui Min", "Mei Ling", "Xin Yi", "Li Ting", "Siew Hwee", "Pei Shan", "Yi Xuan", "Jia Ying",
    "Shu Fen", "Wei Ling", "Bee Lian", "Hui Ying",
)  # fmt: skip

WESTERN_GIVEN_M = ("Daniel", "Marcus", "Oliver", "Rafael", "Stefan", "Anton", "Gregory", "Felix")
WESTERN_GIVEN_F = ("Jessica", "Elena", "Sophia", "Marianne", "Clara", "Beatrice", "Helena", "Nadia")
WESTERN_SURNAMES = (
    "Vasquez", "Pereira", "Hartmann", "Delacroix", "Whitfield", "Moreau", "Lindqvist", "Okafor",
    "Brandt", "Castellano", "Novak", "Fairweather",
)  # fmt: skip

MALAY_GIVEN_M = (
    "Muhammad Hafiz", "Mohd Faizal", "Ahmad Syafiq", "Muhammad Irfan", "Mohamed Zulkifli",
    "Abdul Rashid", "Mohd Hakim", "Muhammad Aiman",
)  # fmt: skip
MALAY_GIVEN_F = ("Nur Aisyah", "Nurul Huda", "Farah Amira", "Noor Aini", "Siti Maryam", "Aliyah")
MALAY_FATHERS = ("Ismail", "Rahmat", "Hassan", "Abdullah", "Yusof", "Osman", "Salleh", "Ibrahim")

INDIAN_GIVEN_M = ("Rajesh", "Suresh", "Anand", "Vikram", "Karthik", "Ganesh", "Murali", "Dinesh")
INDIAN_GIVEN_F = ("Priya", "Lakshmi", "Deepa", "Anitha", "Kavitha", "Meena", "Sheela", "Revathi")
INDIAN_FATHERS = ("Kumar", "Nair", "Pillai", "Subramaniam", "Krishnan", "Raman", "Menon", "Iyer")

MONONYMS = (
    "Suharti", "Sutrisno", "Wahyudi", "Rahayu", "Sumarni", "Hartono", "Winarto", "Sulastri",
    "Kusuma", "Budiyanto",
)  # fmt: skip

TITLES_M = ("Dr", "Prof", "Datuk Seri", "Mr")
TITLES_F = ("Dr", "Mdm", "Prof", "Datin Seri")

# canonical companies used by the three demo bundles (docs 4.1 Companies table)
CANONICAL_DIRECTORS: dict[str, list[tuple[str, str, str, int]]] = {
    # scenario -> [(name as printed, group, sex, birth year)]
    "loan": [
        ("Lim Hui Min", "chinese_order", "F", 1978),
        ("Mohd Faizal bin Rahmat", "malay_patronymic", "M", 1985),
    ],
}


def _pick(rng: random.Random, seq: tuple[str, ...]) -> str:
    return seq[rng.randrange(len(seq))]


def make_name(rng: random.Random, group: str, sex: str) -> str:
    """A fictitious person name in the *printed* (document) form of ``group``."""
    female = sex == "F"
    if group == "western_order":
        return f"{_pick(rng, WESTERN_GIVEN_F if female else WESTERN_GIVEN_M)} {_pick(rng, WESTERN_SURNAMES)}"
    if group == "chinese_order":
        given = CHINESE_GIVEN_F if female else CHINESE_GIVEN_M
        return f"{_pick(rng, CHINESE_SURNAMES)} {_pick(rng, given)}"
    if group == "malay_patronymic":
        word = rng.choice(("binte", "binti")) if female else "bin"
        given = MALAY_GIVEN_F if female else MALAY_GIVEN_M
        return f"{_pick(rng, given)} {word} {_pick(rng, MALAY_FATHERS)}"
    if group == "indian_patronymic":
        given = INDIAN_GIVEN_F if female else INDIAN_GIVEN_M
        return f"{_pick(rng, given)} {'d/o' if female else 's/o'} {_pick(rng, INDIAN_FATHERS)}"
    if group == "mononym":
        return _pick(rng, MONONYMS)
    if group == "titles":
        base = make_name(rng, "chinese_order", sex)
        return f"{_pick(rng, TITLES_F if female else TITLES_M)} {base}"
    raise ValueError(f"unknown name group {group!r}")


_TITLES = ("Datuk Seri", "Datin Seri", "Prof", "Mdm", "Dr", "Mr", "Ms")


def split_title(name: str) -> tuple[str, str]:
    for t in _TITLES:
        if name.startswith(t + " "):
            return t, name[len(t) + 1 :]
    return "", name


def registry_form(name: str, group: str, company_index: int) -> str:
    """The registry-side surface form (Q11): upper-case, title dropped, and on odd company
    indexes the patronymic abbreviated (``bin`` -> ``B.``, ``binte/binti`` -> ``BTE``)."""
    _, bare = split_title(name)
    out = bare.upper()
    if group == "malay_patronymic" and company_index % 2 == 1:
        out = out.replace(" BINTE ", " BTE ").replace(" BINTI ", " BTE ").replace(" BIN ", " B. ")
    return out


def surface_variants(name: str, group: str) -> list[str]:
    """Same-person surface forms used by tests and by the fairness builder."""
    title, bare = split_title(name)
    forms = [name, bare.upper()]
    toks = bare.split()
    if group == "chinese_order" and len(toks) >= 3:
        forms += [
            f"{' '.join(toks[1:])} {toks[0]}",
            f"{toks[0].upper()} {' '.join(toks[1:])}",
            f"{toks[0]} {''.join(t.lower() for t in toks[1:]).capitalize()}",
        ]
    if group == "malay_patronymic":
        forms += [bare.replace(" bin ", " ").replace(" binte ", " ").replace(" binti ", " ")]
    if group == "indian_patronymic":
        forms += [bare.replace(" s/o ", " ").replace(" d/o ", " ")]
    if group == "western_order" and len(toks) == 2:
        forms += [f"{toks[1]} {toks[0]}", f"{toks[1].upper()}, {toks[0]}"]
    if title:
        forms.append(bare)
    seen: list[str] = []
    for f in forms:
        if f not in seen:
            seen.append(f)
    return seen


# -------------------------------------------------------------------------------- companies

PLACE_WORDS = (
    "Bedok", "Clementi", "Jurong", "Kallang", "Tampines", "Woodlands", "Yishun", "Sembawang",
    "Punggol", "Serangoon", "Bishan", "Changi", "Tuas", "Alexandra", "Marsiling", "Queenstown",
    "Redhill", "Geylang", "Hougang", "Boon Lay", "Paya Lebar", "Kranji", "Seletar", "Pioneer",
)  # fmt: skip
PRODUCT_WORDS = (
    "Anchor", "Bolt", "Cable", "Dynamo", "Ember", "Gasket", "Hinge", "Ingot", "Kiln", "Lathe",
    "Marine", "Nozzle", "Orbit", "Piston", "Quartz", "Rivet", "Spindle", "Turbine", "Valve",
    "Winch", "Zenith", "Alloy", "Bearing", "Coil",
)  # fmt: skip
_CANONICAL_NAMES = {
    "Meridian Fastener Supplies",
    "Sembrook Freight",
    "Tanjong Ridge Trading",
}


def company_name(index: int) -> str:
    """Unique ``<place> <product> Pte. Ltd.`` for ``index >= 1``: a counter walks the
    place x product grid with a step coprime to its size, so no two indexes collide for
    ``index < len(PLACE_WORDS) * len(PRODUCT_WORDS)``."""
    n = len(PLACE_WORDS) * len(PRODUCT_WORDS)
    step = 97  # coprime to 576
    k = (index * step) % n
    place = PLACE_WORDS[k % len(PLACE_WORDS)]
    product = PRODUCT_WORDS[(k // len(PLACE_WORDS)) % len(PRODUCT_WORDS)]
    return f"{place} {product} Pte. Ltd."


STREET_WORDS = (
    "Ubi Avenue", "Kaki Bukit Road", "Tuas South Street", "Defu Lane", "Woodlands Industrial Park",
    "Pandan Crescent", "Sin Ming Road", "Jalan Besar", "Kampong Ampat", "Loyang Way",
)  # fmt: skip


def address(rng: random.Random) -> str:
    return (
        f"{rng.randrange(1, 90)} {_pick(rng, STREET_WORDS)} #{rng.randrange(1, 12):02d}-"
        f"{rng.randrange(1, 60):02d}, Singapore {rng.randrange(100000, 999999)}"
    )
