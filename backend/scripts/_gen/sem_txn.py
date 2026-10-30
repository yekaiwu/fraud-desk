"""TXN data: messy real-world bank-statement descriptions with the ground truth kept (docs 9.1).

The generator prints templated narratives (``world.DESC_IN`` / ``DESC_OUT`` and the fixed lines in
``_statement_for_month``). Real statements are not like that: abbreviated, truncated at a fixed
width, upper or lower case, carrying reference codes, mistyped, and sometimes in Malay or romanised
Chinese. ``perturb`` turns a templated description into such a form. It never touches the labels:
the ground truth of a row (category, direction, c4, c5, counterparty) is attached by the caller
before the text is changed, and ``perturb`` retries until the text still carries *evidence* for the
category (and for the counterparty's c5 label), so every label stays derivable in principle.

Hand-written catalogues only; no model is called. The foreign-language wording is ours and has not
been checked by a native speaker (a stated limit). A row at level 0 is the generator's own form
and serves as the control that shows what the perturbation costs.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from . import lexicon as lx
from . import names_data as nd
from .sem_common import (
    SEEDS,
    D,
    check_split,
    edit_distance,
    in_split,
    nd_split,
    pick_weighted,
    s2,
    stream,
    weekday_on_or_before,
)
from .world import DESC_IN, DESC_OUT

LEVELS = (0, 1, 2, 3)
CATEGORIES: tuple[str, ...] = (
    "customer_receipt",
    "supplier_payment",
    "payroll",
    "statutory_contribution",
    "tax_payment",
    "premises",
    "utilities_telecom",
    "loan_repayment",
    "loan_disbursement",
    "insurance",
    "bank_charges",
    "interest_income",
    "owner_transfer",
    "refund",
)
PER_CATEGORY = 24  # per split; 6 rows per level
# the two categories that carry a counterparty name get twice as many rows, so each of the eight
# c5 labels has about fifteen rows per split
DOUBLED = ("customer_receipt", "supplier_payment")

# generator row ``kind`` (world.py LedgerRow.kind) -> category, used to prove the catalogue covers
# every description the generator prints
KIND_TO_CATEGORY = {
    "trade": "customer_receipt",
    "retainer": "customer_receipt",
    "other_cust": "customer_receipt",
    "other_cust_hard": "customer_receipt",
    "supplier": "supplier_payment",
    "misc_sup": "supplier_payment",
    "supplier_hard": "supplier_payment",
    "misc_sup_hard": "supplier_payment",
    "overdraft": "supplier_payment",
    "dup": "supplier_payment",
    "payroll": "payroll",
    "cpf": "statutory_contribution",
    "gst": "tax_payment",
    "rent": "premises",
    "service": "premises",
    "utility": "utilities_telecom",
    "telco": "utilities_telecom",
    "loan": "loan_repayment",
    "insurance": "insurance",
    "svc_fee": "bank_charges",
    "fee": "bank_charges",
    "interest": "interest_income",
}
C4_OF_NONTRADE = {
    "owner_transfer": "owner_transfer",
    "refund": "refund",
    "financing_proceeds": "loan_disbursement",
}


@dataclass(frozen=True)
class CatSpec:
    category: str
    direction: str  # debit | credit
    c4: str | None  # the generator's C4 label (credit rows only)
    templates: tuple[str, ...]  # generator wording; slots in braces
    party: str | None  # customer | supplier | director | None
    amount: tuple[int, int]


SPECS: dict[str, CatSpec] = {
    s.category: s
    for s in (
        CatSpec(
            "customer_receipt",
            "credit",
            "trade_receipt",
            (*DESC_IN, "RETAINER FEE {cp}"),
            "customer",
            (1500, 60000),
        ),
        CatSpec("supplier_payment", "debit", None, tuple(DESC_OUT), "supplier", (400, 45000)),
        CatSpec("payroll", "debit", None, ("GIRO SALARY PAYMENT",), None, (8000, 42000)),
        CatSpec("statutory_contribution", "debit", None, ("CPF CONTRIBUTION",), None, (1500, 7000)),
        CatSpec("tax_payment", "debit", None, ("IRAS GST PAYMENT",), None, (3000, 22000)),
        CatSpec(
            "premises",
            "debit",
            None,
            ("RENT {addr}", "BUILDING SERVICE CHARGE"),
            None,
            (380, 14000),
        ),
        CatSpec(
            "utilities_telecom",
            "debit",
            None,
            ("{util} UTILITIES", "{telco} TELECOM"),
            None,
            (180, 3200),
        ),
        CatSpec("loan_repayment", "debit", None, ("TERM LOAN INSTALMENT",), None, (2500, 16000)),
        CatSpec(
            "loan_disbursement",
            "credit",
            "financing_proceeds",
            ("TERM LOAN DISBURSEMENT {bank}",),
            None,
            (30000, 250000),
        ),
        CatSpec("insurance", "debit", None, ("{insurer} INSURANCE PREMIUM",), None, (600, 5200)),
        CatSpec(
            "bank_charges",
            "debit",
            None,
            ("ACCOUNT SERVICE CHARGE", "FAST/GIRO FEE"),
            None,
            (0, 0),  # fixed: 15.00 or a few cents, set in _amount
        ),
        CatSpec("interest_income", "credit", "other", ("INTEREST CREDIT",), None, (0, 0)),
        CatSpec(
            "owner_transfer",
            "credit",
            "owner_transfer",
            ("FUNDS TRANSFER FROM {director} DIRECTOR LOAN",),
            "director",
            (5000, 80000),
        ),
        CatSpec("refund", "credit", "refund", ("REFUND FROM {cp}",), "supplier", (150, 12000)),
    )
}

# ------------------------------------------------------------------------------ vendor pools
# Fictitious private vendors, hashed into dev or held_out so a vendor seen in the dev rows never
# appears in held_out. (CITYGRID, LINKNET and COVERLINE are the generator's own.)
UTIL_VENDORS = (
    "Citygrid", "Powerlane", "Aquaflow", "Voltara", "Gridwell", "Hydrovale", "Sunpath",
    "Amperbay", "Clearstream", "Brightcurrent", "Nexgrid", "Riverbend", "Terraflow", "Kilowatt Bay",
    "Aquaterra", "Lumenfield", "Currentwise", "Gaspoint", "Powerhaven", "Watercrest",
)  # fmt: skip
TELCO_VENDORS = (
    "Linknet", "Voxline", "Wavecom", "Telenova", "Orbitel", "Netspan", "Signalpoint", "Fibrelink",
    "Cellora", "Pingtel", "Airwave", "Datastream", "Dialwise", "Meshcom", "Skyroute", "Tonewave",
    "Linkpoint", "Callbridge", "Hertzline", "Packetlane",
)  # fmt: skip
INSURERS = (
    "Coverline", "Safeharbour", "Guardwell", "Assurex", "Shieldpoint", "Trustanchor", "Covermark",
    "Securis", "Haven Mutual", "Unity Shield", "Bluecover", "Prudentia", "Ironclad", "Stalwart",
    "Keepwell", "Anchorsure", "Redoak Mutual", "Fortisbay", "Lifeboat", "Citadel Mutual",
)  # fmt: skip
BANKS = (
    "Mock Bank", "Harbourline Bank", "Eastgate Bank", "Crown Savings Bank", "Tidewater Bank",
    "Kestrel Bank", "Rivermouth Bank", "Summit Trust Bank", "Lionsgate Bank", "Orchard Mutual Bank",
    "Baycrest Bank", "Northquay Bank", "Pillar Bank", "Goldcoast Bank", "Westfield Bank",
    "Marlow Bank",
)  # fmt: skip
# brand words for counterparties: the generator's BRANDS plus more of ours
BRANDS_EXT = (
    "Alderbrook", "Brookfield", "Calloway", "Dunmore", "Eastwick", "Fairhaven", "Glenmoor",
    "Hartwell", "Ivybridge", "Jasperline", "Kingsmead", "Larkspur", "Millbrook", "Norwood",
    "Oakhurst", "Pemberton", "Quarrydale", "Ravenscroft", "Stonebridge", "Thornfield", "Upland",
    "Valemont", "Wexford", "Yewdale", "Zephyrcrest", "Amberlane", "Birchgrove", "Cobalt Ridge",
    "Dovecote", "Emberly", "Foxglove", "Greystone", "Hollowbrook", "Inglenook", "Jadeport",
    "Kelmscott", "Linden Park", "Mapleton", "Newbridge", "Orchard Row", "Primrose Hill",
    "Quayside", "Rosewood", "Sandalwood", "Tamarind", "Underhill", "Violet Bay", "Westerly",
)  # fmt: skip
COINED_A = ("Orbi", "Zephyr", "Quanta", "Novak", "Lumi", "Velo", "Axio", "Kryo", "Terra", "Mira",
            "Sorel", "Pyxis", "Danu", "Elara", "Fenwi", "Garnet", "Hexa", "Indri", "Jovi", "Kelo")  # fmt: skip
COINED_B = ("trade", "line", "vest", "kern", "dyne", "mark", "flux", "gen", "pact", "sphere",
            "nova", "tek", "ware", "lytic", "span", "quest", "vance", "bridge", "core", "mont")  # fmt: skip
COINED_SUFFIX = ("Global", "Group", "International", "Solutions", "Partners", "Worldwide")
C5_WEIGHTS = {
    "logistics": 0.13,
    "trading": 0.16,
    "manufacturing": 0.13,
    "professional_services": 0.12,
    "crypto_virtual_assets": 0.11,
    "gambling": 0.11,
    "money_services": 0.11,
    "other": 0.13,
}
RISKY = ("crypto_virtual_assets", "gambling", "money_services")
HARD_CP_SHARE = 0.10  # counterparties with no keyword at all (the generator's "hard rows")
HARD_C5 = ("trading", "logistics", "manufacturing", "other")

# --------------------------------------------------------------------------- abbreviations
# long form -> short forms seen on statements. Counterparty keyword abbreviations come first
# because ``C5_ANCHORS`` accepts exactly these.
C5_ABBREV: dict[str, tuple[str, ...]] = {
    "TRADING": ("TRDG", "TRD"),
    "IMPORTS": ("IMP", "IMPTS"),
    "EXPORTS": ("EXP", "EXPTS"),
    "SUPPLIES": ("SUPP", "SPLS"),
    "LOGISTICS": ("LOGIS", "LOGST"),
    "FREIGHT": ("FRT", "FRGHT"),
    "SHIPPING": ("SHPG",),
    "CARGO": ("CGO",),
    "HAULAGE": ("HLG",),
    "MANUFACTURING": ("MFG", "MFTG"),
    "INDUSTRIES": ("INDS", "IND"),
    "FABRICATION": ("FAB", "FABRN"),
    "CONSULTING": ("CONSLTG", "CSLTG"),
    "ADVISORY": ("ADVSRY",),
    "ACCOUNTING": ("ACCTG",),
    "LEGAL": ("LGL",),
    "DIGITAL ASSETS": ("DIGI ASSETS", "DIG ASSETS"),
    "BLOCKCHAIN": ("BLKCHN",),
    "CASINO": ("CSNO",),
    "GAMING": ("GMNG",),
    "LOTTERY": ("LOTT",),
    "MONEY TRANSFER": ("MONEY TRF", "MNY TRF"),
    "FOREX": ("FX",),
    "HOLDINGS": ("HLDGS",),
    "CAPITAL": ("CAP", "CPTL"),
    "VENTURES": ("VENT",),
    "PROPERTIES": ("PROP", "PPTYS"),
}
GENERAL_ABBREV: dict[str, tuple[str, ...]] = {
    "FUNDS TRANSFER": ("FT", "FUND TRF"),
    "PAYMENT": ("PMT", "PYMT", "PAYMT"),
    "TRANSFER": ("TRF", "TFR", "XFER"),
    "CONTRIBUTION": ("CONTRIB", "CONTRN"),
    "SERVICE": ("SVC", "SERV"),
    "CHARGE": ("CHG", "CHRG"),
    "INSTALMENT": ("INSTL", "INST"),
    "DISBURSEMENT": ("DISB", "DISBMT"),
    "INSURANCE": ("INS", "INSUR"),
    "PREMIUM": ("PREM", "PRM"),
    "BUILDING": ("BLDG", "BLDNG"),
    "UTILITIES": ("UTIL", "UTILS"),
    "TELECOM": ("TELCO", "TELE"),
    "ACCOUNT": ("ACCT", "A/C"),
    "DIRECTOR": ("DIR", "DIRCTR"),
    "REFUND": ("RFND", "RFD"),
    "INTEREST": ("INT", "INTR"),
    "CREDIT": ("CR", "CRDT"),
    "FROM": ("FR", "FRM"),
    "SALARY": ("SAL", "SLRY"),
    "PAYNOW": ("PNOW",),
    "PTE LTD": ("PTE", "P/L", "PTE L", "PTE LT"),
}
ABBREV = {**C5_ABBREV, **GENERAL_ABBREV}
_ABBREV_RE = [
    (re.compile(r"(?<![A-Z])" + re.escape(k) + r"(?![A-Z])"), v)
    for k, v in sorted(ABBREV.items(), key=lambda kv: -len(kv[0]))
]

# --------------------------------------------------------------- foreign-language wording
# Whole alternative narratives; slots as in SPECS. ms = Malay, zh = romanised Mandarin.
# {mon_ms} is a Malay month name, {mon_zh} like "7YUE".
FOREIGN: dict[str, tuple[tuple[str, str], ...]] = {
    "customer_receipt": (
        ("ms", "BAYARAN DARI {cp}"),
        ("ms", "TERIMAAN DARI {cp}"),
        ("zh", "SHOUKUAN {cp}"),
        ("zh", "{cp} HUOKUAN"),
    ),
    "supplier_payment": (
        ("ms", "BAYARAN KEPADA {cp}"),
        ("ms", "BAYARAN PEMBEKAL {cp}"),
        ("zh", "FUKUAN {cp}"),
        ("zh", "FUKUAN HUOKUAN {cp}"),
    ),
    "payroll": (
        ("ms", "GAJI STAF {mon_ms}"),
        ("ms", "BAYARAN GAJI"),
        ("zh", "{mon_zh} GONGZI"),
        ("zh", "GONGZI FUKUAN"),
    ),
    "statutory_contribution": (
        ("ms", "CARUMAN CPF {mon_ms}"),
        ("ms", "BAYARAN CARUMAN CPF"),
        ("zh", "CPF JIAONA GONGJIJIN"),
        ("zh", "{mon_zh} CPF JIAONA"),
    ),
    "tax_payment": (
        ("ms", "BAYARAN CUKAI GST IRAS"),
        ("ms", "CUKAI GST"),
        ("zh", "SHUIKUAN IRAS GST"),
        ("zh", "GST JIAOSHUI"),
    ),
    "premises": (
        ("ms", "SEWA {addr}"),
        ("ms", "CAJ PERKHIDMATAN BANGUNAN"),
        ("zh", "FANGZU {addr}"),
        ("zh", "WUYE FEI"),
    ),
    "utilities_telecom": (
        ("ms", "BIL ELEKTRIK {util}"),
        ("ms", "BIL TELEFON {telco}"),
        ("zh", "DIANFEI {util}"),
        ("zh", "DIANHUA FEI {telco}"),
    ),
    "loan_repayment": (
        ("ms", "BAYARAN BALIK PINJAMAN"),
        ("ms", "ANSURAN PINJAMAN"),
        ("zh", "HUANKUAN DAIKUAN"),
        ("zh", "DAIKUAN HUANKUAN"),
    ),
    "loan_disbursement": (
        ("ms", "PINJAMAN DITERIMA {bank}"),
        ("ms", "PENGELUARAN PINJAMAN {bank}"),
        ("zh", "DAIKUAN FANGKUAN {bank}"),
    ),
    "insurance": (
        ("ms", "PREMIUM INSURANS {insurer}"),
        ("ms", "BAYARAN INSURANS"),
        ("zh", "BAOXIAN FEI {insurer}"),
        ("zh", "BAOFEI {insurer}"),
    ),
    "bank_charges": (
        ("ms", "CAJ BANK"),
        ("ms", "CAJ PERKHIDMATAN AKAUN"),
        ("zh", "YINHANG SHOUXUFEI"),
        ("zh", "SHOUXUFEI"),
    ),
    "interest_income": (
        ("ms", "FAEDAH BANK"),
        ("ms", "KREDIT FAEDAH"),
        ("zh", "LIXI SHOURU"),
        ("zh", "YINHANG LIXI"),
    ),
    "owner_transfer": (
        ("ms", "PINJAMAN PENGARAH {director}"),
        ("ms", "PINJAMAN DARIPADA PENGARAH {director}"),
        ("zh", "DONGSHI JIEKUAN {director}"),
        ("zh", "{director} DONGSHI JIEKUAN"),
    ),
    "refund": (
        ("ms", "PULANGAN DARI {cp}"),
        ("ms", "PULANGAN BAYARAN {cp}"),
        ("zh", "TUIKUAN {cp}"),
        ("zh", "{cp} TUIKUAN"),
    ),
}
MONTHS_MS = ("JANUARI", "FEBRUARI", "MAC", "APRIL", "MEI", "JUN", "JULAI", "OGOS", "SEPTEMBER",
             "OKTOBER", "NOVEMBER", "DISEMBER")  # fmt: skip
CHANNEL_PREFIX = ("FAST", "GIRO", "IBG", "PAYNOW", "NETS")

# ------------------------------------------------------------------------------- evidence
# Lower-case words whose presence (exact, truncated to a prefix of at least 4 letters, or within
# one edit for words of 6+ letters) keeps a label derivable.
CATEGORY_ANCHORS: dict[str, tuple[str, ...]] = {
    "customer_receipt": (
        "from",
        "fr",
        "frm",
        "credit",
        "cr",
        "giro",
        "fast",
        "paynow",
        "pnow",
        "pmt",
        "payment",
        "trf",
        "transfer",
        "funds",
        "ft",
        "retainer",
        "dari",
        "terimaan",
        "shoukuan",
        "huokuan",
        "ibg",
        "nets",
    ),  # fmt: skip
    "supplier_payment": (
        "to",
        "payment",
        "pmt",
        "pymt",
        "giro",
        "fast",
        "funds",
        "transfer",
        "trf",
        "ft",
        "kepada",
        "pembekal",
        "fukuan",
        "huokuan",
        "ibg",
        "nets",
        "paynow",
    ),  # fmt: skip
    "payroll": ("salary", "sal", "slry", "payroll", "gaji", "gongzi"),
    "statutory_contribution": (
        "cpf",
        "contribution",
        "contrib",
        "contrn",
        "caruman",
        "gongjijin",
        "jiaona",
    ),  # fmt: skip
    "tax_payment": ("iras", "gst", "tax", "cukai", "shuikuan", "jiaoshui"),
    "premises": (
        "rent",
        "service",
        "svc",
        "serv",
        "building",
        "bldg",
        "charge",
        "chg",
        "chrg",
        "sewa",
        "fangzu",
        "wuye",
        "perkhidmatan",
        "bangunan",
    ),  # fmt: skip
    "utilities_telecom": (
        "utilities",
        "util",
        "utils",
        "telecom",
        "telco",
        "tele",
        "elektrik",
        "telefon",
        "dianfei",
        "dianhua",
        "bil",
    ),  # fmt: skip
    "loan_repayment": (
        "loan",
        "ln",
        "instalment",
        "instl",
        "inst",
        "pinjaman",
        "ansuran",
        "huankuan",
        "daikuan",
        "repayment",
    ),  # fmt: skip
    "loan_disbursement": (
        "loan",
        "ln",
        "disbursement",
        "disb",
        "disbmt",
        "pinjaman",
        "daikuan",
        "fangkuan",
        "diterima",
        "pengeluaran",
    ),  # fmt: skip
    "insurance": (
        "insurance",
        "ins",
        "insur",
        "premium",
        "prem",
        "prm",
        "insurans",
        "baoxian",
        "baofei",
    ),  # fmt: skip
    "bank_charges": ("charge", "chg", "chrg", "fee", "svc", "service", "caj", "shouxufei"),
    "interest_income": ("interest", "int", "intr", "faedah", "lixi"),
    "owner_transfer": ("director", "dir", "dirctr", "pengarah", "dongshi", "jiekuan"),
    "refund": ("refund", "rfnd", "rfd", "pulangan", "tuikuan"),
}
C5_ANCHORS: dict[str, tuple[str, ...]] = {
    "logistics": (
        "freight",
        "logistics",
        "shipping",
        "cargo",
        "haulage",
        "frt",
        "frght",
        "logis",
        "logst",
        "shpg",
        "cgo",
        "hlg",
    ),  # fmt: skip
    "trading": (
        "trading",
        "imports",
        "exports",
        "supplies",
        "trdg",
        "trd",
        "imp",
        "impts",
        "exp",
        "expts",
        "supp",
        "spls",
    ),  # fmt: skip
    "manufacturing": (
        "manufacturing",
        "industries",
        "fabrication",
        "mfg",
        "mftg",
        "inds",
        "ind",
        "fab",
        "fabrn",
    ),  # fmt: skip
    "professional_services": (
        "consulting",
        "advisory",
        "accounting",
        "legal",
        "conslt",
        "consltg",
        "csltg",
        "advsry",
        "acctg",
        "lgl",
    ),  # fmt: skip
    "crypto_virtual_assets": (
        "digital",
        "assets",
        "crypto",
        "coin",
        "token",
        "blockchain",
        "blkchn",
        "digi",
        "dig",
    ),  # fmt: skip
    "gambling": ("casino", "gaming", "bet", "lottery", "csno", "gmng", "lott"),
    "money_services": ("remit", "forex", "fx", "money", "mny", "rmt"),
    "other": (
        "holdings",
        "capital",
        "ventures",
        "properties",
        "hldgs",
        "cap",
        "cptl",
        "vent",
        "prop",
        "pptys",
    ),  # fmt: skip
}
_WORD = re.compile(r"[a-z0-9]+")


def _word_matches(word: str, anchor: str) -> bool:
    if word == anchor:
        return True
    if len(anchor) >= 6 and edit_distance(word, anchor) <= 1:
        return True
    # a statement field truncated mid-word still shows the start of the word
    return len(word) >= 4 and len(anchor) > len(word) and anchor.startswith(word)


def has_anchor(text: str, anchors: tuple[str, ...]) -> bool:
    words = _WORD.findall(text.casefold())
    return any(_word_matches(w, a) for w in words for a in anchors)


def category_evidence(category: str, text: str) -> bool:
    """The text still carries a word that identifies the category."""
    return has_anchor(text, CATEGORY_ANCHORS[category])


def c5_evidence(label: str, text: str) -> bool:
    """The text still carries a keyword of the counterparty's c5 label (or an abbreviation)."""
    return has_anchor(text, C5_ANCHORS[label])


# ------------------------------------------------------------------------- perturbations
_KEYBOARD = {
    "a": "qs", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg", "g": "fh", "h": "gj",
    "i": "uo", "j": "hk", "k": "jl", "l": "k", "m": "n", "n": "bm", "o": "ip", "p": "o",
    "q": "w", "r": "et", "s": "ad", "t": "ry", "u": "yi", "v": "cb", "w": "qe", "x": "zc",
    "y": "tu", "z": "x",
}  # fmt: skip


def op_abbrev(text: str, rng: random.Random, p: float = 0.7) -> str:
    for rx, shorts in _ABBREV_RE:
        if rng.random() < p:
            text = rx.sub(lambda _m, s=shorts: rng.choice(s), text, count=1)
    return text


def op_typo(text: str, rng: random.Random, n: int = 1) -> str:
    """Up to ``n`` slips inside distinct words of 5+ letters; the first letter is kept."""
    words = text.split(" ")
    cand = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    rng.shuffle(cand)
    for i in cand[:n]:
        w = words[i]
        j = rng.randrange(1, len(w) - 1)
        kind = rng.choice(("swap", "drop", "double", "neighbour"))
        if kind == "swap":
            w = w[:j] + w[j + 1] + w[j] + w[j + 2 :]
        elif kind == "drop":
            w = w[:j] + w[j + 1 :]
        elif kind == "double":
            w = w[:j] + w[j] + w[j:]
        else:
            near = _KEYBOARD.get(w[j].lower())
            if near:
                new = rng.choice(near)
                w = w[:j] + (new.upper() if w[j].isupper() else new) + w[j + 1 :]
        words[i] = w
    return " ".join(words)


def op_case(text: str, rng: random.Random) -> str:
    style = rng.choice(("lower", "title", "sentence", "upper"))
    if style == "lower":
        return text.lower()
    if style == "title":
        return text.title()
    if style == "sentence":
        return text.capitalize()
    return text.upper()


def op_truncate(text: str, rng: random.Random) -> str:
    """A fixed-width narrative field (statements cut at 20-35 characters)."""
    width = rng.randint(22, 36)
    return text[:width].rstrip() if len(text) > width else text


def op_spacing(text: str, rng: random.Random) -> str:
    style = rng.choice(("dash", "slash", "collapse", "double", "star"))
    if style == "dash":
        return text.replace(" ", "-")
    if style == "slash":
        return text.replace(" ", "/", 2)
    if style == "collapse":
        return re.sub(r"\s+", "", text, count=1) if " " in text else text
    if style == "double":
        return text.replace(" ", "  ", 2)
    return text.replace(" ", " * ", 1)


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def op_ref(text: str, rng: random.Random, when: date | None = None) -> str:
    d = when or date(2025, 7, 14)
    forms = (
        lambda: f"REF{_digits(rng, 8)}",
        lambda: f"/ROC/INV{_digits(rng, 4)}",
        lambda: f"TXN {_digits(rng, 8)}",
        lambda: f"ID{_digits(rng, 3)}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{_digits(rng, 2)}",
        lambda: f"{d:%d%m%y} {rng.randrange(8, 18):02d}{rng.randrange(60):02d}",
        lambda: f"FAST{_digits(rng, 12)}",
        lambda: f"BIZ-{_digits(rng, 5)}",
        lambda: "OTHR",
    )
    code = rng.choice(forms)()
    return f"{code} {text}" if rng.random() < 0.35 else f"{text} {code}"


def op_channel(text: str, rng: random.Random) -> str:
    """Drop the long channel wording that banks shorten (FAST PAYMENT TO -> FAST TO, ...)."""
    text = re.sub(r"\b(FAST|GIRO) PAYMENT\b", r"\1", text, count=1)
    return text


@dataclass(frozen=True)
class Messy:
    text: str
    ops: tuple[str, ...]
    langs: tuple[str, ...]
    level_applied: int


_LEVEL_PLAN = {
    # op -> probability, per level
    1: {
        "channel": 0.3,
        "abbrev": 0.5,
        "case": 0.4,
        "ref": 0.4,
        "spacing": 0.0,
        "typo": 0.0,
        "truncate": 0.0,
        "foreign": 0.0,
    },  # fmt: skip
    2: {
        "channel": 0.4,
        "abbrev": 0.8,
        "case": 0.4,
        "ref": 0.6,
        "spacing": 0.15,
        "typo": 0.35,
        "truncate": 0.35,
        "foreign": 0.25,
    },  # fmt: skip
    3: {
        "channel": 0.5,
        "abbrev": 0.95,
        "case": 0.5,
        "ref": 0.8,
        "spacing": 0.3,
        "typo": 0.7,
        "truncate": 0.6,
        "foreign": 0.6,
    },  # fmt: skip
}


def _foreign_text(
    category: str, slots: dict[str, str], rng: random.Random, clean: str = ""
) -> tuple[str, str] | None:
    """A Malay or romanised-Chinese wording of the same transaction: it must name the same things
    as the generator's text (a rent line stays a rent line, a telco bill a telco bill)."""
    used = {k for k, v in slots.items() if v and v in clean}
    options = [
        o
        for o in FOREIGN.get(category, ())
        if set(re.findall(r"\{(\w+)\}", o[1])) - {"mon_ms", "mon_zh"} == used
    ]
    if not options:
        return None
    lang, tpl = rng.choice(options)
    mon = rng.randrange(1, 13)
    fill = dict(slots)
    fill.setdefault("mon_ms", MONTHS_MS[mon - 1])
    fill.setdefault("mon_zh", f"{mon}YUE")
    for key in ("cp", "addr", "util", "telco", "bank", "insurer", "director"):
        fill.setdefault(key, "")
    text = re.sub(r"\s+", " ", tpl.format(**fill)).strip()
    if rng.random() < 0.4 and category in ("customer_receipt", "supplier_payment", "refund"):
        text = f"{rng.choice(('FAST', 'GIRO'))} {text}"  # English channel word, foreign verb
    return lang, text


def perturb(
    clean: str,
    category: str,
    slots: dict[str, str],
    rng: random.Random,
    level: int,
    *,
    c5: str | None = None,
    when: date | None = None,
) -> Messy:
    """Turn the generator's ``clean`` description into a messy one.

    The labels are not arguments and are never changed. The result must still carry category
    evidence and, when ``c5`` is given, evidence for it; after 14 failed attempts the strength is
    lowered one level (``level_applied`` says so) down to 0, which returns ``clean``.
    """
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    lvl = level
    while lvl > 0:
        plan = _LEVEL_PLAN[lvl]
        for _ in range(14):
            ops: list[str] = []
            langs: list[str] = []
            text = clean
            if rng.random() < plan["foreign"]:
                got = _foreign_text(category, slots, rng, clean)
                if got:
                    langs.append(got[0])
                    text = got[1]
                    ops.append("foreign:" + got[0])
            if "foreign:" not in " ".join(ops) and rng.random() < plan["channel"]:
                text = op_channel(text, rng)
                ops.append("channel")
            if rng.random() < plan["abbrev"]:
                text = op_abbrev(text, rng, 0.45 + 0.15 * lvl)
                ops.append("abbrev")
            if rng.random() < plan["typo"]:
                text = op_typo(text, rng, 1 if lvl < 3 else rng.choice((1, 2)))
                ops.append("typo")
            if rng.random() < plan["spacing"]:
                text = op_spacing(text, rng)
                ops.append("spacing")
            if rng.random() < plan["truncate"]:
                text = op_truncate(text, rng)
                ops.append("truncate")
            if rng.random() < plan["case"]:
                text = op_case(text, rng)
                ops.append("case")
            if rng.random() < plan["ref"]:
                text = op_ref(text, rng, when)
                ops.append("ref")
            if not ops or text.strip() == clean.strip():
                continue
            if not category_evidence(category, text):
                continue
            if c5 is not None and not c5_evidence(c5, text):
                continue
            return Messy(text.strip(), tuple(ops), tuple(langs), lvl)
        lvl -= 1
    return Messy(clean, (), (), 0)


# ---------------------------------------------------------------------- catalogue -> rows


def _counterparty(rng: random.Random, label: str, split: str, hard: bool, used: set[str]) -> str:
    """``BRAND TOKEN PTE LTD`` from brands of this split only."""
    brands = in_split((*lx.BRANDS, *BRANDS_EXT), split)
    a = in_split(COINED_A, split)
    b = in_split(COINED_B, split)
    for _ in range(300):
        if hard:
            brand = (rng.choice(a) + rng.choice(b)).capitalize()
            name = f"{brand} {rng.choice(COINED_SUFFIX)} Pte Ltd".upper()
        else:
            token = rng.choice(lx.C5_TOKENS[label])
            name = f"{rng.choice(brands)} {token} Pte Ltd".upper()
        if name not in used:
            used.add(name)
            return name
    raise RuntimeError("counterparty name space exhausted")


def _amount(spec: CatSpec, kind: str, rng: random.Random) -> str:
    if spec.category == "bank_charges":
        return s2(D("15.00" if kind == "ACCOUNT SERVICE CHARGE" else "0.35"))
    if spec.category == "interest_income":
        return s2(D(str(round(rng.uniform(0.8, 7.5), 2))))
    lo, hi = spec.amount
    return s2(D(str(round(rng.uniform(lo, hi), 2))))


def _row_date(rng: random.Random) -> date:
    return weekday_on_or_before(date(2025, 7, 1) + timedelta(days=rng.randrange(0, 184)))


def build_rows(split: str, seed: int | None = None, per_category: int = PER_CATEGORY) -> list[dict]:
    """The TXN set of one split: ``per_category`` rows per category (twice that for ``DOUBLED``), an
    equal share at each level.

    Row fields the model sees: ``date``, ``description``, ``debit``, ``credit``. Everything else is
    the answer key or a diagnostic (``truth``, ``clean_description``, ``level``, ``ops``...).
    """
    check_split(split)
    seed = SEEDS[split] if seed is None else seed
    rng = stream(seed, "txn", split)
    utils = in_split(UTIL_VENDORS, split)
    telcos = in_split(TELCO_VENDORS, split)
    insurers = in_split(INSURERS, split)
    banks = in_split(BANKS, split)
    used_cp: set[str] = set()
    rows: list[dict] = []
    for cat in CATEGORIES:
        spec = SPECS[cat]
        for i in range(per_category * 2 if cat in DOUBLED else per_category):
            level = LEVELS[i % len(LEVELS)]
            tpl = rng.choice(spec.templates)
            slots: dict[str, str] = {}
            cp: str | None = None
            c5 = "other"
            hard = False
            if cat == "utilities_telecom":  # both are filled so a foreign wording can use either
                slots["util"] = rng.choice(utils).upper()
                slots["telco"] = rng.choice(telcos).upper()
            if "{insurer}" in tpl:
                slots["insurer"] = rng.choice(insurers).upper()
            if "{bank}" in tpl:
                slots["bank"] = rng.choice(banks).upper()
            if "{addr}" in tpl:
                n = rng.randrange(1, 90)
                slots["addr"] = f"{n} {rng.choice(nd.STREET_WORDS)}".upper()[:28]
            if spec.party in ("customer", "supplier"):
                c5 = pick_weighted(rng, C5_WEIGHTS)
                hard = c5 not in RISKY and rng.random() < HARD_CP_SHARE
                if hard:
                    c5 = rng.choice(HARD_C5)
                cp = _counterparty(rng, c5, split, hard, used_cp)
                slots["cp"] = cp
            elif spec.party == "director":
                group = rng.choice(nd.NAME_GROUPS)
                person = nd.make_name_ext(rng, group, rng.choice("MF"), nd_split(split))
                cp = nd.registry_form(person, group, 0)
                slots["director"] = cp
            clean = tpl.format(**{k: slots.get(k, "") for k in slots})
            if spec.category == "customer_receipt" and "RETAINER" not in tpl and rng.random() < 0.5:
                clean += f" INV-2025-{rng.randrange(1, 400):04d}"
            when = _row_date(rng)
            messy = perturb(
                clean,
                cat,
                slots,
                rng,
                level,
                c5=None if hard or spec.party not in ("customer", "supplier") else c5,
                when=when,
            )
            amount = _amount(spec, tpl, rng)
            rows.append(
                {
                    "date": when.isoformat(),
                    "description": messy.text,
                    "debit": amount if spec.direction == "debit" else None,
                    "credit": amount if spec.direction == "credit" else None,
                    "truth": {
                        "category": cat,
                        "direction": spec.direction,
                        "c4": spec.c4,
                        "c5": c5,
                        "counterparty": cp if spec.party in ("customer", "supplier") else None,
                    },
                    "clean_description": clean,
                    "level": level,
                    "level_applied": messy.level_applied,
                    "ops": list(messy.ops),
                    "langs": list(messy.langs),
                    "hard_counterparty": hard,
                    "evidence_ok": category_evidence(cat, messy.text)
                    and (
                        hard
                        or spec.party not in ("customer", "supplier")
                        or c5_evidence(c5, messy.text)
                    ),
                }
            )
    rng.shuffle(rows)
    for n, row in enumerate(rows):
        row["row_id"] = f"txn-{split}-{n:04d}"
        row["split"] = split
    return rows


def generator_category(description: str) -> str | None:
    """Category of a description exactly as the generator prints it (used by the sync test).

    The narrow templates are tried first: ``FUNDS TRANSFER FROM {director} DIRECTOR LOAN`` also fits
    the customer template ``FUNDS TRANSFER FROM {cp}``. The balance-brought-forward line of a
    statement is not a transaction and has no category."""
    d = description.strip()
    order = [c for c in SPECS if c not in ("customer_receipt", "supplier_payment")]
    for cat in (*order, "customer_receipt", "supplier_payment"):
        for tpl in SPECS[cat].templates:
            pattern = re.escape(tpl)
            pattern = re.sub(r"\\\{\w+\\\}", ".+", pattern)
            if re.fullmatch(pattern + r"( INV-[\w-]+)?", d):
                return cat
    return None


def count_by(rows: list[dict[str, Any]], *keys: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = "/".join(str(r["truth"][key] if key in r["truth"] else r[key]) for key in keys)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))
