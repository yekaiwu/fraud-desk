"""World-channel and mutation-backed tampers: the world is mutated, then re-rendered (clean
typography and metadata, so only content checks can fire; also the "careful forger" channel).

Each builder receives a ``Ctx`` and returns a ``Plan`` (mutations or a world hook, plus a locator
that finds the edited row or field in the tampered ground truth).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from scripts._eval.tamper_core import (
    Ctx,
    Plan,
    TamperNotApplicable,
    cell,
    credit_rows,
    debit_rows,
    doc_by_key,
    field_target,
    field_value,
    fs_row_index,
    fs_value,
    row_target,
    s2,
    table_cell,
    to_box,
    tx_rows,
)
from scripts._gen.schema import Mutation, Target

D = Decimal
OTHER_HOLDERS = [
    ("ORCHID BAY HOLDINGS PTE. LTD.", "Orchid Bay Holdings Pte. Ltd."),
    ("VANTAGE CREST TRADING PTE. LTD.", "Vantage Crest Trading Pte. Ltd."),
    ("SILVERLINE IMPORTS PTE. LTD.", "Silverline Imports Pte. Ltd."),
]
OTHER_UENS = ["201101234A", "200812345B", "201912345C"]
CRYPTO_NAMES = [
    "NEBULA DIGITAL ASSETS PTE LTD",
    "ORBIT CRYPTO EXCHANGE PTE LTD",
    "HELIX BLOCKCHAIN PTE LTD",
    "VORTEX COIN TRADING PTE LTD",
]
ROUTE_NAMES = [
    "LUMEN GATEWAY HOLDINGS PTE LTD",
    "HARBOUR SPIRE CAPITAL PTE LTD",
    "REDCLIFF NOMINEES PTE LTD",
]
ROUTE_SOURCES = [
    "VANTAGE CREST TRADING PTE LTD",
    "SILVERLINE IMPORTS PTE LTD",
    "PACIFIC ANCHOR VENTURES PTE LTD",
]


def mut(op: str, target: str, **args: Any) -> Mutation:
    return Mutation(op=op, target=target, args=args)  # type: ignore[arg-type]


def _iso(v: str) -> date:
    return date.fromisoformat(v)


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _statements(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.docs("bank_statement")


def _label_row(ctx: Ctx, doc_key: str, row: int) -> dict[str, Any]:
    for r in ctx.labels["rows"]:
        if r["doc_key"] == doc_key and r["row"] == row:
            return r
    return {"c4": None, "c5": "other", "counterparty": None}


def _tx_candidates(
    doc: dict[str, Any], *, credits: bool, min_amount: Decimal | int = 500
) -> list[dict[str, Any]]:
    floor = D(min_amount)
    rows = credit_rows(doc) if credits else debit_rows(doc)
    col = "credit" if credits else "debit"
    return [r for r in rows if D(cell(r, col) or "0") >= floor]


def _simple_locator(doc_key: str, row: int, cols: tuple[str, ...]):  # noqa: ANN202
    def locate(base_gt: dict[str, Any], new_gt: dict[str, Any]):  # noqa: ANN202
        orig = {c: table_cell(base_gt, doc_key, "transactions", row, c) for c in cols}
        edit = {c: table_cell(new_gt, doc_key, "transactions", row, c) for c in cols}
        return row_target(new_gt, doc_key, row), orig, edit

    return locate


# ------------------------------------------------------------------------- bank statements


def t01(ctx: Ctx) -> Plan:
    st = ctx.pick(_statements(ctx), "statement")
    r = ctx.pick(_tx_candidates(st, credits=True), "credit row")
    old = D(cell(r, "credit"))
    new = (old * (1 + D(str(round(ctx.rng.uniform(0.10, 0.40), 3))))).quantize(D("0.01"))
    key, idx = st["key"], r["index"]
    return Plan(
        edit=f"{key} row {idx}: credit {old} -> {new}, balances left alone",
        mutations=[
            mut("override_cell", key, table="transactions", row=idx, column="credit", value=s2(new))
        ],
        locate=_simple_locator(key, idx, ("credit",)),
    )


def _not_last(ctx: Ctx) -> list[dict[str, Any]]:
    sts = _statements(ctx)
    return sts[:-1]


def t02a(ctx: Ctx) -> Plan:
    st = ctx.pick(_not_last(ctx), "non-final statement")
    r = ctx.pick(_tx_candidates(st, credits=True), "credit row")
    old = D(cell(r, "credit"))
    new = (old * D("1.25")).quantize(D("0.01"))
    key, idx = st["key"], r["index"]

    def locate(base_gt, new_gt):  # noqa: ANN001, ANN202
        return (
            field_target(new_gt, key, "closing_balance"),
            {"credit": s2(old), "closing_balance": field_value(base_gt, key, "closing_balance")},
            {"credit": s2(new), "closing_balance": field_value(new_gt, key, "closing_balance")},
        )

    return Plan(
        edit=f"{key} row {idx}: credit {old} -> {new}; balances and totals recomputed to the end of that statement only",
        mutations=[
            mut(
                "override_cell", key, table="transactions", row=idx, column="credit", value=s2(new)
            ),
            mut("recompute_chain", key, from_row=idx, propagate=False),
        ],
        locate=locate,
    )


def _trade_credit_total(ctx: Ctx, key: str) -> Decimal:
    total = D(0)
    for r in ctx.labels["rows"]:
        if r["doc_key"] == key and r.get("c4") in ("trade_receipt", "customer_payment"):
            v = table_cell(ctx.gt, key, "transactions", r["row"], "credit")
            if v:
                total += D(v)
    return total


def _hard_uplift(ctx: Ctx) -> tuple[dict[str, Any], dict[str, Any], Decimal]:
    st = ctx.pick(_not_last(ctx), "non-final statement")
    r = ctx.pick(_tx_candidates(st, credits=True, min_amount=D("1000")), "credit row")
    uplift = max(D("5000"), (_trade_credit_total(ctx, st["key"]) * D("0.05")).quantize(D("0.01")))
    return st, r, uplift


def t02b(ctx: Ctx) -> Plan:
    st, r, uplift = _hard_uplift(ctx)
    old = D(cell(r, "credit"))
    key, idx = st["key"], r["index"]
    return Plan(
        edit=f"{key} row {idx}: credit {old} -> {old + uplift}; every later balance and statement recomputed (consistent chain)",
        mutations=[
            mut(
                "override_cell",
                key,
                table="transactions",
                row=idx,
                column="credit",
                value=s2(old + uplift),
            ),
            mut("recompute_chain", key, from_row=idx, propagate=True),
        ],
        locate=_simple_locator(key, idx, ("credit",)),
    )


def t02c(ctx: Ctx) -> Plan:
    st, r, uplift = _hard_uplift(ctx)
    old = D(cell(r, "credit"))
    key, idx = st["key"], r["index"]
    fy = ctx.doc("financial_statement:FY2025")
    rev = fs_value(fy, "income_statement", "revenue")
    cash_b = fs_value(fy, "balance_sheet", "cash_and_equivalents")
    ar_b = fs_value(fy, "balance_sheet", "trade_receivables")
    wc = fs_value(fy, "cash_flow", "working_capital_changes")
    u = uplift.quantize(D("1"))
    fk = "financial_statement:FY2025"
    # annualised credits rise by 2 x uplift: revenue follows; cash rises by the uplift and trade
    # receivables absorb the rest so the balance sheet still balances and the cash tie holds
    muts = [
        mut(
            "override_cell",
            key,
            table="transactions",
            row=idx,
            column="credit",
            value=s2(old + uplift),
        ),
        mut("recompute_chain", key, from_row=idx, propagate=True),
        mut(
            "override_cell",
            fk,
            table="income_statement",
            line="revenue",
            column="current",
            value=format(rev + 2 * u, "f"),
            recompute="forger",
        ),
        mut(
            "override_cell",
            fk,
            table="balance_sheet",
            line="cash_and_equivalents",
            column="current",
            value=format(cash_b + u, "f"),
            recompute="none",
        ),
        mut(
            "override_cell",
            fk,
            table="balance_sheet",
            line="trade_receivables",
            column="current",
            value=format(ar_b + 2 * u - u, "f"),
            recompute="subtotals",
        ),
        mut(
            "override_cell",
            fk,
            table="cash_flow",
            line="working_capital_changes",
            column="current",
            value=format(wc - 2 * u + u, "f"),
            recompute="subtotals",
        ),
    ]
    return Plan(
        edit=f"{key} row {idx}: credit {old} -> {old + uplift}; chain recomputed; FY2025 revenue +{2 * u}, cash +{u} and receivables adjusted to match",
        mutations=muts,
        locate=_simple_locator(key, idx, ("credit",)),
    )


def t03(ctx: Ctx) -> Plan:
    sts = _statements(ctx)
    if len(sts) < 3:
        raise TamperNotApplicable("needs at least 3 statements")
    st = ctx.pick(sts[1:-1], "middle statement")
    key = st["key"]

    def locate(base_gt, new_gt):  # noqa: ANN001, ANN202
        return Target(doc_key=key), {"statement": key}, {"statement": None}

    return Plan(
        edit=f"{key} removed from the bundle", mutations=[mut("drop_document", key)], locate=locate
    )


def t04(ctx: Ctx) -> Plan:
    st = ctx.pick(_statements(ctx), "statement")
    rows = [r for r in tx_rows(st) if (cell(r, "debit") or cell(r, "credit")) and r["index"] > 1]
    r = ctx.pick(rows, "row")
    end = _iso(field_value(ctx.gt, st["key"], "period_end"))
    new = end + timedelta(days=5)
    key, idx = st["key"], r["index"]
    old = cell(r, "date")
    return Plan(
        edit=f"{key} row {idx}: date {old} -> {new} (outside the period)",
        mutations=[
            mut(
                "override_cell",
                key,
                table="transactions",
                row=idx,
                column="date",
                value=new.isoformat(),
            )
        ],
        locate=_simple_locator(key, idx, ("date",)),
    )


def t05(ctx: Ctx) -> Plan:
    st = ctx.pick(_statements(ctx), "statement")
    key = st["key"]
    own = (field_value(ctx.gt, key, "account_holder") or "").upper()
    name, proper = ctx.pick([o for o in OTHER_HOLDERS if o[0] != own], "other holder")
    return Plan(
        edit=f"{key}: account_holder {own} -> {name}, UEN removed",
        mutations=[mut("swap_party", key, role="account_holder", name=name, uen=None)],
        locate=lambda b, n: (
            field_target(n, key, "account_holder"),
            {"account_holder": field_value(b, key, "account_holder")},
            {"account_holder": field_value(n, key, "account_holder")},
        ),
    )


def t07(ctx: Ctx) -> Plan:
    st = ctx.pick(_statements(ctx), "statement")
    key = st["key"]
    rows = [r for r in debit_rows(st) if D(cell(r, "debit")) >= D("200") and r["index"] > 2]
    r = ctx.pick(rows, "debit row")
    lab = _label_row(ctx, key, r["index"])
    args: dict[str, Any] = {
        "date": cell(r, "date"),
        "description": cell(r, "description"),
        "debit": cell(r, "debit"),
        "c4": lab.get("c4"),
        "c5": lab.get("c5") or "other",
        "counterparty": lab.get("counterparty"),
        "recompute": True,
    }
    desc, dt, amt = cell(r, "description"), cell(r, "date"), cell(r, "debit")

    def locate(base_gt, new_gt):  # noqa: ANN001, ANN202
        d = doc_by_key(new_gt, key)
        same = [
            x
            for x in tx_rows(d)
            if cell(x, "description") == desc and cell(x, "date") == dt and cell(x, "debit") == amt
        ]
        idx = max(x["index"] for x in same) if same else r["index"]
        return (
            row_target(new_gt, key, idx),
            {"rows_matching": str(len(same) - 1)},
            {"rows_matching": str(len(same))},
        )

    return Plan(
        edit=f"{key}: row {r['index']} ({desc}, {amt}) duplicated, chain and totals recomputed",
        mutations=[mut("insert_rows", key, **args)],
        locate=locate,
    )


def t08(ctx: Ctx) -> Plan:
    st = ctx.pick(_not_last(ctx), "non-final statement")
    key = st["key"]
    start, end = (
        _iso(field_value(ctx.gt, key, "period_start")),
        _iso(field_value(ctx.gt, key, "period_end")),
    )
    days = weekdays(start, end)
    if len(days) < 8:
        raise TamperNotApplicable("month too short")
    step = max(1, len(days) // 6)
    muts = []
    for i in range(5):
        amount = D("10000") if i % 2 == 0 else D("20000")
        muts.append(
            mut(
                "insert_rows",
                key,
                date=days[1 + i * step].isoformat(),
                description=f"FAST PAYMENT FROM ROUND CLIENT {i + 1} PTE LTD",
                credit=s2(amount),
                c4="other",
                c5="trading",
                counterparty=f"ROUND CLIENT {i + 1} PTE LTD",
                recompute=True,
            )
        )

    def locate(base_gt, new_gt):  # noqa: ANN001, ANN202
        d = doc_by_key(new_gt, key)
        idxs = [x["index"] for x in tx_rows(d) if "ROUND CLIENT" in (cell(x, "description") or "")]
        return (
            row_target(new_gt, key, min(idxs)),
            {"round_credits": "0"},
            {"round_credits": str(len(idxs))},
        )

    return Plan(
        edit=f"{key}: 5 invented round credits (10,000.00 / 20,000.00); chain recomputed through the last statement",
        mutations=muts,
        locate=locate,
    )


def t09(ctx: Ctx) -> Plan:
    sts = _statements(ctx)
    chosen, n = [], 0
    for st in sts:
        chosen.append(st)
        n += len(tx_rows(st))
        if n >= 160:
            break
    if n < 150:
        raise TamperNotApplicable("fewer than 150 rows")
    muts = []
    for st in chosen:
        for r in tx_rows(st):
            for col in ("debit", "credit"):
                if cell(r, col) and r["cells"].get("description", {}).get("value") is not None:
                    v = D(str(round(ctx.rng.uniform(10, 99999), 2)))
                    muts.append(
                        mut(
                            "override_cell",
                            st["key"],
                            table="transactions",
                            row=r["index"],
                            column=col,
                            value=s2(v),
                        )
                    )
    muts.append(mut("recompute_chain", chosen[0]["key"], from_row=0, propagate=True))
    key = chosen[0]["key"]
    return Plan(
        edit=f"{len(chosen)} statements: {n} amounts replaced by uniform draws, chain recomputed",
        mutations=muts,
        locate=lambda b, nw: (Target(doc_key=key), {"rows": str(n)}, {"rows": str(n)}),
    )


# -------------------------------------------------------------------------------- invoices


def _invoices(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.docs("invoice")


def _inv_lines_idx(inv: dict[str, Any]) -> list[dict[str, Any]]:
    return inv["tables"]["lines"]["rows"]


def _locate_field(key: str, name: str):  # noqa: ANN202
    def locate(b: dict[str, Any], n: dict[str, Any]):  # noqa: ANN202
        return (
            field_target(n, key, name),
            {name: field_value(b, key, name)},
            {name: field_value(n, key, name)},
        )

    return locate


def t10(ctx: Ctx) -> Plan:
    inv = ctx.pick([i for i in _invoices(ctx) if _inv_lines_idx(i)], "invoice")
    key = inv["key"]
    ln = ctx.pick(_inv_lines_idx(inv), "line")
    old = D(cell(ln, "amount"))
    new = (old * D("1.5")).quantize(D("0.01"))
    idx = ln["index"]

    def locate(b, n):  # noqa: ANN001, ANN202
        return (
            row_target(n, key, idx, "lines"),
            {"amount": table_cell(b, key, "lines", idx, "amount")},
            {"amount": table_cell(n, key, "lines", idx, "amount")},
        )

    return Plan(
        edit=f"{key} line {idx}: amount {old} -> {new}, other lines and totals untouched",
        mutations=[
            mut("override_cell", key, table="lines", row=idx, column="amount", value=s2(new))
        ],
        locate=locate,
    )


def t11(ctx: Ctx) -> Plan:
    inv = ctx.pick(_invoices(ctx), "invoice")
    key = inv["key"]
    old = D(field_value(ctx.gt, key, "total"))
    new = (old * D("1.10")).quantize(D("0.01"))
    return Plan(
        edit=f"{key}: total {old} -> {new}, rest untouched",
        mutations=[mut("set_field", key, field="total", value=s2(new))],
        locate=_locate_field(key, "total"),
    )


def t12(ctx: Ctx) -> Plan:
    inv = ctx.pick(_invoices(ctx), "invoice")
    key = inv["key"]
    sub = D(field_value(ctx.gt, key, "subtotal"))
    new = (sub * D("0.07")).quantize(D("0.01"))
    return Plan(
        edit=f"{key}: GST {field_value(ctx.gt, key, 'gst_amount')} -> {new} (7% of subtotal), total recomputed",
        mutations=[
            mut("set_field", key, field="gst_amount", value=s2(new)),
            mut("set_field", key, field="recompute_total", value=True),
        ],
        locate=_locate_field(key, "gst_amount"),
    )


def t13(ctx: Ctx) -> Plan:
    inv = ctx.pick(_invoices(ctx), "invoice")
    key = inv["key"]
    uen = field_value(ctx.gt, key, "supplier_uen") or ""
    gst = field_value(ctx.gt, key, "supplier_gst_no") or ""
    pos = ctx.rng.choice([i for i, c in enumerate(uen[:-1]) if c.isdigit()][2:] or [4])
    new_uen = uen[:pos] + str((int(uen[pos]) + 1) % 10) + uen[pos + 1 :]
    last = gst[-1:] or "K"
    new_gst = gst[:-1] + ("L" if last != "L" else "M")
    muts = [mut("set_field", key, field="supplier_uen", value=new_uen)]
    if gst:
        muts.append(mut("set_field", key, field="supplier_gst_no", value=new_gst))
    return Plan(
        edit=f"{key}: supplier UEN {uen} -> {new_uen}; GST number {gst} -> {new_gst}",
        mutations=muts,
        locate=_locate_field(key, "supplier_uen"),
    )


def t14a(ctx: Ctx) -> Plan:
    inv = ctx.pick(_invoices(ctx), "invoice")
    key = inv["key"]
    d = _iso(field_value(ctx.gt, key, "invoice_date")) - timedelta(days=5)
    return Plan(
        edit=f"{key}: due date {field_value(ctx.gt, key, 'due_date')} -> {d} (before the invoice date)",
        mutations=[mut("set_field", key, field="due_date", value=d.isoformat())],
        locate=_locate_field(key, "due_date"),
    )


def t14b(ctx: Ctx) -> Plan:
    inv = ctx.pick(_invoices(ctx), "invoice")
    key = inv["key"]
    d = ctx.today + timedelta(days=19)
    return Plan(
        edit=f"{key}: invoice date {field_value(ctx.gt, key, 'invoice_date')} -> {d} (after today)",
        mutations=[mut("set_field", key, field="invoice_date", value=d.isoformat())],
        locate=_locate_field(key, "invoice_date"),
    )


def t15(ctx: Ctx) -> Plan:
    inv = ctx.pick(_invoices(ctx), "invoice")
    key = inv["key"]
    name = field_value(ctx.gt, key, "supplier_name")
    uen = field_value(ctx.gt, key, "supplier_uen")
    return Plan(
        edit=f"{key}: buyer {field_value(ctx.gt, key, 'buyer_name')} -> {name} (the supplier)",
        mutations=[
            mut("set_field", key, field="buyer_name", value=name),
            mut("set_field", key, field="buyer_uen", value=uen),
        ],
        locate=_locate_field(key, "buyer_name"),
    )


def _doc_with_number(
    ctx: Ctx, doc_type: str, field_name: str, number: str
) -> dict[str, Any] | None:
    for d in ctx.docs(doc_type):
        if d["fields"].get(field_name, {}).get("value") == number:
            return d
    return None


def t18a(ctx: Ctx) -> Plan:
    cands = [i for i in _invoices(ctx) if field_value(ctx.gt, i["key"], "contract_reference")]
    inv = ctx.pick(cands, "invoice citing a contract")
    key = inv["key"]
    c = _doc_with_number(
        ctx, "contract", "contract_number", field_value(ctx.gt, key, "contract_reference")
    )
    if c is None:
        raise TamperNotApplicable("cited contract not in the bundle")
    value = D(c["fields"]["contract_value"]["value"])
    sub = (value * D("1.35") / D("1.09")).quantize(D("0.01"))
    return Plan(
        edit=f"{key}: subtotal set so the total is about {s2(value * D('1.35'))} (contract {c['key']} value {value} x 1.35)",
        mutations=[mut("set_field", key, field="subtotal_target", value=s2(sub))],
        locate=_locate_field(key, "total"),
    )


def t18b(ctx: Ctx) -> Plan:
    cands = [i for i in _invoices(ctx) if field_value(ctx.gt, i["key"], "po_reference")]
    inv = ctx.pick(cands, "invoice citing a purchase order")
    key = inv["key"]
    po = _doc_with_number(
        ctx, "purchase_order", "po_number", field_value(ctx.gt, key, "po_reference")
    )
    if po is None:
        raise TamperNotApplicable("cited PO not in the bundle")
    total = D(po["fields"]["total"]["value"])
    sub = (total * D("1.12") / D("1.09")).quantize(D("0.01"))
    cur = D(field_value(ctx.gt, key, "subtotal"))
    if sub <= cur:
        sub = (cur * D("1.12")).quantize(D("0.01"))
    return Plan(
        edit=f"{key}: subtotal set so the total is about PO {po['key']} total {total} x 1.12",
        mutations=[mut("set_field", key, field="subtotal_target", value=s2(sub))],
        locate=_locate_field(key, "total"),
    )


def t19(ctx: Ctx) -> Plan:
    paid = [i for i in _invoices(ctx) if field_value(ctx.gt, i["key"], "payment_status") == "paid"]
    inv = ctx.pick(paid, "paid invoice")
    key = inv["key"]
    number = field_value(ctx.gt, key, "invoice_number")
    state: dict[str, Any] = {"removed": []}

    def hook(world: Any) -> None:
        from scripts._gen.world import recompute_chain

        first = None
        for st in world.statements:
            keep = []
            for r in st.rows:
                if r.ref == key:
                    state["removed"].append((st.month, str(r.credit)))
                    first = first or st.key
                else:
                    keep.append(r)
            st.rows = keep
        if first is None:
            raise TamperNotApplicable(f"no credit row references {key}")
        recompute_chain(world, first, 0, True)

    def locate(b, n):  # noqa: ANN001, ANN202
        return (
            field_target(n, key, "payment_status"),
            {"credits_removed": "0"},
            {"credits_removed": str(len(state["removed"]))},
        )

    return Plan(
        edit=f"{number}: the credit(s) paying it removed from the statements, chain recomputed through the last statement; invoice still printed paid",
        hook=hook,
        locate=locate,
    )


# ------------------------------------------------------------------------ financial statements


FY = "financial_statement:FY2025"


def _fs_locator(table: str, line: str, col: str = "current", edit_line: str | None = None):  # noqa: ANN202
    """Locator for an FS edit; ``line`` is the row the flag should point at, ``edit_line`` the line
    that was edited when that differs (T20: the subtotal row versus the edited line)."""

    def locate(b: dict[str, Any], n: dict[str, Any]):  # noqa: ANN202
        bi = fs_row_index(doc_by_key(b, FY), table, line)
        ni = fs_row_index(doc_by_key(n, FY), table, line)
        t = row_target(n, FY, ni, table)
        orig = {line: table_cell(b, FY, table, bi, col)}
        edit = {line: table_cell(n, FY, table, ni, col)}
        if edit_line:
            ei = fs_row_index(doc_by_key(b, FY), table, edit_line)
            orig[edit_line] = table_cell(b, FY, table, ei, col)
            edit[edit_line] = table_cell(
                n, FY, table, fs_row_index(doc_by_key(n, FY), table, edit_line), col
            )
        return t, orig, edit

    return locate


def _whole(v: Decimal) -> str:
    return format(v.quantize(D("1")), "f")


def t20(ctx: Ctx, *, recompute: bool) -> Plan:
    fy = ctx.doc(FY)
    line = ctx.pick(["inventories", "trade_receivables", "other_current_assets"], "line")
    old = fs_value(fy, "balance_sheet", line)
    new = old * D(str(round(ctx.rng.uniform(1.10, 1.30), 3)))
    sub_line = "total_assets" if recompute else "total_current_assets"
    return Plan(
        edit=f"{FY} balance sheet {line}: {old} -> {_whole(new)}; "
        + (
            "subtotals and total assets recomputed, liabilities untouched"
            if recompute
            else "no subtotal recomputed"
        ),
        mutations=[
            mut(
                "override_cell",
                FY,
                table="balance_sheet",
                line=line,
                column="current",
                value=_whole(new),
                recompute="subtotals" if recompute else "none",
            )
        ],
        locate=_fs_locator("balance_sheet", sub_line, edit_line=line),
    )


def t20a(ctx: Ctx) -> Plan:
    return t20(ctx, recompute=True)


def t20b(ctx: Ctx) -> Plan:
    return t20(ctx, recompute=False)


def t21(ctx: Ctx) -> Plan:
    old = fs_value(ctx.doc(FY), "cash_flow", "cash_closing")
    new = old * D("1.07") + D("12345")
    return Plan(
        edit=f"{FY} cash flow closing cash: {old} -> {_whole(new)}",
        mutations=[
            mut(
                "override_cell",
                FY,
                table="cash_flow",
                line="cash_closing",
                column="current",
                value=_whole(new),
                recompute="none",
            )
        ],
        locate=_fs_locator("cash_flow", "cash_closing"),
    )


def t22(ctx: Ctx) -> Plan:
    old = fs_value(ctx.doc(FY), "income_statement", "revenue", "prior")
    new = old * D("1.15")
    return Plan(
        edit=f"{FY} prior-year revenue: {old} -> {_whole(new)}",
        mutations=[
            mut(
                "override_cell",
                FY,
                table="income_statement",
                line="revenue",
                column="prior",
                value=_whole(new),
                recompute="none",
            )
        ],
        locate=_fs_locator("income_statement", "revenue", "prior"),
    )


def t23(ctx: Ctx) -> Plan:
    old = fs_value(ctx.doc(FY), "income_statement", "revenue")
    fake = D(int((float(old) * 8 / 3 / 10000) + 0.5) * 10000)
    return Plan(
        edit=f"{FY} revenue {old} -> {fake}; every subtotal, retained earnings, receivables and the cash-flow working-capital line recomputed",
        mutations=[
            mut(
                "override_cell",
                FY,
                table="income_statement",
                line="revenue",
                column="current",
                value=format(fake, "f"),
                recompute="forger",
            )
        ],
        locate=_fs_locator("income_statement", "revenue"),
    )


def t24(ctx: Ctx) -> Plan:
    name, proper = ctx.pick(OTHER_HOLDERS, "entity")
    uen = ctx.pick(OTHER_UENS, "uen")
    return Plan(
        edit=f"{FY}: company name and UEN changed to {proper} / {uen}",
        mutations=[mut("swap_party", FY, role="company", name=proper, uen=uen)],
        locate=lambda b, n: (
            field_target(n, FY, "company_name"),
            {"company_name": field_value(b, FY, "company_name")},
            {"company_name": field_value(n, FY, "company_name")},
        ),
    )


# ------------------------------------------------------------------------------------- IDs


def _nric_docs(ctx: Ctx) -> list[dict[str, Any]]:
    return [
        d
        for d in ctx.docs("national_id")
        if (d["fields"]["id_number"]["value"] or "")[:1] in "STFG"
    ]


def t50a(ctx: Ctx) -> Plan:
    d = ctx.pick(_nric_docs(ctx), "NRIC card")
    key = d["key"]
    old = field_value(ctx.gt, key, "id_number")
    new = old[:-1] + next(c for c in "ABCDEFGHIZJ" if c != old[-1])
    return Plan(
        edit=f"{key}: id_number {old} -> {new} (check letter changed)",
        mutations=[mut("set_field", key, field="id_number", value=new)],
        locate=_locate_field(key, "id_number"),
    )


def t50b(ctx: Ctx) -> Plan:
    d = ctx.pick(_nric_docs(ctx), "NRIC card")
    key = d["key"]
    old = field_value(ctx.gt, key, "id_number")
    new = "S" + old
    return Plan(
        edit=f"{key}: id_number {old} -> {new} (prefix SS)",
        mutations=[mut("set_field", key, field="id_number", value=new)],
        locate=_locate_field(key, "id_number"),
    )


def t51(ctx: Ctx) -> Plan:
    d = ctx.pick(ctx.docs("passport"), "passport")
    key = d["key"]
    old = field_value(ctx.gt, key, "mrz_line2")
    pos = 9
    new = old[:pos] + str((int(old[pos]) + 3) % 10) + old[pos + 1 :]
    return Plan(
        edit=f"{key}: mrz_line2 document-number check digit {old[pos]} -> {new[pos]}",
        mutations=[mut("set_field", key, field="mrz_line2", value=new)],
        locate=_locate_field(key, "mrz_line2"),
    )


def t52(ctx: Ctx) -> Plan:
    d = ctx.pick(_nric_docs(ctx), "NRIC card")
    key = d["key"]
    new = f"{ctx.today.year + 5}-03-04"
    return Plan(
        edit=f"{key}: date of birth {field_value(ctx.gt, key, 'date_of_birth')} -> {new} (future)",
        mutations=[mut("set_field", key, field="date_of_birth", value=new)],
        locate=_locate_field(key, "date_of_birth"),
    )


# ---------------------------------------------------------------------------------- KYC docs


def _chart(ctx: Ctx) -> dict[str, Any]:
    return ctx.doc("ownership_chart:-")


def _holders_locator(n_key: str = "ownership_chart:-"):  # noqa: ANN202
    def locate(b: dict[str, Any], n: dict[str, Any]):  # noqa: ANN202
        def sig(gt: dict[str, Any]) -> str:
            d = doc_by_key(gt, n_key)
            return ";".join(
                f"{cell(r, 'holder_name')}={cell(r, 'percent')}:ubo={cell(r, 'is_ubo')}"
                for r in d["tables"]["holders"]["rows"]
            )

        t = Target(doc_key=n_key, table="holders")
        dd = doc_by_key(n, n_key)
        if dd is not None and dd["tables"]["holders"]["rows"]:
            t = Target(
                doc_key=n_key,
                table="holders",
                box=to_box(dd["tables"]["holders"]["rows"][0].get("box")),
            )
        return t, {"holders": sig(b)}, {"holders": sig(n)}

    return locate


def _omit_candidate(ctx: Ctx) -> str:
    rows = _chart(ctx)["tables"]["holders"]["rows"]
    top = [
        r for r in rows if cell(r, "parent_name") is None and cell(r, "holder_type") == "individual"
    ]
    top.sort(key=lambda r: D(cell(r, "percent")))
    if len(top) < 2:
        raise TamperNotApplicable("fewer than two individual holders")
    return cell(top[0], "holder_name")


def t53(ctx: Ctx) -> Plan:
    name = _omit_candidate(ctx)
    return Plan(
        edit=f"Ownership chart omits {name}; remaining percentages redistributed to 100.00",
        mutations=[
            mut("set_field", "ownership_chart:-", field="omit_holder", value=name, rescale=True)
        ],
        locate=_holders_locator(),
    )


def t54(ctx: Ctx) -> Plan:
    name = _omit_candidate(ctx)
    return Plan(
        edit=f"Ownership chart omits {name}; percentages left, sum below 100",
        mutations=[
            mut("set_field", "ownership_chart:-", field="omit_holder", value=name, rescale=False)
        ],
        locate=_holders_locator(),
    )


def t55(ctx: Ctx) -> Plan:
    rows = _chart(ctx)["tables"]["holders"]["rows"]
    ubo = [r for r in rows if cell(r, "is_ubo") == "true"]
    if not ubo:
        raise TamperNotApplicable("no UBO marked on the chart")
    muts = [
        mut(
            "override_cell",
            "ownership_chart:-",
            table="holders",
            row=r["index"],
            column="is_ubo",
            value="false",
        )
        for r in ubo
    ]
    return Plan(
        edit=f"Ownership chart: UBO mark removed from {len(ubo)} holder(s) although individuals hold 25.00% or more",
        mutations=muts,
        locate=_holders_locator(),
    )


def t56a(ctx: Ctx) -> Plan:
    name = "Koh Boon Kiat"

    def locate(b, n):  # noqa: ANN001, ANN202
        d = doc_by_key(n, "company_profile:-")
        rows = d["tables"]["directors"]["rows"]
        idx = next((r["index"] for r in rows if cell(r, "director_name") == name), len(rows) - 1)
        return (
            row_target(n, "company_profile:-", idx, "directors"),
            {
                "directors": str(
                    len(doc_by_key(b, "company_profile:-")["tables"]["directors"]["rows"])
                )
            },
            {"directors": str(len(rows))},
        )

    return Plan(
        edit=f"Company profile adds director {name}, who is not a registry officer",
        mutations=[
            mut(
                "set_field",
                "company_profile:-",
                field="add_director",
                value=name,
                in_registry=False,
            )
        ],
        locate=locate,
    )


def _month_bounds(ctx: Ctx, st: dict[str, Any]) -> tuple[date, date]:
    return _iso(field_value(ctx.gt, st["key"], "period_start")), _iso(
        field_value(ctx.gt, st["key"], "period_end")
    )


def _total_value(ctx: Ctx) -> Decimal:
    tot = D(0)
    for st in _statements(ctx):
        for r in tx_rows(st):
            tot += D(cell(r, "debit") or "0") + D(cell(r, "credit") or "0")
    return tot


def t57(ctx: Ctx) -> Plan:
    sts = _statements(ctx)
    total = _total_value(ctx)
    x = (total * D("0.60") / D("6.8")).quantize(D("0.01"))  # 4 credits of x and 4 debits of 0.7x
    muts: list[Mutation] = []
    layout = [(0, 2), (1, 1), (2, 1)] if len(sts) >= 3 else [(0, 4)]
    n = 0
    for mi, pairs in layout:
        st = sts[mi]
        s, e = _month_bounds(ctx, st)
        days = weekdays(s, e)
        for p in range(pairs):
            name = CRYPTO_NAMES[n % len(CRYPTO_NAMES)]
            d1 = days[3 + p * 6]
            d2 = days[min(len(days) - 1, 5 + p * 6)]
            muts.append(
                mut(
                    "insert_rows",
                    st["key"],
                    date=d1.isoformat(),
                    description=f"FAST PAYMENT FROM {name}",
                    credit=s2(x),
                    c4="other",
                    c5="crypto_virtual_assets",
                    counterparty=name,
                    recompute=True,
                )
            )
            muts.append(
                mut(
                    "insert_rows",
                    st["key"],
                    date=d2.isoformat(),
                    description=f"FAST PAYMENT TO {name}",
                    debit=s2(x * D("0.7")),
                    c5="crypto_virtual_assets",
                    counterparty=name,
                    recompute=True,
                )
            )
            n += 1
    first = sts[layout[0][0]]["key"]

    def locate(b, nw):  # noqa: ANN001, ANN202
        d = doc_by_key(nw, first)
        idxs = [
            r["index"]
            for r in tx_rows(d)
            if any(c in (cell(r, "description") or "") for c in CRYPTO_NAMES)
        ]
        return (
            row_target(nw, first, min(idxs)),
            {"crypto_rows": "0"},
            {"crypto_rows": str(len(muts))},
        )

    return Plan(
        edit=f"{len(muts)} crypto-counterparty rows of {x} / {x * D('0.7')} inserted (about 37% of value), chain recomputed",
        mutations=muts,
        locate=locate,
    )


def t58(ctx: Ctx) -> Plan:
    key = "source_of_funds_declaration:-"
    old = D(field_value(ctx.gt, key, "declared_amount"))
    new = old * 5
    return Plan(
        edit=f"SoF declared_amount {old} -> {new} (5x)",
        mutations=[mut("set_field", key, field="declared_amount", value=s2(new))],
        locate=_locate_field(key, "declared_amount"),
    )


def t59(ctx: Ctx) -> Plan:
    name = ctx.pick(ROUTE_SOURCES, "remitter")
    state: dict[str, Any] = {}

    def hook(world: Any) -> None:
        cands = [
            (st, i, r)
            for st in world.statements
            for i, r in enumerate(st.rows)
            if r.ref and str(r.ref).startswith("invoice:") and r.credit
        ]
        if not cands:
            raise TamperNotApplicable("no credit row pays an invoice")
        st, i, r = cands[ctx.rng.randrange(len(cands))]
        state.update(key=st.key, row=i, old=r.description)
        r.description = f"FAST PAYMENT FROM {name}"
        r.counterparty = name
        r.c5 = "trading"

    def locate(b, n):  # noqa: ANN001, ANN202
        return (
            row_target(n, state["key"], state["row"]),
            {"description": state["old"]},
            {"description": f"FAST PAYMENT FROM {name}"},
        )

    return Plan(
        edit=f"A credit paying an invoice now names {name} as the remitter (amount and chain unchanged)",
        hook=hook,
        locate=locate,
    )


def t60(ctx: Ctx) -> Plan:
    sts = _statements(ctx)
    total_credits = sum(D(cell(r, "credit") or "0") for st in sts for r in tx_rows(st))
    x = (total_credits * D("0.12")).quantize(D("0.01"))
    muts: list[Mutation] = []
    per = [sts[i % len(sts)] for i in range(3)]
    first_key = per[0]["key"]
    for i, st in enumerate(per):
        s, e = _month_bounds(ctx, st)
        days = weekdays(s, e)
        d1 = days[4 + i * 3]
        d2 = days[min(len(days) - 1, 6 + i * 3)]
        muts.append(
            mut(
                "insert_rows",
                st["key"],
                date=d1.isoformat(),
                description=f"FAST PAYMENT FROM {ROUTE_SOURCES[i]}",
                credit=s2(x),
                c4="other",
                c5="trading",
                counterparty=ROUTE_SOURCES[i],
                recompute=True,
            )
        )
        muts.append(
            mut(
                "insert_rows",
                st["key"],
                date=d2.isoformat(),
                description=f"FAST PAYMENT TO {ROUTE_NAMES[i]}",
                debit=s2(x * D("0.985")),
                c5="other",
                counterparty=ROUTE_NAMES[i],
                recompute=True,
            )
        )

    def locate(b, n):  # noqa: ANN001, ANN202
        d = doc_by_key(n, first_key)
        idxs = [
            r["index"]
            for r in tx_rows(d)
            if any(c in (cell(r, "description") or "") for c in ROUTE_SOURCES + ROUTE_NAMES)
        ]
        return row_target(n, first_key, min(idxs)), {"routed_pairs": "0"}, {"routed_pairs": "3"}

    return Plan(
        edit=f"3 credit/debit pairs of {x} routed through three entities within 1-3 days",
        mutations=muts,
        locate=locate,
    )


def t06(ctx: Ctx) -> Plan:
    """Page 2 of a statement dropped (bundle channel, built through the generator's drop_page)."""
    sts = _statements(ctx)
    pages = {s["key"]: len(s["tables"]["page_chain"]["rows"]) for s in sts}
    best = max(pages.values())
    st = ctx.pick([s for s in sts if pages[s["key"]] == best], "statement")
    key = st["key"]
    return Plan(
        edit=f"{key}: page 2 of {best} dropped",
        mutations=[mut("drop_page", key, page=2)],
        locate=lambda b, n: (
            Target(doc_key=key, table="page_chain"),
            {"pages": str(best)},
            {"pages": str(best - 1)},
        ),
    )


def t34(ctx: Ctx) -> Plan:
    cons = ctx.docs("contract")
    if len(cons) < 2:
        raise TamperNotApplicable("needs two contracts")
    a, b = cons[0]["key"], cons[1]["key"]
    return Plan(
        edit=f"{b} reuses the signature images of {a} (pixel-identical)",
        mutations=[mut("reuse_signature", b, **{"from": a})],
        locate=lambda g0, g1: (
            Target(doc_key=b),
            {"signature": f"{b} own signature"},
            {"signature": f"identical to {a}"},
        ),
    )


def t17(ctx: Ctx) -> Plan:
    invs = _invoices(ctx)
    keys = sorted(i["key"] for i in invs)
    if len(keys) < 5:
        raise TamperNotApplicable("fewer than 5 invoices")
    k = ctx.rng.randrange(1, len(keys) - 2)
    drop = keys[k : k + 2]
    return Plan(
        edit=f"Invoices {', '.join(drop)} removed from the supplier's sequence",
        mutations=[mut("drop_document", d) for d in drop],
        locate=lambda g0, g1: (
            Target(doc_key=drop[0]),
            {"invoices": ",".join(drop)},
            {"invoices": None},
        ),
    )


WORLD_BUILDERS = {
    "T01": t01,
    "T02a": t02a,
    "T02b": t02b,
    "T02c": t02c,
    "T03": t03,
    "T04": t04,
    "T05": t05,
    "T06": t06,
    "T07": t07,
    "T08": t08,
    "T09": t09,
    "T10": t10,
    "T11": t11,
    "T12": t12,
    "T13": t13,
    "T14a": t14a,
    "T14b": t14b,
    "T15": t15,
    "T17": t17,
    "T18a": t18a,
    "T18b": t18b,
    "T19": t19,
    "T20a": t20a,
    "T20b": t20b,
    "T21": t21,
    "T22": t22,
    "T23": t23,
    "T24": t24,
    "T34": t34,
    "T50a": t50a,
    "T50b": t50b,
    "T51": t51,
    "T52": t52,
    "T53": t53,
    "T54": t54,
    "T55": t55,
    "T56a": t56a,
    "T57": t57,
    "T58": t58,
    "T59": t59,
    "T60": t60,
}
