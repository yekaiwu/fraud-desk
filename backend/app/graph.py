"""In-case entity graph (docs/checks-kyc-aml.md sections 3.2 and 4.10).

Nodes are entities (the applicant, counterparties, invoice parties, owners, directors); edges are
bank flows, invoices, ownership and directorships. Only ``flow`` edges feed ``bs_pass_through``.
The graph is built from one case only (no cross-case graph), is deterministic (entities are created
in first-seen order over documents by numeric id and rows by index) and never reads the wall clock.
Names come from documents written by the person being checked: they are data, never instructions.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, NamedTuple

import networkx as nx

from app.core.context import CaseContext, natural_key
from app.core.models import Row
from app.names import (
    entity_key,
    has_legal_token,
    is_truncation,
    looks_like_nric,
    match_company,
    match_person,
    normalise_id,
    safe_text,
    sorted_rows,
)

APPLICANT_KEY = "@applicant"

_CHANNEL_PREFIX = re.compile(
    r"^(?:FAST|GIRO|PAYNOW|IBG|TT|INWARD|OUTWARD)\b(?:\s+(?:PAYMENT|TRANSFER|CREDIT))?\s*(?:FROM|TO)?",
    re.IGNORECASE,
)
_UEN_IN_TEXT = re.compile(r"\b\d{8,9}[A-Z]\b")


class Entity(NamedTuple):
    key: str
    display: str
    kind: Literal["applicant", "company", "person", "unknown"]
    uen: str | None


class Flow(NamedTuple):
    src: str
    dst: str
    direction: Literal["in", "out"]
    date: date
    amount: Decimal
    currency: str
    document_id: str
    row: int
    counterparty_raw: str
    c4: str | None
    c5: str | None


class Hop(NamedTuple):
    credit: Flow
    debit: Flow
    gap_days: int
    diff: Decimal  # credit amount minus debit amount (what the account kept)


class PassThrough(NamedTuple):
    hops: list[Hop]
    entities: list[str]
    credit_total: Decimal
    credits_total_all: Decimal
    share: float
    circular: list[str]


def counterparty_of(row: Row) -> str | None:
    """The ``counterparty`` annotation label, else the description minus a leading channel prefix."""
    ann = row.annotations.get("counterparty")
    if ann is not None and ann.label and ann.label.strip():
        return ann.label.strip()
    desc = row.cells["description"].text() if "description" in row.cells else ""
    desc = " ".join(desc.split())
    if not desc:
        return None
    stripped = _CHANNEL_PREFIX.sub("", desc, count=1).strip(" \t-:,")
    return stripped or None


def doc_number(document_id: str) -> int:
    digits = re.findall(r"\d+", document_id)
    return int(digits[0]) if digits else 0


def uen_in(text: str | None) -> str | None:
    m = _UEN_IN_TEXT.search((text or "").upper())
    return m.group(0) if m else None


def _label_if_confident(row: Row, name: str, min_prob: float) -> str | None:
    ann = row.annotations.get(name)
    if ann is None or ann.probability < min_prob:
        return None
    return ann.label


class EntityGraph:
    """A ``networkx.MultiDiGraph`` of entities plus the ordered list of bank flows."""

    def __init__(self, applicant: Entity | None = None) -> None:
        self.g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._flows: list[Flow] = []
        self._order: list[str] = []
        self._used_keys: set[str] = set()
        app = applicant or Entity(APPLICANT_KEY, "Applicant", "applicant", None)
        self._applicant_known = applicant is not None and bool(
            applicant.uen or applicant.display != "Applicant"
        )
        self.g.add_node(APPLICANT_KEY, entity=app)
        self._order.append(APPLICANT_KEY)
        self._used_keys.add(APPLICANT_KEY)

    # ---------------------------------------------------------------- entities

    def entity(self, key: str) -> Entity:
        return self.g.nodes[key]["entity"]

    def display(self, key: str) -> str:
        return self.entity(key).display if key in self.g else key

    def entities(self) -> list[Entity]:
        return [self.entity(k) for k in self._order]

    def _matches(self, entity: Entity, raw: str, uen: str | None, kind: str) -> bool:
        if uen and entity.uen:
            return normalise_id(uen) == normalise_id(entity.uen)
        if kind == "person" or entity.kind == "person":
            return match_person(raw, entity.display).verdict == "same"
        if match_company(raw, entity.display, allow_truncation=False).verdict == "same":
            return True
        return is_truncation(raw, entity.display)

    def resolve(self, raw: str, uen: str | None = None, *, kind: str = "unknown") -> Entity:
        """The existing entity for this name/UEN, or a new one. Exact normalised names, equal UENs
        and truncation prefixes merge; typo variants do not. Deterministic."""
        name = safe_text(raw, 120)
        uen_n = normalise_id(uen) or None
        if not name and not uen_n:
            raise ValueError("cannot resolve an entity without a name or a UEN")
        name = name or (uen_n or "")
        for key in self._order:  # applicant first, then creation order
            if key == APPLICANT_KEY and not self._applicant_known:
                continue
            existing = self.entity(key)
            if self._matches(existing, name, uen_n, kind):
                return self._maybe_upgrade(existing, name, uen_n, kind)
        return self._create(name, uen_n, kind)

    def _maybe_upgrade(self, existing: Entity, name: str, uen: str | None, kind: str) -> Entity:
        if existing.key == APPLICANT_KEY:
            return existing
        display = existing.display
        if len(name) > len(display) and is_truncation(name, display):
            display = name  # a later, fuller spelling replaces a truncated one (key is unchanged)
        new_uen = existing.uen or uen
        new_kind = existing.kind
        if new_kind == "unknown" and kind in ("company", "person"):
            new_kind = kind  # type: ignore[assignment]
        if (display, new_uen, new_kind) == (existing.display, existing.uen, existing.kind):
            return existing
        updated = existing._replace(display=display, uen=new_uen, kind=new_kind)
        self.g.nodes[existing.key]["entity"] = updated
        return updated

    def _create(self, name: str, uen: str | None, kind: str) -> Entity:
        ent_kind: Literal["company", "person", "unknown"]
        if kind == "person":
            ent_kind = "person"
        elif kind == "company" or has_legal_token(name):
            ent_kind = "company"
        else:
            ent_kind = "unknown"
        base = entity_key(name, "person" if ent_kind == "person" else "company") or (
            "uen_" + re.sub(r"[^a-z0-9]", "", (uen or "").lower())
        )
        key, n = base, 2
        while key in self._used_keys or key == APPLICANT_KEY:
            key = f"{base}_{n}"
            n += 1
        entity = Entity(key, name, ent_kind, uen)
        self.g.add_node(key, entity=entity)
        self._order.append(key)
        self._used_keys.add(key)
        return entity

    # ---------------------------------------------------------------- build

    @classmethod
    def build(cls, ctx: CaseContext) -> EntityGraph:
        app = ctx.applicant
        applicant = Entity(
            APPLICANT_KEY,
            safe_text(app.name, 120) if app.name else "Applicant",
            "applicant",
            normalise_id(app.uen) or None,
        )
        graph = cls(applicant)
        min_prob = ctx.settings.classifier_min_prob
        for doc in ctx.docs():
            if doc.status not in ("ok", "partial"):
                continue
            if doc.doc_type == "bank_statement":
                graph._add_statement(doc, min_prob)
            elif doc.doc_type == "invoice":
                graph._add_invoice(doc)
            elif doc.doc_type == "shareholder_register":
                graph._add_holders(doc)
            elif doc.doc_type == "ownership_chart":
                graph._add_chart(doc)
            elif doc.doc_type == "company_profile":
                graph._add_holders(doc)
                graph._add_directors(doc)
        graph._add_registry(ctx)
        return graph

    def _add_statement(self, doc: Any, min_prob: float) -> None:
        table = doc.tables.get("transactions")
        currency = (doc.fields["currency"].text() if "currency" in doc.fields else "").upper()
        for row in sorted_rows(table):
            when = row.cells["date"].date_()
            debit = row.cells["debit"].money()
            credit = row.cells["credit"].money()
            if when is None or (debit is None) == (credit is None):
                continue
            amount = credit if credit is not None else debit
            if amount is None or amount <= 0:
                continue
            raw = counterparty_of(row)
            if raw is None:
                continue
            desc_uen = uen_in(row.cells["description"].text())
            entity = self.resolve(raw, desc_uen)
            if entity.key == APPLICANT_KEY:
                continue  # own-account transfer: dropped
            direction: Literal["in", "out"] = "in" if credit is not None else "out"
            flow = Flow(
                src=entity.key if direction == "in" else APPLICANT_KEY,
                dst=APPLICANT_KEY if direction == "in" else entity.key,
                direction=direction,
                date=when,
                amount=amount,
                currency=currency,
                document_id=doc.id,
                row=row.index,
                counterparty_raw=safe_text(raw, 120),
                c4=_label_if_confident(row, "c4", min_prob) if direction == "in" else None,
                c5=_label_if_confident(row, "c5", min_prob),
            )
            self._flows.append(flow)
            self.g.add_edge(
                flow.src,
                flow.dst,
                key=f"flow:{doc.id}:{row.index}",
                kind="flow",
                flow=flow,
                document_id=doc.id,
                row=row.index,
                direction=direction,
                date=when.isoformat(),
                amount=format(amount, "f"),
                currency=currency,
            )

    def _party(self, name: str | None, uen: str | None, kind: str) -> Entity | None:
        if not (name or "").strip() and not (uen or "").strip():
            return None
        return self.resolve(name or "", uen, kind=kind)

    def _add_invoice(self, doc: Any) -> None:
        f = doc.fields
        supplier = self._party(f["supplier_name"].text(), f["supplier_uen"].text(), "company")
        buyer = self._party(f["buyer_name"].text(), f["buyer_uen"].text(), "company")
        if supplier is None or buyer is None:
            return
        total = f["total"].money()
        self.g.add_edge(
            supplier.key,
            buyer.key,
            key=f"invoice:{doc.id}",
            kind="invoice",
            document_id=doc.id,
            invoice_number=safe_text(f["invoice_number"].text(), 40),
            total=format(total, "f") if total is not None else None,
        )

    def _add_holders(self, doc: Any) -> None:
        for row in sorted_rows(doc.tables.get("shareholders")):
            name = row.cells["holder_name"].text()
            ident = row.cells["id_or_uen"].text()
            kind = "person" if looks_like_nric(ident) else "unknown"
            holder = self._party(name, ident if kind != "person" else None, kind)
            if holder is None:
                continue
            self._add_owns(holder, doc.id, row, row.cells["percent"].money())

    def _add_chart(self, doc: Any) -> None:
        for row in sorted_rows(doc.tables.get("holders")):
            if row.cells["parent_name"].text().strip():
                continue  # look-through rows are not owners of the applicant
            kind = "person" if row.cells["holder_type"].text() == "individual" else "company"
            ident = row.cells["id_or_uen"].text()
            holder = self._party(
                row.cells["holder_name"].text(), None if kind == "person" else ident, kind
            )
            if holder is not None:
                self._add_owns(holder, doc.id, row, row.cells["percent"].money())

    def _add_owns(self, holder: Entity, source: str, row: Row, percent: Decimal | None) -> None:
        if holder.key == APPLICANT_KEY:
            return
        self.g.add_edge(
            holder.key,
            APPLICANT_KEY,
            key=f"owns:{source}:{row.index}",
            kind="owns",
            document_id=source,
            percent=format(percent, "f") if percent is not None else None,
        )

    def _add_directors(self, doc: Any) -> None:
        for row in sorted_rows(doc.tables.get("directors")):
            person = self._party(row.cells["director_name"].text(), None, "person")
            if person is not None and person.key != APPLICANT_KEY:
                self.g.add_edge(
                    person.key,
                    APPLICANT_KEY,
                    key=f"director_of:{doc.id}:{row.index}",
                    kind="director_of",
                    document_id=doc.id,
                )

    def _add_registry(self, ctx: CaseContext) -> None:
        if ctx.registry is None or not ctx.applicant.uen:
            return
        entity = ctx.registry.lookup(ctx.applicant.uen)
        if entity is None:
            return
        for i, sh in enumerate(entity.shareholders):
            kind = "person" if sh.holder_type == "individual" else "company"
            holder = self._party(sh.name, None if kind == "person" else sh.id_or_uen, kind)
            if holder is not None and holder.key != APPLICANT_KEY:
                self.g.add_edge(
                    holder.key,
                    APPLICANT_KEY,
                    key=f"owns:registry:{i}",
                    kind="owns",
                    document_id="registry",
                    percent=sh.percent,
                )
        for i, officer in enumerate(entity.officers):
            if "director" not in officer.position.lower():
                continue
            person = self._party(officer.name, None, "person")
            if person is not None and person.key != APPLICANT_KEY:
                self.g.add_edge(
                    person.key,
                    APPLICANT_KEY,
                    key=f"director_of:registry:{i}",
                    kind="director_of",
                    document_id="registry",
                )

    # ---------------------------------------------------------------- queries

    def flows(self, direction: Literal["in", "out"] | None = None) -> list[Flow]:
        """Bank flows sorted by (date, numeric document id, row)."""
        chosen = [f for f in self._flows if direction is None or f.direction == direction]
        return sorted(chosen, key=lambda f: (f.date, doc_number(f.document_id), f.row))

    def pass_through(
        self,
        *,
        window_days: int,
        tol_pct: Decimal,
        min_amount: Decimal,
        min_pairs: int,
        min_entities: int,
        tolerance_abs: Decimal,
    ) -> PassThrough | None:
        """Credits followed within ``window_days`` by a near-equal debit to another entity.

        Each credit (in date order) takes the best unmatched debit: smallest gap, then smallest
        amount difference, then document and row. The share threshold is the caller's decision
        (``PassThrough.share`` carries the number)."""
        credits = self.flows("in")
        debits = self.flows("out")
        debit_dates = [d.date for d in debits]
        used: set[int] = set()
        hops: list[Hop] = []
        for credit in credits:
            if credit.amount < min_amount or credit.c4 == "refund":
                continue
            allowed = max(tolerance_abs, tol_pct * credit.amount)
            last = credit.date + timedelta(days=window_days)
            best: tuple[tuple[int, Decimal, int, int], int] | None = None
            j = bisect_left(debit_dates, credit.date)
            while j < len(debits) and debits[j].date <= last:
                debit = debits[j]
                if (
                    j not in used
                    and debit.dst != credit.src
                    and debit.dst != APPLICANT_KEY
                    and abs(credit.amount - debit.amount) <= allowed
                ):
                    rank = (
                        (debit.date - credit.date).days,
                        abs(credit.amount - debit.amount),
                        doc_number(debit.document_id),
                        debit.row,
                    )
                    if best is None or rank < best[0]:
                        best = (rank, j)
                j += 1
            if best is not None:
                used.add(best[1])
                debit = debits[best[1]]
                hops.append(Hop(credit, debit, best[0][0], credit.amount - debit.amount))
        if len(hops) < min_pairs:
            return None
        entities: list[str] = []
        for hop in hops:
            for key in (hop.credit.src, hop.debit.dst):
                if key not in entities and key != APPLICANT_KEY:
                    entities.append(key)
        if len(entities) < min_entities:
            return None
        sources = {hop.credit.src for hop in hops}
        circular: list[str] = []
        for hop in hops:
            if hop.debit.dst in sources and hop.debit.dst not in circular:
                circular.append(hop.debit.dst)
        credit_total = sum((h.credit.amount for h in hops), Decimal(0))
        all_total = sum((c.amount for c in credits), Decimal(0))
        share = float(credit_total / all_total) if all_total > 0 else 0.0
        return PassThrough(hops, entities, credit_total, all_total, share, circular)

    def summary(self) -> dict[str, Any]:
        """Plain JSON: ``{"nodes": [...], "edges": [...]}``, amounts as decimal strings."""
        nodes = [
            {"key": e.key, "display": e.display, "kind": e.kind, "uen": e.uen}
            for e in self.entities()
        ]
        edges: list[dict[str, Any]] = []
        for src, dst, key, data in self.g.edges(keys=True, data=True):
            item = {"src": src, "dst": dst, "id": key}
            item.update({k: v for k, v in data.items() if k != "flow"})
            edges.append(item)
        edges.sort(key=lambda e: (e["kind"], natural_key(e["id"]), e["src"], e["dst"]))
        return {"nodes": nodes, "edges": edges}
