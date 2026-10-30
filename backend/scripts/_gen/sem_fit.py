"""FIT data: semantic tampers injected into otherwise clean bundles, with exact labels (docs 9.3).

Three families, each with tampered cases, matched clean controls and ordinary clean cases:

* ``goods_fit``      invoice goods from the wrong industry for the declared business.
* ``income_fit``     the occupation and the amount in a source-of-funds declaration do not fit.
* ``coherence_fit``  company profile, invoices and statement counterparties do not describe one
                     business.

A *base* is a coherent set of extracted documents (company profile, three invoices, one bank
statement, and for ``income_fit`` a source-of-funds declaration) built from a hand-written
industry catalogue, with arithmetic that is exactly right (totals, GST, running balance), so the
only thing wrong in a tampered case is the injected thing. Each base yields up to three cases:

* ``tampered``       the base plus one semantic tamper, with the exact evidence a correct claim
                     would cite (document, field or row, quoted value);
* ``unusual_legit``  the same base plus an unusual but legitimate twist (a delivery line, a
                     sideline, a general-trade profile, a one-off purchase, a high-pay
                     occupation...). It is matched to the tampered case and must NOT be flagged;
* ``ordinary``       the base alone (the first N_ORDINARY bases).

Industries and occupations are split by hand between ``dev`` and ``held_out``; a held-out case only
ever uses held-out ones, including the wrong-industry donor. The documents have the shape of
``ground_truth.json`` documents (fields and tables of ``{value, raw}``), so they load without any
rendering or OCR. No model is called anywhere; the set is ours and an upper bound.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from . import ids as idm
from . import names_data as nd
from .sem_common import (
    SEEDS,
    D,
    check_split,
    fmt_amount,
    fmt_date,
    in_split,
    nd_split,
    q2,
    s2,
    stream,
)
from .sem_entity import BRAND_WORDS, PREFIX_GROUPS
from .sem_industry import (
    GENERIC_LINES,
    MODEST_MAX,
    Industry,
    Occupation,
    industries_of,
    occupations_of,
)
from .sem_txn import TELCO_VENDORS, UTIL_VENDORS
from .world import DESC_IN, DESC_OUT

FAMILIES = ("goods_fit", "income_fit", "coherence_fit")
SHORT = {"goods_fit": "goods", "income_fit": "income", "coherence_fit": "coher"}
KIND_OF = {
    "goods_fit": "goods_industry_mismatch",
    "income_fit": "income_occupation_implausible",
    "coherence_fit": "activity_incoherence",
}
N_BASES = 24  # per family and split: 24 tampered + 24 unusual_legit
N_ORDINARY = 16  # the first 16 bases also yield an ordinary clean case (two per industry)
DIFFICULTIES = ("easy", "medium", "hard")
SECTOR = {  # broad sector, used for how far apart two industries are
    "electronics_wholesale": "goods", "construction_materials": "goods", "seafood_trading": "goods",
    "garment_trading": "goods", "medical_supplies": "goods", "printing_packaging": "goods",
    "auto_parts": "goods", "it_services": "services", "furniture_manufacturing": "goods",
    "chemical_trading": "goods", "jewellery_trading": "goods", "marine_engineering": "services",
    "freight_logistics": "services", "food_catering": "services", "cleaning_services": "services",
    "education_training": "services",
}  # fmt: skip
GENERAL_ACTIVITY = ("General wholesale trade", "46900")
MONTH = (2025, 9)
TODAY = date(2025, 10, 6)

# Field and column order of the four document types used here; the test suite checks them
# against ``app.core.schemas.DOC_SCHEMAS`` so a schema change cannot go unnoticed.
PROFILE_FIELDS = (
    "company_name", "uen", "entity_type", "status", "incorporation_date", "registered_address",
    "principal_activity", "ssic_code", "paid_up_capital", "currency", "retrieved_date",
)  # fmt: skip
INVOICE_FIELDS = (
    "invoice_number", "invoice_date", "due_date", "supplier_name", "supplier_uen", "supplier_gst_no",
    "buyer_name", "buyer_uen", "currency", "subtotal", "gst_rate", "gst_amount", "total",
    "po_reference", "contract_reference", "payment_status", "paid_date",
)  # fmt: skip
STATEMENT_FIELDS = (
    "bank_name", "account_holder", "uen", "account_number", "period_start", "period_end",
    "opening_balance", "closing_balance", "currency", "page_x_of_y", "total_debits", "total_credits",
)  # fmt: skip
SOF_FIELDS = (
    "declarant_name", "company_name", "uen", "declaration_date", "declared_amount", "currency",
    "origin_category", "origin_description", "counterparty_name", "invoice_reference",
    "period_start", "period_end", "receiving_account",
)  # fmt: skip
TABLES = {
    "company_profile": {
        "directors": ("director_name", "id_number", "nationality", "position", "appointment_date"),
        "shareholders": ("holder_name", "id_or_uen", "shares", "percent"),
    },
    "invoice": {"lines": ("description", "quantity", "unit_price", "gst_amount", "amount")},
    "bank_statement": {
        "transactions": ("date", "description", "debit", "credit", "balance"),
        "page_chain": ("page", "page_x", "page_y", "account_number"),
    },
    "source_of_funds_declaration": {},
}
FIELDS = {
    "company_profile": PROFILE_FIELDS,
    "invoice": INVOICE_FIELDS,
    "bank_statement": STATEMENT_FIELDS,
    "source_of_funds_declaration": SOF_FIELDS,
}

# ------------------------------------------------------------------------------ document form


def cell(value: str | None, raw: str | None = None) -> dict[str, str | None]:
    return {"value": value, "raw": (value if raw is None else raw) if value is not None else None}


def money_cell(v: Decimal) -> dict[str, str | None]:
    return cell(s2(v), fmt_amount(v))


def date_cell(d: date) -> dict[str, str | None]:
    return cell(d.isoformat(), fmt_date(d))


def make_document(
    key: str,
    doc_type: str,
    fields: dict[str, dict[str, str | None]],
    tables: dict[str, list[dict[str, dict[str, str | None]]]] | None = None,
) -> dict[str, Any]:
    """A document in the ``ground_truth.json`` shape; every schema field and table is present."""
    unknown = set(fields) - set(FIELDS[doc_type])
    if unknown:
        raise KeyError(f"unknown field(s) for {doc_type}: {sorted(unknown)}")
    out_fields = {f: fields.get(f, cell(None)) for f in FIELDS[doc_type]}
    out_tables: dict[str, Any] = {}
    for name, cols in TABLES[doc_type].items():
        rows = (tables or {}).get(name, [])
        out_tables[name] = {
            "columns": list(cols),
            "rows": [
                {"index": i, "cells": {c: r.get(c, cell(None)) for c in cols}}
                for i, r in enumerate(rows)
            ],
        }
    return {"key": key, "doc_type": doc_type, "fields": out_fields, "tables": out_tables}


# ------------------------------------------------------------------------------ plan objects


@dataclass
class Party:
    name: str  # printed, "Crestview Digital Mart Pte. Ltd."
    uen: str

    @property
    def upper(self) -> str:
        return self.name.upper().replace("PTE. LTD.", "PTE LTD")


@dataclass
class Line:
    desc: str
    qty: int
    price: Decimal

    @property
    def amount(self) -> Decimal:
        return q2(D(self.qty) * self.price)


@dataclass
class Inv:
    no: str
    day: date
    buyer: Party
    lines: list[Line]
    po: str
    paid: date

    @property
    def subtotal(self) -> Decimal:
        return q2(sum((ln.amount for ln in self.lines), D(0)))

    @property
    def gst(self) -> Decimal:
        return q2(self.subtotal * D("9.00") / D(100))

    @property
    def total(self) -> Decimal:
        return q2(self.subtotal + self.gst)


@dataclass
class Ev:
    day: date
    desc: str
    debit: Decimal | None = None
    credit: Decimal | None = None
    kind: str = "misc"
    cp: str | None = None


@dataclass
class Person:
    name: str
    nric: str
    appointed: date


@dataclass
class Plan:
    split: str
    industry: Industry
    name: str
    uen: str
    gst_no: str
    address: str
    incorporated: date
    capital: Decimal
    account: str
    activity: str
    ssic: str
    people: list[Person]
    customers: list[Party]
    suppliers: list[Party]
    invoices: list[Inv]
    events: list[Ev]
    sof: dict[str, Any] | None = None
    occupation: Occupation | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def upper(self) -> str:
        return self.name.upper().replace("PTE. LTD.", "PTE LTD")


# --------------------------------------------------------------------------------- builders


def _brands(split: str) -> tuple[str, ...]:
    words = tuple(w for g in PREFIX_GROUPS for w in g) + BRAND_WORDS
    return in_split(words, split)


def _party(rng: random.Random, brand: str, words: str) -> Party:
    return Party(f"{brand} {words} Pte. Ltd.", idm.random_uen(rng, rng.randint(2004, 2021)))


def _lines(
    rng: random.Random,
    items: tuple[tuple[str, int, int], ...],
    n: int,
) -> list[Line]:
    chosen = rng.sample(items, min(n, len(items)))
    out = []
    for desc, lo, hi in chosen:
        price = q2(D(str(round(rng.uniform(lo, hi), 2))))
        target = rng.uniform(250, 3200)
        qty = max(1, min(400, round(target / max(float(price), 0.5))))
        out.append(Line(desc, qty, price))
    return out


def build_base(split: str, seed: int, family: str, index: int) -> Plan:
    """The coherent, clean base of one case group. Pure function of its arguments."""
    check_split(split)
    rng = stream(seed, "fit", family, index, "base")
    pool = industries_of(split)
    ind = pool[index % len(pool)]  # every industry gets the same number of bases
    brands = list(_brands(split))
    rng.shuffle(brands)
    name = f"{brands[0]} {ind.name_words} Pte. Ltd."
    inc = date(rng.randint(2016, 2021), rng.randint(1, 12), rng.randint(1, 28))
    people = []
    for k in range(2):
        # The first director is the declarant of the income family. A short or one-word name is
        # matched poorly by the deterministic ``x_sof_support`` (which reads the statement text
        # without row annotations here), so the declarant gets a name of 15+ characters whose last word has 5+.
        while True:
            group = rng.choice(
                [g for g in nd.NAME_GROUPS if g != "mononym"] if k == 0 else nd.NAME_GROUPS
            )
            pname = nd.make_name_ext(rng, group, rng.choice("MF"), nd_split(split))
            if k > 0 or (len(pname) >= 15 and len(pname.split()[-1]) >= 5):
                break
        people.append(
            Person(
                pname,
                idm.random_nric(rng, rng.randint(1965, 1990)),
                inc + timedelta(days=rng.randint(0, 60)),
            )
        )
    customers = [_party(rng, brands[1 + k], f"{rng.choice(ind.customer_words)}") for k in range(6)]
    suppliers = [_party(rng, brands[7 + k], rng.choice(ind.supplier_words)) for k in range(4)]
    invoices = []
    for k in range(3):
        day = date(2025, 8, 4) + timedelta(days=7 * k + rng.randint(0, 4))
        invoices.append(
            Inv(
                no=f"INV-2025-{101 + k:04d}",
                day=day,
                buyer=customers[k],
                lines=_lines(rng, ind.sells, rng.choice((4, 5))),
                po=f"PO-{rng.randint(1000, 9999)}",
                paid=date(2025, 9, 3) + timedelta(days=7 * k + rng.randint(0, 3)),
            )
        )
    plan = Plan(
        split=split,
        industry=ind,
        name=name,
        uen=idm.random_uen(rng, inc.year),
        gst_no=idm.random_gst_no(rng),
        address=nd.address(rng),
        incorporated=inc,
        capital=D(rng.choice((100000, 200000, 300000, 500000))),
        account="".join(str(rng.randrange(10)) for _ in range(10)),
        activity=rng.choice(ind.activity),
        ssic=ind.ssic,
        people=people,
        customers=customers,
        suppliers=suppliers,
        invoices=invoices,
        events=[],
    )
    plan.events = _base_events(rng, plan)
    return plan


def _wd(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _base_events(rng: random.Random, plan: Plan) -> list[Ev]:
    """The September statement's events other than invoice payments (those follow the invoices)."""
    ind, split = plan.industry, plan.split
    ev: list[Ev] = []
    y, m = MONTH

    def day(lo: int, hi: int) -> date:
        return _wd(date(y, m, rng.randint(lo, min(hi, 30))))

    for cust in plan.customers[3:6]:
        tpl = rng.choice(DESC_IN)
        ev.append(
            Ev(
                day(2, 26),
                tpl.format(cp=cust.upper),
                credit=q2(D(str(rng.uniform(1800, 9500)))),
                kind="other_cust",
                cp=cust.upper,
            )  # fmt: skip
        )
    for k in range(5):
        sup = plan.suppliers[k % len(plan.suppliers)]
        tpl = rng.choice(DESC_OUT).format(cp=sup.upper)
        if rng.random() < 0.5:
            tpl += " " + rng.choice(ind.memos)
        ev.append(
            Ev(
                day(2, 27),
                tpl,
                debit=q2(D(str(rng.uniform(1500, 12000)))),
                kind="supplier",
                cp=sup.upper,
            )  # fmt: skip
        )
    util = rng.choice(in_split(UTIL_VENDORS, split))
    telco = rng.choice(in_split(TELCO_VENDORS, split))
    ev += [
        Ev(
            _wd(date(y, m, 25)),
            "GIRO SALARY PAYMENT",
            debit=q2(D(str(rng.uniform(8000, 14000)))),
            kind="payroll",
        ),  # fmt: skip
        Ev(
            _wd(date(y, m, 14)),
            "CPF CONTRIBUTION",
            debit=q2(D(str(rng.uniform(1600, 2800)))),
            kind="cpf",
        ),  # fmt: skip
        Ev(
            _wd(date(y, m, 1)),
            f"RENT {plan.address.split(',')[0][:28].upper()}",
            debit=D(rng.choice((2800, 3200, 3600, 4200, 4800))) + D("0.00"),
            kind="rent",
        ),  # fmt: skip
        Ev(_wd(date(y, m, 15)), "BUILDING SERVICE CHARGE", debit=D("380.00"), kind="service"),
        Ev(
            _wd(date(y, m, 8)),
            f"{util.upper()} UTILITIES",
            debit=q2(D(str(rng.uniform(260, 900)))),
            kind="utility",
        ),  # fmt: skip
        Ev(
            _wd(date(y, m, 19)),
            f"{telco.upper()} TELECOM",
            debit=q2(D(str(rng.uniform(180, 420)))),
            kind="telco",
        ),  # fmt: skip
    ]
    return ev


# ---------------------------------------------------------------------------------- rendering


def _statement_rows(plan: Plan) -> list[Ev]:
    y, m = MONTH
    rows = list(plan.events)
    for inv in plan.invoices:
        if (inv.paid.year, inv.paid.month) == (y, m):
            rows.append(
                Ev(
                    inv.paid,
                    f"{DESC_IN[0].format(cp=inv.buyer.upper)} {inv.no}",
                    credit=inv.total,
                    kind="trade",
                    cp=inv.buyer.upper,
                )  # fmt: skip
            )
    rows.sort(key=lambda e: (e.day, 0 if e.credit else 1, e.desc))
    return rows


def render_profile(plan: Plan) -> dict[str, Any]:
    f = {
        "company_name": cell(plan.name),
        "uen": cell(plan.uen),
        "entity_type": cell("Private Company Limited by Shares"),
        "status": cell("Live"),
        "incorporation_date": date_cell(plan.incorporated),
        "registered_address": cell(plan.address),
        "principal_activity": cell(plan.activity),
        "ssic_code": cell(plan.ssic),
        "paid_up_capital": money_cell(plan.capital),
        "currency": cell("SGD"),
        "retrieved_date": date_cell(TODAY),
    }
    directors = [
        {
            "director_name": cell(p.name),
            "id_number": cell(p.nric),
            "nationality": cell("Singapore Citizen"),
            "position": cell("Director"),
            "appointment_date": date_cell(p.appointed),
        }
        for p in plan.people
    ]
    holders = []
    total = 10000
    shares = [6000, 4000]
    for p, n in zip(plan.people, shares, strict=True):
        holders.append(
            {
                "holder_name": cell(p.name),
                "id_or_uen": cell(p.nric),
                "shares": cell(str(n), f"{n:,}"),
                "percent": cell(s2(D(n) * 100 / D(total)), f"{n * 100 / total:.2f}"),
            }
        )
    return make_document(
        "company_profile:-", "company_profile", f, {"directors": directors, "shareholders": holders}
    )


def render_invoice(plan: Plan, inv: Inv) -> dict[str, Any]:
    f = {
        "invoice_number": cell(inv.no),
        "invoice_date": date_cell(inv.day),
        "due_date": date_cell(inv.day + timedelta(days=30)),
        "supplier_name": cell(plan.name),
        "supplier_uen": cell(plan.uen),
        "supplier_gst_no": cell(plan.gst_no),
        "buyer_name": cell(inv.buyer.name),
        "buyer_uen": cell(inv.buyer.uen),
        "currency": cell("SGD"),
        "subtotal": money_cell(inv.subtotal),
        "gst_rate": cell("9.00", "9%"),
        "gst_amount": money_cell(inv.gst),
        "total": money_cell(inv.total),
        "po_reference": cell(inv.po),
        "contract_reference": cell(None),
        "payment_status": cell("paid", "PAID"),
        "paid_date": date_cell(inv.paid),
    }
    lines = [
        {
            "description": cell(ln.desc),
            "quantity": cell(str(ln.qty)),
            "unit_price": money_cell(ln.price),
            "gst_amount": cell(None),
            "amount": money_cell(ln.amount),
        }
        for ln in inv.lines
    ]
    return make_document(f"invoice:{inv.no}", "invoice", f, {"lines": lines})


def render_statement(plan: Plan) -> dict[str, Any]:
    rows = _statement_rows(plan)
    net_min = D(0)
    run = D(0)
    for e in rows:
        run += (e.credit or D(0)) - (e.debit or D(0))
        net_min = min(net_min, run)
    opening = q2(D(math.ceil(float(-net_min) / 1000) * 1000 + 12000))
    bal = opening
    out = []
    for e in rows:
        bal = q2(bal + (e.credit or D(0)) - (e.debit or D(0)))
        out.append(
            {
                "date": date_cell(e.day),
                "description": cell(e.desc),
                "debit": money_cell(e.debit) if e.debit is not None else cell(None),
                "credit": money_cell(e.credit) if e.credit is not None else cell(None),
                "balance": money_cell(bal),
            }
        )
    y, m = MONTH
    first, last = date(y, m, 1), date(y, m, 30)
    deb = q2(sum((e.debit or D(0) for e in rows), D(0)))
    cre = q2(sum((e.credit or D(0) for e in rows), D(0)))
    f = {
        "bank_name": cell("MOCK BANK"),
        "account_holder": cell(plan.upper),
        "uen": cell(plan.uen),
        "account_number": cell(plan.account),
        "period_start": date_cell(first),
        "period_end": date_cell(last),
        "opening_balance": money_cell(opening),
        "closing_balance": money_cell(bal),
        "currency": cell("SGD"),
        "page_x_of_y": cell("1 of 1"),
        "total_debits": money_cell(deb),
        "total_credits": money_cell(cre),
    }
    return make_document(f"bank_statement:{y}-{m:02d}", "bank_statement", f, {"transactions": out})


def render_sof(plan: Plan) -> dict[str, Any]:
    """The declarant moves their own money into the company: the counterparty is the declarant
    and the period is the month of the transfer, so the deterministic ``x_sof_support`` finds the
    credit and stays silent. Where the money really came from is told only in the description."""
    assert plan.sof is not None
    s = plan.sof
    f = {
        "declarant_name": cell(s["declarant"]),
        "company_name": cell(plan.name),
        "uen": cell(plan.uen),
        "declaration_date": date_cell(TODAY),
        "declared_amount": cell(s2(s["amount"]), f"S$ {fmt_amount(s['amount'])}"),
        "currency": cell("SGD"),
        "origin_category": cell(s["category"], s["category_raw"]),
        "origin_description": cell(s["description"]),
        "counterparty_name": cell(s["declarant"]),
        "invoice_reference": cell(None),
        "period_start": date_cell(date(2025, 9, 1)),
        "period_end": date_cell(date(2025, 9, 30)),
        "receiving_account": cell(plan.account),
    }
    return make_document("source_of_funds_declaration:-", "source_of_funds_declaration", f, None)


def render_documents(plan: Plan) -> list[dict[str, Any]]:
    docs = [render_profile(plan)]
    docs += [render_invoice(plan, inv) for inv in plan.invoices]
    docs.append(render_statement(plan))
    if plan.sof is not None:
        docs.append(render_sof(plan))
    return docs


# --------------------------------------------------------------------------------- locators


def _loc_field(doc_key: str, field_name: str, quote: str) -> dict[str, Any]:
    return {"document": doc_key, "field": field_name, "quote": quote}


def _loc_row(doc_key: str, table: str, row: int, column: str, quote: str) -> dict[str, Any]:
    return {"document": doc_key, "table": table, "row": row, "column": column, "quote": quote}


def _statement_locators(plan: Plan, names: list[str]) -> list[dict[str, Any]]:
    rows = _statement_rows(plan)
    key = f"bank_statement:{MONTH[0]}-{MONTH[1]:02d}"
    out = []
    for i, e in enumerate(rows):
        for n in names:
            if e.cp == n or n in e.desc:
                out.append(_loc_row(key, "transactions", i, "description", n))
                break
    return out


# ---------------------------------------------------------------------------------- tampers


def _donor(split: str, ind: Industry, rng: random.Random, near: bool = False) -> Industry:
    """An industry of the same split with another family; ``near`` keeps the broad sector."""
    cands = [i for i in industries_of(split) if i.id != ind.id and i.family != ind.family]
    if near:
        same = [i for i in cands if SECTOR[i.id] == SECTOR[ind.id]]
        cands = same or cands
    else:
        far = [i for i in cands if SECTOR[i.id] != SECTOR[ind.id]]
        cands = far or cands
    return rng.choice(cands)


def _n_replace(difficulty: str, n_lines: int) -> int:
    return {"easy": n_lines, "medium": max(2, n_lines // 2), "hard": 1}[difficulty]


def goods_edit(
    plan: Plan, rng: random.Random, difficulty: str, items: tuple
) -> list[dict[str, Any]]:
    """Replace lines of one invoice with ``items`` (priced from their ranges). Returns the
    evidence locators of the replaced lines."""
    k = rng.randrange(len(plan.invoices))
    inv = plan.invoices[k]
    n = _n_replace(difficulty, len(inv.lines))
    idx = sorted(rng.sample(range(len(inv.lines)), n))
    new = _lines(rng, items, n)
    if len(new) < n:  # a short list: reuse
        new = (new * n)[:n]
    ev = []
    for i, ln in zip(idx, new, strict=True):
        inv.lines[i] = ln
        ev.append(_loc_row(f"invoice:{inv.no}", "lines", i, "description", ln.desc))
    return ev


def tamper_goods(plan: Plan, rng: random.Random, difficulty: str) -> tuple[list[dict], dict]:
    donor = _donor(plan.split, plan.industry, rng)
    ev = goods_edit(plan, rng, difficulty, donor.sells)
    return ev, {"donor_industry": donor.id}


def control_goods(
    plan: Plan, rng: random.Random, difficulty: str, kind: str
) -> tuple[list[dict[str, Any]], str]:
    """Legitimate twists that replace the lines the tamper would replace."""
    if kind == "generic_service_lines":
        ev = goods_edit(plan, rng, difficulty, GENERIC_LINES)
        return ev, "delivery, installation, warranty and similar service lines on a goods invoice"
    ev = goods_edit(plan, rng, difficulty, plan.industry.sidelines)
    return ev, "items from a plausible sideline of the same trade"


GOODS_CONTROLS = ("generic_service_lines", "adjacent_sideline", "general_trade_profile")


# ---- income


def _an(title: str) -> str:
    """``an architect``, ``a bus captain``."""
    return f"{'an' if title[0] in 'aeiou' else 'a'} {title}"


def _employer(rng: random.Random, plan: Plan, occ: Occupation) -> str:
    return f"{rng.choice(_brands(plan.split))} {occ.employer_noun}"


def _set_sof(plan: Plan, **kw: Any) -> None:
    """Attach the declaration and the matching director-loan credit on the statement."""
    plan.sof = {
        "declarant": plan.people[0].name,
        "category_raw": {
            "salary": "Salary",
            "inheritance": "Inheritance",
            "asset_sale": "Sale of assets",
            "investment_returns": "Investment returns",
        }[kw["category"]],
        **kw,
    }
    plan.events = [e for e in plan.events if e.kind != "director_loan"]
    plan.events.append(
        Ev(
            _wd(date(2025, 9, 12)),
            f"FUNDS TRANSFER FROM {plan.people[0].name.upper()} DIRECTOR LOAN",
            credit=kw["amount"],
            kind="director_loan",
            cp=plan.people[0].name.upper(),
        )  # fmt: skip
    )


def _salary_sof(plan: Plan, rng: random.Random, occ: Occupation, amount: Decimal) -> dict[str, Any]:
    emp = _employer(rng, plan, occ)
    desc = (
        f"Salary and year-end bonus earned from my employment as {_an(occ.title)} at {emp} "
        "for the twelve months to August 2025."
    )
    return {
        "category": "salary",
        "amount": amount,
        "description": desc,
    }


def _savings_sof(
    plan: Plan, rng: random.Random, occ: Occupation, amount: Decimal, years: int
) -> dict[str, Any]:
    emp = _employer(rng, plan, occ)
    desc = f"Savings accumulated from my salary as {_an(occ.title)} at {emp} over the past {years} years."
    return {
        "category": "salary",
        "amount": amount,
        "description": desc,
    }


def _round_amount(v: float) -> Decimal:
    step = 1000 if v >= 20000 else 100
    return D(int(round(v / step)) * step)


MULT = {"easy": (10.0, 16.0), "medium": (6.0, 9.0), "hard": (3.5, 5.5)}


def tamper_income(plan: Plan, rng: random.Random, difficulty: str, index: int) -> tuple[list, dict]:
    occs = occupations_of(plan.split)
    occ = rng.choice(occs)
    lo, hi = MULT[difficulty]
    mult = rng.uniform(lo, hi)
    if index % 2 == 0:
        amount = _round_amount(occ.annual_max * mult)
        sof = _salary_sof(plan, rng, occ, amount)
        subtype = "salary_too_high"
    else:
        years = rng.randint(4, 15)
        amount = _round_amount(occ.savings_max(years) * mult)
        sof = _savings_sof(plan, rng, occ, amount, years)
        subtype = "savings_too_high"
    _set_sof(plan, **sof)
    plan.occupation = occ
    ev = [
        _loc_field("source_of_funds_declaration:-", "origin_description", occ.title),
        _loc_field("source_of_funds_declaration:-", "declared_amount", s2(amount)),
    ]
    return ev, {
        "subtype": subtype,
        "occupation": occ.id,
        "multiple_of_plausible_max": round(mult, 1),
    }


INCOME_CONTROLS = (
    "high_pay_plausible",
    "modest_savings_plausible",
    "non_salary_source",
    "former_role_plausible",
)


def control_income(plan: Plan, rng: random.Random, kind: str) -> tuple[list, str, dict]:
    occs = occupations_of(plan.split)
    high = [o for o in occs if o.monthly_hi >= 15000]
    modest = [o for o in occs if o.monthly_hi <= MODEST_MAX]
    if kind == "high_pay_plausible":
        occ = rng.choice(high)
        amount = _round_amount(occ.annual_max * rng.uniform(0.55, 0.95))
        sof = _salary_sof(plan, rng, occ, amount)
        ratio: float | None = float(amount) / occ.annual_max
        note = f"a high-pay occupation ({occ.title}) with an amount inside its range"
    elif kind == "modest_savings_plausible":
        occ = rng.choice(modest)
        years = rng.randint(8, 20)
        amount = _round_amount(occ.savings_max(years) * rng.uniform(0.2, 0.5))
        sof = _savings_sof(plan, rng, occ, amount, years)
        ratio = float(amount) / occ.savings_max(years)
        note = f"a modest occupation ({occ.title}) with savings well inside what it could save"
    elif kind == "non_salary_source":
        occ = rng.choice(modest)
        category = rng.choice(("asset_sale", "inheritance"))
        amount = _round_amount(rng.uniform(300_000, 2_500_000))
        what = (
            "the sale of my late parents' freehold shophouse"
            if category == "asset_sale"
            else "the estate of my late grandfather, paid out in August 2025"
        )
        sof = {
            "category": category,
            "amount": amount,
            "description": (
                f"Proceeds from {what}. I work as {_an(occ.title)}; these funds are not from my "
                "salary."
            ),
        }
        ratio = None  # the source is not salary: the occupation does not bear on the amount
        note = f"a modest occupation ({occ.title}) but the declared source is not salary"
    else:  # former_role_plausible
        old = rng.choice(high)
        occ = rng.choice(modest)
        amount = _round_amount(old.annual_max * rng.uniform(0.5, 0.9))
        emp = _employer(rng, plan, old)
        sof = {
            "category": "salary",
            "amount": amount,
            "description": (
                f"Salary and bonus from my former role as {_an(old.title)} at {emp}, which I left in "
                f"2024. I now work as {_an(occ.title)}."
            ),
        }
        ratio = float(amount) / old.annual_max  # measured against the former role
        note = f"current job ({occ.title}) is modest, the funds come from a former {old.title} role"
    _set_sof(plan, **sof)
    plan.occupation = occ
    watch = [
        _loc_field("source_of_funds_declaration:-", "origin_description", occ.title),
        _loc_field("source_of_funds_declaration:-", "declared_amount", s2(sof["amount"])),
    ]
    return (
        watch,
        note,
        {
            "occupation": occ.id,
            "multiple_of_plausible_max": None if ratio is None else round(ratio, 2),
        },
    )


# ---- coherence


def _swap_names(plan: Plan, rng: random.Random, donor: Industry, difficulty: str) -> list[str]:
    """Replace statement counterparties by donor-industry names. Returns the new upper names."""
    sup_idx = [i for i, e in enumerate(plan.events) if e.kind == "supplier"]
    cust_idx = [i for i, e in enumerate(plan.events) if e.kind == "other_cust"]
    chosen = {"easy": sup_idx + cust_idx[:2], "medium": sup_idx, "hard": rng.sample(sup_idx, 2)}[
        difficulty
    ]
    brands = list(_brands(plan.split))
    rng.shuffle(brands)
    new_names = []
    for n, i in enumerate(sorted(chosen)):
        e = plan.events[i]
        words = donor.supplier_words if e.kind == "supplier" else donor.customer_words
        party = Party(f"{brands[n]} {rng.choice(words)} Pte. Ltd.", "")
        tpl = rng.choice(DESC_OUT if e.kind == "supplier" else DESC_IN).format(cp=party.upper)
        if e.kind == "supplier" and rng.random() < 0.6:
            tpl += " " + rng.choice(donor.memos)
        plan.events[i] = Ev(e.day, tpl, e.debit, e.credit, e.kind, party.upper)
        new_names.append(party.upper)
    return new_names


def tamper_coherence(
    plan: Plan, rng: random.Random, difficulty: str, index: int
) -> tuple[list, dict]:
    if index % 2 == 0:  # statement counterparties belong to another business
        donor = _donor(plan.split, plan.industry, rng)
        names = _swap_names(plan, rng, donor, difficulty)
        ev = _statement_locators(plan, names)
        return ev, {"subtype": "statement_odd", "donor_industry": donor.id,
                    "odd_document": f"bank_statement:{MONTH[0]}-{MONTH[1]:02d}"}  # fmt: skip
    donor = _donor(plan.split, plan.industry, rng, near=(difficulty == "hard"))
    plan.activity, plan.ssic = donor.activity[0], donor.ssic
    ev = [_loc_field("company_profile:-", "principal_activity", donor.activity[0])]
    return ev, {"subtype": "profile_odd", "donor_industry": donor.id,
                "odd_document": "company_profile:-"}  # fmt: skip


ONE_OFFS = (
    ("OFFICE RENOVATION", "Interior Fit-Out"),
    ("COMPANY VEHICLE", "Motors"),
    ("LEGAL FEES", "Law Practice"),
    ("AUDIT FEE", "Assurance"),
    ("SIGNAGE", "Signage"),
)


def control_coherence(
    plan: Plan, rng: random.Random, difficulty: str, subtype: str, index: int, kind: str
) -> tuple[list, str, dict]:
    if subtype == "statement_odd":
        donor = _donor(plan.split, plan.industry, rng)
        if kind == "diversified_profile":
            names = _swap_names(plan, rng, donor, difficulty)
            plan.activity = (
                f"{plan.activity}; and {donor.activity[0][0].lower()}{donor.activity[0][1:]}"
            )
            watch = _statement_locators(plan, names)
            return watch, "the profile declares a second line of business that the statement shows", {
                "donor_industry": donor.id}  # fmt: skip
        # one_off_expense: unrelated one-off payees, not a second business
        brands = list(_brands(plan.split))
        rng.shuffle(brands)
        names = []
        sup_idx = [i for i, e in enumerate(plan.events) if e.kind == "supplier"]
        for n, i in enumerate(rng.sample(sup_idx, 2)):
            e = plan.events[i]
            memo, word = ONE_OFFS[(index + n) % len(ONE_OFFS)]
            party = Party(f"{brands[n]} {word} Pte. Ltd.", "")
            plan.events[i] = Ev(e.day, f"{DESC_OUT[0].format(cp=party.upper)} {memo}", e.debit,
                                e.credit, "supplier", party.upper)  # fmt: skip
            names.append(party.upper)
        return (
            _statement_locators(plan, names),
            "one-off payments to unrelated payees (fit-out, vehicle, legal)",
            {},
        )
    # profile_odd family: the profile is general or lists a plausible extra line; invoices and
    # statement still describe the declared trade
    if kind == "generic_profile":
        plan.activity, plan.ssic = GENERAL_ACTIVITY
        note = "a general wholesale trade profile over a specific trade"
    else:
        side = rng.choice(plan.industry.sidelines)[0].split(" (")[0].lower()
        plan.activity = f"{plan.activity}; and supply of {side}"
        note = "the profile lists a plausible sideline as well"
    return [_loc_field("company_profile:-", "principal_activity", plan.activity)], note, {}


COHERENCE_CONTROLS = {
    "statement_odd": ("diversified_profile", "one_off_expense"),
    "profile_odd": ("generic_profile", "profile_with_sideline"),
}


# ------------------------------------------------------------------------------- the cases


def _difficulty(index: int) -> str:
    return DIFFICULTIES[index % 3]


def case_id(split: str, family: str, index: int, variant: str) -> str:
    """An opaque id: it must not tell a model (or a prompt writer skimming ids) which variant a
    case is. ``base_id`` is the readable one and is shared by the cases built on one base."""
    digest = hashlib.sha256(f"{split}:{family}:{index}:{variant}".encode()).hexdigest()[:8]
    return f"fit-{split}-{SHORT[family]}-{digest}"


def _case(
    plan: Plan,
    split: str,
    family: str,
    index: int,
    variant: str,
    *,
    subtype: str,
    difficulty: str | None,
    evidence: list[dict[str, Any]],
    watch: list[dict[str, Any]],
    note: str,
    extra: dict[str, Any],
    matched_to: str | None,
) -> dict[str, Any]:
    tampered = variant == "tampered"
    return {
        "case_id": case_id(split, family, index, variant),
        "base_id": f"{split}-{SHORT[family]}-{index:02d}",
        "split": split,
        "family": family,
        "label": "tampered" if tampered else "clean",
        "variant": variant,
        "subtype": subtype,
        "difficulty": difficulty,
        "industry": plan.industry.id,
        "matched_to": matched_to,
        "expected": {
            "kind": KIND_OF[family] if tampered else None,
            "evidence": evidence if tampered else [],
            "note": note,
            **(
                {"odd_document": extra["odd_document"]}
                if tampered and "odd_document" in extra
                else {}
            ),
        },
        "watch": [] if tampered else watch,
        "details": {k: v for k, v in extra.items() if k != "odd_document"},
        "documents": render_documents(plan),
    }


def build_cases(split: str, seed: int | None = None, n_bases: int = N_BASES) -> list[dict]:
    """The FIT set of one split: per family ``n_bases`` tampered, ``n_bases`` matched
    unusual-but-legitimate controls and ``N_ORDINARY`` ordinary clean cases."""
    check_split(split)
    seed = SEEDS[split] if seed is None else seed
    out: list[dict] = []
    for family in FAMILIES:
        for i in range(n_bases):
            diff = _difficulty(i)
            t_id = case_id(split, family, i, "tampered")
            # ---- tampered
            plan = build_base(split, seed, family, i)
            rng = stream(seed, "fit", family, i, "tamper")
            extra: dict[str, Any]
            if family == "goods_fit":
                ev, extra = tamper_goods(plan, rng, diff)
                sub = "wrong_industry_goods"
                note = (
                    f"{diff}: lines from {extra['donor_industry']} on a {plan.industry.id} invoice"
                )
            elif family == "income_fit":
                ev, extra = tamper_income(plan, rng, diff, i)
                sub = extra.pop("subtype")
                note = (
                    f"{diff}: amount is {extra['multiple_of_plausible_max']}x the plausible maximum"
                )
            else:
                ev, extra = tamper_coherence(plan, rng, diff, i)
                sub = extra.pop("subtype")
                note = (
                    f"{diff}: {sub.replace('_', ' ')}, other documents describe {plan.industry.id}"
                )
            out.append(_case(plan, split, family, i, "tampered", subtype=sub, difficulty=diff,
                             evidence=ev, watch=[], note=note, extra=extra, matched_to=None))  # fmt: skip
            # ---- matched unusual-but-legitimate control (same base)
            plan = build_base(split, seed, family, i)
            rng = stream(seed, "fit", family, i, "control")
            if family == "goods_fit":
                kind = GOODS_CONTROLS[i % len(GOODS_CONTROLS)]
                # the third kind replays the tamper's own edit, so it must use the tamper's stream
                if kind == "general_trade_profile":
                    t_rng = stream(seed, "fit", family, i, "tamper")
                    watch, note = _goods_general(plan, t_rng, diff)
                else:
                    watch, note = control_goods(plan, rng, diff, kind)
                extra, sub = {}, kind
            elif family == "income_fit":
                kind = INCOME_CONTROLS[i % len(INCOME_CONTROLS)]
                watch, note, extra = control_income(plan, rng, kind)
                sub = kind
            else:
                t_sub = "statement_odd" if i % 2 == 0 else "profile_odd"
                kinds = COHERENCE_CONTROLS[t_sub]
                kind = kinds[(i // 2) % len(kinds)]
                if t_sub == "statement_odd":
                    watch, note, extra = control_coherence(plan, stream(seed, "fit", family, i, "tamper"),
                                                           diff, t_sub, i, kind)  # fmt: skip
                else:
                    watch, note, extra = control_coherence(plan, rng, diff, t_sub, i, kind)
                sub = kind
            out.append(_case(plan, split, family, i, "unusual_legit", subtype=sub, difficulty=diff,
                             evidence=[], watch=watch, note=note, extra=extra, matched_to=t_id))  # fmt: skip
            # ---- ordinary clean (the first N_ORDINARY bases)
            if i < N_ORDINARY:
                plan = build_base(split, seed, family, i)
                rng = stream(seed, "fit", family, i, "ordinary")
                note = "no twist"
                if family == "income_fit":
                    occ = rng.choice(occupations_of(split))
                    if i % 2 == 0:
                        amount = _round_amount(occ.annual_max * rng.uniform(0.3, 0.8))
                        _set_sof(plan, **_salary_sof(plan, rng, occ, amount))
                    else:
                        years = rng.randint(5, 15)
                        amount = _round_amount(occ.savings_max(years) * rng.uniform(0.15, 0.5))
                        _set_sof(plan, **_savings_sof(plan, rng, occ, amount, years))
                    plan.occupation = occ
                out.append(_case(plan, split, family, i, "ordinary", subtype="ordinary",
                                 difficulty=None, evidence=[], watch=[], note=note, extra={},
                                 matched_to=None))  # fmt: skip
    stream(seed, "fit", "order").shuffle(out)  # no runs of tampered / control / ordinary
    return out


def _goods_general(plan: Plan, t_rng: random.Random, diff: str) -> tuple[list, str]:
    """Replay the tamper's edit (same stream, same draws) and give the company a general-trade
    profile, so the invoices are identical to the tampered case and only the profile differs."""
    donor = _donor(plan.split, plan.industry, t_rng)
    ev = goods_edit(plan, t_rng, diff, donor.sells)
    plan.activity, plan.ssic = GENERAL_ACTIVITY
    return ev, f"a general wholesale trade profile invoicing {donor.id.replace('_', ' ')} goods"
