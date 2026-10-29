"""Classifier interface, registry and the decision rule (docs/classification.md 3.1, 4.9).

One interface (``Classifier``: ``classify(text, labels, question=...) -> {label: probability}``) with
three implementations behind it: rules, a local model, a hosted small LLM. ``decide`` is the only
place where several answers become one, and it can only RAISE risk: a non-specific answer
(``other``/``boilerplate``/low probability) never overrides a confident specific one.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from app.core.models import C1_LABELS, C3_LABELS, C4_LABELS, C5_LABELS, Strict

if TYPE_CHECKING:
    from app.core.context import CaseContext

Question = Literal["C1", "C3", "C4", "C5"]

NONSPECIFIC: dict[str, set[str]] = {
    "C1": {"other", "boilerplate"},
    "C3": set(),
    "C4": {"other"},
    "C5": {"other"},
}
RISK_LABELS_C5 = ("crypto_virtual_assets", "gambling", "money_services")  # risk-first order
RISK_MIN_PROB = 0.50
LABELS: dict[str, tuple[str, ...]] = {
    "C1": tuple(C1_LABELS),
    "C3": tuple(C3_LABELS),
    "C4": tuple(C4_LABELS),
    "C5": tuple(C5_LABELS),
}

LABEL_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "C1": {
        "bank_statement": "A bank account statement listing dated transactions with debits, credits and a running balance.",
        "invoice": "A tax invoice from a supplier to a buyer with line items, GST and a total.",
        "financial_statement": "Annual or management accounts: income statement, balance sheet or cash flow statement.",
        "contract": "A commercial contract or agreement between named parties with a value and terms.",
        "purchase_order": "A purchase order issued by a buyer to a supplier for goods or services.",
        "company_profile": "A company business profile (BizFile style) with UEN, officers and shareholders.",
        "certificate_of_incorporation": "A certificate stating that a company has been incorporated.",
        "ownership_chart": "A diagram or list of shareholdings showing owners and their percentages.",
        "shareholder_register": "A register of members listing shareholders and the shares they hold.",
        "source_of_funds_declaration": "A signed declaration describing where funds come from.",
        "national_id": "A Singapore NRIC or FIN identity card (front or back).",
        "passport": "A passport identity page with a machine readable zone.",
        "boilerplate": "Generic legal text, notices, disclaimers, cover pages or blank pages with no case content.",
        "other": "Any other document that fits none of the other labels.",
    },
    "C3": {
        "dbs": "A statement issued by DBS or POSB.",
        "ocbc": "A statement issued by OCBC.",
        "uob": "A statement issued by UOB.",
        "other_bank": "A statement issued by another named bank.",
        "software_generated": "A statement printed by the company's own accounting or office software with no bank letterhead.",
    },
    "C4": {
        "trade_receipt": "Payment for goods or services that cites an invoice or purchase order.",
        "customer_payment": "A payment received from a customer with no invoice reference.",
        "financing_proceeds": "Proceeds of a loan, facility or other borrowing.",
        "owner_transfer": "Money put in by a director, shareholder or owner.",
        "inter_company": "A transfer between related or group companies.",
        "refund": "A refund, reversal or returned payment.",
        "other": "Any other kind of credit, or unknown.",
    },
    "C5": {
        "logistics": "Freight, shipping, courier, warehousing or transport.",
        "trading": "Import, export, wholesale or commodity trading.",
        "manufacturing": "Manufacturing, engineering or industrial production.",
        "professional_services": "Consulting, legal, accounting, audit or similar services.",
        "crypto_virtual_assets": "Cryptocurrency, virtual assets or exchanges.",
        "gambling": "Casinos, betting, lotteries or gaming.",
        "money_services": "Remittance, money changing or payment services.",
        "other": "Any other activity, or unknown.",
    },
}


class ClassifierUnavailable(Exception):  # noqa: N818 - name fixed by the docs
    """Raised by ``get_classifier`` / the local adapter when a classifier cannot run."""


class Classifier(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]

    def classify(
        self,
        text: str,
        labels: Sequence[str],
        *,
        question: Question,
        context: dict[str, str] | None = None,
    ) -> dict[str, float]: ...


# ----------------------------------------------------------------------------- registry

_REGISTRY: dict[str, type] = {}


def register_classifier(*, name: Literal["rules", "local", "llm"]) -> Callable[[type], type]:
    def deco(cls: type) -> type:
        _REGISTRY[name] = cls
        return cls

    return deco


def discover() -> None:
    """Import every ``app.classify`` module except ``base`` and ``stage`` (idempotent)."""
    package = importlib.import_module("app.classify")
    for info in pkgutil.iter_modules(package.__path__):
        if info.name in ("base", "stage") or info.name.startswith("_"):
            continue
        importlib.import_module(f"app.classify.{info.name}")


def get_classifier(name: str, ctx: CaseContext) -> Classifier:
    """Build a registered classifier. ``llm`` is built with ``ctx.llm``, ``ctx.costs`` and
    ``ctx.settings``. Raises :class:`ClassifierUnavailable` when it cannot run."""
    discover()
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ClassifierUnavailable(f"unknown classifier: {name}")
    if name == "llm":
        return cls(ctx.llm, ctx.costs, ctx.settings)
    if name == "local":
        return cls(ctx.settings)
    return cls()


# ----------------------------------------------------------------------------- batching


class ClassifyItem(Strict):
    text: str
    context: dict[str, str] = {}
    cache_extra: str = ""


def classify_many(
    clf: Classifier, items: Sequence[ClassifyItem], labels: Sequence[str], *, question: str
) -> list[dict[str, float] | None]:
    """Classify many items. Uses ``clf.classify_many`` when the class has it (the hosted classifier
    batches 50 rows per call and returns ``None`` for rows it had to abandon), else loops."""
    batched = getattr(clf, "classify_many", None)
    if batched is not None:
        return batched(items, labels, question=question)
    return [
        clf.classify(it.text, labels, question=question, context=it.context or None)  # type: ignore[arg-type]
        for it in items
    ]


# ----------------------------------------------------------------------------- decision rule


class Candidate(Strict):
    classifier: str  # "rules", "llm:<model>", "openjev-0.8b"
    version: str
    distribution: dict[str, float]
    rationale: str | None = None  # a hosted model's own one-line reason; data only


class Decision(Strict):
    label: str
    probability: float
    classifier: str
    version: str
    distribution: dict[str, float]
    low_confidence: bool
    disagreement: str | None = None
    rationale: str | None = None  # the winning candidate's rationale; never read by routing


def top(distribution: dict[str, float]) -> tuple[str, float]:
    """Argmax; ties go to the earlier key."""
    best_label, best_p = "", -1.0
    for label, p in distribution.items():
        if p > best_p:
            best_label, best_p = label, p
    return best_label, max(best_p, 0.0)


def _short(classifier: str) -> str:
    return classifier.split(":", 1)[0]


def _describe(candidates: Sequence[Candidate]) -> str:
    parts = []
    for c in candidates:
        label, p = top(c.distribution)
        parts.append(f"{_short(c.classifier)}={label}({p:.2f})")
    return " ".join(parts)


def decide(question: str, candidates: Sequence[Candidate], *, min_prob: float) -> Decision:
    """Combine ordered candidates (rules first, then local, then hosted) into one decision.

    Guardrails: a non-specific answer cannot override a confident specific one; for C5 a risk label
    (crypto, gambling, money services) with probability >= 0.50 beats any benign label.
    """
    if not candidates:
        raise ValueError("decide needs at least one candidate")
    nonspecific = NONSPECIFIC.get(question, set())
    tops = [top(c.distribution) for c in candidates]

    winner_index: int | None = None
    confident = False
    if question == "C5":
        risky = [
            (RISK_LABELS_C5.index(label), -p, i)
            for i, (label, p) in enumerate(tops)
            if label in RISK_LABELS_C5 and p >= RISK_MIN_PROB
        ]
        if risky:
            winner_index = min(risky)[2]
            confident = tops[winner_index][1] >= min_prob
    if winner_index is None:
        specific = [
            i for i, (label, p) in enumerate(tops) if label not in nonspecific and p >= min_prob
        ]
        if specific:
            winner_index = max(specific, key=lambda i: (tops[i][1], -i))
            confident = True
        else:
            winner_index = len(candidates) - 1  # the last consulted candidate
            confident = False

    label, probability = tops[winner_index]
    winner = candidates[winner_index]
    disagreement = None
    if confident:
        others = [
            (l2, p2)
            for i, (l2, p2) in enumerate(tops)
            if i != winner_index and l2 not in nonspecific and p2 >= min_prob and l2 != label
        ]
        if others:
            disagreement = _describe(candidates)
    return Decision(
        label=label,
        probability=probability,
        classifier=winner.classifier,
        version=winner.version,
        distribution=dict(winner.distribution),
        low_confidence=not confident,
        disagreement=disagreement,
        rationale=winner.rationale,
    )
