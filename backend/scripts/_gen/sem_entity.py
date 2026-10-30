"""ENTITY data: pairs of company names that are the same legal entity or different ones (docs 9.2).

Hand-written pools (brand words, founder names, initialisms, descriptors, legal forms) and plain
code; no model is called. A *base entity* is a brand, a descriptor, a legal-form family and an
optional region. Every pair is built from base entities of one split; the split key of a base is a
hash of its brand (the first four letters of a brand word, the whole founder name, the whole
initialism), so no base name and no brand prefix crosses from dev to held_out. Generic words
(Trading, Engineering, Pte Ltd) are shared by both splits on purpose: they are the signal the pairs
are about, not names.

Label convention (ours): *same* means one legal entity written two ways; *different* means two
legal entities, including a holding company and its subsidiary, or the same name in two
jurisdictions. A bank may treat related entities as one customer; that is a policy question this set
does not answer, and the label comes from our construction, not from a registry.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, replace

from .sem_common import (
    SEEDS,
    check_split,
    edit_distance,
    split_of_key,
    stem_key,
    stream,
)

SAME_CLASSES: tuple[str, ...] = (
    "legal_suffix",
    "abbreviation",
    "word_order",
    "transliteration",
    "initialism",
    "region_marker",
    "typo",
    "combined",
)
DIFFERENT_CLASSES: tuple[str, ...] = (
    "shared_generic",
    "shared_brand",
    "same_prefix",
    "same_industry",
    "group_member",
    "jurisdiction_form",
    "initial_conflict",
    "given_name_differs",
)
PER_CLASS = 24  # per split

# ------------------------------------------------------------------------------------ pools
# Brand words that do not share their first four letters with a prefix group below.
BRAND_WORDS = (
    "Acacia", "Aldgate", "Alpine", "Amberley", "Anchorage", "Arbor", "Ardent", "Argosy",
    "Ashgrove", "Aster", "Aurora", "Avalon", "Baywatch", "Beacon", "Belmont", "Bexley",
    "Birchwood", "Blackthorn", "Bramley", "Briarwood", "Burnside", "Caldera", "Camden",
    "Cardinal", "Carlton", "Cascade", "Cavendish", "Chantry", "Chestnut", "Cobblestone",
    "Copperfield", "Cormorant", "Cranbrook", "Cypress", "Dalton", "Danbury", "Dartmoor",
    "Delmar", "Denholm", "Dorchester", "Drayton", "Dunmore", "Eastleigh", "Edgewater", "Elmgrove",
    "Emerald", "Endeavour", "Everton", "Fernhill", "Finchley", "Fordham", "Foxton", "Galloway",
    "Garrison", "Glendale", "Granton", "Halcyon", "Hamilton", "Hartley", "Haverford", "Hawthorn",
    "Heathrow", "Hillcrest", "Holloway", "Huntley", "Ingram", "Jarvis", "Juniper", "Kempton",
    "Kestrel", "Kingfisher", "Lancaster", "Langdon", "Lexington", "Lombard", "Lyndhurst",
    "Madison", "Marlowe", "Mayfair", "Meadowbank", "Monarch", "Montrose", "Nelson", "Newhaven",
    "Oakley", "Orchid", "Osprey", "Oxford", "Paragon", "Parkside", "Pelican", "Penrose",
    "Plymouth", "Portman", "Quantum", "Radnor", "Raleigh", "Regent", "Ridgeway", "Rowan",
    "Salford", "Sandford", "Selwyn", "Sheridan", "Somerset", "Sterling", "Sutton", "Tamarind",
    "Tatton", "Thistle", "Trafalgar", "Trident", "Tulip", "Vanguard", "Vernon", "Wakefield",
    "Warwick", "Waverley", "Wellington", "Whitby", "Windsor", "Winston", "Wycombe", "Yorkshire",
    "Zenith", "Almond", "Banyan", "Cinder", "Dewdrop", "Ebony", "Frontier", "Gazelle", "Horizon",
    "Ivory", "Jasmine", "Kinetic", "Lotus", "Mosaic", "Nectar", "Opal", "Pioneer", "Quartz",
    "Radiant", "Saffron", "Tidal", "Umbra", "Velvet", "Willowbrook", "Yonder", "Zephyr",
)  # fmt: skip
# Words sharing a four-letter prefix: the "same prefix" class pairs two words of one group.
PREFIX_GROUPS = (
    ("Crestview", "Crestmont", "Crestbrook"),
    ("Northgate", "Northbrook", "Northfield"),
    ("Silverline", "Silvercrest", "Silvermere"),
    ("Greenfield", "Greenhaven", "Greenwood"),
    ("Brightwell", "Brightmoor", "Brightstone"),
    ("Eastbrook", "Eastgate", "Eastmere"),
    ("Westcliff", "Westmead", "Westbourne"),
    ("Bluewater", "Bluecrest", "Bluefield"),
    ("Goldcrest", "Goldmere", "Goldhaven"),
    ("Stonehaven", "Stonebridge", "Stonecroft"),
    ("Pinecrest", "Pinehurst", "Pinegrove"),
    ("Harbourview", "Harbourgate", "Harbourside"),
    ("Maplewood", "Maplecrest", "Maplebrook"),
    ("Ironbridge", "Ironwood", "Ironclad"),
    ("Lakeside", "Lakeview", "Lakemont"),
    ("Riverbend", "Riverside", "Rivergate"),
    ("Starlight", "Starfield", "Starcrest"),
    ("Highgate", "Highfield", "Highbury"),
    ("Kingsway", "Kingsmead", "Kingsford"),
    ("Fairview", "Fairmont", "Fairhaven"),
    ("Clearwater", "Clearbrook", "Clearview"),
    ("Cedarbrook", "Cedarcrest", "Cedarvale"),
)  # fmt: skip
INITIALISMS = (
    "AKS", "BMT", "CJL", "DKM", "ELG", "FHT", "GKR", "HPL", "JMS", "KTL", "LCW", "MNP", "NSB",
    "PRT", "RGH", "STK", "TWL", "VKN", "WYH", "ZTC", "BKL", "CMR", "DSV", "EJP", "FLN", "GTW",
    "HBD", "JRK", "KWM", "LPS", "MHT", "NGB", "PKC", "RDL", "SVN", "TBH", "VWC", "WJM", "YRG",
    "ZKP", "ABK", "CFH", "DLR", "EMT", "FPS", "GHJ", "HWR", "JNV", "KBD", "LFG",
)  # fmt: skip

# Chinese surnames and given-name syllables as written in Singapore (Hokkien / Teochew / Cantonese
# romanisation) with the Hanyu Pinyin of the same characters. Only unambiguous pairs; Goh and Ng
# are both 吴 (Wu), so a pair that differs only between them is never built.
SURNAME_PINYIN = {
    "Tan": "Chen", "Lim": "Lin", "Lee": "Li", "Ng": "Wu", "Goh": "Wu", "Teo": "Zhang",
    "Chua": "Cai", "Koh": "Gao", "Ong": "Wang", "Wong": "Huang", "Ho": "He", "Yeo": "Yang",
    "Chew": "Zhou", "Foo": "Hu", "Low": "Luo", "Soh": "Su", "Seah": "Xie", "Lau": "Liu",
}  # fmt: skip
GIVEN_PINYIN = {
    "Hock": "Fu", "Seng": "Sheng", "Kim": "Jin", "Huat": "Fa", "Soon": "Shun", "Chye": "Cai",
    "Teck": "De", "Meng": "Ming", "Chuan": "Quan", "Hup": "He", "Leong": "Liang", "Hwa": "Hua",
    "Kee": "Ji",
}  # fmt: skip
FOUNDER_GIVEN = (
    ("Hock", "Seng"), ("Kim", "Huat"), ("Soon", "Meng"), ("Chye", "Meng"), ("Teck", "Leong"),
    ("Chuan", "Hwa"), ("Hup", "Seng"), ("Kee", "Huat"), ("Hock", "Soon"), ("Kim", "Seng"),
    ("Teck", "Huat"), ("Meng", "Kee"), ("Leong", "Seng"), ("Hwa", "Chye"), ("Soon", "Teck"),
    ("Chuan", "Seng"), ("Hup", "Kim"), ("Kee", "Soon"),
)  # fmt: skip
# distinct second syllables that are different names (never a spelling variant of each other)
DIFFERENT_GIVEN = ("Seng", "Huat", "Soon", "Meng", "Leong", "Chye", "Kim", "Hock", "Teck", "Kee")

# descriptor -> industry; same_industry pairs two different descriptors of one industry
DESCRIPTORS: dict[str, tuple[str, ...]] = {
    "engineering": ("Engineering", "Precision Engineering", "Marine Engineering",
                    "Mechanical Engineering", "Electrical Engineering", "Engineering Works"),  # fmt: skip
    "trading": ("Trading", "General Trading", "Hardware Trading", "Import & Export", "Enterprise",
                "Supplies"),  # fmt: skip
    "logistics": ("Logistics", "Freight Services", "Shipping Agency", "Transport", "Cargo Services",
                  "Haulage"),  # fmt: skip
    "construction": ("Construction", "Building Services", "Civil Works", "Interior Design",
                     "Renovation Works"),  # fmt: skip
    "technology": ("Technologies", "Systems", "Digital Solutions", "Software", "IT Services",
                   "Electronics", "Data Systems"),  # fmt: skip
    "food": ("Foods", "Catering Services", "Food Industries", "Bakery", "Seafood"),
    "consulting": ("Consulting", "Advisory", "Management Services", "Business Solutions",
                   "Corporate Services"),  # fmt: skip
    "manufacturing": ("Manufacturing", "Industries", "Fabrication", "Metal Works", "Plastics"),
    "health": ("Medical Supplies", "Healthcare", "Pharmaceuticals", "Dental Supplies"),
    "property": ("Properties", "Realty", "Development", "Property Management", "Investments"),
}  # fmt: skip
INDUSTRY_OF = {d: ind for ind, ds in DESCRIPTORS.items() for d in ds}
ALL_DESCRIPTORS = tuple(INDUSTRY_OF)

WORD_ABBREV = {
    "Engineering": ("Engg", "Eng"),
    "Trading": ("Trdg",),
    "Logistics": ("Logis",),
    "International": ("Intl",),
    "Technologies": ("Tech", "Techs"),
    "Services": ("Svcs",),
    "Solutions": ("Soln",),
    "Management": ("Mgmt",),
    "Manufacturing": ("Mfg",),
    "Industries": ("Inds",),
    "Construction": ("Constn",),
    "Enterprise": ("Ent",),
    "Development": ("Dev",),
    "Properties": ("Props",),
    "Investments": ("Invest",),
    "Systems": ("Sys",),
    "Electrical": ("Elec",),
    "Mechanical": ("Mech",),
    "Pharmaceuticals": ("Pharma",),
    "Supplies": ("Suppl",),
    "Healthcare": ("Hlthcare",),
    "&": ("And",),
}
# English descriptor word -> Malay (translation, shown as a transliteration pair)
MALAY_WORD = {
    "Engineering": "Kejuruteraan",
    "Trading": "Perniagaan",
    "Enterprise": "Perusahaan",
    "Construction": "Pembinaan",
    "Transport": "Pengangkutan",
    "Services": "Perkhidmatan",
    "Properties": "Hartanah",
    "Foods": "Makanan",
    "Technologies": "Teknologi",
    "Industries": "Perindustrian",
    "Management": "Pengurusan",
}  # fmt: skip

LEGAL: dict[str, tuple[str, ...]] = {
    "pte": ("Pte Ltd", "Pte. Ltd.", "PTE LTD", "Private Limited", "(Pte) Ltd", "Pte Ltd."),
    "sdn": ("Sdn Bhd", "Sdn. Bhd.", "SDN BHD", "Sendirian Berhad"),
    "ltd": ("Limited", "Ltd", "Ltd."),
    "co": ("Co., Ltd.", "Co Ltd", "Company Limited", "Co., Limited"),
    "inc": ("Inc", "Inc.", "Incorporated"),
    "corp": ("Corp", "Corporation", "Corp."),
    "llp": ("LLP", "L.L.P."),
}
FORM_WEIGHTS = (("pte", 10), ("sdn", 3), ("ltd", 3), ("co", 3), ("inc", 2), ("corp", 2))
REGION_FORMS = {  # code -> (legal family, printed forms)
    "S": ("pte", ("(S)", "(Singapore)", "Singapore")),
    "M": ("sdn", ("(M)", "(Malaysia)", "Malaysia")),
    "HK": ("co", ("(HK)", "(Hong Kong)", "Hong Kong")),
}
HOLDING_WORDS = ("Holdings", "Group", "Capital", "Equity")

# ----------------------------------------------------------------------------- base entity


@dataclass(frozen=True)
class Base:
    kind: str  # word | pair | the | founder | initialism
    brand: tuple[str, ...]
    desc: str
    form: str = "pte"
    region: str = ""

    @property
    def split_key(self) -> str:
        if self.kind == "founder":
            return "".join(self.brand)
        if self.kind == "initialism":
            return self.brand[0]
        return stem_key(self.brand[0])

    @property
    def split(self) -> str:
        return split_of_key(self.split_key)

    @property
    def industry(self) -> str:
        return INDUSTRY_OF.get(self.desc, "")


def render(
    b: Base,
    *,
    legal: str | None = "",
    desc: str | None = None,
    brand: str | None = None,
    region: str | None = None,
    the_last: bool = False,
) -> str:
    """Print a base entity. ``legal=""`` means the first listed form, ``None`` means none."""
    if brand is None:
        if b.kind == "pair":
            brand = f"{b.brand[0]} & {b.brand[1]}"
        elif b.kind == "the":
            brand = b.brand[0] if the_last else f"The {b.brand[0]}"
        elif b.kind == "founder":
            brand = " ".join(b.brand)
        elif b.kind == "initialism":
            brand = ".".join(b.brand[0]) + "."
        else:
            brand = b.brand[0]
    parts = [brand, b.desc if desc is None else desc]
    if region is not None:
        parts.append(region)
    elif b.region:
        parts.append(REGION_FORMS[b.region][1][0])
    if legal == "":
        legal = LEGAL[b.form][0]
    if legal:
        parts.append(legal)
    text = " ".join(p for p in parts if p)
    return f"{text}, The" if b.kind == "the" and the_last else text


def _pick_form(rng: random.Random) -> str:
    return rng.choices([f for f, _ in FORM_WEIGHTS], [w for _, w in FORM_WEIGHTS])[0]


class Pool:
    """All base entities of one split, drawn deterministically."""

    def __init__(self, split: str, seed: int) -> None:
        self.split = check_split(split)
        self.seed = seed
        self.words = tuple(w for w in BRAND_WORDS if split_of_key(stem_key(w)) == split)
        self.groups = tuple(g for g in PREFIX_GROUPS if split_of_key(stem_key(g[0])) == split)
        self.initialisms = tuple(i for i in INITIALISMS if split_of_key(i) == split)
        self.founders = tuple(
            (s, g1, g2)
            for s in SURNAME_PINYIN
            for g1, g2 in FOUNDER_GIVEN
            if split_of_key(f"{s}{g1}{g2}") == split
        )
        self.all_words = tuple(w for g in self.groups for w in g) + self.words

    def word_base(self, rng: random.Random, **kw: str) -> Base:
        word = rng.choice(self.all_words)
        return Base("word", (word,), kw.get("desc") or rng.choice(ALL_DESCRIPTORS),
                    kw.get("form") or _pick_form(rng), kw.get("region", ""))  # fmt: skip

    def pair_base(self, rng: random.Random) -> Base:
        """``Alpine & Rowan``: two brand words of this split (so both are of the split)."""
        first, second = rng.sample(self.words, 2)
        return Base("pair", (first, second), rng.choice(ALL_DESCRIPTORS), _pick_form(rng))

    def the_base(self, rng: random.Random) -> Base:
        return Base("the", (rng.choice(self.all_words),), "Group", _pick_form(rng))

    def founder_base(self, rng: random.Random) -> Base:
        return Base("founder", rng.choice(self.founders), rng.choice(ALL_DESCRIPTORS),
                    rng.choice(("pte", "pte", "sdn", "co")))  # fmt: skip

    def initialism_base(self, rng: random.Random) -> Base:
        return Base("initialism", (rng.choice(self.initialisms),), rng.choice(ALL_DESCRIPTORS),
                    _pick_form(rng))  # fmt: skip


# ------------------------------------------------------------------------- surface style


def style(text: str, rng: random.Random) -> str:
    """Independent print style of one side: as-is, upper case or lower case."""
    r = rng.random()
    if r < 0.55:
        return text
    return text.upper() if r < 0.9 else text.lower()


def _other_legal(rng: random.Random, form: str, current: str) -> str:
    choices = [x for x in LEGAL[form] if x != current]
    return rng.choice(choices)


# ------------------------------------------------------------------ audit key (tests, not model)
_LEGAL_TOKENS = {
    "pte", "ltd", "private", "limited", "sdn", "bhd", "sendirian", "berhad", "co", "company",
    "inc", "incorporated", "corp", "corporation", "llp", "the",
}  # fmt: skip
_REGION_RX = (
    (re.compile(r"\((?:s|singapore)\)|\bsingapore\b", re.I), " @sg "),
    (re.compile(r"\((?:m|malaysia)\)|\bmalaysia\b", re.I), " @my "),
    (re.compile(r"\((?:hk|hong kong)\)|\bhong kong\b", re.I), " @hk "),
)
_SHORT_TO_LONG = {
    s.casefold(): long.casefold() for long, shorts in WORD_ABBREV.items() for s in shorts
}
_SHORT_TO_LONG["and"] = "&"
_MALAY_TO_EN = {v.casefold(): k.casefold() for k, v in MALAY_WORD.items()}
_TRANSLIT = {k.casefold(): v.casefold() for k, v in {**SURNAME_PINYIN, **GIVEN_PINYIN}.items()}
_TRANSLIT.update({"ng": "wu", "goh": "wu"})
_LEGAL_FAMILY = (
    ("llp", "llp"), ("sendirian", "sdn"), ("sdn", "sdn"), ("private", "pte"), ("pte", "pte"),
    ("incorporated", "inc"), ("inc", "inc"), ("corporation", "corp"), ("corp", "corp"),
    ("company", "co"), ("co", "co"), ("limited", "ltd"), ("ltd", "ltd"),
)  # fmt: skip


def legal_family(name: str) -> str:
    words = re.sub(r"[^a-z ]", " ", name.casefold()).split()
    for token, fam in _LEGAL_FAMILY:
        if token in words:
            if token in ("co", "company") and ("ltd" in words or "limited" in words):
                return "co"
            return fam
    return ""


def core_key(name: str) -> tuple[str, ...]:
    """What identifies the entity apart from its legal-form spelling: the brand and descriptor
    words, abbreviations expanded, Malay and pinyin spellings mapped to one form, a run of single
    letters read as one initialism, region marked, word order ignored. Used by the tests to prove
    that every *same* pair is the same under mechanical rules and no *different* pair is."""
    s = name
    for rx, tag in _REGION_RX:
        s = rx.sub(tag, s)
    s = s.casefold().replace("&", " & ")
    s = re.sub(r"[().,']", " ", s)
    tokens = [t for t in s.split() if t not in _LEGAL_TOKENS]
    merged: list[str] = []
    run = ""
    for t in [*tokens, ""]:
        if len(t) == 1 and t.isalpha() and t != "&":
            run += t
            continue
        if run:
            merged.append(run)
            run = ""
        if t:
            merged.append(t)
    out = []
    for t in merged:
        t = _SHORT_TO_LONG.get(t, t)
        t = _MALAY_TO_EN.get(t, t)
        t = _TRANSLIT.get(t, t)
        out.append(t)
    return tuple(sorted(out))


def full_key(name: str) -> tuple[tuple[str, ...], str]:
    return core_key(name), legal_family(name)


# ----------------------------------------------------------------------------- same pairs


def _same_legal_suffix(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    kind = rng.choice(("word", "word", "pair", "founder", "initialism"))
    b = _base_of(pool, rng, kind)
    a_legal = rng.choice(LEGAL[b.form])
    b_legal: str | None = _other_legal(rng, b.form, a_legal)
    note = "other spelling of the legal form"
    if rng.random() < 0.25:
        b_legal, note = None, "legal form left off"
    return render(b, legal=a_legal), render(b, legal=b_legal), False, note


def _same_abbreviation(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    while True:
        b = _base_of(pool, rng, rng.choice(("word", "word", "pair", "founder")))
        words = b.desc.split()
        if any(w in WORD_ABBREV for w in words):
            break
    short = " ".join(rng.choice(WORD_ABBREV[w]) if w in WORD_ABBREV else w for w in words)
    a_legal = rng.choice(LEGAL[b.form])
    b_legal = _other_legal(rng, b.form, a_legal) if rng.random() < 0.5 else a_legal
    return (
        render(b, legal=a_legal),
        render(b, legal=b_legal, desc=short),
        False,
        "descriptor abbreviated",
    )


def _same_word_order(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    kind = rng.choice(("pair", "founder", "the"))
    b = _base_of(pool, rng, kind)
    a_legal = rng.choice(LEGAL[b.form])
    if kind == "pair":
        swapped = f"{b.brand[1]} & {b.brand[0]}"
        return (
            render(b, legal=a_legal),
            render(b, legal=a_legal, brand=swapped),
            False,
            "partners swapped",
        )
    if kind == "founder":
        s, g1, g2 = b.brand
        given_first = f"{g1} {g2} {s}"
        return (
            render(b, legal=a_legal),
            render(b, legal=a_legal, brand=given_first),
            False,
            "surname moved last",
        )
    return (
        render(b, legal=a_legal),
        render(b, legal=a_legal, the_last=True),
        False,
        "'The' moved to the end",
    )


def _same_transliteration(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    if rng.random() < 0.5:
        b = _base_of(pool, rng, "founder")
        s, g1, g2 = b.brand
        pin = f"{SURNAME_PINYIN[s]} {GIVEN_PINYIN[g1]} {GIVEN_PINYIN[g2]}"
        a_legal = rng.choice(LEGAL[b.form])
        return (
            render(b, legal=a_legal),
            render(b, legal=a_legal, brand=pin),
            True,
            "dialect romanisation to Hanyu Pinyin",
        )
    while True:
        b = replace(_base_of(pool, rng, "word"), form="sdn")
        words = b.desc.split()
        if all(w in MALAY_WORD for w in words):
            break
    malay = " ".join(MALAY_WORD.get(w, w) for w in words)
    a_legal = rng.choice(LEGAL["sdn"])
    return (
        render(b, legal=a_legal),
        render(b, legal=a_legal, desc=malay),
        True,
        "descriptor translated to Malay",
    )


def _same_initialism(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    b = _base_of(pool, rng, "initialism")
    letters = b.brand[0]
    forms = [".".join(letters) + ".", letters, " ".join(letters), ".".join(letters)]
    x, y = rng.sample(sorted(set(forms)), 2)
    a_legal = rng.choice(LEGAL[b.form])
    return (
        render(b, legal=a_legal, brand=x),
        render(b, legal=a_legal, brand=y),
        False,
        "initials written with and without full stops",
    )


def _same_region(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    code = rng.choice(tuple(REGION_FORMS))
    form, printed = REGION_FORMS[code]
    b = replace(_base_of(pool, rng, "word"), region=code, form=form)
    x, y = rng.sample(printed, 2)
    a_legal = rng.choice(LEGAL[form])
    return (
        render(b, legal=a_legal, region=x),
        render(b, legal=a_legal, region=y),
        False,
        "region marker spelled two ways",
    )


def _typo_word(word: str, rng: random.Random, taken: set[str]) -> str:
    for _ in range(40):
        j = rng.randrange(2, len(word) - 1)
        kind = rng.choice(("swap", "drop", "double", "rn", "o0"))
        if kind == "swap":
            out = word[:j] + word[j + 1] + word[j] + word[j + 2 :]
        elif kind == "drop":
            out = word[:j] + word[j + 1 :]
        elif kind == "double":
            out = word[:j] + word[j] + word[j:]
        elif kind == "rn" and "m" in word[1:]:
            k = word.index("m", 1)
            out = word[:k] + "rn" + word[k + 1 :]
        elif kind == "o0" and "o" in word[1:]:
            k = word.index("o", 1)
            out = word[:k] + "0" + word[k + 1 :]
        else:
            continue
        # a slip must not land on another brand of the pool (that would be a different entity)
        if out != word and all(edit_distance(out.casefold(), t.casefold()) > 1 for t in taken):
            return out
    return word[:-1] if len(word) > 5 else word + word[-1]


def _same_typo(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    while True:
        b = pool.word_base(rng)
        if len(b.brand[0]) >= 7:
            break
    others = {w for w in pool.all_words if w != b.brand[0]}
    slip = _typo_word(b.brand[0], rng, others)
    a_legal = rng.choice(LEGAL[b.form])
    return (
        render(b, legal=a_legal),
        render(b, legal=a_legal, brand=slip),
        False,
        "one-character slip in the brand (OCR or typing)",
    )


def _same_combined(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    while True:
        b = _base_of(pool, rng, rng.choice(("word", "pair", "founder")))
        words = b.desc.split()
        if any(w in WORD_ABBREV for w in words):
            break
    short = " ".join(rng.choice(WORD_ABBREV[w]) if w in WORD_ABBREV else w for w in words)
    a_legal = rng.choice(LEGAL[b.form])
    b_legal: str | None = _other_legal(rng, b.form, a_legal)
    brand = None
    if b.kind == "pair":
        brand = f"{b.brand[1]} & {b.brand[0]}"
    elif b.kind == "founder":
        brand = f"{b.brand[1]} {b.brand[2]} {b.brand[0]}"
    y = render(b, legal=b_legal, desc=short, brand=brand)
    if rng.random() < 0.5:
        y = y.upper()
    return render(b, legal=a_legal), y, False, "legal form, abbreviation, order and case together"


def _base_of(pool: Pool, rng: random.Random, kind: str) -> Base:
    if kind == "pair":
        return pool.pair_base(rng)
    if kind == "founder":
        return pool.founder_base(rng)
    if kind == "the":
        return pool.the_base(rng)
    if kind == "initialism":
        return pool.initialism_base(rng)
    return pool.word_base(rng)


# -------------------------------------------------------------------------- different pairs


def _diff_shared_generic(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    while True:
        a = pool.word_base(rng)
        b = replace(pool.word_base(rng), desc=a.desc, form=a.form)
        if edit_distance(a.brand[0].casefold(), b.brand[0].casefold()) >= 4 and (
            stem_key(a.brand[0]) != stem_key(b.brand[0])
        ):
            return render(a), render(b), False, "same descriptor and legal form, different brand"


def _diff_shared_brand(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    while True:
        a = pool.word_base(rng)
        d2 = rng.choice(ALL_DESCRIPTORS)
        if INDUSTRY_OF[d2] != a.industry:
            b = replace(a, desc=d2)
            return render(a), render(b), True, "same brand word, different line of business"


def _diff_same_prefix(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    group = rng.choice(pool.groups)
    w1, w2 = rng.sample(group, 2)
    a = pool.word_base(rng)
    same_desc = rng.random() < 0.6
    a = replace(a, brand=(w1,))
    b = replace(a, brand=(w2,), desc=a.desc if same_desc else rng.choice(ALL_DESCRIPTORS))
    return render(a), render(b), True, "brands share a four-letter prefix"


def _diff_same_industry(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    while True:
        a = pool.word_base(rng)
        d2 = rng.choice(DESCRIPTORS[a.industry])
        b = replace(pool.word_base(rng), desc=d2, form=a.form)
        if d2 != a.desc and stem_key(a.brand[0]) != stem_key(b.brand[0]):
            return render(a), render(b), True, "different firms in one industry"


def _diff_group_member(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    a = pool.word_base(rng)
    word = rng.choice(HOLDING_WORDS)
    b = replace(a, desc=f"{a.desc} {word}") if rng.random() < 0.4 else replace(a, desc=word)
    return render(a), render(b), True, "holding or group company of the same brand"


def _diff_jurisdiction_form(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    r = rng.random()
    a = pool.word_base(rng)
    if r < 0.5:
        ra, rb = rng.sample(tuple(REGION_FORMS), 2)
        a = replace(a, region=ra, form=REGION_FORMS[ra][0])
        b = replace(a, region=rb, form=REGION_FORMS[rb][0])
        return render(a), render(b), True, "same name registered in two jurisdictions"
    f1, f2 = rng.sample(("pte", "llp", "inc", "corp", "ltd"), 2)
    if r < 0.8:
        f1 = "pte"
        f2 = rng.choice(("llp", "inc", "corp"))
    a = replace(a, form=f1)
    return render(a), render(replace(a, form=f2)), True, "same name, different legal form"


def _diff_initial_conflict(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    # the changed initialism is itself a base of this split (its key hashes into the split)
    while True:
        a = pool.initialism_base(rng)
        letters = list(a.brand[0])
        if rng.random() < 0.5:
            i = rng.randrange(3)
            letters[i] = rng.choice([c for c in "ABCDEFGHJKLMNPRSTVWYZ" if c != letters[i]])
            other, note = "".join(letters), "one initial differs"
        else:
            other, note = "".join(reversed(letters)), "same initials in another order"
        if other != a.brand[0] and split_of_key(other) == pool.split:
            return render(a), render(replace(a, brand=(other,))), True, note


def _diff_given_name(pool: Pool, rng: random.Random) -> tuple[str, str, bool, str]:
    if rng.random() < 0.75:
        while True:
            a = pool.founder_base(rng)
            s, g1, g2 = a.brand
            g3 = rng.choice([g for g in DIFFERENT_GIVEN if g not in (g1, g2)])
            second = (s, g1, g3) if rng.random() < 0.5 else (s, g3, g2)
            if split_of_key("".join(second)) == pool.split:  # the other name is a base of the split
                b = replace(a, brand=second)
                return render(a), render(b), True, "same surname, different given name"
    a = pool.pair_base(rng)
    other = rng.choice([w for w in pool.words if w not in a.brand])
    b = replace(a, brand=(a.brand[0], other))
    return render(a), render(b), True, "same first partner, different second partner"


SAME_BUILDERS = {
    "legal_suffix": _same_legal_suffix,
    "abbreviation": _same_abbreviation,
    "word_order": _same_word_order,
    "transliteration": _same_transliteration,
    "initialism": _same_initialism,
    "region_marker": _same_region,
    "typo": _same_typo,
    "combined": _same_combined,
}
DIFFERENT_BUILDERS = {
    "shared_generic": _diff_shared_generic,
    "shared_brand": _diff_shared_brand,
    "same_prefix": _diff_same_prefix,
    "same_industry": _diff_same_industry,
    "group_member": _diff_group_member,
    "jurisdiction_form": _diff_jurisdiction_form,
    "initial_conflict": _diff_initial_conflict,
    "given_name_differs": _diff_given_name,
}
HARD_CLASSES = {"transliteration", "typo", "combined", "same_prefix", "group_member",
                "jurisdiction_form", "initial_conflict"}  # fmt: skip


def build_pairs(split: str, seed: int | None = None, per_class: int = PER_CLASS) -> list[dict]:
    """The ENTITY set of one split: ``per_class`` pairs per class (8 same, 8 different)."""
    check_split(split)
    seed = SEEDS[split] if seed is None else seed
    pool = Pool(split, seed)
    out: list[dict] = []
    seen: set[frozenset[str]] = set()
    for label, classes, builders in (
        ("same", SAME_CLASSES, SAME_BUILDERS),
        ("different", DIFFERENT_CLASSES, DIFFERENT_BUILDERS),
    ):
        for cls in classes:
            rng = stream(seed, "entity", split, cls)
            made = 0
            attempts = 0
            while made < per_class:
                attempts += 1
                if attempts > 4000:
                    raise RuntimeError(f"cannot build {per_class} unique {cls} pairs for {split}")
                a, b, _flag, note = builders[cls](pool, rng)
                if a.casefold() == b.casefold() and label == "same" and cls != "legal_suffix":
                    continue
                key = frozenset((a.casefold(), b.casefold()))
                if len(key) < 2 or key in seen:
                    continue
                seen.add(key)
                if (
                    label == "different"
                    and core_key(a) == core_key(b)
                    and legal_family(a) == legal_family(b)
                ):
                    continue
                if rng.random() < 0.5:
                    a, b = b, a
                out.append(
                    {
                        "a": style(a, rng),
                        "b": style(b, rng),
                        "label": label,
                        "class": cls,
                        "hard": cls in HARD_CLASSES,
                        "note": note,
                    }
                )
                made += 1
    rng = stream(seed, "entity", split, "order")
    rng.shuffle(out)
    for n, row in enumerate(out):
        row["pair_id"] = f"ent-{split}-{n:04d}"
        row["split"] = split
    return out
