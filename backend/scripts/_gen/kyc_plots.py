"""The KYC and AML fraud stories (deck slides 14 and 15) as world edits.

Every function takes a World and returns a new one (deep copy); the clean world is never touched.
Streams are ``plot:*`` keyed by ``(seed, scenario, company_index)`` and *not* by variant, so
``fraud`` and ``forger`` make the same picks and differ only in the planted slip.

KYC (docs 4.2): crypto counterparties in a logistics company's statements, a fourth profile
director who is not a registry officer, a chart that omits a registry shareholder.
AML: a SoF claim for S$1,000,000.00 from a buyer who never pays, three credits from other companies
that are forwarded within days, and the invoice behind the claim.
"""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from decimal import Decimal

from . import ids as idm
from . import lexicon as lx
from . import names_data as nd
from .kyc_view import Sof, omit_holder, view_of
from .world import (
    World,
    add_transactions,
    month_bounds,
    q2,
    recompute_chain,
    spread,
    stream,
    weekday_on_or_after,
)
from .world_types import InvLine, Invoice, LedgerRow, Party

D = Decimal
CRYPTO = "crypto_virtual_assets"
PT_WINDOW_DAYS, PT_TOL_PCT, PT_MIN_AMOUNT = 5, D("0.03"), D("5000.00")
MAX_ROWS = 66  # three statement pages of 22 rows


# ------------------------------------------------------------------------------ shared


def all_rows(w: World) -> list[tuple[str, int, LedgerRow]]:
    return [(st.key, i, r) for st in w.statements for i, r in enumerate(st.rows)]


def turnover(w: World) -> Decimal:
    return sum(((r.debit or r.credit or D(0)) for _k, _i, r in all_rows(w)), D(0))


def negative_months(w: World) -> set[str]:
    return {st.month for st in w.statements if any(r.balance < 0 for r in st.rows)}


def pass_through_hops(w: World) -> list[tuple[LedgerRow, LedgerRow]]:
    """The ``bs_pass_through`` pairing (checks-kyc-aml 4.10) re-implemented on the world: a credit
    of at least 5,000.00 followed within 5 days by a debit within 3% to another entity."""
    rows = [r for _k, _i, r in all_rows(w)]
    credits = [r for r in rows if r.credit is not None and r.credit >= PT_MIN_AMOUNT]
    credits = [r for r in credits if r.c4 != "refund" and r.counterparty]
    debits = [r for r in rows if r.debit is not None and r.counterparty]
    used: set[int] = set()
    hops: list[tuple[LedgerRow, LedgerRow]] = []
    for cr in sorted(credits, key=lambda r: r.date):
        assert cr.credit is not None
        best: tuple[int, Decimal, int] | None = None
        for j, db in enumerate(debits):
            assert db.debit is not None
            gap = (db.date - cr.date).days
            if (
                j in used
                or not 0 <= gap <= PT_WINDOW_DAYS
                or db.counterparty == cr.counterparty
                or abs(cr.credit - db.debit) > max(D("1.00"), PT_TOL_PCT * cr.credit)
            ):
                continue
            cand = (gap, abs(cr.credit - db.debit), j)
            if best is None or cand < best:
                best = cand
        if best is not None:
            used.add(best[2])
            hops.append((cr, debits[best[2]]))
    return hops


def pass_through_pattern(w: World) -> bool:
    """True when ``bs_pass_through`` would fire (3 hops, 3 entities, 20% of credits)."""
    hops = pass_through_hops(w)
    ents = {h[0].counterparty for h in hops} | {h[1].counterparty for h in hops}
    credits_all = sum((r.credit or D(0) for _k, _i, r in all_rows(w)), D(0))
    routed = sum((h[0].credit or D(0) for h in hops), D(0))
    return (
        len(hops) >= 3 and len(ents) >= 3 and credits_all > 0 and routed / credits_all >= D("0.2")
    )


def crypto_share(w: World) -> Decimal:
    """Share of the statements' transaction value whose counterparty is a crypto/VA business."""
    tot = turnover(w)
    cry = sum(((r.debit or r.credit or D(0)) for _k, _i, r in all_rows(w) if r.c5 == CRYPTO), D(0))
    return cry / tot if tot else D(0)


# ------------------------------------------------------------------------------------ KYC


def plant_crypto(w: World) -> World:
    """Insert 12 crypto-counterparty rows (4 per month) worth 37-40% of the resulting turnover.

    Credits and debits are spread so the balances stay healthy, the overdraft month of the clean
    world stays the only negative month, and no credit/debit pair looks like a pass-through (the
    careful forger must not trip ``bs_*``). Retries with fresh sub-streams until all hold."""
    base_neg = negative_months(w)
    used = {r.counterparty for _k, _i, r in all_rows(w) if r.counterparty}
    base_turn = turnover(w)
    n_months = len(w.statements)
    for attempt in range(600):
        rng = stream(w.seed, w.scenario, w.company_index, f"plot:crypto:{attempt}")
        names = [lx.make_counterparty(rng, CRYPTO, used) for _ in range(4)]
        cand = copy.deepcopy(w)
        total = q2(base_turn * D(str(rng.uniform(0.60, 0.68))))
        weights = [rng.uniform(0.85, 1.15) for _ in range(n_months)]
        wsum = sum(weights)
        for st, wt in zip(cand.statements, weights, strict=True):
            v_m = q2(total * D(str(wt / wsum)))
            share_cr = D(str(rng.uniform(0.47, 0.50)))
            cr_amts = spread(rng, q2(v_m * share_cr), 2, sigma=0.4)
            db_amts = spread(rng, q2(v_m - q2(v_m * share_cr)), 2, sigma=0.4)
            first, last = month_bounds(*map(int, st.month.split("-")))
            days = sorted(
                {
                    weekday_on_or_after(first + timedelta(days=rng.randint(15, 27)), last)
                    for _ in range(12)
                }
            )
            if len(days) < 4:
                break
            pick = sorted(rng.sample(days, 4))
            seq = [
                ("cr", cr_amts[0]),
                ("db", db_amts[0]),
                ("cr", cr_amts[1]),
                ("db", db_amts[1]),
            ]
            new_rows = []
            for dt, (kind, amt) in zip(pick, seq, strict=True):
                cp = rng.choice(names)
                if kind == "cr":
                    new_rows.append(
                        LedgerRow(dt, f"FAST PAYMENT FROM {cp}", None, amt, D(0), "other", CRYPTO, cp, None, "crypto_in")
                    )  # fmt: skip
                else:
                    new_rows.append(
                        LedgerRow(dt, f"FAST PAYMENT TO {cp}", amt, None, D(0), None, CRYPTO, cp, None, "crypto_out")
                    )  # fmt: skip
            add_transactions(cand, st.key, new_rows, recompute=False)
        else:
            recompute_chain(cand, cand.statements[0].key, 0, True)
            amounts = [
                r.debit or r.credit for _k, _i, r in all_rows(cand) if r.kind.startswith("crypto")
            ]
            if (
                all(len(st.rows) <= MAX_ROWS for st in cand.statements)
                and negative_months(cand) == base_neg
                and not pass_through_pattern(cand)
                and len(pass_through_hops(cand)) <= 2
                and crypto_share(cand) >= D("0.34")
                and all(a is not None and a % 100 != 0 for a in amounts)
                and len(set(amounts)) == len(amounts)
            ):
                return cand
    raise RuntimeError("could not plant crypto rows that keep the statements healthy")


def plant_director(w: World) -> World:
    """A fourth profile director with an NRIC card who is not a registry officer (CR-80)."""
    out = copy.deepcopy(w)
    if w.company_index == 0:
        name, group, by = "Koh Boon Kiat", "chinese_order", 1979
    else:
        rng = stream(w.seed, w.scenario, w.company_index, "plot:director")
        name, group, by = nd.make_name(rng, "chinese_order", "M"), "chinese_order", 1980
    from .world import add_person

    p = add_person(
        out, name, group=group, sex="M", birth_year=by, on_profile=True, in_registry=False
    )
    p.appointment = weekday_on_or_after(date(2026, 3, 2))
    return out


def plant_ownership(w: World, variant: str) -> tuple[World, dict[str, str]]:
    """fraud: the chart omits the last registry holder (75% printed, register true).
    forger: chart and register both drop that holder and are rescaled to 100.00."""
    out = copy.deepcopy(w)
    v = view_of(out)
    last = [r for r in v.chart if r.parent is None][-1].name
    if variant == "fraud":
        omit_holder(v, last, rescale=False, where="chart")
    else:
        omit_holder(v, last, rescale=True, where="both")
    omitted = next(h for h in out.holders if h.name == last)
    return out, {
        "omitted_holder": omitted.name,
        "omitted_percent": f"{omitted.percent:.2f}",
        "printed_total": f"{sum((r.percent for r in v.chart if r.parent is None), D(0)):.2f}",
    }


# ------------------------------------------------------------------------------------ AML

PLANTED_NUMBER_SUFFIX = 107  # first regular sequence number (world CALENDAR["aml"]["first_seq"])
ROUTE = [
    ("VANTAGE CREST TRADING PTE LTD", "LUMEN GATEWAY HOLDINGS PTE LTD", date(2026, 7, 14), D("333333.33")),
    ("SILVERLINE IMPORTS PTE LTD", "HARBOUR SPIRE CAPITAL PTE LTD", date(2026, 7, 28), D("333333.33")),
    ("PACIFIC ANCHOR VENTURES PTE LTD", "REDCLIFF NOMINEES PTE LTD", date(2026, 8, 11), D("333333.34")),
]  # fmt: skip
SOF_AMOUNT = D("1000000.00")
BUYER_NAME = "Orchid Bay Holdings Pte. Ltd."
PLANTED_INVOICE = "INV-2026-0117"
SERIES_PREFIX = "SI-"


def renumber_regular_invoices(w: World) -> World:
    """Give the regular invoices their own series (``SI-2026-0107`` ...) so the planted
    ``INV-2026-0117`` (dated 2026-07-01, earlier than four regular invoices) cannot look back-dated
    inside one numbering series. Ledger descriptions and refs follow. Applied to every AML variant."""
    out = copy.deepcopy(w)
    mapping = {i.number: SERIES_PREFIX + i.number[len("INV-") :] for i in out.invoices}
    pat = re.compile("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)))
    for inv in out.invoices:
        inv.number = mapping[inv.number]
    for st in out.statements:
        for r in st.rows:
            r.description = pat.sub(lambda m: mapping[m.group(0)], r.description)
            if r.ref:
                r.ref = pat.sub(lambda m: mapping[m.group(0)], r.ref)
    for k in ("instalment_invoice", "voided_number"):
        if isinstance(out.notes.get(k), str):
            out.notes[k] = mapping.get(out.notes[k], out.notes[k])
    if "h1_pair" in out.notes:
        out.notes["h1_pair"] = [mapping.get(n, n) for n in out.notes["h1_pair"]]
    return out


def _route_rows(rng_key: tuple[int, str, int]) -> tuple[list[LedgerRow], list[LedgerRow]]:
    rng = stream(*rng_key, "plot:route")
    credits, debits = [], []
    for src, dst, when, amt in ROUTE:
        c5_src = lx.label_from_tokens(src) or "other"
        c5_dst = lx.label_from_tokens(dst) or "other"
        credits.append(
            LedgerRow(when, f"FAST PAYMENT FROM {src}", None, amt, D(0), "customer_payment", c5_src, src, None, "routed_in")
        )  # fmt: skip
        gap = rng.randint(1, 3)  # Tue + 1..3 days is Wed..Fri
        pct = D(str(round(rng.uniform(98.05, 99.45), 2)))
        debits.append(
            LedgerRow(when + timedelta(days=gap), f"FAST PAYMENT TO {dst}", q2(amt * pct / 100), None, D(0), None, c5_dst, dst, None, "routed_out")
        )  # fmt: skip
    return credits, debits


def plant_routing(w: World) -> World:
    """Three credits of 333,333.33 / 333,333.33 / 333,333.34 forwarded within 1 to 3 days at
    98 to 99.5%. The retained slice is absorbed by the later overdraft payment (or the biggest later
    supplier payment) so the statements after August are byte-identical to the clean control."""
    out = copy.deepcopy(w)
    credits, debits = _route_rows((w.seed, w.scenario, w.company_index))
    for cr, db in zip(credits, debits, strict=True):
        for r in (cr, db):
            key = f"bank_statement:{r.date:%Y-%m}"
            add_transactions(out, key, [r], recompute=False)
    first = f"bank_statement:{credits[0].date:%Y-%m}"
    retained = sum((c.credit or D(0)) for c in credits) - sum((d.debit or D(0)) for d in debits)
    last_dt = max(d.date for d in debits)
    base_neg = negative_months(w)
    for kinds in (("overdraft",), ("supplier",)):
        cands = [
            r
            for _k, _i, r in all_rows(out)
            if r.kind in kinds and r.date > last_dt and (r.debit or D(0)) > retained
        ]
        if not cands:
            continue
        target = max(cands, key=lambda r: r.debit or D(0)) if kinds == ("supplier",) else cands[0]
        trial = copy.deepcopy(out)
        for _k, _i, r in all_rows(trial):
            if (
                r.kind == target.kind
                and r.date == target.date
                and r.description == target.description
            ):
                r.debit = (r.debit or D(0)) + retained
                break
        recompute_chain(trial, first, 0, True)
        if negative_months(trial) == base_neg:
            trial.notes["plot_chain_months"] = _changed_openings(w, trial)
            return trial
    recompute_chain(out, first, 0, True)
    out.notes["plot_chain_months"] = _changed_openings(w, out)
    return out


def _changed_openings(base: World, new: World) -> list[str]:
    old = {st.month: st.opening for st in base.statements}
    return [st.month for st in new.statements if old.get(st.month) != st.opening]


def planted_invoice(w: World, variant: str) -> Invoice:
    """INV-2026-0117 to Orchid Bay Holdings, total 1,000,000.00. forger: 9% GST (917,431.19 +
    82,568.81). fraud: GST printed at 8% (925,925.93 + 74,074.07), the sloppy slip ``inv_gst`` sees."""
    buyer = Party(
        name=BUYER_NAME,
        uen=idm.make_uen(2018, 40771),
        gst_no=None,
        address="31 Marina Orchid Way #08-02, Singapore 018935",
    )
    rate = D("8.00") if variant == "fraud" else D("9.00")
    subtotal = D("925925.93") if variant == "fraud" else D("917431.19")
    gst = q2(subtotal * rate / 100)
    lines = [
        InvLine("Bulk trading consignment, general merchandise (lot A)", D(1250), D("400.00"), D("500000.00")),
        InvLine("Bulk trading consignment, general merchandise (lot B)", D(800), D("350.00"), D("280000.00")),
    ]  # fmt: skip
    rest = q2(subtotal - D("780000.00"))
    lines.append(InvLine("Handling, packing and delivery charges", D(1), rest, rest))
    n = len(w.invoices)
    number = f"INV-2026-{PLANTED_NUMBER_SUFFIX + n:04d}"
    return Invoice(
        number=number,
        date=date(2026, 7, 1),
        due=date(2026, 7, 31),
        supplier=w.company.party,
        buyer=buyer,
        lines=lines,
        subtotal=subtotal,
        gst_rate=rate,
        gst_amount=gst,
        total=q2(subtotal + gst),
        layout="A",
        status=None,
        paid_date=None,
    )


def make_sof(w: World, variant: str, invoice: Invoice | None) -> Sof:
    """clean: the declarant's claim is one real paid invoice and its credit supports it.
    fraud/forger: S$1,000,000.00 from Orchid Bay under INV-2026-0117, window 2026-07-01..08-31."""
    director = next(p for p in w.people if p.is_director)
    acct = w.company.account_number
    decl = weekday_on_or_after(date(2026, 9, 30))
    if variant != "clean":
        assert invoice is not None
        start, end = date(2026, 7, 1), date(2026, 8, 31)
        text = (
            f"Proceeds from the sale of goods to {BUYER_NAME} under invoice {invoice.number}, "
            f"received between 01 Jul 2026 and 31 Aug 2026."
        )
        return Sof(
            director.name,
            decl,
            SOF_AMOUNT,
            "SGD",
            "sales_proceeds",
            text,
            BUYER_NAME,
            invoice.number,
            start,
            end,
            acct,
        )
    inst = w.notes.get("instalment_invoice")
    h1 = set(w.notes.get("h1_pair", []))
    paid = [
        i
        for i in w.invoices
        if i.status == "paid"
        and i.paid_date
        and i.number != inst
        and i.number not in h1
        and i.paid_date <= decl
    ]
    inv = max(paid, key=lambda i: (i.total, i.number))
    assert inv.paid_date is not None
    start, end = inv.date, month_bounds(inv.paid_date.year, inv.paid_date.month)[1]
    text = (
        f"Proceeds from the sale of goods to {inv.buyer.name} under invoice {inv.number}, "
        f"received between {start:%d %b %Y} and {end:%d %b %Y}."
    )
    return Sof(
        director.name,
        decl,
        inv.total,
        "SGD",
        "sales_proceeds",
        text,
        inv.buyer.name,
        inv.number,
        start,
        end,
        acct,
    )
