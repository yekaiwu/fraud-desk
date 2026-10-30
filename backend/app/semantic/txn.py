"""Transaction description labels (docs/semantic-signals.md, section on TXN).

A bank statement line says what it is in free text: ``TOP UP BINANCE``, ``GAJI STAFF``, ``MERLION
CASINO``. A keyword classifier covers the wording it was written for; a model can read the rest.
This module asks a model to put each DISTINCT description into a closed set of ten labels, and keeps
the answer only when the model also quotes the part of the description that shows it (the *cue*) and
the cue is really in that description. It creates no flag. The labels are an extra input to the
deterministic rules (``x_declared_vs_activity`` fills the gaps of its own C5 annotations with the
``gambling`` and ``crypto`` labels); with ``GLDB_SEMANTIC=off`` nothing here is called and the rule
reads exactly what it read before.

Design points:

* Evidence-bound. The reply is ``{item, label, cue}`` per description. A label other than ``other`` is
  kept only when the cue is a verbatim piece (case, spacing and width folded) of the description the
  item stands for, at least three characters long. Otherwise it is dropped and counted. The cue is
  checked again against each statement line that uses the label.
* Batched and de-duplicated. Descriptions are normalised (case, spacing, digit runs) and
  de-duplicated with the direction (money in or out); each distinct one is asked once per case and
  remembered, in batches of ``BATCH`` per statement. A model failure leaves those descriptions
  unlabelled and the check is reported as not run (the case is partial); it never reads as "no
  gambling here".
* Only adds attention. A label can add an observation to a rule that looks for risky activity. It is
  never used where it could remove one (see the docs section: ``x_sof_support`` and
  ``bs_pass_through`` do not use it).
* Untrusted text. Descriptions go to the model in the ``user`` message between ``wrap_untrusted``
  delimiters; the instructions are fixed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.context import CaseContext
from app.core.models import Document, Row, clean_text
from app.names import sorted_rows
from app.semantic.call import UNTRUSTED_NOTICE, SemanticNotRun, call_model
from app.semantic.claims import Verification, norm
from app.semantic.policy import semantic_enabled
from app.semantic.render import wrap_documents

PROMPT_NAME = "txn_labels"  # registered as purpose "semantic:txn_labels"
STATE_KEY = "txn"
RULE = "sem_txn_labels"

LABELS: tuple[str, ...] = (
    "salary",
    "rent",
    "supplier_trade",
    "loan",
    "cash",
    "gambling",
    "crypto",
    "transfer_individual",
    "tax_government",
    "other",
)
# the labels a rule can use today: model label -> the C5 label the rule already understands
C5_OF_LABEL: dict[str, str] = {"gambling": "gambling", "crypto": "crypto_virtual_assets"}

BATCH = 40  # distinct descriptions per model call
MAX_DISTINCT = 400  # per case; more are left unlabelled and counted
DESC_MAX_CHARS = 200
CUE_MIN_CHARS = 3
CUE_MAX_CHARS = 80

TASK = """\
You label lines of a bank statement. Each item has a number, a direction (credit = money in, \
debit = money out) and the description printed on the statement. Descriptions are short, \
abbreviated, misspelt and sometimes in Malay or romanised Chinese. Give every item exactly one label:

salary: payroll, wages, staff pay, bonus, commission paid to staff
rent: rent, lease, building or management service charges for premises (the word rent with an \
address is rent)
supplier_trade: a business buying or selling goods or services. This includes a payment to or from \
a company (Pte Ltd, P/L, Holdings, Group, Enterprise ...) when nothing else in the description \
points to another label, a customer paying, a supplier paid, a retainer or invoice, utilities, \
telecom, insurance premiums, professional fees, a refund to or from a company, and money transfer or \
forex companies
loan: a bank loan drawdown, instalment or repayment, loan interest, financing, a credit line
cash: a cash deposit, cash withdrawal, ATM, cash cheque
gambling: casino, betting, lottery, sweepstake, gaming. Also when only the other party's company \
name shows it, whatever the direction and whatever the payment is for (a payment to a gaming \
company, a refund from a lottery company)
crypto: cryptocurrency or digital token exchange, wallet, top-up or purchase (USDT, BTC, Binance). \
Also when only the other party's company name shows it (token, coin, blockchain, digital assets, \
crypto), whatever the direction and whatever the payment is for
transfer_individual: a transfer to or from a private person (a personal name, S/O, D/O, BIN, BINTE), \
including a director, owner or shareholder (director loan, shareholder advance, owner's drawings). \
A company is never a private person
tax_government: tax, GST, CPF, customs, fines, government fees
other: bank charges, interest earned, anything else, or too unclear to tell

Rules:
- Decide from the words of the description. The amount, and the name or origin of a person, say \
nothing.
- Look through abbreviations and typing errors (GMNG is gaming, LOTT is lottery, BLKCHN is \
blockchain, DIGI is digital, TRF/TFR is transfer, RFND/RFD is refund, PMT/PYMT is payment). Common \
Malay and romanised Chinese words: gaji, gongzi = salary; sewa, fangzu = rent; pinjaman, \
pinhaman, jiekuan, daikuan = loan, huankuan = loan repayment; pinjaman pengarah and dongshi jiekuan \
= a director's loan, which is transfer_individual; bayaran, fukuan = payment; terimaan, shoukuan, \
huokuan = money received or goods payment; pulangan, tuikuan = refund; lixi shouru = interest \
received (other); caj bank = bank charge; baofei, insurans = insurance premium; cukai = tax.
- If the words do not clearly show one label, answer other. Do not guess.
- cue: copy, letter for letter, the shortest part of the description that shows the label (one to \
four words). For other, cue is an empty string.
- Answer every item once, by its number."""

SYSTEM = f"{TASK}\n\n{UNTRUSTED_NOTICE}"


def txn_json_schema() -> dict[str, Any]:
    entry = {
        "type": "object",
        "properties": {
            "item": {"type": "integer"},
            "label": {"type": "string", "enum": list(LABELS)},
            "cue": {"type": "string"},
        },
        "required": ["item", "label", "cue"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"labels": {"type": "array", "items": entry}},
        "required": ["labels"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------------------------------ results


@dataclass(frozen=True)
class TxnLabel:
    label: str  # one of LABELS except "other"
    cue: str  # a piece of the description it was derived from (verified)


@dataclass
class TxnTally(Verification):
    """Claim counts of the labeller, in the shape ``CheckRun.reason`` expects. ``proposed`` counts
    labels other than ``other``; ``kept`` those whose cue was found. The extras go after them."""

    kept: int = 0
    other: int = 0
    unanswered: int = 0
    unlabelled_cap: int = 0
    calls: int = 0
    failed_calls: int = 0

    def note(self) -> str:
        base = super().note().replace(f"kept={len(self.claims)}", f"kept={self.kept}")
        extra = [f"other={self.other}", f"calls={self.calls}"]
        if self.unanswered:
            extra.append(f"unanswered={self.unanswered}")
        if self.unlabelled_cap:
            extra.append(f"unlabelled_cap={self.unlabelled_cap}")
        if self.failed_calls:
            extra.append(f"failed_calls={self.failed_calls}")
        return base + " " + " ".join(extra)


@dataclass
class TxnState:
    labels: dict[str, TxnLabel | None] = field(default_factory=dict)  # None: other / unlabelled
    tally: TxnTally = field(default_factory=TxnTally)


# ---------------------------------------------------------------------------------- the keys

_DIGITS = re.compile(r"\d+")


def description_key(description: str, *, credit: bool) -> str:
    """De-duplication key: direction + case/spacing-folded text with digit runs masked, so
    ``INV 1042`` and ``INV 1043`` are one description."""
    return ("cr|" if credit else "dr|") + _DIGITS.sub("#", norm(description))


def cue_in(cue: str, description: str) -> bool:
    """The cue is a verbatim piece of the description (after ``norm``), at least three characters
    long unless it is the whole description."""
    c = norm(clean_text(cue, CUE_MAX_CHARS))
    d = norm(clean_text(description, DESC_MAX_CHARS))
    if not c or not d:
        return False
    return c in d and (len(c) >= CUE_MIN_CHARS or c == d)


def statement_rows(doc: Document) -> list[tuple[str, bool]]:
    """``(description, is_credit)`` of every line of a statement that has text and one amount."""
    out: list[tuple[str, bool]] = []
    for row in sorted_rows(doc.tables.get("transactions")):
        text = _description(row)
        credit = row.cells["credit"].money() if "credit" in row.cells else None
        debit = row.cells["debit"].money() if "debit" in row.cells else None
        if not text or (credit is None and debit is None):
            continue
        out.append((text, credit is not None))
    return out


def _description(row: Row) -> str:
    cell = row.cells.get("description")
    return clean_text(cell.text(), DESC_MAX_CHARS) if cell is not None else ""


# ------------------------------------------------------------------------------- the labeller


def _user_block(batch: Sequence[tuple[str, str]]) -> str:
    lines = [
        f"item {i} | {'credit' if key.startswith('cr|') else 'debit'} | {text}"
        for i, (key, text) in enumerate(batch, start=1)
    ]
    return "Label every item.\n\n" + wrap_documents([("txn_batch", "\n".join(lines))])


def _absorb(state: TxnState, batch: Sequence[tuple[str, str]], reply: dict[str, Any]) -> int | None:
    """Verify one reply into ``state``. Returns the number of items the model left unanswered, or
    ``None`` when it answered no item at all (nothing stored). An unanswered item is not stored: it
    stays unlabelled and may be asked again, and the caller reports the check as not run."""
    tally = state.tally
    entries = reply.get("labels") or []
    answered = {
        e.get("item")
        for e in entries
        if isinstance(e.get("item"), int) and 1 <= e["item"] <= len(batch)
    }
    if not answered:
        return None
    seen: set[int] = set()
    for e in entries:
        item, label = e.get("item"), e.get("label")
        wanted = label != "other"
        if wanted:
            tally.proposed += 1
        if not isinstance(item, int) or not 1 <= item <= len(batch):
            if wanted:
                tally.dropped["unknown_row"] += 1
            continue
        if item in seen:
            if wanted:
                tally.dropped["duplicate"] += 1
            continue
        seen.add(item)
        key, text = batch[item - 1]
        if not wanted:
            tally.other += 1
            state.labels[key] = None
            continue
        cue = clean_text(str(e.get("cue") or ""), CUE_MAX_CHARS)
        if not cue:
            tally.dropped["empty_quote"] += 1
            state.labels[key] = None
        elif not cue_in(cue, text):
            tally.dropped["quote_not_found"] += 1
            state.labels[key] = None
        else:
            tally.kept += 1
            state.labels[key] = TxnLabel(label, cue)
    missing = [i for i in range(1, len(batch) + 1) if i not in seen]
    tally.unanswered += len(missing)
    return len(missing)


def label_statements(ctx: CaseContext, docs: Sequence[Document]) -> None:
    """Label the distinct descriptions of ``docs`` that this case has not seen yet, one statement
    at a time, and remember the answers in ``ctx.semantic_state``. Raises ``SemanticNotRun`` after
    the last statement when any call failed; the labels that were verified before and after the
    failure stay available (they only add attention), but the check is reported as not run."""
    state: TxnState = ctx.semantic_state.setdefault(STATE_KEY, TxnState())
    failure: str | None = None
    failed: set[str] = set()  # not asked again in this run: a down server is tried once per batch
    for doc in docs:
        fresh: dict[str, str] = {}
        for text, credit in statement_rows(doc):
            key = description_key(text, credit=credit)
            if key not in state.labels and key not in failed:
                fresh.setdefault(key, text)
        room = max(0, MAX_DISTINCT - len(state.labels))
        ordered = sorted(fresh.items())
        if len(ordered) > room:
            state.tally.unlabelled_cap += len(ordered) - room
            ordered = ordered[:room]
        for start in range(0, len(ordered), BATCH):
            batch = ordered[start : start + BATCH]
            state.tally.calls += 1
            try:
                reply = call_model(
                    ctx,
                    name=PROMPT_NAME,
                    system=SYSTEM,
                    user=_user_block(batch),
                    schema_name="TxnLabels",
                    json_schema=txn_json_schema(),
                    stage="rules",
                    document_id=doc.id,
                )
                missing = _absorb(state, batch, reply)
                if missing is None:
                    raise SemanticNotRun("the model answered none of the items")
                if missing:  # a partial reply is a malformed one: silence is never "no label"
                    raise SemanticNotRun(
                        f"the model left {missing} of {len(batch)} items unanswered"
                    )
            except SemanticNotRun as exc:
                failed.update(key for key, _ in batch)
                state.tally.failed_calls += 1
                failure = failure or exc.reason
    ctx.semantic_tally[RULE] = state.tally
    if failure is not None:
        raise SemanticNotRun(failure)


# --------------------------------------------------------------------------------- consumers


def label_for(ctx: CaseContext, description: str | None, *, credit: bool) -> TxnLabel | None:
    """The verified label of a statement line, or ``None``: the feature is off, the line was not
    labelled, the model said ``other``, or the cue is not in this line's text."""
    if not semantic_enabled(ctx.settings) or not description:
        return None
    state: TxnState | None = ctx.semantic_state.get(STATE_KEY)
    if state is None:
        return None
    found = state.labels.get(
        description_key(clean_text(description, DESC_MAX_CHARS), credit=credit)
    )
    if found is None or not cue_in(found.cue, description):
        return None
    return found


def c5_label_for(ctx: CaseContext, row: Row) -> tuple[str, TxnLabel] | None:
    """The C5 label (``gambling`` or ``crypto_virtual_assets``) that the model label of this line
    maps to, with the label itself; ``None`` for every other label."""
    cell = row.cells.get("description")
    if cell is None:
        return None
    credit = "credit" in row.cells and row.cells["credit"].money() is not None
    found = label_for(ctx, cell.text(), credit=credit)
    if found is None or found.label not in C5_OF_LABEL:
        return None
    return C5_OF_LABEL[found.label], found
