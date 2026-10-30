"""Row annotation pass: counterparty, C4 and C5 (docs/ingest-and-extraction.md 6.7).

This is the only place ``Row.annotations`` are written. After every document is extracted, for each
bank statement row:

* ``counterparty``: a deterministic parser over ``description`` (strip a leading payment-rail
  prefix, take the text up to a reference marker). The text is never interpreted;
* ``c5`` (counterparty activity) on **every** row and ``c4`` (credit category) **only on rows with a
  credit**, through the configured classifier (classification.md 4.6 to 4.9).

A classifier that fails (unavailable, schema, budget, anything) leaves the row un-annotated and the
document gets the warning ``c4_c5_unavailable``; this pass never raises. The classifier layer is a
same-wave module, so it is imported inside the default factory and is injectable for tests
(``classifier_factory(ctx) -> RowClassifier``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.context import CaseContext
from app.core.models import C4_LABELS, C5_LABELS, Annotation, Document, Row
from app.extract.cascade import cap_warnings

log = logging.getLogger("gldb.extract")

COUNTERPARTY_CLASSIFIER = "counterparty-parser"
BATCH = 50

_RAIL = re.compile(
    r"^\s*(?:FAST\s+PAYMENT\s+FROM|FAST\s+PAYMENT\s+TO|PAYNOW\s+FROM|PAYNOW\s+TO|GIRO\s+FROM|"
    r"GIRO\s+TO|GIRO\s+CREDIT|GIRO\s+PAYMENT\s+TO|GIRO\s+PAYMENT\s+FROM|"
    r"FUNDS\s+TRANSFER\s+FROM|FUNDS\s+TRANSFER\s+TO|TRF\s+FROM|TRF\s+TO|"
    r"INWARD\s+REMITTANCE\s+FROM|OUTWARD\s+REMITTANCE\s+TO|CHEQUE\s+DEPOSIT|CASH\s+DEPOSIT)"
    r"\b[\s:\-]*",
    re.I,
)
# a reference marker, or a document number such as ``SI-2026-0109`` / ``INV-2026-0117``
# the slash of the patronymic markers S/O, D/O, A/L and A/P ("RAJESH S/O KUMAR") belongs to the name
_REF = re.compile(
    r"\s(?:REF|INV|PO)\b|\s-\s|(?<!\b[SDA])/|/(?![OLP]\b)|\s[A-Z]{1,5}-\d{4}-\d{2,}\b", re.I
)


def parse_counterparty(description: str | None) -> str | None:
    """Name after a payment-rail prefix, up to a reference marker; ``None`` when there is no
    recognised prefix (for example ``SERVICE CHARGE``) or nothing follows it."""
    if not description:
        return None
    text = " ".join(description.split())
    m = _RAIL.match(text)
    if m is None:
        return None
    rest = text[m.end() :]
    cut = _REF.search(rest)
    if cut is not None:
        rest = rest[: cut.start()]
    rest = rest.strip(" -:;,")
    return rest or None


@dataclass
class RowItem:
    text: str
    context: dict[str, str]


class RowClassifier(Protocol):
    def classify(
        self, question: Literal["C4", "C5"], items: Sequence[RowItem]
    ) -> list[Annotation | None]: ...


ClassifierFactory = Callable[[CaseContext], RowClassifier]


class _DefaultRowClassifier:
    """Rules baseline, plus the hosted classifier where the mode and the decision call for it."""

    def __init__(self, ctx: CaseContext):
        from app.classify import base  # same-wave module: imported late on purpose

        self.base = base
        self.ctx = ctx
        self.mode = ctx.settings.classifier_mode
        self.rules = base.get_classifier("rules", ctx)
        self.hosted = None
        if self.mode in ("llm", "cascade"):
            try:
                self.hosted = base.get_classifier("llm", ctx)
            except base.ClassifierUnavailable:
                self.hosted = None

    def classify(
        self, question: Literal["C4", "C5"], items: Sequence[RowItem]
    ) -> list[Annotation | None]:
        base = self.base
        labels = base.LABELS[question]
        min_prob = self.ctx.settings.classifier_min_prob
        ci = [base.ClassifyItem(text=i.text, context=i.context) for i in items]
        rules_dist = base.classify_many(self.rules, ci, labels, question=question)
        cands = [
            [base.Candidate(classifier=self.rules.name, version=self.rules.version, distribution=d)]
            for d in rules_dist
        ]
        decisions = [base.decide(question, c, min_prob=min_prob) for c in cands]
        if self.hosted is not None:
            escalate = [
                k
                for k, d in enumerate(decisions)
                if self.mode == "llm"
                or d.low_confidence
                or d.label in base.NONSPECIFIC.get(question, set())
            ]
            for start in range(0, len(escalate), BATCH):
                part = escalate[start : start + BATCH]
                try:
                    dists = base.classify_many(
                        self.hosted, [ci[k] for k in part], labels, question=question
                    )
                except Exception as exc:  # the row keeps its rules annotation
                    log.info("hosted %s failed: %s", question, type(exc).__name__)
                    break
                for k, dist in zip(part, dists, strict=True):
                    model_for = getattr(self.ctx.llm, "model_for", None)
                    name = f"llm:{model_for('cheap')}" if model_for else self.hosted.name
                    cands[k].append(
                        base.Candidate(
                            classifier=name, version=self.hosted.version, distribution=dist
                        )
                    )
                    decisions[k] = base.decide(question, cands[k], min_prob=min_prob)
        return [
            Annotation(
                label=d.label,
                probability=min(1.0, max(0.0, d.probability)),
                classifier=d.classifier,
                version=d.version,
            )
            for d in decisions
        ]


def _related_parties(ctx: CaseContext) -> str:
    names: list[str] = []
    for doc in ctx.case.documents:
        if doc.doc_type == "company_profile":
            names += [r.cells["director_name"].text() for r in _rows(doc, "directors")]
            names += [r.cells["holder_name"].text() for r in _rows(doc, "shareholders")]
        elif doc.doc_type in ("shareholder_register", "ownership_chart"):
            tname = "shareholders" if doc.doc_type == "shareholder_register" else "holders"
            names += [r.cells["holder_name"].text() for r in _rows(doc, tname)]
    return "|".join(dict.fromkeys(n for n in names if n))


def _rows(doc: Document, table: str) -> list[Row]:
    t = doc.tables.get(table)
    return list(t.rows) if t else []


def annotate_rows(ctx: CaseContext, *, classifier_factory: ClassifierFactory | None = None) -> int:
    """Write ``counterparty``, ``c4`` and ``c5`` annotations; returns the number of rows touched."""
    docs = [d for d in ctx.docs("bank_statement") if d.status != "failed"]
    touched = 0
    applicant = ctx.applicant.name or ""
    related = _related_parties(ctx)
    classifier: RowClassifier | None = None
    classifier_error = False
    try:
        classifier = (classifier_factory or _DefaultRowClassifier)(ctx)
    except Exception as exc:  # unavailable classifier layer: counterparty parsing still works
        log.info("row classifier unavailable: %s", type(exc).__name__)
        classifier_error = True

    for doc in docs:
        table = doc.tables.get("transactions")
        if table is None or not table.rows:
            continue
        for row in table.rows:
            row.annotations = {}
            cp = parse_counterparty(row.cells["description"].value)
            if cp:
                row.annotations["counterparty"] = Annotation(
                    label=cp, probability=1.0, classifier=COUNTERPARTY_CLASSIFIER, version="1"
                )
        missing = False
        if classifier is not None and not classifier_error:
            try:
                with ctx.costs.scope(stage="extract", document_id=doc.id):
                    _classify_doc(ctx, doc, classifier, applicant, related)
            except Exception as exc:
                log.info("row classification failed: %s", type(exc).__name__)
                missing = True
        else:
            missing = True
        for row in table.rows:
            credit = row.cells["credit"].money() is not None
            if "c5" not in row.annotations or (credit and "c4" not in row.annotations):
                missing = True
        if missing:
            doc.warnings = cap_warnings([*doc.warnings, "c4_c5_unavailable"])
        touched += len(table.rows)
    return touched


def _classify_doc(
    ctx: CaseContext, doc: Document, clf: RowClassifier, applicant: str, related: str
) -> None:
    rows = doc.tables["transactions"].rows

    def run(question: Literal["C4", "C5"], idx: list[int]) -> None:
        valid = C4_LABELS if question == "C4" else C5_LABELS
        for start in range(0, len(idx), BATCH):
            part = idx[start : start + BATCH]
            items = []
            for k in part:
                row = rows[k]
                cp = row.annotations.get("counterparty")
                credit = row.cells["credit"].value
                ctxd = {"applicant_name": applicant, "counterparty": cp.label if cp else ""}
                if question == "C4":
                    ctxd["amount"] = credit or ""
                    ctxd["related_parties"] = related
                else:
                    ctxd["direction"] = "credit" if credit else "debit"
                items.append(RowItem(row.cells["description"].text(), ctxd))
            try:
                out = clf.classify(question, items)
            except Exception as exc:  # leave this batch un-annotated
                log.info("%s batch failed: %s", question, type(exc).__name__)
                continue
            for k, ann in zip(part, out, strict=False):
                if ann is not None and ann.label in valid:
                    rows[k].annotations["c4" if question == "C4" else "c5"] = ann

    run("C5", list(range(len(rows))))
    run("C4", [k for k, r in enumerate(rows) if r.cells["credit"].money() is not None])
