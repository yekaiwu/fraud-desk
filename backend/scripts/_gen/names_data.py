"""Fictitious name lists and person-name generators (own lists, no Faker).

Groups cover plan 4.6 / docs 4.7: western order, Chinese order (surname first), Malay and Indian
patronymics (``bin/binte/binti``, ``s/o``, ``d/o``), mononyms and titles.
"""

from __future__ import annotations

import hashlib
import random
from functools import cache

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


# ------------------------------------------------------------------ extended pools (name fairness)
#
# Larger fictitious pools for the dev/test fairness set (scripts/_eval/name_fairness.py). The six
# tuples above are left untouched: the 270-pair slice and the demo bundles are built from them. Every
# token below is assigned to "dev" or "test" by a hash of the token, and a name is only built from
# tokens of one split, so no base name (and no given-name or patronym token) crosses the split.

EXT_MALAY_GIVEN_M = (
    "Hafiz", "Faizal", "Syafiq", "Irfan", "Zulkifli", "Rashid", "Hakim", "Aiman", "Amirul",
    "Haikal", "Danial", "Razak", "Farhan", "Imran", "Khairul", "Nazri", "Shahrul", "Hisham",
    "Azman", "Firdaus", "Ridzuan", "Taufik", "Zaidi", "Kamal", "Samsudin", "Hairul", "Luqman",
    "Nasir", "Roslan", "Saiful", "Shafiq", "Azlan", "Fadzil", "Hanif", "Idris", "Jefri", "Kamarul",
    "Lutfi", "Mazlan", "Norman", "Othman", "Rizal", "Sufian", "Tarmizi", "Yazid", "Zainal",
    "Badrul", "Effendi", "Fauzi", "Anuar",
)  # fmt: skip
EXT_MALAY_PREFIX_M = ("Muhammad", "Mohd", "Ahmad", "Abdul", "")
EXT_MALAY_GIVEN_F = (
    "Aisyah", "Huda", "Amira", "Aini", "Maryam", "Aliyah", "Nabilah", "Syahirah", "Farhana",
    "Liyana", "Izzati", "Hidayah", "Atiqah", "Balqis", "Dayana", "Elyana", "Fatimah", "Hajar",
    "Intan", "Jasmine", "Khairunnisa", "Latifah", "Mardhiah", "Natasha", "Qistina", "Rohana",
    "Sabrina", "Syuhada", "Wardah", "Zulaikha", "Nadia", "Sofea", "Umairah", "Zarina",
)  # fmt: skip
EXT_MALAY_PREFIX_F = ("Nur", "Nurul", "Siti", "Noor", "")
EXT_MALAY_FATHERS = (
    "Ismail", "Rahmat", "Hassan", "Abdullah", "Yusof", "Osman", "Salleh", "Ibrahim", "Ahmad",
    "Hamid", "Jaafar", "Karim", "Latif", "Mansor", "Nordin", "Omar", "Rahim", "Sulaiman", "Taib",
    "Wahab", "Zakaria", "Bakar", "Daud", "Ghani", "Harun", "Idrus", "Jamil", "Kassim", "Lazim",
    "Mahmud", "Naim", "Puteh", "Rashid", "Samad", "Tahir", "Usman", "Yaakob", "Zainudin", "Aziz",
    "Basri", "Hashim", "Shamsudin", "Marzuki", "Ramli",
)  # fmt: skip

EXT_INDIAN_GIVEN_M = (
    "Rajesh", "Suresh", "Anand", "Vikram", "Karthik", "Ganesh", "Murali", "Dinesh", "Ramesh",
    "Vijay", "Mohan", "Arun", "Naveen", "Sanjay", "Prakash", "Senthil", "Balaji", "Chandran",
    "Devan", "Elango", "Gopinath", "Harish", "Jegan", "Kannan", "Lingam", "Manoj", "Pandian",
    "Rajan", "Saravanan", "Thiru", "Udaya", "Venkat", "Yogesh", "Ashok", "Bharat", "Chelvan",
    "Deepak", "Eswaran", "Gunalan", "Ilango", "Kishore", "Logan", "Mahesh", "Nagarajan",
    "Pradeep", "Ravi", "Selvam", "Tamil", "Vignesh", "Sundar",
)  # fmt: skip
EXT_INDIAN_GIVEN_F = (
    "Priya", "Lakshmi", "Deepa", "Anitha", "Kavitha", "Meena", "Sheela", "Revathi", "Divya",
    "Anita", "Bhavani", "Chitra", "Devi", "Geetha", "Hema", "Indira", "Jayanthi", "Kamala",
    "Latha", "Malathi", "Nirmala", "Padma", "Radha", "Saroja", "Tamilselvi", "Uma", "Vasanthi",
    "Yamini", "Sumathi", "Vidya", "Shanthi", "Rani", "Sangeetha", "Thenmozhi",
)  # fmt: skip
EXT_INDIAN_FATHERS = (
    "Kumar", "Nair", "Pillai", "Subramaniam", "Krishnan", "Raman", "Menon", "Iyer", "Rajan",
    "Gopal", "Singh", "Nathan", "Muthu", "Samy", "Arumugam", "Balakrishnan", "Chellappan",
    "Doraisamy", "Ganapathy", "Haridas", "Jeyaraj", "Kandasamy", "Letchumanan", "Maniam",
    "Narayanan", "Palaniappan", "Ramasamy", "Sivalingam", "Thangaraju", "Veerasamy", "Anbalagan",
    "Devadas", "Ethirajan", "Govindasamy", "Jayakumar", "Karuppiah", "Marimuthu", "Perumal",
    "Rajendran", "Selvaraj",
)  # fmt: skip

EXT_CHINESE_SURNAMES = CHINESE_SURNAMES + (
    "Chong", "Cheong", "Kwok", "Leong", "Lau", "Liew", "Phua", "Tang", "Wee", "Yap", "Yong",
    "Chia", "Fong", "Gan", "Hui", "Kang", "Lam", "Mok", "Neo", "Oh", "Png", "Saw", "Thng", "Wu",
    "Yee", "Ang", "Boey", "Chee",
)  # fmt: skip
EXT_CHINESE_SYL_M = (
    ("Wei", "Jun", "Kai", "Zhi", "Boon", "Eng", "Cheng", "Jia", "Yong", "Kok", "Wen", "Jian",
     "Hong", "Ming", "Chee", "Kian", "Teck", "Seng", "Yew", "Soon"),
    ("Ming", "Jie", "Xiang", "Hao", "Kiat", "Hock", "Wei", "Sheng", "Leong", "Hui", "Keong",
     "Huat", "Loong", "Siong", "Aun", "Yang", "Cheng", "Wah", "Fatt", "Meng"),
)  # fmt: skip
EXT_CHINESE_SYL_F = (
    ("Hui", "Mei", "Xin", "Li", "Siew", "Pei", "Yi", "Jia", "Shu", "Wei", "Bee", "Lay", "Swee",
     "Ai", "Yan", "Pek"),
    ("Min", "Ling", "Yi", "Ting", "Hwee", "Shan", "Xuan", "Ying", "Fen", "Lian", "Hoon", "Choo",
     "Kim", "Nah", "Ping", "Lan"),
)  # fmt: skip

EXT_WESTERN_GIVEN_M = (
    "Daniel", "Marcus", "Oliver", "Rafael", "Stefan", "Anton", "Gregory", "Felix", "Adrian",
    "Bernard", "Conrad", "Dominic", "Edmund", "Florian", "Gerald", "Hector", "Ivan", "Julian",
    "Karl", "Leonard", "Martin", "Nathan", "Oscar", "Patrick", "Quentin", "Roland", "Simon",
    "Theodore", "Vincent", "Warren",
)  # fmt: skip
EXT_WESTERN_GIVEN_F = (
    "Jessica", "Elena", "Sophia", "Marianne", "Clara", "Beatrice", "Helena", "Nadia", "Alice",
    "Bianca", "Camille", "Diana", "Eleanor", "Fiona", "Greta", "Hannah", "Irene", "Josephine",
    "Katrina", "Louisa", "Monica", "Natalia", "Olivia", "Penelope", "Rebecca", "Sabrina",
    "Tessa", "Ursula", "Valerie", "Wendy",
)  # fmt: skip
EXT_WESTERN_SURNAMES = (
    "Vasquez", "Pereira", "Hartmann", "Delacroix", "Whitfield", "Moreau", "Lindqvist", "Okafor",
    "Brandt", "Castellano", "Novak", "Fairweather", "Abernathy", "Blackwood", "Carmichael",
    "Dunleavy", "Eriksen", "Fontaine", "Grimaldi", "Hollingsworth", "Ibarra", "Jorgensen",
    "Kowalski", "Lancaster", "Montgomery", "Nakamura", "Oyelaran", "Pemberton", "Quintero",
    "Rutherford", "Sandoval", "Thornton", "Underwood", "Valdez", "Wellington", "Yarborough",
    "Zimmerman", "Ashworth", "Bellamy", "Cavanaugh",
)  # fmt: skip

EXT_MONONYM_STEMS = (
    "Sur", "Han", "Wid", "Bud", "Sar", "Tri", "Yos", "Dar", "Kus", "Rah", "Sum", "Win", "Gun",
    "Sus", "Sut", "Pra", "Set", "Wah", "Her", "Agus", "Bam", "Dwi", "Eko", "Fik", "Joko", "Mul",
    "Nur", "Pur", "Rus", "Tek",
)  # fmt: skip
EXT_MONONYM_SUFFIXES = (
    "yadi", "anto", "ati", "ono", "ani", "ika", "wati", "mono", "ardi", "ianto", "yono", "jaya",
    "wan", "man", "tama", "kusuma", "rini", "lah", "sari", "dewa",
)  # fmt: skip


def split_of(token: str) -> str:
    """``"dev"`` or ``"test"`` for a name token: a hash of the token, so the split is stable and
    does not depend on pool order."""
    digest = hashlib.sha256(token.casefold().encode()).digest()
    return "dev" if digest[0] % 2 == 0 else "test"


@cache
def _in_split(pool: tuple[str, ...], split: str) -> tuple[str, ...]:
    return tuple(t for t in pool if split_of(t) == split)


def make_name_ext(rng: random.Random, group: str, sex: str, split: str) -> str:
    """A fictitious person name in the printed form of ``group`` built only from the tokens of
    ``split`` (``"dev"`` or ``"test"``): given name and family token both hash into the split."""
    female = sex == "F"
    if group == "malay_patronymic":
        given = rng.choice(_in_split(EXT_MALAY_GIVEN_F if female else EXT_MALAY_GIVEN_M, split))
        prefix = rng.choice(EXT_MALAY_PREFIX_F if female else EXT_MALAY_PREFIX_M)
        word = rng.choice(("binte", "binti")) if female else "bin"
        father = rng.choice(_in_split(EXT_MALAY_FATHERS, split))
        return f"{prefix} {given} {word} {father}".strip()
    if group == "indian_patronymic":
        given = rng.choice(_in_split(EXT_INDIAN_GIVEN_F if female else EXT_INDIAN_GIVEN_M, split))
        father = rng.choice(_in_split(EXT_INDIAN_FATHERS, split))
        return f"{given} {'d/o' if female else 's/o'} {father}"
    if group in ("chinese_order", "titles"):
        first, second = EXT_CHINESE_SYL_F if female else EXT_CHINESE_SYL_M
        sur = rng.choice(_in_split(EXT_CHINESE_SURNAMES, split))
        a = rng.choice(_in_split(first, split))
        b = rng.choice(_in_split(second, split))
        name = f"{sur} {a} {b}"
        if group == "titles":
            return f"{rng.choice(TITLES_F if female else TITLES_M)} {name}"
        return name
    if group == "western_order":
        given_pool = EXT_WESTERN_GIVEN_F if female else EXT_WESTERN_GIVEN_M
        given = rng.choice(_in_split(given_pool, split))
        return f"{given} {rng.choice(_in_split(EXT_WESTERN_SURNAMES, split))}"
    if group == "mononym":
        stem = rng.choice(_in_split(EXT_MONONYM_STEMS, split))
        return stem + rng.choice(_in_split(EXT_MONONYM_SUFFIXES, split))
    raise ValueError(f"unknown name group {group!r}")


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
