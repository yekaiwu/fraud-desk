"""Counterparty lexicon (docs 4.1): the generator's ground truth for C5 and the vocabulary the
rules classifier is expected to cover (Contract request 5)."""

from __future__ import annotations

import random
import re

C5_TOKENS: dict[str, tuple[str, ...]] = {
    "logistics": ("Freight", "Logistics", "Shipping", "Cargo", "Haulage"),
    "trading": ("Trading", "Imports", "Exports", "Supplies"),
    "manufacturing": ("Manufacturing", "Industries", "Fabrication"),
    "professional_services": ("Consulting", "Advisory", "Accounting", "Legal"),
    "crypto_virtual_assets": ("Digital Assets", "Crypto", "Coin", "Token", "Blockchain"),
    "gambling": ("Casino", "Gaming", "Bet", "Lottery"),
    "money_services": ("Remit", "Money Transfer", "Forex"),
    "other": ("Holdings", "Capital", "Ventures", "Properties"),
}
C5_LABELS: tuple[str, ...] = tuple(C5_TOKENS)

BRANDS = (
    "Acme", "Beacon", "Cedar", "Delta", "Evergreen", "Falcon", "Granite", "Harbour", "Iris",
    "Juniper", "Keystone", "Lumen", "Maple", "Nimbus", "Onyx", "Pinnacle", "Quill", "Redwood",
    "Silverline", "Summit", "Tidewater", "Umber", "Vantage", "Willow", "Yarrow", "Zenith",
    "Crestview", "Pacific Anchor", "Northgate", "Eastbrook", "Starling", "Bluewater", "Ironbark",
    "Goldleaf", "Windmere", "Ashford", "Brightwell", "Clearpoint", "Driftwood", "Elmstead",
)  # fmt: skip

# coined brands with no lexicon token (hard rows, ~10%)
_SYL_A = ("Orbi", "Zephyr", "Quanta", "Novak", "Lumi", "Velo", "Axio", "Kryo", "Terra", "Mira")
_SYL_B = ("trade", "line", "vest", "kern", "dyne", "mark", "flux", "gen", "pact", "sphere")
_SUFFIX = ("Global", "Group", "International", "Solutions", "Partners", "Worldwide")

_TOKEN_RE = {
    label: re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(t) for t in toks) + r")(?![A-Za-z])", re.I
    )
    for label, toks in C5_TOKENS.items()
}
_ALL_TOKENS = [t.lower() for toks in C5_TOKENS.values() for t in toks]


def label_from_tokens(name: str) -> str | None:
    """C5 label derivable from a keyword in ``name`` (priority: risky labels first), else None."""
    for label in (
        "crypto_virtual_assets",
        "gambling",
        "money_services",
        "logistics",
        "manufacturing",
        "professional_services",
        "trading",
        "other",
    ):
        if _TOKEN_RE[label].search(name):
            return label
    return None


def has_token(name: str) -> bool:
    return label_from_tokens(name) is not None


def _legal(rng: random.Random) -> str:
    return "PTE LTD"


def make_counterparty(rng: random.Random, label: str, used: set[str]) -> str:
    """``BRAND TOKEN PTE LTD`` (upper case, as printed on statements), unique within ``used``."""
    for _ in range(200):
        brand = BRANDS[rng.randrange(len(BRANDS))]
        token = C5_TOKENS[label][rng.randrange(len(C5_TOKENS[label]))]
        name = f"{brand} {token} Pte Ltd".upper()
        if name not in used:
            used.add(name)
            return name
    raise RuntimeError("counterparty name space exhausted")


def make_hard_counterparty(rng: random.Random, used: set[str]) -> str:
    """A fictitious brand carrying no lexicon token (substring-safe)."""
    for _ in range(200):
        core = rng.choice(_SYL_A) + rng.choice(_SYL_B)
        name = f"{core} {rng.choice(_SUFFIX)} Pte Ltd".upper()
        if name in used or any(tok in name.lower() for tok in _ALL_TOKENS):
            continue
        used.add(name)
        return name
    raise RuntimeError("hard counterparty name space exhausted")


def proper(name: str) -> str:
    """``ACME TRADING PTE LTD`` -> ``Acme Trading Pte. Ltd.`` (invoice and contract form)."""
    words = [w.capitalize() for w in name.split()]
    if len(words) >= 2 and words[-2:] == ["Pte", "Ltd"]:
        words[-2:] = ["Pte.", "Ltd."]
    return " ".join(words)
