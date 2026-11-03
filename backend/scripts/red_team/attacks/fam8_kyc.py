"""Family 8: company, ownership and identity documents (KYC and AML bundles)."""

from __future__ import annotations

import random
import re
from decimal import Decimal

from ..core import Bundle, Outcome, attack
from ..pdfkit import PdfEdit
from ._util import sub_text_everywhere, valid_uen

F = "F8 company, ownership and identity"


def _no_registry(b: Bundle, rng: random.Random) -> None:
    b.registry_path = None


def _swap_all(
    b: Bundle, pairs: list[tuple[str, str]], only: tuple[str, ...] | None = None
) -> list[str]:
    touched = []
    for n, data in list(b.files.items()):
        if only and not n.startswith(only):
            continue
        new = data
        for old, repl in pairs:
            new = sub_text_everywhere(new, old, repl)
        if new != data:
            b.replace(n, new)
            touched.append(n)
    return touched


@attack(
    "R80",
    F,
    "Invented shell company: name and checksum-valid UEN replaced consistently in every document of the bundle",
    scenario="kyc",
)
def r80(b: Bundle, rng: random.Random) -> Outcome:
    uen = valid_uen(rng, 2017)
    touched = _swap_all(
        b,
        [
            ("Sembrook Freight Pte. Ltd.", "Harlow Marine Logistics Pte. Ltd."),
            ("SEMBROOK FREIGHT PTE. LTD.", "HARLOW MARINE LOGISTICS PTE. LTD."),
            ("201681302M", uen),
        ],
    )
    return Outcome(
        "Same people, same figures, but a company that has no record anywhere; every document agrees with every other.",
        touched,
    )


@attack(
    "R81",
    F,
    "As R80 but the applicant is also given a registry-free bundle (no registry file at all)",
    scenario="kyc",
    control=_no_registry,
)
def r81(b: Bundle, rng: random.Random) -> Outcome:
    b.registry_path = None
    return r80(b, rng)


@attack(
    "R82",
    F,
    "Ownership split below the UBO threshold: Kestrel 25.00% -> 24.00%, Tan 45.00% -> 46.00% in chart, register and profile",
    scenario="kyc",
)
def r82(b: Bundle, rng: random.Random) -> Outcome:
    touched = _swap_all(
        b,
        [
            ("25.00%", "24.00%"),
            ("45.00%", "46.00%"),
            ("125,000", "120,000"),
            ("225,000", "230,000"),
        ],
        only=("ownership_chart", "shareholder_register", "company_profile"),
    )
    return Outcome(
        "A beneficial owner is moved from 25% to 24% so that nobody above the threshold has to be named; all three ownership documents agree.",
        touched,
    )


@attack(
    "R83",
    F,
    "As R82 without the company registry reference (no outside source to disagree)",
    scenario="kyc",
    control=_no_registry,
)
def r83(b: Bundle, rng: random.Random) -> Outcome:
    b.registry_path = None
    return r82(b, rng)


def _icao_digit(s: str) -> str:
    w = (7, 3, 1)
    tot = 0
    for i, ch in enumerate(s):
        v = int(ch) if ch.isdigit() else (0 if ch == "<" else ord(ch) - 55)
        tot += v * w[i % 3]
    return str(tot % 10)


@attack(
    "R84",
    F,
    "Passport: expiry extended by two years and every MRZ check digit recomputed",
    scenario="kyc",
    consistent=True,
)
def r84(b: Bundle, rng: random.Random) -> Outcome:
    n = "passport_1.pdf"
    e = PdfEdit(b.files[n])
    items = e.items(0)
    line2 = [i for i in items if re.match(r"^[A-Z0-9<]{9}\d[A-Z]{3}\d{6}", i.text)][0]
    t = line2.text
    num, nat, dob, sex, exp = t[0:9], t[10:13], t[13:19], t[20], t[21:27]
    new_exp = exp[:2].replace(exp[:2], f"{int(exp[:2]) + 2:02d}") + exp[2:]
    pers = t[28:42]
    body = (
        num
        + _icao_digit(num)
        + nat
        + dob
        + _icao_digit(dob)
        + sex
        + new_exp
        + _icao_digit(new_exp)
        + pers
        + _icao_digit(pers)
    )
    comp = _icao_digit(
        num
        + _icao_digit(num)
        + dob
        + _icao_digit(dob)
        + new_exp
        + _icao_digit(new_exp)
        + pers
        + _icao_digit(pers)
    )
    new = body + comp
    e.set_text(line2, new, align="left")
    for it in items:
        if it.text == "28 May 2031":
            e.set_text(it, "28 May 2033", align="left")
    b.replace(n, e.dump())
    return Outcome("A longer-lived passport; the machine-readable zone is internally perfect.", [n])


@attack(
    "R85",
    F,
    "Director's ID number replaced by another checksum-valid NRIC on the ID card, profile, chart and register",
    scenario="kyc",
)
def r85(b: Bundle, rng: random.Random) -> Outcome:
    from stdnum import sg  # noqa: F401

    def nric(prefix: str, digits: str) -> str:
        w = (2, 7, 6, 5, 4, 3, 2)
        tot = sum(int(d) * k for d, k in zip(digits, w, strict=False))
        if prefix in "TG":
            tot += 4
        table = "JZIHGFEDCBA" if prefix in "ST" else "XWUTRQPNMLK"
        return prefix + digits + table[tot % 11]

    new = nric("S", "1102038")
    touched = _swap_all(b, [("S1102037B", new)])
    return Outcome(
        "A different but valid identity number for the same named director in every document that mentions it.",
        touched,
    )


@attack(
    "R86",
    F,
    "Incorporation date moved from 18 Jun 2016 to 18 Jun 2012 on certificate and profile",
    scenario="kyc",
)
def r86(b: Bundle, rng: random.Random) -> Outcome:
    touched = _swap_all(
        b,
        [("18 Jun 2016", "18 Jun 2012"), ("18 June 2016", "18 June 2012")],
        only=("certificate_of_incorporation", "company_profile"),
    )
    return Outcome(
        "An older company looks more established; the two documents that state the date agree.",
        touched,
    )


@attack(
    "R87",
    F,
    "Boomerang funds: two fee rows turned into a S$60,000 credit from a customer and a S$59,950 debit back to it two days later",
    scenario="loan",
)
def r87(b: Bundle, rng: random.Random) -> Outcome:
    from ._util import stmts

    ss = stmts(b)
    s = ss.stmts[2]
    fees = [r for r in s.rows if r.kind == "wd" and r.amount < Decimal("1")]
    a_row, b_row = fees[0], next(r for r in fees if r.date.text != fees[0].date.text)
    s.retarget(a_row, "FAST PAYMENT FROM ZEPHYRDYNE GLOBAL PTE LTD", "dep", Decimal("60000"))
    s.retarget(b_row, "FAST PAYMENT TO ZEPHYRDYNE GLOBAL PTE LTD", "wd", Decimal("59950"))
    ss.recompute_from(2)
    ss.write_back(b.files)
    return Outcome(
        "Lend-and-return through one counterparty inflates turnover by S$60,000 and leaves the month-end balance almost unchanged.",
        [x.name for x in ss.stmts[2:]],
    )


@attack(
    "R88",
    F,
    "Boomerang through one counterparty in an AML case (credit S$60,000 then debit S$59,950 two days later)",
    scenario="aml",
)
def r88(b: Bundle, rng: random.Random) -> Outcome:
    from ._util import stmts

    ss = stmts(b)
    s = ss.stmts[2]
    fees = [r for r in s.rows if r.kind == "wd" and r.amount < Decimal("1")]
    a_row = fees[0]
    b_row = next(r for r in fees if r.date.text != fees[0].date.text)
    s.retarget(a_row, "FAST PAYMENT FROM ZEPHYRDYNE GLOBAL PTE LTD", "dep", Decimal("60000"))
    s.retarget(b_row, "FAST PAYMENT TO ZEPHYRDYNE GLOBAL PTE LTD", "wd", Decimal("59950"))
    ss.recompute_from(2)
    ss.write_back(b.files)
    return Outcome(
        "Same round trip, in the use case that has a pass-through rule.",
        [x.name for x in ss.stmts[2:]],
    )


def _sof(b: Bundle, factor: Decimal) -> Outcome:
    n = "source_of_funds_declaration.pdf"
    e = PdfEdit(b.files[n])
    it = [i for i in e.items(0) if i.text.startswith("S$ ")][0]
    cur = Decimal(it.text[3:].replace(",", ""))
    e.set_text(it, f"S$ {cur * factor:,.2f}", align="left")
    b.replace(n, e.dump())
    return Outcome(
        f"Declared amount x{factor}; the invoice, window and counterparty are unchanged.", [n]
    )


@attack(
    "R89",
    F,
    "Source-of-funds declaration amount raised 30% over what the named counterparty paid",
    scenario="aml",
)
def r89(b: Bundle, rng: random.Random) -> Outcome:
    return _sof(b, Decimal("1.30"))


@attack("R89b", F, "Source-of-funds declaration amount doubled", scenario="aml")
def r89b(b: Bundle, rng: random.Random) -> Outcome:
    return _sof(b, Decimal("2.00"))
