"""Put a bank statement that arrived in pieces back together, or say that it could not be.

A statement photographed or scanned page by page reaches the pipeline as separate image files, and
segmentation never spans files, so every page becomes its own ``bank_statement`` document. Rules
that need the whole statement (opening + credits - debits = closing, "page x of y", the closing
balance of one month against the opening of the next, credits summed across statements) would then
compare a fragment with a whole and raise high flags that only mean "we lost the other pages".

This module runs once, at the end of the extract stage, and decides for every statement made of
scanned pages whose own "page x of y" chain is incomplete:

* ``assembled``: pages of one statement, ordered with the cues below, complete from page 1 to
  page y. Whole-statement rules read them as ONE statement (``logical_docs``).
* ``incomplete``: a page is missing, so the statement cannot be verified.
* ``ambiguous``: two statements fit equally well, so no page is guessed onto either.

The last two are NOT flagged as tampering. They are recorded in ``Document.statement`` and become
"pages not reviewed": the whole-statement rules skip them, coverage drops, the case is partial (it
cannot reach the fast lane) and one info flag says why. A chain that is verifiable and broken
(markers out of order, repeated, or skipping a page inside one file) is left alone and the
existing ``bs_page_chain`` flags it. Digital documents are never touched.

Cues (all deterministic, no model call): account number, period, bank name, currency, the "page x
of y" markers, the running balance (the last balance of one page equals the balance of the next
page's first row before its amount) and transaction dates against the statement period. Markers
and identity decide; balance and dates only break ties, so a page whose balance was edited still
joins the right statement and the running-balance rule flags the edit.

Limits, stated plainly: a page that the extractor read wrongly (a misread marker or account
number) falls out as ``incomplete``; two pages that are identical in every cue stay ``ambiguous``;
a forged page that carries the right markers and a consistent balance is accepted like any other.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.context import CaseContext, natural_key
from app.core.models import Document, Row, StatementLink, Table
from app.core.money import approx_eq

USABLE = ("ok", "partial")
MAX_REASON_CHARS = 300


class AssembledStatement(Document):
    """Run-time view of the members of an ``assembled`` group as one statement. Never stored.

    ``origin_rows`` / ``origin_fields`` map a row of the merged tables, or a field, back to the
    stored member document so ``make_flag`` can point a flag at the page the evidence is on."""

    member_ids: list[str] = []
    origin_rows: dict[str, list[tuple[str, int]]] = {}
    origin_fields: dict[str, str] = {}
    all_marked: bool = True  # every member page carries a "page x of y" marker


# ------------------------------------------------------------------------------ profile


@dataclass
class _Piece:
    doc: Document
    acct: str = ""
    currency: str = ""
    bank: str = ""
    xs: list[int] = field(default_factory=list)
    y: int | None = None
    start: date | None = None
    end: date | None = None
    opening: Decimal | None = None
    closing: Decimal | None = None
    first_prior: Decimal | None = None  # balance before the first row
    last_balance: Decimal | None = None
    first_date: date | None = None
    last_date: date | None = None
    broken: bool = False  # markers out of order, repeated or inconsistent: not our business

    @property
    def x_first(self) -> int | None:
        return self.xs[0] if self.xs else None

    @property
    def x_last(self) -> int | None:
        return self.xs[-1] if self.xs else None

    @property
    def whole(self) -> bool:
        """Complete by itself: the existing rules judge it."""
        if self.xs:
            if self.y is not None:
                return self.xs == list(range(1, self.y + 1))
            return self.xs == [1] and self.opening is not None and self.closing is not None
        return self.opening is not None and self.closing is not None


def _norm(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def _text(doc: Document, name: str) -> str:
    cell = doc.fields.get(name)
    return (cell.value or "").strip() if cell is not None else ""


def _profile(doc: Document) -> _Piece:
    p = _Piece(doc=doc)
    p.acct = _norm(_text(doc, "account_number"))
    p.currency = _text(doc, "currency").upper()
    p.bank = _norm(_text(doc, "bank_name"))
    for name, attr in (("period_start", "start"), ("period_end", "end")):
        cell = doc.fields.get(name)
        setattr(p, attr, cell.date_() if cell is not None else None)
    for name in ("opening", "closing"):
        cell = doc.fields.get(f"{name}_balance")
        setattr(p, name, cell.money() if cell is not None else None)

    chain = doc.tables.get("page_chain")
    ys: list[int] = []
    if chain is not None:
        for row in chain.rows:
            if not p.acct:
                cell = row.cells.get("account_number")
                if cell is not None and cell.value:
                    p.acct = _norm(cell.value)
            cx, cy = row.cells.get("page_x"), row.cells.get("page_y")
            x = cx.int_() if cx is not None else None
            if x is None:
                continue
            p.xs.append(x)
            y = cy.int_() if cy is not None else None
            if y is not None:
                ys.append(y)
    if len(set(ys)) > 1:
        p.broken = True
    p.y = ys[0] if ys else None
    if p.xs and any(b - a != 1 for a, b in zip(p.xs, p.xs[1:], strict=False)):
        p.broken = True  # a gap or repeat between adjacent pages of one file: verifiable, flag it
    if p.xs and p.y is not None and (p.xs[0] < 1 or p.xs[-1] > p.y):
        p.broken = True

    table = doc.tables.get("transactions")
    if table is not None:
        _profile_rows(p, table)
    return p


def _profile_rows(p: _Piece, table: Table) -> None:
    printed = []
    for row in table.rows:
        cells = row.cells
        debit = cells["debit"].money() if "debit" in cells else None
        credit = cells["credit"].money() if "credit" in cells else None
        balance = cells["balance"].money() if "balance" in cells else None
        when = cells["date"].date_() if "date" in cells else None
        if debit is None and credit is None and balance is None:
            continue
        printed.append((debit, credit, balance))
        if when is not None:
            p.first_date = p.first_date or when
            p.last_date = when
    if not printed:
        return
    debit, credit, balance = printed[0]
    if balance is not None and (debit is None) != (credit is None):
        p.first_prior = balance - (credit or Decimal(0)) + (debit or Decimal(0))
    p.last_balance = printed[-1][2]


# ------------------------------------------------------------------------------ linking


def _compatible(a: _Piece, b: _Piece) -> bool:
    """No identity cue contradicts the other: same account, currency, bank, total, period."""
    if a.acct and b.acct and a.acct != b.acct:
        return False
    if a.currency and b.currency and a.currency != b.currency:
        return False
    if a.bank and b.bank and a.bank != b.bank:
        return False
    if a.y is not None and b.y is not None and a.y != b.y:
        return False
    return not (a.start and a.end and b.start and b.end and (a.start, a.end) != (b.start, b.end))


def _in_period(piece: _Piece, when: date | None) -> bool | None:
    if when is None or piece.start is None or piece.end is None:
        return None
    return piece.start <= when <= piece.end


def _link(a: _Piece, b: _Piece, tol: Decimal) -> int | None:
    """``None`` when ``b`` cannot be the page after ``a``, else a tie-break score (higher is
    better). Balance and date order only rank; they never rule a candidate out."""
    if a is b or not _compatible(a, b):
        return None
    if a.x_last is not None and b.x_first is not None and b.x_first != a.x_last + 1:
        return None
    # a page that already ends the statement cannot be followed, one that starts it cannot follow
    if (a.y is not None and a.x_last == a.y) or (a.x_last is None and a.closing is not None):
        return None
    if b.x_first == 1 or (b.x_first is None and b.opening is not None):
        return None
    for piece in (a, b):
        if _in_period(piece, b.first_date) is False:
            return None
    score = 0
    balance_ok: bool | None = None
    if a.last_balance is not None and b.first_prior is not None:
        balance_ok = approx_eq(a.last_balance, b.first_prior, tol)
        score += 2 if balance_ok else -2
    if a.first_date and b.first_date:
        score += 1 if b.first_date >= a.first_date else -1
    if a.last_date and b.first_date:
        score += 1 if b.first_date >= a.last_date else -1
    # without markers on both sides only an exact balance hand-over makes a page the next one
    if (a.x_last is None or b.x_first is None) and balance_ok is not True:
        return None
    return score


def _best(scores: dict[int, int]) -> list[int]:
    if not scores:
        return []
    top = max(scores.values())
    return sorted(k for k, v in scores.items() if v == top)


def _match(pieces: Sequence[_Piece], tol: Decimal) -> tuple[dict[int, int], set[int]]:
    """Mutually unique successor links ``{index: next index}`` and the set of indices that still
    had more than one equally good partner (ambiguous). A link is confirmed only when each side is
    the other's single best candidate; confirmed pages leave the pool and the rest is retried, so
    shuffled and interleaved uploads resolve by elimination without guessing."""
    n = len(pieces)
    forward: dict[int, dict[int, int]] = {i: {} for i in range(n)}
    backward: dict[int, dict[int, int]] = {j: {} for j in range(n)}
    for i in range(n):
        for j in range(n):
            score = _link(pieces[i], pieces[j], tol)
            if score is not None:
                forward[i][j] = score
                backward[j][i] = score
    links: dict[int, int] = {}
    taken: set[int] = set()
    while True:
        succ = {
            i: _best({j: s for j, s in forward[i].items() if j not in taken})
            for i in range(n)
            if i not in links
        }
        pred = {
            j: _best({i: s for i, s in backward[j].items() if i not in links})
            for j in range(n)
            if j not in taken
        }
        confirmed = next(
            ((i, js[0]) for i, js in sorted(succ.items()) if len(js) == 1 and pred[js[0]] == [i]),
            None,
        )
        if confirmed is None:
            ambiguous = {i for i, js in succ.items() if len(js) > 1}
            ambiguous |= {j for j, iss in pred.items() if len(iss) > 1}
            ambiguous |= {j for js in succ.values() if len(js) > 1 for j in js}
            ambiguous |= {i for iss in pred.values() if len(iss) > 1 for i in iss}
            return links, ambiguous
        links[confirmed[0]] = confirmed[1]
        taken.add(confirmed[1])


def _chains(n: int, links: dict[int, int]) -> tuple[list[list[int]], set[int]]:
    """The chains of linked pages and the indices of pages that sit on a cycle of links (two
    markerless pages whose balances hand over to each other). A cycle has no start, so it is cut
    at its lowest index and reported, never dropped."""
    targets = set(links.values())
    out: list[list[int]] = []
    seen: set[int] = set()
    for start in range(n):
        if start in targets:
            continue
        chain = [start]
        while chain[-1] in links:
            chain.append(links[chain[-1]])
        out.append(chain)
        seen.update(chain)
    cyclic: set[int] = set()
    for start in range(n):
        if start in seen:
            continue
        chain = [start]
        while links[chain[-1]] not in chain:
            chain.append(links[chain[-1]])
        out.append(chain)
        seen.update(chain)
        cyclic.update(chain)
    return out, cyclic


def _complete(chain: Sequence[_Piece]) -> bool:
    first, last = chain[0], chain[-1]
    starts = first.x_first == 1 or (first.x_first is None and first.opening is not None)
    if last.y is not None and last.x_last is not None:
        ends = last.x_last == last.y
    else:
        ends = last.closing is not None
    return starts and ends


def _describe(chain: Sequence[_Piece]) -> tuple[str, list[int], int | None]:
    """Why a chain is not complete: (reason, missing page numbers, announced page count)."""
    ys = [p.y for p in chain if p.y is not None]
    total = ys[0] if ys else None
    have = sorted({x for p in chain for x in p.xs})
    if total is not None and have:
        missing = [k for k in range(1, total + 1) if k not in have]
        shown = ", ".join(f"{x}" for x in have)
        gone = ", ".join(str(k) for k in missing)
        word = "page" if len(missing) == 1 else "pages"
        return (
            f"only page {shown} of {total} could be matched to this statement; "
            f"{word} {gone} missing or not matched",
            missing,
            total,
        )
    if have:
        return (
            f"only page {', '.join(map(str, have))} could be matched to this statement; the rest is "
            "missing or not matched",
            [],
            None,
        )
    return (
        "no page numbers, and the pages do not join into a statement with an opening and a closing balance",
        [],
        None,
    )


# ------------------------------------------------------------------------------ the stage


def _scan_only(ctx: CaseContext, doc: Document) -> bool:
    try:
        pages = ctx.file(doc.file_id).pages
    except KeyError:
        return False
    span = [p for p in pages if doc.page_start <= p.page <= doc.page_end]
    return bool(span) and all(p.is_scan for p in span)


def assemble_statements(ctx: CaseContext) -> dict[str, int]:
    """Write ``Document.statement`` for scanned bank statements that are not whole by themselves.
    Idempotent. Returns counts for the stage detail (empty when there is nothing to do)."""
    docs = [d for d in ctx.case.documents if d.doc_type == "bank_statement"]
    for d in docs:
        d.statement = None
    candidates: list[_Piece] = []
    for d in sorted(docs, key=lambda d: natural_key(d.id)):
        if d.status not in USABLE or not _scan_only(ctx, d):
            continue
        piece = _profile(d)
        if piece.broken or piece.whole:
            continue
        candidates.append(piece)
    if not candidates:
        return {}

    tol = ctx.settings.money_tolerance
    links, ambiguous = _match(candidates, tol)
    counts = {"assembled": 0, "incomplete": 0, "ambiguous": 0, "pages": 0}
    chains, cyclic = _chains(len(candidates), links)
    ambiguous |= cyclic
    for number, chain_idx in enumerate(chains, start=1):
        chain = [candidates[i] for i in chain_idx]
        group = f"s{number}"
        members = [p.doc.id for p in chain]
        if len(chain) > 1 and _complete(chain) and not set(chain_idx) & ambiguous:
            state, reason, missing, parts = "assembled", "", [], None
            ys = [p.y for p in chain if p.y is not None]
            parts = ys[0] if ys else None
        elif set(chain_idx) & ambiguous:
            state = "ambiguous"
            reason = (
                "the uploaded pages follow one another in a circle, so no first page was found"
                if set(chain_idx) & cyclic
                else "the uploaded pages fit more than one statement equally well, so none was "
                "joined to another"
            )
            missing, parts = [], None
        else:
            state = "incomplete"
            reason, missing, parts = _describe(chain)
        link = StatementLink(
            group=group,
            state=state,  # type: ignore[arg-type]
            members=members,
            reason=reason[:MAX_REASON_CHARS],
            parts=parts,
            missing=missing,
        )
        for p in chain:
            p.doc.statement = link
        counts[state] += 1
        if state != "assembled":
            counts["pages"] += sum(p.doc.page_end - p.doc.page_start + 1 for p in chain)
    return {k: v for k, v in counts.items() if v}


# ------------------------------------------------------------------------------ the view


def _merge(members: Sequence[Document]) -> AssembledStatement:
    lead = members[0]
    fields = {}
    origin_fields: dict[str, str] = {}
    last_wins = {"closing_balance", "total_debits", "total_credits"}
    for name, cell in lead.fields.items():
        chosen, owner = cell, lead.id
        pool = list(members)[::-1] if name in last_wins else list(members)
        for m in pool:
            candidate = m.fields.get(name)
            if candidate is not None and candidate.value is not None:
                chosen, owner = candidate, m.id
                break
        fields[name] = chosen
        origin_fields[name] = owner
    tables: dict[str, Table] = {}
    origin_rows: dict[str, list[tuple[str, int]]] = {}
    for tname, table in lead.tables.items():
        rows: list[Row] = []
        origin: list[tuple[str, int]] = []
        pages: list[int] = []
        for m in members:
            part = m.tables.get(tname)
            if part is None:
                continue
            for row in part.rows:
                rows.append(row.model_copy(update={"index": len(rows)}))
                origin.append((m.id, row.index))
            pages += part.pages
        tables[tname] = Table(name=table.name, columns=list(table.columns), rows=rows, pages=pages)
        origin_rows[tname] = origin
    span = sum(m.page_end - m.page_start + 1 for m in members)
    marked = all(
        any(
            "page_x" in r.cells and r.cells["page_x"].int_() is not None
            for r in m.tables["page_chain"].rows
        )
        for m in members
        if "page_chain" in m.tables
    )
    warnings = list(dict.fromkeys(w for m in members for w in m.warnings))
    # built from already validated members: skip re-validating every cell on each rule call
    return AssembledStatement.model_construct(
        id=lead.id,
        file_id=lead.file_id,
        page_start=lead.page_start,
        page_end=lead.page_start + span - 1,
        doc_type=lead.doc_type,
        classification=lead.classification,
        issuer=next((m.issuer for m in members if m.issuer is not None), None),
        status="partial" if any(m.status == "partial" for m in members) else "ok",
        extractor=lead.extractor,
        fields=fields,
        tables=tables,
        words=[],
        warnings=warnings[:50],
        error=None,
        statement=lead.statement,
        member_ids=[m.id for m in members],
        origin_rows=origin_rows,
        origin_fields=origin_fields,
        all_marked=marked,
    )


def logical_docs(ctx: CaseContext, *types: str, whole_only: bool = False) -> list[Document]:
    """``ctx.docs(*types)`` with bank statements seen as statements: the members of an assembled
    group become one ``AssembledStatement`` (at the lead's id position); pieces that could not be
    assembled stay as they are, or are left out with ``whole_only=True`` (use that for every rule
    that needs the whole statement, since a piece would be compared with a whole)."""
    docs = ctx.docs(*types)
    if not any(d.statement is not None for d in docs):
        return docs
    by_id = {d.id: d for d in ctx.case.documents}
    out: list[Document] = []
    for d in docs:
        link = d.statement
        if d.doc_type != "bank_statement" or link is None:
            out.append(d)
        elif link.state == "assembled":
            if link.members[0] != d.id:
                continue
            members = [by_id[i] for i in link.members if i in by_id]
            if len(members) == len(link.members) and all(m.status in USABLE for m in members):
                out.append(_merge(members))
            elif not whole_only:
                out.append(d)
        elif not whole_only:
            out.append(d)
    return out


def unresolved_groups(ctx: CaseContext) -> list[tuple[StatementLink, list[Document]]]:
    """Statements that could not be assembled, one entry per group, in lead-document order."""
    seen: dict[str, tuple[StatementLink, list[Document]]] = {}
    for d in ctx.docs("bank_statement"):
        link = d.statement
        if link is None or link.state == "assembled":
            continue
        seen.setdefault(link.group, (link, []))[1].append(d)
    return list(seen.values())
