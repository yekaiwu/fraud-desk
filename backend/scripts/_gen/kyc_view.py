"""What the KYC / AML documents *print*, kept apart from the registry truth.

The World (``world.py``) models the company, its people, holders and registry entry. The ownership
chart, the shareholder register, the passport and the source-of-funds declaration are *views* of that
truth that a plot may bend (chart omits a holder, register redistributed, MRZ edited ...). They live
in ``World.notes["kyc"]`` so ``apply_plot`` deep copies them with the world.

``apply_kyc_mutation`` understands the document targets that ``world.apply_plot`` does not
(``ownership_chart:-``, ``shareholder_register:-``, ``company_profile:-``, ``passport:1``,
``source_of_funds_declaration:-``) so the eval's world-channel tampers (T51, T53 to T58) can use the
ordinary ``Mutation`` records. Ops and args are documented on that function.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import ids as idm
from .schema import Mutation
from .world import World, add_person, apply_plot, q2
from .world_types import Person

D = Decimal
UBO_THRESHOLD = D("25.00")
KYC_TARGETS = (
    "ownership_chart",
    "shareholder_register",
    "company_profile",
    "passport",
    "source_of_funds_declaration",
    "certificate_of_incorporation",
)


@dataclass
class ChartRow:
    name: str
    holder_type: str  # individual | company
    id_or_uen: str | None
    percent: Decimal  # as printed; a child's percent is held *in its parent* (data-model 6.8)
    parent: str | None = None
    is_ubo: bool = False


@dataclass
class RegRow:
    name: str
    id_or_uen: str | None
    shares: int
    percent: Decimal
    share_class: str = "Ordinary"
    acquired: date | None = None


@dataclass
class Sof:
    declarant: str
    declaration_date: date
    declared_amount: Decimal
    currency: str
    origin_category: str  # sales_proceeds | ...
    origin_description: str
    counterparty: str | None
    invoice_reference: str | None
    period_start: date | None
    period_end: date | None
    receiving_account: str | None


@dataclass
class KycView:
    chart: list[ChartRow] = field(default_factory=list)
    register: list[RegRow] = field(default_factory=list)
    total_shares: int = 0
    chart_threshold: Decimal = UBO_THRESHOLD
    sof: Sof | None = None
    passport_over: dict[str, str] = field(default_factory=dict)  # visual / MRZ overrides
    cert_over: dict[str, str] = field(default_factory=dict)


def view_of(w: World) -> KycView:
    v = w.notes.get("kyc")
    if v is None:
        v = build_view(w)
        w.notes["kyc"] = v
    return v


def _owner_for(w: World, uen_or_id: str | None) -> Person | None:
    o = w.notes.get("kestrel_owner")
    return o if isinstance(o, Person) and o.id_number == uen_or_id else None


def build_view(w: World) -> KycView:
    """The honest documents: chart, register and the numbers they print equal the registry."""
    v = KycView()
    for h in w.holders:
        ubo = h.holder_type == "individual" and h.percent >= UBO_THRESHOLD
        v.chart.append(ChartRow(h.name, h.holder_type, h.id_or_uen, h.percent, None, ubo))
        if h.holder_type == "company":
            inter = next((e for e in w.intermediates if e["uen"] == h.id_or_uen), None)
            for s in (inter or {}).get("shareholders", []):
                pct = D(s["percent"])
                owner = _owner_for(w, s["id_or_uen"])
                eff = h.percent * pct / 100
                v.chart.append(
                    ChartRow(
                        owner.name if owner else str(s["name"]).title(),
                        s["holder_type"],
                        s["id_or_uen"],
                        pct,
                        h.name,
                        s["holder_type"] == "individual" and eff >= UBO_THRESHOLD,
                    )
                )
    inc = w.company.incorporation
    for i, h in enumerate(w.holders):
        when = inc if h.holder_type == "individual" else inc + timedelta(days=700 + 11 * i)
        v.register.append(
            RegRow(
                (h.registry_name or h.name).upper(),
                h.id_or_uen,
                h.shares,
                h.percent,
                h.share_class,
                when,
            )
        )
    v.total_shares = sum(h.shares for h in w.holders)
    return v


# -------------------------------------------------------------------------------- list edits


def _rescale(rows: list[Any], total: Decimal | None = None) -> None:
    """Rescale the direct rows' percentages to ``total`` (last row absorbs rounding)."""
    total = total if total is not None else D("100.00")
    direct = [r for r in rows if getattr(r, "parent", None) is None]
    s = sum((r.percent for r in direct), D(0))
    if not direct or s == 0:
        return
    run = D(0)
    for r in direct[:-1]:
        r.percent = q2(r.percent * total / s)
        run += r.percent
    direct[-1].percent = q2(total - run)


def _rescale_shares(reg: list[RegRow], total_shares: int) -> None:
    run = 0
    for r in reg[:-1]:
        r.shares = int((D(total_shares) * r.percent / 100).to_integral_value(ROUND_HALF_UP))
        run += r.shares
    if reg:
        reg[-1].shares = total_shares - run


def omit_holder(v: KycView, fragment: str, *, rescale: bool, where: str = "both") -> str | None:
    """Drop the direct holder whose name contains ``fragment`` (and its children) from the chart
    and/or the register. ``rescale`` redistributes the rest to 100.00 (the careful forger).
    Returns the omitted holder's name."""
    frag = fragment.casefold()
    omitted: str | None = None
    if where in ("chart", "both"):
        hit = next((r for r in v.chart if r.parent is None and frag in r.name.casefold()), None)
        if hit is not None:
            omitted = hit.name
            v.chart = [r for r in v.chart if r is not hit and r.parent != hit.name]
            if rescale:
                _rescale(v.chart)
    if where in ("register", "both"):
        hit_r = next((r for r in v.register if frag in r.name.casefold()), None)
        if hit_r is not None:
            omitted = omitted or hit_r.name
            v.register = [r for r in v.register if r is not hit_r]
            if rescale:
                _rescale(v.register)
                _rescale_shares(v.register, v.total_shares)
    return omitted


# ----------------------------------------------------------------------------- mutations


def is_kyc_target(target: str) -> bool:
    return target.split(":", 1)[0] in KYC_TARGETS


def apply_kyc_mutation(w: World, m: Mutation) -> None:
    """Apply one KYC-document mutation in place (callers deep copy first).

    * ``ownership_chart:-`` / ``shareholder_register:-``
        - ``set_field {field:"omit_holder", value:<name fragment>, rescale:bool}``
        - ``override_cell {table:"holders"|"shareholders", row, column, value}``
          (columns ``percent``, ``holder_name``, ``is_ubo``, ``shares``)
    * ``company_profile:-``: ``set_field {field:"add_director", value:<name>, in_registry:bool}``
    * ``passport:<n>``: ``set_field {field:"mrz_line1"|"mrz_line2"|"passport_number"|"date_of_birth", value}``
    * ``source_of_funds_declaration:-``: ``set_field {field:"declared_amount"|"counterparty_name"|..., value}``
    """
    v = view_of(w)
    kind = m.target.split(":", 1)[0]
    a = m.args
    if kind in ("ownership_chart", "shareholder_register"):
        _chart_register(v, kind, m)
    elif kind == "company_profile":
        if a.get("field") != "add_director":
            raise ValueError(f"unsupported company_profile mutation {a!r}")
        add_person(
            w,
            str(a["value"]),
            on_profile=True,
            in_registry=bool(a.get("in_registry", False)),
        )
    elif kind == "passport":
        v.passport_over[str(a["field"])] = str(a["value"])
    elif kind == "certificate_of_incorporation":
        v.cert_over[str(a["field"])] = str(a["value"])
    elif kind == "source_of_funds_declaration":
        if v.sof is None:
            raise ValueError("this bundle has no source-of-funds declaration")
        f = str(a["field"])
        val = a.get("value")
        if f == "declared_amount":
            v.sof.declared_amount = q2(D(str(val)))
        elif f in ("origin_category", "origin_description", "counterparty_name", "currency"):
            setattr(v.sof, {"counterparty_name": "counterparty"}.get(f, f), str(val))
        elif f in ("period_start", "period_end", "declaration_date"):
            setattr(v.sof, f, date.fromisoformat(str(val)))
        else:
            raise ValueError(f"unsupported source_of_funds_declaration field {f!r}")
    else:  # pragma: no cover - guarded by is_kyc_target
        raise ValueError(m.target)


def _chart_register(v: KycView, kind: str, m: Mutation) -> None:
    a = m.args
    if m.op == "set_field" and a.get("field") == "omit_holder":
        where = "chart" if kind == "ownership_chart" else "register"
        if omit_holder(v, str(a["value"]), rescale=bool(a.get("rescale")), where=where) is None:
            raise ValueError(f"no holder matching {a['value']!r}")
        return
    if m.op == "override_cell":
        rows = v.chart if kind == "ownership_chart" else v.register
        r = rows[int(a["row"])]
        col, val = str(a["column"]), a.get("value")
        if col == "percent":
            r.percent = q2(D(str(val)))
        elif col == "holder_name":
            r.name = str(val)
        elif col == "is_ubo" and isinstance(r, ChartRow):
            r.is_ubo = str(val).lower() == "true"
        elif col == "shares" and isinstance(r, RegRow):
            r.shares = int(str(val))
        else:
            raise ValueError(f"unsupported column {col!r}")
        return
    raise ValueError(f"unsupported {kind} mutation {m.op} {a!r}")


def apply_mutations(w: World, muts: list[Mutation]) -> World:
    """Apply ``muts`` in order to a deep copy of ``w``: KYC-document targets here, everything
    else through ``world.apply_plot`` (which also copies)."""
    cur = copy.deepcopy(w)
    for m in muts:
        if is_kyc_target(m.target):
            apply_kyc_mutation(cur, m)
        else:
            cur = apply_plot(cur, [m])
    return cur


def passport_mrz(w: World, p: Person) -> tuple[str, str]:
    """TD3 lines for ``p``; ``KycView.passport_over`` may replace either line (T51)."""
    assert p.passport_expiry is not None
    l1, l2 = idm.build_td3(
        issuing=p.nationality,
        surname=p.surname,
        given_names=p.given_names,
        passport_no=p.id_number,
        nationality=p.nationality,
        dob=p.birth,
        sex=p.sex,
        expiry=p.passport_expiry,
    )
    over = view_of(w).passport_over
    return over.get("mrz_line1", l1), over.get("mrz_line2", l2)
