"""The World: a pure function of (scenario, company_index, seed, today, quirks) (docs 4.1).

Streams: ``random.Random(f"{seed}:{scenario}:{company_index}:{purpose}")``. Variant and tamper
streams use their own purpose keys and never consume the company streams, so untouched documents
of clean/fraud/forger variants are byte-identical.

Public mutation primitives for plots and tampers: ``apply_plot``, ``add_transactions``,
``recompute_chain``, ``add_person``, ``add_holder``.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import ids as idm
from . import lexicon as lx
from . import names_data as nd
from . import world_fs as wfs
from .schema import FIXED_TODAY, LAYOUTS, BundleSpec, Mutation
from .world_types import (
    Company,
    Contract,
    Customer,
    FinStmt,
    Holder,
    InvLine,
    Invoice,
    LedgerRow,
    Party,
    Person,
    PurchaseOrder,
    Statement,
)

D = Decimal
CENT = D("0.01")
GST_RATE = D("9.00")

CALENDAR: dict[str, dict[str, Any]] = {
    "loan": {
        "first": (2025, 7),
        "months": 6,
        "fs": True,
        "layout": "dbs_style",
        "invoices": 14,
        "inv_year": 2025,
        "first_seq": 101,
    },
    "kyc": {
        "first": (2026, 7),
        "months": 3,
        "fs": False,
        "layout": "ocbc_style",
        "invoices": 0,
        "inv_year": 2026,
        "first_seq": 101,
    },
    "aml": {
        "first": (2026, 4),
        "months": 6,
        "fs": False,
        "layout": "uob_style",
        "invoices": 10,
        "inv_year": 2026,
        "first_seq": 107,
    },
}
REGISTRY_AS_OF = date(2026, 9, 30)

CATALOG = [
    ("M8 x 40mm hex bolt, zinc plated (box of 100)", 14, 40),
    ("M10 flange nut, stainless steel (box of 50)", 22, 60),
    ("M6 socket head cap screw, black (box of 200)", 18, 55),
    ("Self-drilling roofing screw 5.5 x 50 (carton)", 45, 110),
    ("Heavy duty wedge anchor M12 (box of 25)", 38, 95),
    ("Plain washer M10, galvanised (bag of 500)", 9, 28),
    ("Threaded rod M12 x 1m, grade 8.8 (bundle of 10)", 60, 150),
    ("Cable tie 300mm UV resistant (pack of 1000)", 12, 36),
    ("Stainless pop rivet 4.8 x 12 (box of 1000)", 20, 52),
    ("Industrial hinge 100mm, zinc (carton of 24)", 55, 130),
]
PRODUCTS_FREIGHT = [
    ("Freight consolidation, LCL", 400, 1500),
    ("Customs documentation handling", 80, 300),
    ("Port handling and transfer", 150, 600),
]
SUPPLIER_LABELS = [
    "trading",
    "manufacturing",
    "logistics",
    "trading",
    "manufacturing",
    "professional_services",
    "trading",
    "logistics",
]


# ------------------------------------------------------------------------------------ utils


def stream(seed: int, scenario: str, company_index: int, purpose: str) -> random.Random:
    return random.Random(f"{seed}:{scenario}:{company_index}:{purpose}")


def q2(x: Decimal | float | int | str) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def weekday_on_or_after(d: date, hi: date | None = None) -> date:
    out = d
    while not is_weekday(out):
        out += timedelta(days=1)
    if hi is not None and out > hi:
        out = d
        while not is_weekday(out) or out > hi:
            out -= timedelta(days=1)
    return out


def weekday_on_or_before(d: date, lo: date | None = None) -> date:
    out = d
    while not is_weekday(out):
        out -= timedelta(days=1)
    if lo is not None and out < lo:
        return weekday_on_or_after(lo)
    return out


def month_bounds(y: int, m: int) -> tuple[date, date]:
    first = date(y, m, 1)
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return first, nxt - timedelta(days=1)


def add_months(first: tuple[int, int], n: int) -> list[tuple[int, int]]:
    y, m = first
    out = []
    for _ in range(n):
        out.append((y, m))
        y, m = y + (m == 12), (m % 12) + 1
    return out


def spread(rng: random.Random, total: Decimal, n: int, sigma: float = 1.1) -> list[Decimal]:
    """Split ``total`` into ``n`` positive 2-dp amounts with log-normal weights (sum exact)."""
    if n <= 0:
        return []
    w = [math.exp(rng.gauss(0, sigma)) for _ in range(n)]
    s = sum(w)
    parts = [q2(total * D(str(x / s))) for x in w]
    parts[-1] = q2(total - sum(parts[:-1], D(0)))
    # keep every part positive and not an exact multiple of 1000
    for i, p in enumerate(parts):
        if p <= 0:
            parts[i] = D("10.07")
        if p % 1000 == 0:
            parts[i] = p + D("0.37")
            parts[-1 if i != n - 1 else 0] -= D("0.37")
    return parts


# ------------------------------------------------------------------------------------ World


@dataclass
class World:
    scenario: str
    company_index: int
    seed: int
    today: date
    quirks: list[str]
    layout: str
    variant: str = "clean"
    company: Company = None  # type: ignore[assignment]
    people: list[Person] = field(default_factory=list)
    holders: list[Holder] = field(default_factory=list)
    intermediates: list[dict[str, Any]] = field(default_factory=list)  # extra registry entities
    customers: list[Customer] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    pos: list[PurchaseOrder] = field(default_factory=list)
    fs: dict[str, FinStmt] = field(default_factory=dict)
    dropped_docs: set[str] = field(default_factory=set)
    producer_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    registry_as_of: date = REGISTRY_AS_OF
    notes: dict[str, Any] = field(default_factory=dict)

    def rng(self, purpose: str) -> random.Random:
        return stream(self.seed, self.scenario, self.company_index, purpose)

    def statement(self, month: str) -> Statement:
        for s in self.statements:
            if s.month == month:
                return s
        raise KeyError(month)

    def invoice(self, number: str) -> Invoice:
        for i in self.invoices:
            if i.number == number:
                return i
        raise KeyError(number)

    def person(self, key: str) -> Person:
        for p in self.people:
            if p.key == key:
                return p
        raise KeyError(key)

    def customer_by_name(self, name: str) -> Customer | None:
        for c in self.customers:
            if c.party.name == name:
                return c
        return None

    def registry_entities(self) -> list[dict[str, Any]]:
        """The company and any intermediate holder companies in the data-model section 8 shape."""
        return [registry_entity(self), *copy.deepcopy(self.intermediates)]

    # convenience for plots
    @property
    def applicant_name(self) -> str:
        return self.company.party.name


# ------------------------------------------------------------------------------- identity


def _canonical_people(scenario: str) -> list[tuple[str, str, str, int, str]]:
    # (name, group, sex, birth_year, id_kind)
    return {
        "loan": [
            ("Lim Hui Min", "chinese_order", "F", 1978, "nric"),
            ("Mohd Faizal bin Rahmat", "malay_patronymic", "M", 1985, "nric"),
        ],
        "kyc": [
            ("Tan Wei Ming", "chinese_order", "M", 1972, "nric"),
            ("Muhammad Hafiz bin Ismail", "malay_patronymic", "M", 1980, "nric"),
            ("Elena Vasquez", "western_order", "F", 1988, "passport"),
        ],
        "aml": [
            ("Rajesh s/o Kumar", "indian_patronymic", "M", 1975, "nric"),
            ("Wong Jessica", "chinese_order", "F", 1983, "nric"),
        ],
    }[scenario]


def _make_company(w: World, rng: random.Random) -> None:
    sc, idx = w.scenario, w.company_index
    if idx == 0:
        name = {
            "loan": "Meridian Fastener Supplies Pte. Ltd.",
            "kyc": "Sembrook Freight Pte. Ltd.",
            "aml": "Tanjong Ridge Trading Pte. Ltd.",
        }[sc]
        activity = {
            "loan": "Wholesale of fasteners and industrial supplies (trading)",
            "kyc": "Freight forwarding and logistics",
            "aml": "General wholesale trade",
        }[sc]
        ssic = {"loan": "46900", "kyc": "52291", "aml": "46900"}[sc]
    else:
        name = nd.company_name(idx)
        activity = {
            "loan": "Wholesale of industrial supplies (trading)",
            "kyc": "Freight forwarding and logistics",
            "aml": "General wholesale trade",
        }[sc]
        ssic = {"loan": "46900", "kyc": "52291", "aml": "46900"}[sc]
    inc_year = rng.randint(2016, 2021)
    inc = date(inc_year, rng.randint(1, 12), rng.randint(1, 28))
    uen = idm.random_uen(rng, inc_year)
    capital = (
        D(rng.choice([200000, 300000, 500000])) if idx else D(300000 if sc == "loan" else 500000)
    )
    party = Party(name=name, uen=uen, gst_no=idm.random_gst_no(rng), address=nd.address(rng))
    w.company = Company(
        party=party,
        incorporation=inc,
        activity=activity,
        ssic=ssic,
        paid_up_capital=q2(capital),
        bank_name={
            "dbs_style": "DBS-STYLE MOCK BANK",
            "ocbc_style": "OCBC-STYLE MOCK BANK",
            "uob_style": "UOB-STYLE MOCK BANK",
            "software": "",
        }[w.layout],
        account_number="".join(str(rng.randrange(10)) for _ in range(10)),
    )


def _make_people(w: World, rng: random.Random) -> None:
    sc, idx = w.scenario, w.company_index
    if idx == 0:
        spec = _canonical_people(sc)
    else:
        n = 3 if sc == "kyc" else 2
        spec = []
        for i in range(n):
            group = nd.NAME_GROUPS[(idx + i * 2) % len(nd.NAME_GROUPS)]
            sex = rng.choice("MF")
            if sc == "kyc" and i == 2:
                group, sex = "western_order", "F"
            spec.append(
                (
                    nd.make_name(rng, group, sex),
                    group,
                    sex,
                    rng.randint(1962, 1992),
                    "passport" if (sc == "kyc" and i == 2) else "nric",
                )
            )
    for i, (name, group, sex, by, kind) in enumerate(spec):
        birth = date(by, rng.randint(1, 12), rng.randint(1, 28))
        if kind == "nric":
            idnum = idm.random_nric(rng, by)
            nat = "SG"
        else:
            idnum = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(2)) + "".join(
                str(rng.randrange(10)) for _ in range(6)
            )
            nat = "ESP"
        _, bare = nd.split_title(name)
        parts = bare.split()
        surname = parts[-1] if group in ("western_order",) else parts[0]
        given = " ".join(parts[:-1]) if group == "western_order" else " ".join(parts[1:])
        p = Person(
            key=f"p{i}",
            name=name,
            group=group,
            sex=sex,
            birth=birth,
            id_number=idnum,
            nationality=nat,
            id_kind=kind,
            appointment=w.company.incorporation,
            registry_name=nd.registry_form(name, group, idx),
            address=nd.address(rng),
            issue_date=date(
                birth.year + 18 + rng.randint(0, 6), rng.randint(1, 12), rng.randint(1, 28)
            ),
            surname=surname,
            given_names=given,
            passport_expiry=date(2031, rng.randint(1, 12), rng.randint(1, 28))
            if kind == "passport"
            else None,
            sig_seed=rng.randrange(1, 10**6),
        )
        w.people.append(p)
    # shareholders
    cap = int(w.company.paid_up_capital)
    ps = w.people
    if sc == "kyc" and idx == 0:
        # Tan 45, Hafiz 30, Kestrel Capital Holdings Pte. Ltd. 25 (itself held 100% by an individual)
        kuen = idm.random_uen(rng, 2015)
        owner_by = 1976
        owner = Person(
            key="px0",
            name="Teo Boon Heng",
            group="chinese_order",
            sex="M",
            birth=date(owner_by, 5, 17),
            id_number=idm.random_nric(rng, owner_by),
            registry_name="TEO BOON HENG",
            address=nd.address(rng),
            is_director=False,
            on_profile=False,
            in_registry=False,
            sig_seed=rng.randrange(1, 10**6),
        )
        w.holders = [
            Holder(
                ps[0].name,
                "individual",
                ps[0].id_number,
                cap * 45 // 100,
                D("45.00"),
                ps[0].registry_name,
                "p0",
                is_ubo=True,
            ),
            Holder(
                ps[1].name,
                "individual",
                ps[1].id_number,
                cap * 30 // 100,
                D("30.00"),
                ps[1].registry_name,
                "p1",
                is_ubo=True,
            ),
            Holder(
                "Kestrel Capital Holdings Pte. Ltd.",
                "company",
                kuen,
                cap * 25 // 100,
                D("25.00"),
                "Kestrel Capital Holdings Pte. Ltd.",
            ),
        ]
        w.notes["kestrel_owner"] = owner
        w.intermediates.append(
            {
                "uen": kuen,
                "entity_name": "Kestrel Capital Holdings Pte. Ltd.",
                "entity_type": "Private Company Limited by Shares",
                "status": "Live",
                "incorporation_date": "2015-06-10",
                "registered_address": nd.address(rng),
                "principal_activity": "Investment holding",
                "ssic_code": "64200",
                "paid_up_capital": "100000.00",
                "currency": "SGD",
                "officers": [
                    {
                        "name": owner.registry_name,
                        "id_number": owner.id_number,
                        "nationality": "SG",
                        "position": "Director",
                        "appointment_date": "2015-06-10",
                        "cessation_date": None,
                    }
                ],
                "shareholders": [
                    {
                        "name": owner.registry_name,
                        "id_or_uen": owner.id_number,
                        "holder_type": "individual",
                        "shares": 100000,
                        "percent": "100.00",
                        "share_class": "Ordinary",
                    }
                ],
            }
        )
    else:
        pcts = {2: [D("60.00"), D("40.00")], 3: [D("50.00"), D("30.00"), D("20.00")]}
        if sc == "aml" and idx == 0:
            pcts[2] = [D("70.00"), D("30.00")]
        elif idx and len(ps) == 2:
            pcts[2] = [D(str(rng.choice([60, 70, 55]))), D(0)]
            pcts[2][1] = D(100) - pcts[2][0]
        for p, pc in zip(ps, pcts[len(ps)], strict=True):
            w.holders.append(
                Holder(
                    p.name,
                    "individual",
                    p.id_number,
                    int(cap * pc / 100),
                    pc,
                    p.registry_name,
                    p.key,
                    is_ubo=pc >= 25,
                )
            )


# ------------------------------------------------------------------------------- customers


def _make_customers(w: World, rng: random.Random) -> tuple[list[str], set[str]]:
    """Three major customers (contracts), minor customers (invoices only) and a pool of
    non-invoice customers; returns supplier counterparty names. Names are keyword names."""
    used: set[str] = set()
    labels = [
        "trading",
        "manufacturing",
        "logistics",
        "trading",
        "manufacturing",
        "trading",
        "logistics",
        "manufacturing",
        "trading",
        "trading",
        "manufacturing",
        "logistics",
    ]
    n_major = 3 if w.scenario == "loan" else (1 if w.scenario == "aml" else 0)
    for i, label in enumerate(labels):
        cp = lx.make_counterparty(rng, label, used)
        party = Party(
            name=lx.proper(cp),
            uen=idm.random_uen(rng, rng.randint(2004, 2022)),
            gst_no=idm.random_gst_no(rng) if i < 8 else None,
            address=nd.address(rng),
        )
        w.customers.append(Customer(party=party, label=label, major=i < n_major, counterparty=cp))
    suppliers = [lx.make_counterparty(rng, lab, used) for lab in SUPPLIER_LABELS]
    return suppliers, used


# ---------------------------------------------------------------------------------- invoices


def _lines_for(rng: random.Random, n: int, target: Decimal, freight: bool) -> list[InvLine]:
    cat = PRODUCTS_FREIGHT if freight else CATALOG
    items = rng.sample(cat, min(n, len(cat)))
    weights = [rng.uniform(0.5, 1.5) for _ in items]
    s = sum(weights)
    out = []
    for (desc, lo, hi), wt in zip(items, weights, strict=True):
        tgt = target * D(str(wt / s))
        guess = rng.uniform(lo, hi)
        qty = max(1, round(float(tgt) / guess))
        price = q2(tgt / qty)
        out.append(InvLine(desc, D(qty), price, q2(D(qty) * price)))
    return out


def _price_invoice(inv: Invoice) -> None:
    inv.subtotal = q2(sum((ln.amount for ln in inv.lines), D(0)))
    if inv.layout == "B":
        for ln in inv.lines:
            ln.gst = q2(ln.amount * GST_RATE / 100)
        inv.gst_amount = q2(sum((ln.gst or D(0) for ln in inv.lines), D(0)))
    else:
        for ln in inv.lines:
            ln.gst = None
        inv.gst_amount = q2(inv.subtotal * GST_RATE / 100)
    inv.total = q2(inv.subtotal + inv.gst_amount)


def _gst_diff(inv: Invoice) -> Decimal:
    return abs(
        q2(sum((q2(ln.amount * GST_RATE / 100) for ln in inv.lines), D(0)))
        - q2(inv.subtotal * GST_RATE / 100)
    )


def _make_invoices(w: World, rng: random.Random, trade_target: Decimal, hard: set[str]) -> None:
    cal = CALENDAR[w.scenario]
    n = w.notes.get("invoice_count") or cal["invoices"]
    if n <= 0:
        return
    sc = w.scenario
    n_over = min(4, max(0, n - 6)) if sc == "loan" else 0
    y0, m0 = cal["first"]
    start = date(y0, m0, 10)
    last_inv = date(2025, 11, 14) if sc == "loan" else date(2026, 8, 14)
    span = (last_inv - start).days
    dates = sorted(
        weekday_on_or_after(
            start + timedelta(days=int(span * (i + rng.uniform(0.05, 0.95)) / n)), last_inv
        )
        for i in range(n)
    )
    majors = [c for c in w.customers if c.major]
    minors = [c for c in w.customers if not c.major][:5]
    first_seq = cal["first_seq"]
    numbers = [f"INV-{cal['inv_year']}-{first_seq + i:04d}" for i in range(n)]
    if "H3" in w.quirks and n > 4:
        # voided invoice: numbering skips one number (not first, not last)
        skip = rng.randint(2, n - 2)
        numbers = [
            f"INV-{cal['inv_year']}-{first_seq + i + (1 if i >= skip else 0):04d}" for i in range(n)
        ]
        w.notes["voided_number"] = f"INV-{cal['inv_year']}-{first_seq + skip:04d}"
    # buyers: majors for ~2/3 of invoices
    buyers: list[Customer] = []
    for i in range(n):
        if majors and (i < len(majors) * 2 or rng.random() < 0.55):
            buyers.append(majors[i % len(majors)] if i < len(majors) * 2 else rng.choice(majors))
        else:
            buyers.append(rng.choice(minors or majors))
    # sizes: paid invoices carry ~55% of the trade target
    n_paid = n - n_over
    base = (trade_target * D("0.55")) / max(1, n_paid)
    sizes = [q2(base * D(str(rng.uniform(0.6, 1.5)))) for _ in range(n)]
    po_idx = set(rng.sample(range(n_paid + 0), min(4, n))) if sc == "loan" else set()
    # overdue (unpaid) invoices: citing a contract, not citing a PO, dated well inside coverage
    over_idx: list[int] = []
    if n_over:
        eligible = [i for i in range(n) if date(2025, 8, 4) <= dates[i] <= date(2025, 10, 20)]
        cont = [i for i in eligible if buyers[i].major]
        picks = cont[:3]
        rest = [i for i in eligible if i not in picks]
        picks += rest[: n_over - len(picks)]
        over_idx = sorted(picks)[:n_over]
        if len(over_idx) < n_over:  # fall back: the latest invoices before the cut-off
            for i in range(n):
                if i not in over_idx and len(over_idx) < n_over:
                    over_idx.append(i)
        po_idx -= set(over_idx)
        if len(po_idx) < 4:
            for i in range(n):
                if i not in over_idx and i not in po_idx and len(po_idx) < 4:
                    po_idx.add(i)
    b_count = 0
    for i in range(n):
        layout = "B" if (i % 3 == 1) else "A"
        n_lines = rng.randint(3, 5) if layout == "B" else rng.randint(2, 4)
        inv = Invoice(
            number=numbers[i],
            date=dates[i],
            due=dates[i] + timedelta(days=rng.choice([14, 30, 30, 45, 60])),
            supplier=w.company.party,
            buyer=buyers[i].party,
            lines=[],
            subtotal=D(0),
            gst_rate=GST_RATE,
            gst_amount=D(0),
            total=D(0),
            layout=layout,
            contract_ref=None,
        )
        freight = sc == "kyc"
        for attempt in range(400):
            inv.lines = _lines_for(
                rng,
                5 if (layout == "B" and "Q02" in w.quirks and b_count == 0) else n_lines,
                sizes[i],
                freight,
            )
            _price_invoice(inv)
            if layout == "B" and "Q02" in w.quirks and b_count == 0:
                # Q02: per-line GST differs from the net method (rounding), by 0.02 where found, else 0.01
                if _gst_diff(inv) >= (D("0.02") if attempt < 300 else D("0.01")):
                    break
            else:
                break
        if layout == "B":
            b_count += 1
        w.invoices.append(inv)
    # contracts for major customers
    for ci, c in enumerate(majors, start=1):
        c.contract_n = ci
    for i, inv in enumerate(w.invoices):
        if buyers[i].major:
            inv.contract_ref = f"C-{cal['inv_year']}-{buyers[i].contract_n:03d}"
    w.notes["over_idx"] = over_idx
    w.notes["po_idx"] = sorted(po_idx)
    w.notes["buyers"] = [b.party.name for b in buyers]
    # payment plan
    end_cov = month_bounds(*add_months(cal["first"], w.notes["months"])[-1])[1]
    cap_pay = end_cov - timedelta(days=8)
    h1_done = False
    for i, inv in enumerate(w.invoices):
        if i in over_idx:
            inv.status, inv.paid_date = None, None
            continue
        lag = rng.randint(5, 38)
        paid = weekday_on_or_after(inv.date + timedelta(days=lag), inv.date + timedelta(days=40))
        if paid > cap_pay:
            paid = weekday_on_or_before(cap_pay)
        inv.status, inv.paid_date = "paid", paid
    # quirk Q04: one paid invoice in two instalments (decided in the ledger)
    paid_idx = [i for i in range(n) if i not in over_idx]
    if "Q04" in w.quirks and paid_idx:
        w.notes["instalment_invoice"] = w.invoices[paid_idx[min(2, len(paid_idx) - 1)]].number
    # hard H1: one credit pays two invoices of the same buyer (both stay inside 5-40 days)
    if "H1" in w.quirks:
        for a in paid_idx:
            for b in paid_idx:
                ia, ib = w.invoices[a], w.invoices[b]
                if (
                    a < b
                    and ia.buyer.name == ib.buyer.name
                    and (ib.date - ia.date).days <= 30
                    and not h1_done
                ):
                    when = weekday_on_or_after(
                        ib.date + timedelta(days=7), ia.date + timedelta(days=40)
                    )
                    if when > cap_pay:
                        continue
                    ia.paid_date = when
                    ib.paid_date = when
                    w.notes["h1_pair"] = [ia.number, ib.number]
                    h1_done = True
    # Q12: one blank page in one invoice PDF
    if "Q12" in w.quirks and w.invoices:
        w.invoices[min(3, len(w.invoices) - 1)].blank_page = True
    # POs
    for pn, i in enumerate(sorted(po_idx), start=1):
        inv = w.invoices[i]
        inv.po_ref = f"PO-{''.join(ch for ch in inv.buyer.name if ch.isupper())[:3]}-{rng.randint(1000, 9999)}"
        w.pos.append(
            PurchaseOrder(
                n=pn,
                number=inv.po_ref,
                date=weekday_on_or_before(inv.date - timedelta(days=rng.randint(10, 25))),
                buyer=inv.buyer,
                supplier=inv.supplier,
                lines=copy.deepcopy(inv.lines),
                subtotal=inv.subtotal,
                gst_amount=inv.gst_amount,
                total=inv.total,
                delivery=weekday_on_or_before(inv.date - timedelta(days=2)),
                contract_ref=inv.contract_ref,
            )
        )
    # contracts
    for c in majors:
        mine = [
            inv
            for inv in w.invoices
            if inv.contract_ref and inv.contract_ref.endswith(f"{c.contract_n:03d}")
        ]
        billed = sum((inv.total for inv in mine), D(0))
        value = D(math.ceil(float(billed * D("1.25")) / 10000) * 10000)
        first = min((inv.date for inv in mine), default=date(cal["first"][0], cal["first"][1], 10))
        cdate = weekday_on_or_before(first - timedelta(days=rng.randint(40, 70)))
        eff = weekday_on_or_after(cdate + timedelta(days=rng.randint(5, 15)))
        w.contracts.append(
            Contract(
                n=c.contract_n or 0,
                number=f"C-{cal['inv_year']}-{c.contract_n:03d}",
                title="Supply Agreement for Industrial Fasteners"
                if sc != "kyc"
                else "Freight Services Agreement",
                contract_date=cdate,
                effective=eff,
                end=date(cal["inv_year"] + 1, 6, 30),
                seller=w.company.party,
                buyer=c.party,
                value=q2(value),
                payment_terms=rng.choice(
                    ["30 days from invoice date", "45 days from invoice date", "Net 30"]
                ),
                signatory_seller=w.people[0].name,
                signatory_buyer=nd.make_name(rng, "chinese_order", rng.choice("MF")),
                sig_seller_seed=w.people[0].sig_seed,
                sig_buyer_seed=rng.randrange(1, 10**6),
            )
        )


# ---------------------------------------------------------------------------------- ledger

DESC_OUT = [
    "GIRO PAYMENT TO {cp}",
    "FAST PAYMENT TO {cp}",
    "FUNDS TRANSFER TO {cp}",
    "GIRO PAYMENT TO {cp}",
]
DESC_IN = [
    "FAST PAYMENT FROM {cp}",
    "GIRO CREDIT {cp}",
    "FUNDS TRANSFER FROM {cp}",
    "PAYNOW FROM {cp}",
]


@dataclass
class _Ev:
    date: date
    order: int
    desc: str
    debit: Decimal | None = None
    credit: Decimal | None = None
    c4: str | None = None
    c5: str = "other"
    cp: str | None = None
    ref: str | None = None
    kind: str = "misc"
    discretionary: bool = False
    fee: bool = False


def _plan_trade(
    w: World, rng: random.Random, trade_target: Decimal, months: list[tuple[int, int]]
) -> dict[str, Any]:
    """Credits that are fixed in advance: invoice payments, retainer, other customers."""
    paid = [inv for inv in w.invoices if inv.status == "paid"]
    inv_total = sum((inv.total for inv in paid), D(0))
    retainer = D(max(2000, int(round(float(trade_target) / len(months) * 0.07 / 1000)) * 1000))
    if w.company_index == 0 and w.scenario == "loan":
        retainer = D(5000)
    other_total = q2(trade_target - inv_total - retainer * len(months))
    if other_total < trade_target * D("0.05"):
        other_total = q2(trade_target * D("0.12"))
    return {"retainer": retainer, "other_total": other_total, "paid": paid}


def _statement_for_month(
    w: World,
    rng: random.Random,
    y: int,
    m: int,
    opening: Decimal,
    ctx: dict[str, Any],
    mi: int,
    n_months: int,
    attempt: int,
) -> tuple[list[LedgerRow], Decimal]:
    B: Decimal = ctx["B"]
    floor = q2(B * D("0.15"))
    first, last = month_bounds(y, m)
    wd = lambda d: weekday_on_or_after(d, last)  # noqa: E731

    def rand_day() -> date:
        return wd(first + timedelta(days=rng.randint(0, (last - first).days)))

    ev: list[_Ev] = []
    # --- credits fixed in advance: invoice payments
    for inv in ctx["paid_by_month"].get((y, m), []):
        for amt, dt, part in inv_parts(w, inv):
            if (dt.year, dt.month) != (y, m):
                continue
            show_no = (int(inv.number[-2:]) % 2 == 0) or part is not None
            desc = rng.choice(DESC_IN).format(cp=ctx["cp_of"][inv.buyer.name]) + (
                f" {inv.number}" if show_no else ""
            )
            ev.append(
                _Ev(
                    dt,
                    1,
                    desc,
                    credit=amt,
                    c4="trade_receipt",
                    c5=ctx["label_of"][inv.buyer.name],
                    cp=ctx["cp_of"][inv.buyer.name],
                    ref=f"invoice:{inv.number}",
                    kind="trade",
                )
            )
    # --- retainer (Q08)
    if "Q08" in w.quirks:
        rc = ctx["retainer_customer"]
        ev.append(
            _Ev(
                wd(first + timedelta(days=2)),
                1,
                f"RETAINER FEE {rc.counterparty}",
                credit=ctx["retainer"],
                c4="customer_payment",
                c5=rc.label,
                cp=rc.counterparty,
                kind="retainer",
            )
        )
    else:
        ctx["other_total"] += ctx["retainer"]
    # --- other customers
    n_other = rng.randint(4, 7)
    share = ctx["other_total"] * D(str(ctx["month_w"][mi]))
    for amt in spread(rng, q2(share), n_other):
        cust = rng.choice(ctx["other_customers"])
        ev.append(
            _Ev(
                rand_day(),
                1,
                rng.choice(DESC_IN).format(cp=cust.counterparty),
                credit=amt,
                c4="customer_payment",
                c5=cust.label,
                cp=cust.counterparty,
                kind="other_cust",
            )
        )
    # --- interest (Q01) and non-trade credits (Q05)
    if "Q01" in w.quirks:
        ev.append(
            _Ev(
                weekday_on_or_before(last),
                1,
                "INTEREST CREDIT",
                credit=q2(D(rng.uniform(0.8, 7.5))),
                c4="other",
                c5="other",
                kind="interest",
            )
        )
    for nt in ctx["nontrade"].get(mi, []):
        ev.append(
            _Ev(rand_day(), 1, nt[0], credit=nt[1], c4=nt[2], c5=nt[3], cp=nt[4], kind="nontrade")
        )
    # --- debits
    sup_total = q2(B * D(str(rng.uniform(0.54, 0.64))) * D(str(ctx["sup_scale"])))
    n_sup = rng.randint(8, 12)
    sup_amts = spread(rng, sup_total, n_sup, sigma=0.9)
    for amt in sup_amts:
        cp = rng.choice(ctx["suppliers"])
        ev.append(
            _Ev(
                rand_day(),
                2,
                rng.choice(DESC_OUT).format(cp=cp),
                debit=amt,
                c5=ctx["sup_label"][cp],
                cp=cp,
                kind="supplier",
                discretionary=True,
            )
        )
    for _ in range(rng.randint(2, 6)):
        cp = rng.choice(ctx["suppliers"])
        ev.append(
            _Ev(
                rand_day(),
                2,
                rng.choice(DESC_OUT).format(cp=cp),
                debit=q2(D(rng.uniform(90, 950))),
                c5=ctx["sup_label"][cp],
                cp=cp,
                kind="misc_sup",
                discretionary=True,
            )
        )
    ev.append(
        _Ev(
            wd(date(y, m, 25)),
            2,
            "GIRO SALARY PAYMENT",
            debit=q2(B * D(str(rng.uniform(0.11, 0.125)))),
            c5="other",
            cp="PAYROLL",
            kind="payroll",
        )
    )
    ev.append(
        _Ev(
            wd(date(y, m, 14)),
            2,
            "CPF CONTRIBUTION",
            debit=q2(B * D(str(rng.uniform(0.019, 0.023)))),
            c5="other",
            cp="CPF",
            kind="cpf",
        )
    )
    ev.append(
        _Ev(
            wd(first),
            2,
            f"RENT {ctx['rent_label']}",
            debit=ctx["rent"],
            c5="other",
            cp="LANDLORD",
            kind="rent",
        )
    )
    ev.append(
        _Ev(
            wd(date(y, m, 15)),
            2,
            "BUILDING SERVICE CHARGE",
            debit=ctx["service"],
            c5="other",
            cp="LANDLORD",
            kind="service",
        )
    )
    ev.append(
        _Ev(
            wd(date(y, m, 8)),
            2,
            "CITYGRID UTILITIES",
            debit=q2(B * D(str(rng.uniform(0.008, 0.013)))),
            c5="other",
            cp="CITYGRID UTILITIES",
            kind="utility",
        )
    )
    ev.append(
        _Ev(
            wd(date(y, m, 19)),
            2,
            "LINKNET TELECOM",
            debit=q2(D(rng.uniform(180, 420))),
            c5="other",
            cp="LINKNET TELECOM",
            kind="telco",
        )
    )
    ev.append(
        _Ev(
            wd(date(y, m, 20)),
            2,
            "TERM LOAN INSTALMENT",
            debit=ctx["loan_inst"],
            c5="other",
            cp="TERM LOAN",
            kind="loan",
        )
    )
    if mi % 3 == 0:
        ev.append(
            _Ev(
                wd(date(y, m, 11)),
                2,
                "COVERLINE INSURANCE PREMIUM",
                debit=q2(B * D("0.012")),
                c5="other",
                cp="COVERLINE INSURANCE",
                kind="insurance",
            )
        )
    if w.scenario in ("loan", "aml") and m in ctx["gst_months"]:
        ev.append(
            _Ev(
                wd(date(y, m, 28)),
                2,
                "IRAS GST PAYMENT",
                debit=q2(B * D(str(rng.uniform(0.07, 0.10)))),
                c5="other",
                cp="IRAS",
                kind="gst",
            )
        )
    if "Q01" in w.quirks:
        ev.append(
            _Ev(
                weekday_on_or_before(last),
                3,
                "ACCOUNT SERVICE CHARGE",
                debit=D("15.00"),
                c5="other",
                kind="svc_fee",
            )
        )
    # H2: two identical same-day same-amount payments to the same counterparty
    if "H2" in w.quirks and mi == 1:
        cp = ctx["suppliers"][0]
        dt = rand_day()
        amt = q2(D(rng.uniform(1800, 4200)))
        for _ in range(2):
            ev.append(
                _Ev(
                    dt,
                    2,
                    f"FAST PAYMENT TO {cp}",
                    debit=amt,
                    c5=ctx["sup_label"][cp],
                    cp=cp,
                    kind="dup",
                    fee=True,
                )
            )
    # fees on outgoing FAST/GIRO payments
    if "Q01" in w.quirks:
        for e in list(ev):
            if e.kind in ("supplier", "misc_sup") and rng.random() < 0.5:
                ev.append(
                    _Ev(
                        e.date,
                        3,
                        "FAST/GIRO FEE",
                        debit=q2(D(rng.choice([0.15, 0.20, 0.35, 0.50]))),
                        c5="other",
                        kind="fee",
                    )
                )
    # overdraft month (Q07): the largest supplier payment becomes the overdraft
    od_idx = ctx.get("overdraft_month")
    if od_idx == mi:
        cands = sorted((e for e in ev if e.kind == "supplier"), key=lambda e: -(e.debit or 0))
        big = [e for e in ev if e.credit is not None and e.kind in ("trade", "other_cust")]
        late = [
            e for e in big if 4 <= e.date.day <= 14
        ]  # early in the month, so the account recovers
        if cands and big:
            tgt_credit = max(late or big, key=lambda e: e.credit or 0)
            od = cands[0]
            od.date = max(first, tgt_credit.date)
            if od.date.day < 3:
                od.date = wd(first + timedelta(days=2))
            od.order = 0
            od.kind = "overdraft"
            od.discretionary = False
            tgt_credit.date = max(tgt_credit.date, od.date)
            tgt_credit.order = 1
    ev.sort(key=lambda e: (e.date, e.order))
    rows: list[LedgerRow] = []
    bal = opening
    for e in ev:
        debit, credit = e.debit, e.credit
        if e.kind == "overdraft":
            debit = q2(bal + D(rng.randint(600, 3500)) + D("0.19"))
        elif e.discretionary and debit is not None:
            room = bal - floor
            if room < debit:
                debit = q2(room) if room >= 300 else None
        if debit is None and credit is None:
            continue
        bal = q2(bal + (credit or D(0)) - (debit or D(0)))
        rows.append(LedgerRow(e.date, e.desc, debit, credit, bal, e.c4, e.c5, e.cp, e.ref, e.kind))
    return rows, bal


def inv_parts(w: World, inv: Invoice) -> list[tuple[Decimal, date, int | None]]:
    """(amount, date, part) credits that pay ``inv`` (Q04 instalments split in two)."""
    assert inv.paid_date is not None
    if w.notes.get("instalment_invoice") == inv.number:
        first = q2(inv.total * D("0.55"))
        second = q2(inv.total - first)
        d1 = weekday_on_or_before(inv.paid_date - timedelta(days=6), inv.date + timedelta(days=5))
        if d1 >= inv.paid_date:
            d1 = weekday_on_or_before(inv.paid_date - timedelta(days=1))
        return [(first, d1, 1), (second, inv.paid_date, 2)]
    if w.notes.get("h1_pair") and inv.number in w.notes["h1_pair"]:
        other = [
            i for i in w.invoices if i.number in w.notes["h1_pair"] and i.number != inv.number
        ][0]
        if inv.number == w.notes["h1_pair"][0]:
            return [(q2(inv.total + other.total), inv.paid_date, None)]
        return []
    return [(inv.total, inv.paid_date, None)]


def _build_ledger(
    w: World,
    rng: random.Random,
    trade_target: Decimal,
    months: list[tuple[int, int]],
    hard_pool: set[str],
    used: set[str],
    suppliers: list[str],
) -> None:
    n_months = len(months)
    plan = _plan_trade(w, rng, trade_target, months)
    B = q2(trade_target / n_months)
    others = [c for c in w.customers if not c.major][5:] or [c for c in w.customers if not c.major]
    retainer_customer = (others or w.customers)[0]
    cp_of = {c.party.name: c.counterparty for c in w.customers}
    label_of = {c.party.name: c.label for c in w.customers}
    month_w = [rng.uniform(0.8, 1.2) for _ in range(n_months)]
    sw = sum(month_w)
    month_w = [x / sw for x in month_w]
    paid_by_month: dict[tuple[int, int], list[Invoice]] = {}
    for inv in plan["paid"]:
        for _a, dt, _p in inv_parts(w, inv):
            paid_by_month.setdefault((dt.year, dt.month), []).append(inv)
    for key, lst in paid_by_month.items():
        seen, uniq = set(), []
        for i in lst:
            if i.number not in seen:
                seen.add(i.number)
                uniq.append(i)
        paid_by_month[key] = uniq
    # non-trade credits (Q05): director injection, supplier refund, financing proceeds
    nontrade: dict[int, list[tuple[str, Decimal, str, str, str | None]]] = {}
    if "Q05" in w.quirks:
        nt_total = trade_target * D("0.10")
        director = w.people[0]
        nontrade.setdefault(1, []).append(
            (
                f"FUNDS TRANSFER FROM {director.registry_name} DIRECTOR LOAN",
                q2(nt_total * D("0.42")),
                "owner_transfer",
                "other",
                director.registry_name,
            )
        )
        nontrade.setdefault(min(3, n_months - 1), []).append(
            (
                "REFUND FROM " + suppliers[1],
                q2(nt_total * D("0.08")),
                "refund",
                lx.label_from_tokens(suppliers[1]) or "trading",
                suppliers[1],
            )
        )
        nontrade.setdefault(min(2, n_months - 1), []).append(
            (
                "TERM LOAN DISBURSEMENT MOCK BANK",
                q2(nt_total * D("0.40")),
                "financing_proceeds",
                "other",
                "TERM LOAN",
            )
        )
        nontrade.setdefault(n_months - 1, []).append(
            (
                "REFUND FROM " + suppliers[3],
                q2(nt_total * D("0.10")),
                "refund",
                lx.label_from_tokens(suppliers[3]) or "trading",
                suppliers[3],
            )
        )
    rent = D(max(1200, int(round(float(B) * 0.06 / 100)) * 100))
    if w.company_index == 0 and w.scenario == "loan":
        rent = D("4500.00")
    ctx = {
        "B": B,
        "retainer": plan["retainer"],
        "other_total": plan["other_total"],
        "month_w": month_w,
        "paid_by_month": paid_by_month,
        "cp_of": cp_of,
        "label_of": label_of,
        "retainer_customer": retainer_customer,
        "other_customers": others or w.customers,
        "suppliers": suppliers,
        "sup_label": {cp: (lx.label_from_tokens(cp) or "trading") for cp in suppliers},
        "nontrade": nontrade,
        "rent": q2(rent),
        "service": D("380.00"),
        "rent_label": w.company.party.address.split(",")[0][:28].upper(),
        "loan_inst": q2(B * D("0.035") // 10 * 10 + D("0.00")),
        "gst_months": {8, 11} if w.scenario == "loan" else {7, 10},
        "overdraft_month": (
            n_months // 2 + 1
            if "Q07" in w.quirks and n_months > 3
            else (1 if "Q07" in w.quirks else None)
        ),
        "sup_scale": 1.0,
    }
    ctx["loan_inst"] = q2(D(int(float(B) * 0.035 // 10 * 10)))
    opening = q2(B * D(str(rng.uniform(0.8, 1.2))))
    first_open = opening
    acct = w.company.account_number
    holder = w.company.party.name
    for mi, (y, m) in enumerate(months):
        # a cash controller: spend more on suppliers while the balance runs above about one
        # month of trade credits, less when it runs low, so no month starts with an unusually
        # large pile of cash (which would make the Q07 overdraft month unrecoverable)
        ctx["sup_scale"] = float(max(D("0.75"), min(D("1.7"), 1 + (opening - B) / (B * D("0.6")))))
        ok = False
        for attempt in range(450):
            r = stream(w.seed, w.scenario, w.company_index, f"ledger:{y}-{m:02d}:{attempt}")
            # the ctx other_total is consumed destructively only for Q08-less runs; copy it
            c2 = dict(ctx)
            rows, closing = _statement_for_month(w, r, y, m, opening, c2, mi, n_months, attempt)
            n = len(rows)
            neg = any(x.balance < 0 for x in rows)
            want_od = ctx["overdraft_month"] == mi
            if (
                34 <= n <= 58 - (1 if w.layout == "dbs_style" else 0)
                and (neg == want_od)
                and closing
                > B * D("0.12" if attempt < 200 else ("0.04" if attempt < 350 else "0.0"))
            ):
                ok = True
                break
        if not ok:
            raise RuntimeError(f"could not build a statement for {y}-{m:02d} (rows={n}, neg={neg})")
        first, last = month_bounds(y, m)
        st = Statement(
            month=f"{y}-{m:02d}",
            account_number=acct,
            account_holder=holder,
            uen=w.company.party.uen,
            bank_name=w.company.bank_name or None,
            period_start=first,
            period_end=last,
            opening=opening,
            closing=closing,
            total_debits=q2(sum((x.debit or D(0) for x in rows), D(0))),
            total_credits=q2(sum((x.credit or D(0) for x in rows), D(0))),
            rows=rows,
            layout=w.layout,
        )
        if w.layout == "dbs_style":
            st.brought_forward_row = True
            st.rows.insert(
                0,
                LedgerRow(
                    first,
                    "BALANCE BROUGHT FORWARD",
                    None,
                    None,
                    opening,
                    None,
                    "other",
                    None,
                    None,
                    "bf",
                ),
            )
        w.statements.append(st)
        opening = closing
    w.notes["first_opening"] = first_open
    _assign_hard_rows(w, rng, hard_pool, used)


def _assign_hard_rows(w: World, rng: random.Random, hard_pool: set[str], used: set[str]) -> None:
    """Rename ~10% of rows to hard counterparties (no lexicon token, truth label kept)."""
    rows = [
        (st, r)
        for st in w.statements
        for r in st.rows
        if r.kind in ("supplier", "misc_sup", "other_cust")
    ]
    total = sum(1 for st in w.statements for r in st.rows)
    k = round(0.10 * total)
    pick = rng.sample(range(len(rows)), min(k, len(rows)))
    names: dict[tuple[str, str], str] = {}
    for i in sorted(pick):
        st, r = rows[i]
        old = r.counterparty or ""
        key = (old, r.c5)
        if key not in names:
            names[key] = lx.make_hard_counterparty(rng, used)
        new = names[key]
        r.description = r.description.replace(old, new) if old else r.description
        r.counterparty = new
        r.kind = r.kind + "_hard"


# --------------------------------------------------------------------------- financials


def _build_fs(w: World, rng: random.Random, revenue: Decimal) -> None:
    last = w.statements[-1]
    cash = wfs.whole(last.closing)
    w.fs = wfs.build_fs_documents(
        rng,
        company_name=w.company.party.name,
        uen=w.company.party.uen,
        revenue25=revenue,
        cash25=cash,
        share_capital=D(int(w.company.paid_up_capital)),
        unaudited_fy2025="Q14" in w.quirks,
        thousands_fy2024="Q06" in w.quirks,
        auditor="Harbourview Assurance LLP",
    )


# ----------------------------------------------------------------------------- build_world


def build_world(
    scenario: str,
    company_index: int = 0,
    seed: int = 4002,
    today: date = FIXED_TODAY,
    quirks: list[str] | None = None,
    *,
    layout: str | None = None,
    invoice_count: int | None = None,
    statement_months: int | None = None,
) -> World:
    from .quirks import default_quirks

    cal = CALENDAR[scenario]
    quirks = list(quirks if quirks is not None else default_quirks(company_index))
    if layout is None:
        layout = (
            cal["layout"]
            if company_index == 0 or company_index >= 90
            else LAYOUTS[company_index % 4]
        )
    w = World(
        scenario=scenario,
        company_index=company_index,
        seed=seed,
        today=today,
        quirks=quirks,
        layout=layout,
    )
    n_months = statement_months or cal["months"]
    w.notes["months"] = n_months
    w.notes["invoice_count"] = invoice_count
    months = add_months(cal["first"], n_months)
    _make_company(w, w.rng("identity"))
    _make_people(w, w.rng("people"))
    rng_c = w.rng("customers")
    suppliers, used = _make_customers(w, rng_c)
    # revenue and the trade-credit target
    if cal["fs"]:
        if company_index == 0:
            revenue = D(900000)
        else:
            revenue = D(int(w.rng("plan").uniform(500_000, 1_600_000) // 10000 * 10000))
        covered = (months_end(months) - date(months[0][0], months[0][1], 1)).days + 1
        trade = q2(revenue * D(covered) / D(365) * D(str(w.rng("plan2").uniform(0.97, 1.03))))
        w.notes["revenue"] = revenue
    else:
        trade = q2(D(str(w.rng("plan").uniform(60000, 130000))) * n_months)
    w.notes["trade_target"] = trade
    _make_invoices(w, w.rng("invoices"), trade, set())
    _build_ledger(w, w.rng("ledger"), trade, months, set(), used, suppliers)
    if cal["fs"]:
        _build_fs(w, w.rng("fs"), revenue)
    return w


def months_end(months: list[tuple[int, int]]) -> date:
    return month_bounds(*months[-1])[1]


def world_from_spec(spec: BundleSpec) -> World:
    return build_world(
        spec.scenario,
        spec.company_index,
        spec.seed,
        spec.today,
        spec.quirks,
        layout=spec.layout,
        invoice_count=spec.invoice_count,
        statement_months=spec.statement_months,
    )


# ----------------------------------------------------------------------------------- registry


def registry_entity(w: World) -> dict[str, Any]:
    c = w.company
    return {
        "uen": c.party.uen,
        "entity_name": c.party.name,
        "entity_type": c.entity_type,
        "status": c.status,
        "incorporation_date": c.incorporation.isoformat(),
        "registered_address": c.party.address,
        "principal_activity": c.activity,
        "ssic_code": c.ssic,
        "paid_up_capital": format(c.paid_up_capital, "f"),
        "currency": c.currency,
        "officers": [
            {
                "name": p.registry_name,
                "id_number": p.id_number,
                "nationality": p.nationality if p.id_kind == "nric" else p.nationality[:2],
                "position": p.position,
                "appointment_date": p.appointment.isoformat(),
                "cessation_date": None,
            }
            for p in w.people
            if p.in_registry and p.is_director
        ],
        "shareholders": [
            {
                "name": h.registry_name or h.name,
                "id_or_uen": h.id_or_uen,
                "holder_type": h.holder_type,
                "shares": h.shares,
                "percent": format(h.percent, "f"),
                "share_class": h.share_class,
            }
            for h in w.holders
        ],
    }


# ------------------------------------------------------------------------ mutation primitives


def recompute_chain(w: World, stmt_key: str, from_row: int = 0, propagate: bool = True) -> None:
    """Recompute running balances from ``from_row`` of the statement, then its closing balance
    and printed totals; when ``propagate`` every later statement is recomputed too (opening =
    previous closing)."""
    month = stmt_key.split(":", 1)[1] if ":" in stmt_key else stmt_key
    order = sorted(w.statements, key=lambda s: s.month)
    started = False
    for st in order:
        if st.month == month:
            started = True
            _recompute(st, from_row)
        elif started and propagate:
            prev = order[order.index(st) - 1]
            st.opening = prev.closing
            _recompute(st, 0)
        elif started:
            break


def _recompute(st: Statement, from_row: int) -> None:
    bal = st.opening if from_row == 0 else st.rows[from_row - 1].balance
    for i, r in enumerate(st.rows):
        if i < from_row:
            continue
        bal = q2(bal + (r.credit or D(0)) - (r.debit or D(0)))
        r.balance = bal
    st.closing = st.rows[-1].balance if st.rows else st.opening
    st.total_debits = q2(sum((r.debit or D(0) for r in st.rows), D(0)))
    st.total_credits = q2(sum((r.credit or D(0) for r in st.rows), D(0)))


def add_transactions(
    w: World,
    stmt_key: str,
    rows: list[LedgerRow],
    *,
    recompute: bool = True,
    propagate: bool = True,
) -> None:
    """Insert rows in date order (after rows of equal date) and optionally recompute."""
    month = stmt_key.split(":", 1)[1]
    st = w.statement(month)
    for r in rows:
        pos = len(st.rows)
        for i, ex in enumerate(st.rows):
            if ex.kind != "bf" and ex.date > r.date:
                pos = i
                break
        st.rows.insert(pos, r)
    if recompute:
        recompute_chain(w, stmt_key, 0, propagate)


def add_person(
    w: World,
    name: str,
    *,
    group: str = "chinese_order",
    sex: str = "M",
    birth_year: int = 1980,
    on_profile: bool = True,
    in_registry: bool = False,
    is_director: bool = True,
    id_kind: str = "nric",
    nationality: str = "SG",
) -> Person:
    rng = w.rng(f"add_person:{len(w.people)}")
    birth = date(birth_year, rng.randint(1, 12), rng.randint(1, 28))
    idnum = (
        idm.random_nric(rng, birth_year)
        if id_kind == "nric"
        else "".join(rng.choice("ABCDEFGH") for _ in range(2))
        + "".join(str(rng.randrange(10)) for _ in range(6))
    )
    p = Person(
        key=f"p{len(w.people)}",
        name=name,
        group=group,
        sex=sex,
        birth=birth,
        id_number=idnum,
        nationality=nationality,
        id_kind=id_kind,
        appointment=w.company.incorporation,
        registry_name=nd.registry_form(name, group, w.company_index),
        address=nd.address(rng),
        issue_date=date(birth_year + 18, 6, 1),
        is_director=is_director,
        on_profile=on_profile,
        in_registry=in_registry,
        sig_seed=rng.randrange(1, 10**6),
    )
    w.people.append(p)
    return p


def add_holder(w: World, holder: Holder) -> None:
    w.holders.append(holder)


# ----------------------------------------------------------------------------------- plots


def _find_row_cell(st: Statement, row: int, column: str) -> tuple[LedgerRow, str]:
    return st.rows[row], column


def apply_plot(world: World, mutations: list[Mutation]) -> World:
    """Apply world-channel mutations to a deep copy of ``world`` and return it.

    Ops (``target`` is a doc_key; ``-`` for world level):

    * ``override_cell``: statements ``{table:"transactions", row, column, value}`` or
      ``{field:"closing_balance"|"opening_balance"|"total_debits"|"total_credits"|"account_holder"|"uen", value}``;
      invoices ``{field, value}`` (``subtotal gst_amount total invoice_date due_date payment_status paid_date
      supplier_uen supplier_gst_no buyer_name``) or ``{table:"lines", row, column:"amount"|..., value}``;
      financial statements ``{table, line, column:"current"|"prior", value, recompute:"none"|"subtotals"|"forger"}``;
      people ``{field:"id_number"|"date_of_birth"}``.
    * ``recompute_chain``: ``{from_row=0, propagate=true}``.
    * ``drop_document``: removes the document from the bundle.
    * ``drop_page``: ``{page}`` of a statement is not printed (its rows vanish from the page chain).
    * ``insert_rows``: statements ``{date, description, debit|credit, c4, c5, counterparty, recompute=true}``.
    * ``set_field``: like ``override_cell`` for document-level fields; additionally
      ``{field:"subtotal_target", value}`` rebuilds an invoice so its subtotal equals the value.
    * ``swap_party``: ``{role:"account_holder"|"supplier"|"buyer"|"company", name, uen}``.
    * ``reuse_signature``: contract ``{from:"contract:1"}`` reuses that contract's signature images.
    * ``set_producer``: ``{producer, creator?, mod_days?}`` Info overrides applied at render.
    """
    w = copy.deepcopy(world)
    for m in mutations:
        _apply_one(w, m)
    return w


def _doc_obj(w: World, key: str) -> Any:
    kind, _, suffix = key.partition(":")
    if kind == "bank_statement":
        return w.statement(suffix)
    if kind == "invoice":
        return w.invoice(suffix)
    if kind == "financial_statement":
        return w.fs[suffix]
    if kind == "contract":
        return next(c for c in w.contracts if c.n == int(suffix))
    if kind == "purchase_order":
        return next(p for p in w.pos if p.n == int(suffix))
    if kind == "national_id":
        return w.people[int(suffix) - 1]
    raise KeyError(key)


def _dec(v: Any) -> Decimal:
    return D(str(v))


def _apply_one(w: World, m: Mutation) -> None:
    a = m.args
    op = m.op
    if op == "drop_document":
        w.dropped_docs.add(m.target)
        return
    if op == "set_producer":
        w.producer_overrides[m.target] = {k: v for k, v in a.items()}
        return
    obj = _doc_obj(w, m.target)
    kind = m.target.split(":", 1)[0]
    if op == "recompute_chain":
        recompute_chain(w, m.target, int(a.get("from_row", 0) or 0), bool(a.get("propagate", True)))
    elif op == "drop_page":
        obj.dropped_pages.append(int(a["page"]))
    elif op == "insert_rows":
        row = LedgerRow(
            date=date.fromisoformat(str(a["date"])),
            description=str(a["description"]),
            debit=_dec(a["debit"]) if a.get("debit") is not None else None,
            credit=_dec(a["credit"]) if a.get("credit") is not None else None,
            balance=D(0),
            c4=a.get("c4"),  # type: ignore[arg-type]
            c5=str(a.get("c5") or "other"),
            counterparty=a.get("counterparty"),  # type: ignore[arg-type]
            kind="inserted",
        )
        add_transactions(
            w,
            m.target,
            [row],
            recompute=bool(a.get("recompute", True)),
            propagate=bool(a.get("propagate", True)),
        )
    elif op == "reuse_signature":
        obj.sig_reuse_from = str(a["from"])
    elif op == "swap_party":
        _swap_party(w, obj, kind, a)
    elif op in ("override_cell", "set_field"):
        _set(w, obj, kind, a)
    else:  # pragma: no cover
        raise ValueError(op)


def _swap_party(w: World, obj: Any, kind: str, a: dict[str, Any]) -> None:
    name, uen = a.get("name"), a.get("uen")
    role = a.get("role")
    if kind == "bank_statement":
        obj.account_holder = str(name)
        obj.uen = str(uen) if uen else None
    elif kind == "invoice":
        target = obj.supplier if role == "supplier" else obj.buyer
        new = copy.copy(target)
        if name:
            new.name = str(name)
        if uen is not None:
            new.uen = str(uen)
        if role == "supplier":
            obj.supplier = new
        else:
            obj.buyer = new
    elif kind == "financial_statement":
        obj.company_name = str(name)
        obj.uen = str(uen) if uen else obj.uen
    else:
        raise ValueError(f"swap_party on {kind}")


def _set(w: World, obj: Any, kind: str, a: dict[str, Any]) -> None:
    v = a.get("value")
    if kind == "bank_statement":
        if a.get("table") == "transactions":
            r = obj.rows[int(a["row"])]
            col = str(a["column"])
            if col in ("debit", "credit", "balance"):
                setattr(r, col, _dec(v) if v is not None else None)
            elif col == "date":
                r.date = date.fromisoformat(str(v))
            elif col == "description":
                r.description = str(v)
        else:
            f = str(a["field"])
            if f in ("closing_balance", "opening_balance"):
                setattr(obj, "closing" if f.startswith("closing") else "opening", _dec(v))
            elif f in ("total_debits", "total_credits", "account_number", "account_holder", "uen"):
                setattr(
                    obj,
                    f,
                    _dec(v) if f.startswith("total") else (str(v) if v is not None else None),
                )
            else:
                raise ValueError(f)
    elif kind == "invoice":
        if a.get("table") == "lines":
            ln = obj.lines[int(a["row"])]
            col = str(a["column"])
            setattr(ln, col, str(v) if col == "description" else _dec(v))
        else:
            f = str(a["field"])
            if f == "subtotal_target":
                _rebuild_invoice(obj, _dec(v))
            elif f in ("subtotal", "gst_amount", "total"):
                setattr(obj, f, _dec(v))
            elif f in ("invoice_date", "due_date", "paid_date"):
                setattr(
                    obj,
                    {"invoice_date": "date", "due_date": "due", "paid_date": "paid_date"}[f],
                    date.fromisoformat(str(v)) if v else None,
                )
            elif f == "payment_status":
                obj.status = str(v) if v else None
            elif f in (
                "supplier_uen",
                "supplier_gst_no",
                "supplier_name",
                "buyer_name",
                "buyer_uen",
            ):
                role, attr = f.split("_", 1)
                party = copy.copy(obj.supplier if role == "supplier" else obj.buyer)
                setattr(party, {"uen": "uen", "gst_no": "gst_no", "name": "name"}[attr], str(v))
                if role == "supplier":
                    obj.supplier = party
                else:
                    obj.buyer = party
            elif f == "recompute_total":
                obj.total = q2(obj.subtotal + obj.gst_amount)
            else:
                raise ValueError(f)
    elif kind == "financial_statement":
        line = wfs.find_line(obj, str(a["table"]), str(a["line"]))
        col = str(a.get("column", "current"))
        mode = str(a.get("recompute", "none"))
        if mode == "forger" and a["line"] == "revenue":
            wfs.forge_revenue(obj, _dec(v))
            return
        setattr(line, col, _dec(v) if v is not None else None)
        if mode == "subtotals":
            wfs.recompute_subtotals(obj, str(a["table"]))
    elif kind == "contract":
        f = str(a["field"])
        setattr(obj, {"contract_value": "value"}.get(f, f), _dec(v) if f == "contract_value" else v)
    elif kind == "national_id":
        f = str(a["field"])
        if f == "id_number":
            obj.id_number = str(v)
        elif f == "date_of_birth":
            obj.birth = date.fromisoformat(str(v))
        else:
            raise ValueError(f)
    else:
        raise ValueError(f"cannot set fields on {kind}")


def _rebuild_invoice(inv: Invoice, target: Decimal) -> None:
    """Make the invoice subtotal equal ``target`` by adding one variation line, then reprice."""
    current = sum((ln.amount for ln in inv.lines), D(0))
    extra = q2(target - current)
    if extra > 0:
        inv.lines.append(InvLine("Variation order - additional consignment", D(1), extra, extra))
    _price_invoice(inv)
