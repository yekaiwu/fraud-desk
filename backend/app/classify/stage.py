"""Classify stage (docs/classification.md 3.4, 4.4 to 4.9).

C1 per page, default segmentation into documents, C3 issuer for bank statements. Rules decide;
a local or hosted classifier is consulted only for escalation items (low confidence, ``other`` or
``boilerplate``) and can only RAISE risk, never lower it (``base.decide``). Nothing is dropped:
``other``, ``boilerplate`` and low-probability pages stay visible as documents. C4/C5 are not run
here: they need extracted rows (the extract stage's annotation pass uses ``get_classifier``).

Idempotent: the stage clears ``case.documents`` and every ``PageInfo.classification`` first.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal

from app.classify.base import (
    NONSPECIFIC,
    Candidate,
    ClassifierUnavailable,
    Decision,
    decide,
    get_classifier,
    top,
)
from app.classify.rules import (
    RulesClassifier,
    continuation,
    normalise_text,
)
from app.core.context import CaseContext, natural_key
from app.core.costs import LLMBudgetExceeded
from app.core.errors import LLMNoFixture, LLMSchemaError, LLMTransportError
from app.core.models import (
    C1_LABELS,
    C3_LABELS,
    Classification,
    Document,
    FileRef,
    PageInfo,
    StageOutcome,
    Strict,
    Word,
)
from app.core.textlayout import page_marker

log = logging.getLogger(__name__)

MODEL_TEXT_CHARS = {"C1": 4000, "C3": 3000}
BLANK_DPI = 50
BLANK_PIXEL_MAX = 250  # a grey value below this is ink
BLANK_P = 0.90
BLANK_PAGE_REASON = "The page has no text and shows no visible content"
CONTINUATION_REASON = "Table-only page of a longer document: takes the label of the previous page"
LOCAL_MIN_SCORE = 0.35

INJECTION_PATTERNS = [
    re.compile(p)
    for p in (
        r"ignore\s+(?:(?:all|any|the|previous|above|prior)\s+)*(?:instructions|rules|checks)",
        r"\bdisregard\b",
        r"classify\s+(?:this|the)\s+(?:page|document)\s+as",
        r"label\s+(?:this|the)\s+(?:page|document)",
        r"this\s+(?:page|document)\s+is\s+(?:boilerplate|not)",
        r"system\s+prompt",
        r"you\s+are\s+(?:an?|the)\s+(?:assistant|ai|model)",
        r"do\s+not\s+(?:flag|check|report)",
        r"no\s+need\s+to\s+(?:check|verify)",
        r"skip\s+(?:this|the)\s+(?:page|check)",
    )
]


class Segment(Strict):
    file_id: str
    page_start: int
    page_end: int
    label: str
    probability: float
    distribution: dict[str, float] | None = None


def injection_suspected(text: str) -> bool:
    """Instruction-like text. Only ever a warning: it never changes a label."""
    variants = {text.casefold(), normalise_text(text)}
    return any(p.search(v) for p in INJECTION_PATTERNS for v in variants)


# ----------------------------------------------------------------------------- segmentation


def _mean_distribution(dists: list[dict[str, float] | None]) -> dict[str, float] | None:
    if not dists or any(d is None for d in dists):
        return None
    keys: list[str] = []
    for d in dists:
        keys += [k for k in d or {} if k not in keys]
    n = len(dists)
    return {k: round(sum((d or {}).get(k, 0.0) for d in dists) / n, 4) for k in keys}


def segment_pages(
    file: FileRef, pages: Sequence[PageInfo], texts: Mapping[int, str]
) -> list[Segment]:
    """Every page lands in exactly one segment (classification.md 4.4). Pages must already carry
    their C1 ``classification``; pages with an ``error`` form ``other`` segments of probability 0."""
    segments: list[Segment] = []
    current: list[PageInfo] = []
    errored = False

    def close() -> None:
        nonlocal current
        if not current:
            return
        if errored:
            segments.append(
                Segment(
                    file_id=file.id,
                    page_start=current[0].page,
                    page_end=current[-1].page,
                    label="other",
                    probability=0.0,
                    distribution=None,
                )
            )
        else:
            probs = [p.classification.probability for p in current if p.classification]
            segments.append(
                Segment(
                    file_id=file.id,
                    page_start=current[0].page,
                    page_end=current[-1].page,
                    label=current[0].classification.label if current[0].classification else "other",
                    probability=round(sum(probs) / len(probs), 4) if probs else 0.0,
                    distribution=_mean_distribution(
                        [
                            p.classification.distribution if p.classification else None
                            for p in current
                        ]
                    ),
                )
            )
        current = []

    previous_marker: tuple[int, int] | None = None
    for page in sorted(pages, key=lambda p: p.page):
        if page.error:
            if not (current and errored):
                close()
            errored = True
            current.append(page)
            previous_marker = None
            continue
        label = page.classification.label if page.classification else "other"
        marker = page_marker(texts.get(page.page, ""))
        new = not current or errored
        if not new:
            assert current[0].classification is not None
            new = label != current[0].classification.label
        if not new and marker is not None and marker[0] == 1:
            new = True  # a "page 1 of N" restart (also covers a changed N)
        if (
            not new
            and marker
            and previous_marker
            and marker[1] != previous_marker[1]
            and marker[0] == 1
        ):
            new = True
        if new:
            close()
            errored = False
        current.append(page)
        previous_marker = marker
    close()
    return segments


# ----------------------------------------------------------------------------- escalation


@dataclass
class _Unit:
    text: str  # what models see (already cut)
    context: dict[str, str]
    rules: Candidate
    page: int | None = None
    document_id: str | None = None
    decision: Decision | None = None
    failed: bool = False
    reasons: list[str] = field(default_factory=list)  # rules cues for the rules' top label


@dataclass
class _Run:
    ctx: CaseContext
    warnings: dict[str, None] = field(default_factory=dict)
    partial_reasons: list[str] = field(default_factory=list)
    escalation_unavailable: int = 0
    disagreements: int = 0
    budget_hits: int = 0
    first_budget: LLMBudgetExceeded | None = None
    hard_failures: int = 0
    _local: object | None = None
    _local_checked: bool = False
    _hosted: object | None = None
    _hosted_checked: bool = False

    # -- helpers --------------------------------------------------------------

    @property
    def min_prob(self) -> float:
        return self.ctx.settings.classifier_min_prob

    def warn(self, message: str) -> None:
        self.warnings.setdefault(message, None)

    def local(self):  # noqa: ANN201
        if not self._local_checked:
            self._local_checked = True
            try:
                self._local = get_classifier("local", self.ctx)
            except ClassifierUnavailable:
                self._local = None
                self.warn("classifier_unavailable: local")
        return self._local

    def hosted(self):  # noqa: ANN201
        if not self._hosted_checked:
            self._hosted_checked = True
            try:
                self._hosted = get_classifier("llm", self.ctx)
            except ClassifierUnavailable:
                self._hosted = None
                self.warn("classifier_unavailable: llm")
        return self._hosted

    def _is_escalation_item(self, unit: _Unit, question: str) -> bool:
        label, p = top(unit.rules.distribution)
        return p < self.min_prob or label in NONSPECIFIC[question]

    def _failure(self, exc: Exception) -> None:
        self.escalation_unavailable += 1
        if isinstance(exc, LLMBudgetExceeded):
            self.budget_hits += 1
            self.first_budget = self.first_budget or exc
            self.warn("classify: escalation_unavailable: budget")
        elif isinstance(exc, LLMNoFixture | ClassifierUnavailable):
            self.warn("classify: escalation_unavailable: no_fixture")
        elif isinstance(exc, LLMSchemaError):
            self.hard_failures += 1
            self.warn("classify: escalation_unavailable: schema")
        elif isinstance(exc, LLMTransportError):
            self.hard_failures += 1
            self.warn("classify: escalation_unavailable: transport")
        else:
            self.hard_failures += 1
            self.warn("classify: escalation_unavailable: error")

    # -- local ---------------------------------------------------------------

    def _run_local(self, question: str, labels: Sequence[str], units: list[_Unit]):  # noqa: ANN202
        local = self.local()
        out: dict[int, Candidate] = {}
        if local is None:
            return out
        name = getattr(local, "display_name", local.name)
        for index, unit in enumerate(units):
            use = list(labels)
            if question == "C1":
                use = _pruned_c1_labels(unit.rules.distribution)
            started = time.perf_counter()
            try:
                with self.ctx.costs.scope(page=unit.page, document_id=unit.document_id):
                    dist = local.classify(
                        unit.text, use, question=question, context=unit.context or None
                    )
                    self.ctx.costs.record(
                        purpose=f"classify:{question}",
                        model=str(name),
                        prompt_tokens=0,
                        completion_tokens=0,
                        seconds=time.perf_counter() - started,
                        usd=Decimal("0"),
                    )
            except Exception as exc:  # noqa: BLE001 - local failures keep the rules result
                self._failure(exc)
                continue
            out[index] = Candidate(
                classifier=str(name), version=str(local.version), distribution=dist
            )
        return out

    # -- hosted --------------------------------------------------------------

    def _hosted_one(self, hosted, question: str, labels: Sequence[str], unit: _Unit):  # noqa: ANN001, ANN202
        try:
            with self.ctx.costs.scope(page=unit.page, document_id=unit.document_id):
                with_rationale = getattr(hosted, "classify_with_rationale", None)
                if with_rationale is not None:
                    return with_rationale(
                        unit.text, labels, question=question, context=unit.context or None
                    )
                return (
                    hosted.classify(
                        unit.text, labels, question=question, context=unit.context or None
                    ),
                    None,
                )
        except Exception as exc:  # noqa: BLE001 - converted to a kept rules result
            return exc

    def _run_hosted(
        self, question: str, labels: Sequence[str], units: list[_Unit], wanted: list[int]
    ) -> dict[int, Candidate]:
        hosted = self.hosted()
        out: dict[int, Candidate] = {}
        if hosted is None or not wanted:
            for _ in wanted:
                self.escalation_unavailable += 1
            return out
        workers = max(1, self.ctx.settings.max_page_workers)
        remaining = self.ctx.settings.max_llm_calls_per_case - self.ctx.costs.calls_used
        if len(wanted) > remaining:
            workers = 1  # a budget trip must hit the same items on every run
        if workers == 1 or len(wanted) == 1:
            results = [self._hosted_one(hosted, question, labels, units[i]) for i in wanted]
        else:
            fn = self.ctx.costs.wrap(self._hosted_one)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(fn, hosted, question, labels, units[i]) for i in wanted]
                results = [f.result() for f in futures]
        for index, result in zip(wanted, results, strict=True):
            if isinstance(result, Exception):
                self._failure(result)
            else:
                dist, rationale = result
                out[index] = Candidate(
                    classifier=hosted.display_name,
                    version=str(hosted.version),
                    distribution=dist,
                    rationale=rationale,
                )
        return out

    # -- the mode logic --------------------------------------------------------

    def classify_units(self, question: str, labels: Sequence[str], units: list[_Unit]) -> None:
        """Fill ``unit.decision`` for every unit according to ``GLDB_CLASSIFIER_MODE``."""
        mode = self.ctx.settings.classifier_mode
        eligible = [i for i, u in enumerate(units) if self._is_escalation_item(u, question)]
        candidates: dict[int, list[Candidate]] = {i: [u.rules] for i, u in enumerate(units)}

        if mode == "rules":
            self.escalation_unavailable += len(eligible)
        elif mode == "local":
            targets = list(range(len(units)))
            got = self._run_local(question, labels, [units[i] for i in targets])
            if self._local is None:
                self.escalation_unavailable += len(eligible)
            for k, cand in got.items():
                candidates[targets[k]].append(cand)
        elif mode == "llm":
            wanted = list(range(len(units)))
            for i, cand in self._run_hosted(question, labels, units, wanted).items():
                candidates[i].append(cand)
        else:  # cascade
            local_targets = list(eligible)
            got = self._run_local(question, labels, [units[i] for i in local_targets])
            for k, cand in got.items():
                candidates[local_targets[k]].append(cand)
            wanted = []
            for i in eligible:
                interim = decide(question, candidates[i], min_prob=self.min_prob)
                if (
                    interim.low_confidence
                    or interim.label in NONSPECIFIC[question]
                    or interim.disagreement
                ):
                    wanted.append(i)
            for i, cand in self._run_hosted(question, labels, units, wanted).items():
                candidates[i].append(cand)

        for i, unit in enumerate(units):
            unit.decision = decide(question, candidates[i], min_prob=self.min_prob)
            if unit.decision.disagreement:
                self.disagreements += 1


def _pruned_c1_labels(rules_dist: dict[str, float]) -> list[str]:
    """Local model label set: the rules' top 3, ``other`` and anything the rules scored >= 0.35."""
    ranked = sorted(rules_dist, key=lambda k: (-rules_dist[k], list(rules_dist).index(k)))
    keep = set(ranked[:3]) | {"other"} | {k for k, v in rules_dist.items() if v >= LOCAL_MIN_SCORE}
    return [label for label in C1_LABELS if label in keep]


# ----------------------------------------------------------------------------- stage


def _classification(decision: Decision, reasons: Sequence[str] = ()) -> Classification:
    """``reasons`` are rules cues, kept only when the rules classifier made the decision; the
    rationale is a hosted model's own sentence, kept only when that model's label won. Both are
    explanation: nothing downstream reads them."""
    from_rules = decision.classifier == RulesClassifier.name
    return Classification(
        label=decision.label,
        probability=decision.probability,
        classifier=decision.classifier,
        version=decision.version,
        distribution=decision.distribution,
        reasons=list(reasons) if from_rules else [],
        rationale=decision.rationale,
    )


def _page_has_ink(ctx: CaseContext, file: FileRef, page: int) -> bool:
    """True unless the rendered page is really blank. A page without a text layer that is not a
    scan can still carry content: text converted to vector outlines, or a drawing. Those must reach
    the escalation path (plan 5.3), never the free ``boilerplate`` label. A missing original (no
    file to look at) counts as blank; any other failure to render counts as content."""
    if file.mime != "application/pdf":
        return True  # an image is a page of pixels: never "no content"
    try:
        import pymupdf

        path = ctx.file_path(file.id)
        if not path.exists():
            return False
        doc = pymupdf.open(path)
        try:
            pix = doc.load_page(page - 1).get_pixmap(
                dpi=BLANK_DPI, colorspace=pymupdf.csGRAY, alpha=False
            )
        finally:
            doc.close()
        return min(pix.samples, default=255) < BLANK_PIXEL_MAX
    except Exception as exc:  # noqa: BLE001 - when unsure, escalate rather than call it blank
        log.warning("blank-page check failed: %s", type(exc).__name__)
        return True


def _page_units(
    run: _Run, file: FileRef
) -> tuple[list[_Unit], list[PageInfo], dict[int, str], dict[int, Classification], list[int]]:
    """Phase 1 for one file: rules (with the continuation rule) for every readable page."""
    ctx = run.ctx
    rules = RulesClassifier()
    units: list[_Unit] = []
    unit_pages: list[PageInfo] = []
    texts: dict[int, str] = {}
    blank: dict[int, Classification] = {}
    injected: list[int] = []
    previous: tuple[str, float] | None = None
    applicant = ctx.case.applicant.name
    context = {"applicant_name": applicant} if applicant else {}
    for page in sorted(file.pages, key=lambda p: p.page):
        if page.error:
            previous = None
            continue
        text = ctx.page_text(file.id, page.page)
        texts[page.page] = text
        if page.word_count == 0 and not page.is_scan and not _page_has_ink(ctx, file, page.page):
            blank[page.page] = Classification(
                label="boilerplate",
                probability=BLANK_P,
                classifier=rules.name,
                version=rules.version,
                distribution={"boilerplate": BLANK_P, "other": round(1 - BLANK_P, 4)},
                reasons=[BLANK_PAGE_REASON],
            )
            previous = ("boilerplate", BLANK_P)
            continue
        dist = rules.classify(text, C1_LABELS, question="C1")
        inherited = continuation(text, dist, previous, min_prob=run.min_prob)
        if inherited is not None:
            dist = inherited
        label, p = top(dist)
        previous = (label, p)
        reasons = (
            [CONTINUATION_REASON]
            if inherited is not None
            else rules.explain(text, label, question="C1")
        )
        units.append(
            _Unit(
                text=text[: MODEL_TEXT_CHARS["C1"]],
                context=dict(context),
                rules=Candidate(classifier=rules.name, version=rules.version, distribution=dist),
                page=page.page,
                reasons=reasons,
            )
        )
        unit_pages.append(page)
        if injection_suspected(text):
            injected.append(page.page)
    return units, unit_pages, texts, blank, injected


def _decider(pages: Sequence[PageInfo]) -> tuple[str, str]:
    """Classifier name/version of a segment: the common one, else the lowest-confidence page's."""
    names = {
        (p.classification.classifier, p.classification.version) for p in pages if p.classification
    }
    if len(names) == 1:
        return next(iter(names))
    lowest = min(
        (p for p in pages if p.classification),
        key=lambda p: p.classification.probability,  # type: ignore[union-attr]
    )
    assert lowest.classification is not None
    return lowest.classification.classifier, lowest.classification.version


def run_stage(ctx: CaseContext) -> StageOutcome:
    with ctx.costs.scope(stage="classify"):
        return _execute(ctx)


def _execute(ctx: CaseContext) -> StageOutcome:
    run = _Run(ctx)
    rules = RulesClassifier()
    files = sorted(ctx.case.files, key=lambda f: natural_key(f.id))
    ctx.case.documents = []
    for f in files:
        for page in f.pages:
            page.classification = None

    # Phase 1 and 2: C1 for every page of every file --------------------------
    per_file: dict[str, dict] = {}
    all_units: list[_Unit] = []
    owners: list[tuple[str, PageInfo]] = []
    for f in files:
        units, unit_pages, texts, blank, injected = _page_units(run, f)
        per_file[f.id] = {"texts": texts, "blank": blank, "injected": injected, "notes": {}}
        all_units += units
        owners += [(f.id, p) for p in unit_pages]
    run.classify_units("C1", C1_LABELS, all_units)

    low_confidence = other_pages = boilerplate_pages = 0
    for (file_id, page), unit in zip(owners, all_units, strict=True):
        decision = unit.decision
        assert decision is not None
        page.classification = _classification(decision, unit.reasons)
        if decision.low_confidence:
            low_confidence += 1
        if decision.label == "other":
            other_pages += 1
        elif decision.label == "boilerplate":
            boilerplate_pages += 1
        if decision.disagreement:
            per_file[file_id]["notes"].setdefault(page.page, []).append(
                f"classify: classifier_disagreement page {page.page}: {decision.disagreement}"
            )
    for f in files:
        info = per_file[f.id]
        for page in f.pages:
            if page.page in info["blank"]:
                page.classification = info["blank"][page.page]
                boilerplate_pages += 1
                info["notes"].setdefault(page.page, []).append(f"classify: blank page {page.page}")
        for number in info["injected"]:
            info["notes"].setdefault(number, []).append(
                f"classify: injection_suspected: page {number}"
            )

    # Phase 3: segmentation into documents -----------------------------------
    documents: list[Document] = []
    injected_count = sum(len(per_file[f.id]["injected"]) for f in files)
    counter = 0
    for f in files:
        info = per_file[f.id]
        for seg in segment_pages(f, f.pages, info["texts"]):
            counter += 1
            pages = [p for p in f.pages if seg.page_start <= p.page <= seg.page_end]
            words: list[Word] = []
            for p in sorted(pages, key=lambda p: p.page):
                words += ctx.page_words.get((f.id, p.page), [])
            warnings: list[str] = []
            for p in pages:
                warnings += info["notes"].get(p.page, [])
            if all(p.error for p in pages):
                reason = next(p.error for p in pages if p.error)
                documents.append(
                    Document(
                        id=f"d{counter}",
                        file_id=f.id,
                        page_start=seg.page_start,
                        page_end=seg.page_end,
                        doc_type="other",
                        classification=Classification(
                            label="other",
                            probability=0.0,
                            classifier=rules.name,
                            version=rules.version,
                        ),
                        status="failed",
                        error=reason,
                        words=[],
                        warnings=warnings,
                    )
                )
                continue
            classifier, version = _decider(pages)
            same = [
                p.classification
                for p in sorted(pages, key=lambda p: p.page)
                if p.classification
                and p.classification.classifier == classifier
                and p.classification.label == seg.label
            ]
            reasons = list(dict.fromkeys(r for c in same for r in c.reasons))
            rationale = next((c.rationale for c in same if c.rationale), None)
            documents.append(
                Document(
                    id=f"d{counter}",
                    file_id=f.id,
                    page_start=seg.page_start,
                    page_end=seg.page_end,
                    doc_type=seg.label,  # type: ignore[arg-type]
                    classification=Classification(
                        label=seg.label,
                        probability=seg.probability,
                        classifier=classifier,
                        version=version,
                        distribution=seg.distribution,
                        reasons=reasons,
                        rationale=rationale,
                    ),
                    status="received_not_checked" if seg.label in NONSPECIFIC["C1"] else "ok",
                    words=words,
                    warnings=warnings,
                )
            )
    ctx.case.documents = documents

    # C3 issuer for bank statements ------------------------------------------
    statements = [d for d in documents if d.doc_type == "bank_statement" and d.status == "ok"]
    c3_units: list[_Unit] = []
    for d in statements:
        text = ctx.page_text(d.file_id, d.page_start)[: MODEL_TEXT_CHARS["C3"]]
        meta = ctx.file(d.file_id).pdf_meta
        context = {"producer": meta.get("Producer", ""), "creator": meta.get("Creator", "")}
        dist = rules.classify(text, C3_LABELS, question="C3", context=context)
        c3_units.append(
            _Unit(
                text=text,
                context=context,
                rules=Candidate(classifier=rules.name, version=rules.version, distribution=dist),
                page=d.page_start,
                document_id=d.id,
                reasons=rules.explain(text, top(dist)[0], question="C3", context=context),
            )
        )
    run.classify_units("C3", C3_LABELS, c3_units)
    for d, unit in zip(statements, c3_units, strict=True):
        assert unit.decision is not None
        d.issuer = _classification(unit.decision, unit.reasons)
        if unit.decision.disagreement:
            d.warnings.append(f"classify: classifier_disagreement C3: {unit.decision.disagreement}")

    counters = {
        "low_confidence": low_confidence,
        "other_pages": other_pages,
        "boilerplate_pages": boilerplate_pages,
        "injected_count": injected_count,
    }
    return _outcome(run, files, documents, counters)


def _outcome(
    run: _Run, files: list[FileRef], documents: list[Document], counters: dict[str, int]
) -> StageOutcome:
    pages = [p for f in files for p in f.pages]

    def versions(values: set[str]) -> str:
        return "+".join(sorted(values)) if values else "none"

    c1 = {
        f"{p.classification.classifier}@{p.classification.version}"
        for p in pages
        if p.classification
    }
    c3 = {f"{d.issuer.classifier}@{d.issuer.version}" for d in documents if d.issuer}
    hosted = run._hosted
    detail: dict[str, int | float | str] = {
        "pages": len(pages),
        "documents": len(documents),
        "low_confidence_pages": counters["low_confidence"],
        "other_pages": counters["other_pages"],
        "boilerplate_pages": counters["boilerplate_pages"],
        "hosted_calls": int(getattr(hosted, "calls", 0)) if hosted is not None else 0,
        "disagreements": run.disagreements,
        "injection_suspected": counters["injected_count"],
        "escalation_unavailable": run.escalation_unavailable,
        "classifier_C1": versions(c1),
        "classifier_C3": versions(c3),
    }
    reasons: list[str] = []
    if run.first_budget is not None:
        reasons.append(f"{run.first_budget}; {run.budget_hits} items kept the rules result")
    if run.hard_failures:
        reasons.append(
            f"classify: hosted classifier failed for {run.hard_failures} items; rules result kept"
        )
    partial = bool(reasons)
    return StageOutcome(
        detail=detail,
        warnings=list(run.warnings),
        partial=partial,
        partial_reason=" | ".join(reasons) if reasons else None,
    )
