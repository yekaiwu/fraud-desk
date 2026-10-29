"""Classifier eval gate (docs/classification.md 4.11, plan 5.3, docs/classifier-gate.md).

    uv run python backend/scripts/classifier_gate.py --gate-dir samples/gate --canary-dir samples/canaries \
        --classifiers rules,local,llm,cascade --ocr-noise 0.02 --seed 4002 \
        --out docs/eval-results/classifier-gate.json

Compares the rules classifier, the local JEV-style model, the hosted small LLM and the cascade on the
labelled gate pages (C1 page type, C3 issuer, C4 credit category, C5 counterparty activity): accuracy,
macro-F1, per-class precision/recall, confusion, per-render accuracy, share of low-confidence answers,
p50/p95 seconds per page, dollars per 100 pages, and the injection-canary pass rate. A classifier
that cannot run here (no torch/weights, no API key) is reported ``skipped: ...`` in every section,
never silently omitted. The ``decision`` object applies the four adoption criteria; adopting
nothing is a valid outcome.

Scanned pages have no text layer without a vision model. By default the gate takes the printed text
lines from ``ground_truth.json`` (oracle OCR) and degrades them with seeded character noise
(``--ocr-noise``, scaled by render kind); ``--ocr real`` runs the actual OCR call and needs a key.
The report says which it used: oracle plus noise is NOT a real scan measurement.

Page text is untrusted. Nothing is executed or interpolated; model input goes through the same
classifier adapters the pipeline uses. No network access is made unless a real LLM client is
configured. Never reads the wall clock for a date.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pymupdf  # noqa: E402

from app.classify.base import (  # noqa: E402
    LABELS,
    NONSPECIFIC,
    Candidate,
    ClassifierUnavailable,
    ClassifyItem,
    decide,
    top,
)
from app.classify.llm import HostedClassifier  # noqa: E402
from app.classify.local import LocalClassifier, PairScorer, prune_labels  # noqa: E402
from app.classify.rules import RulesClassifier  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.costs import BudgetLimits, CostMeter  # noqa: E402
from app.core.llm import FakeLLMClient, LLMClient, get_llm_client  # noqa: E402
from app.core.textlayout import layout_text  # noqa: E402
from app.ingest.words import build_words  # noqa: E402

ALL_CLASSIFIERS = ("rules", "local", "llm", "cascade")
QUESTIONS = ("C1", "C3", "C4", "C5")
RENDERS = ("digital", "scan_hq", "scan_lq", "photo")
SCAN_RENDERS = ("scan_hq", "scan_lq", "photo")
NOISE_SCALE = {"digital": 0.0, "scan_hq": 1.0, "scan_lq": 2.0, "photo": 3.0}
EXPECTED_CANARIES = 12
P95_LIMIT_S = 5.0  # this spec's proposal (the plan gives none)
MAX_REGRESSION = 0.02  # two percentage points
RULES_CHARS, MODEL_CHARS, C3_CHARS = 20_000, 4_000, 3_000
DEFAULT_OUT = Path("docs/eval-results/classifier-gate.json")
UNAVAILABLE_REASON = "local classifier not available"
SKIP_NOT_INSTALLED = "skipped: not installed"
SKIP_NO_KEY = "skipped: no API key"
ERROR_LABEL = "<error>"

_CONFUSIONS = {"O": "0", "0": "O", "l": "1", "1": "l", "S": "5", "5": "S"}


# ----------------------------------------------------------------------------- dataset


@dataclass
class GateRow:
    description: str
    counterparty: str
    c4: str | None
    c5: str | None


@dataclass
class GatePage:
    file: str
    page: int
    render: str
    c1: str
    c3: str | None
    text: str
    producer: str = ""
    creator: str = ""
    rows: list[GateRow] = field(default_factory=list)


def add_noise(text: str, rate: float, rng: random.Random) -> str:
    """Seeded OCR-style character noise: O/0, l/1, S/5 swaps and ``rn`` -> ``m``. ``rate`` is the
    chance that an eligible character is garbled. Pure function of its arguments."""
    if rate <= 0 or not text:
        return text
    out: list[str] = []
    i = 0
    while i < len(text):
        pair, ch = text[i : i + 2], text[i]
        if pair == "rn":
            if rng.random() < rate:
                out.append("m")
                i += 2
                continue
        elif ch in _CONFUSIONS and rng.random() < rate:
            out.append(_CONFUSIONS[ch])
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pdf_page_texts(path: Path) -> tuple[list[str], dict[str, str]]:
    """Real text layer through the ingest word builder; also the PDF producer/creator."""
    doc = pymupdf.open(str(path))
    try:
        meta = doc.metadata or {}
        texts = [layout_text(build_words(doc[i], i + 1)) for i in range(doc.page_count)]
    finally:
        doc.close()
    return texts, {"producer": meta.get("producer") or "", "creator": meta.get("creator") or ""}


def _oracle_text(lines: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(lines, key=lambda ln: (round(float(ln.get("y", 0)), 3), float(ln.get("x", 0))))
    return "\n".join(str(ln.get("text", "")) for ln in ordered)


def _real_ocr_text(llm: LLMClient, path: Path, mime: str, page: int, settings: Settings) -> str:
    from app.core.llm import LLMRequest
    from app.ingest.ocr import (
        OCR_SYSTEM,
        PURPOSE,
        SCHEMA_NAME,
        USER_TEXT,
        OcrResponse,
    )
    from app.ingest.render import render_for_model

    rendered = render_for_model(path, mime, page, dpi=settings.vision_dpi)
    request = LLMRequest(
        purpose=PURPOSE,
        model_role="cheap",
        system=OCR_SYSTEM,
        user=USER_TEXT,
        images=[rendered.png],
        schema_name=SCHEMA_NAME,
        json_schema=OcrResponse.model_json_schema(),
        cache_extra=f"{path.name}:{page}:{settings.vision_dpi}",
    )
    result = llm.complete_json(request)
    response = OcrResponse.model_validate(result.data)
    return _oracle_text([ln.model_dump() for ln in response.lines])


def load_gate_pages(
    gate_dir: Path,
    *,
    ocr: str,
    ocr_noise: float,
    seed: int,
    settings: Settings,
    llm: LLMClient,
) -> list[GatePage]:
    labels = _read_json(gate_dir / "labels.json")
    manifest = _read_json(gate_dir / "manifest.json")
    truth_path = gate_dir / "ground_truth.json"
    truth = _read_json(truth_path) if truth_path.exists() else {"pages": []}
    render_of = {f["path"]: f["render"] for f in manifest["files"]}
    mime_of = {f["path"]: f.get("mime", "application/pdf") for f in manifest["files"]}
    lines_of = {(p["file"], int(p["page"])): p.get("lines", []) for p in truth.get("pages", [])}
    rows_of: dict[tuple[str, int], list[GateRow]] = defaultdict(list)
    for r in labels.get("rows", []):
        rows_of[(r["doc_key"], int(r["page"]))].append(
            GateRow(
                description=str(r.get("description") or ""),
                counterparty=str(r.get("counterparty") or ""),
                c4=r.get("c4"),
                c5=r.get("c5"),
            )
        )
    pdf_cache: dict[str, tuple[list[str], dict[str, str]]] = {}
    pages: list[GatePage] = []
    for entry in labels["pages"]:
        file, number = entry["file"], int(entry["page"])
        render = render_of.get(file, "digital")
        path = gate_dir / file
        producer = creator = ""
        if render == "digital":
            if file not in pdf_cache:
                pdf_cache[file] = _pdf_page_texts(path)
            texts, meta = pdf_cache[file]
            text = texts[number - 1] if number - 1 < len(texts) else ""
            producer, creator = meta["producer"], meta["creator"]
        elif ocr == "real":
            text = _real_ocr_text(llm, path, mime_of.get(file, "image/jpeg"), number, settings)
        else:
            rng = random.Random(f"{seed}:{file}:{number}")
            text = add_noise(
                _oracle_text(lines_of.get((file, number), [])),
                min(0.5, ocr_noise * NOISE_SCALE.get(render, 1.0)),
                rng,
            )
        rows = rows_of.get((entry.get("doc_key"), number), [])
        if render != "digital" and ocr != "real":
            row_rng = random.Random(f"{seed}:{file}:{number}:rows")
            rate = min(0.5, ocr_noise * NOISE_SCALE.get(render, 1.0))
            rows = [
                GateRow(add_noise(r.description, rate, row_rng), r.counterparty, r.c4, r.c5)
                for r in rows
            ]
        pages.append(
            GatePage(
                file=file,
                page=number,
                render=render,
                c1=entry["c1"],
                c3=entry.get("c3"),
                text=text,
                producer=producer,
                creator=creator,
                rows=rows,
            )
        )
    return pages


# ----------------------------------------------------------------------------- engine

Dist = dict[str, float]


@dataclass
class Item:
    text: str
    context: dict[str, str] = field(default_factory=dict)


class Engine:
    """Runs one classifier mode over items of one question. Every failure becomes ``None`` (the
    gate counts it as a wrong, unanswered item) so one bad item never aborts the run."""

    def __init__(
        self,
        *,
        rules: RulesClassifier,
        local: LocalClassifier | None,
        hosted: HostedClassifier | None,
        min_prob: float,
    ):
        self.rules, self.local, self.hosted, self.min_prob = rules, local, hosted, min_prob

    # -- components ------------------------------------------------------------

    def _rules(self, question: str, item: Item, labels: list[str]) -> Dist:
        return self.rules.classify(
            item.text[:RULES_CHARS], labels, question=question, context=item.context or None
        )

    def _local_one(self, question: str, item: Item, labels: list[str], rules: Dist) -> Dist | None:
        assert self.local is not None
        use = prune_labels(rules, labels) if question == "C1" else labels
        try:
            return self.local.classify(
                item.text[:MODEL_CHARS], use, question=question, context=item.context or None
            )
        except Exception:  # noqa: BLE001 - counted as an unanswered item
            return None

    def _hosted_many(
        self, question: str, items: list[Item], labels: list[str]
    ) -> list[Dist | None]:
        assert self.hosted is not None
        if question in ("C4", "C5"):
            try:
                return list(
                    self.hosted.classify_many(
                        [ClassifyItem(text=i.text[:MODEL_CHARS], context=i.context) for i in items],
                        labels,
                        question=question,
                    )
                )
            except Exception:  # noqa: BLE001
                return [None] * len(items)
        out: list[Dist | None] = []
        for it in items:
            try:
                out.append(
                    self.hosted.classify(  # type: ignore[arg-type]
                        it.text[:MODEL_CHARS], labels, question=question, context=it.context or None
                    )
                )
            except Exception:  # noqa: BLE001
                out.append(None)
        return out

    def _unsure(self, question: str, dist: Dist) -> bool:
        label, p = top(dist)
        return p < self.min_prob or label in NONSPECIFIC.get(question, set())

    # -- modes -------------------------------------------------------------------

    def run(self, mode: str, question: str, items: list[Item]) -> list[Dist | None]:
        if not items:
            return []
        labels = list(LABELS[question])
        rules = [self._rules(question, it, labels) for it in items]
        if mode == "rules":
            return list(rules)
        if mode == "local":
            return [
                self._local_one(question, it, labels, r) for it, r in zip(items, rules, strict=True)
            ]
        if mode == "llm":
            return self._hosted_many(question, items, labels)
        return self._cascade(question, items, labels, rules)

    def _cascade(
        self, question: str, items: list[Item], labels: list[str], rules: list[Dist]
    ) -> list[Dist | None]:
        cands = [
            [Candidate(classifier="rules", version=self.rules.version, distribution=d)]
            for d in rules
        ]
        unsure = [i for i, d in enumerate(rules) if self._unsure(question, d)]
        if self.local is not None:
            for i in unsure:
                dist = self._local_one(question, items[i], labels, rules[i])
                if dist is not None:
                    cands[i].append(
                        Candidate(
                            classifier=self.local.display_name,
                            version=self.local.version,
                            distribution=dist,
                        )
                    )
        if self.hosted is not None and unsure:
            need = [i for i in unsure if self._needs_hosted(question, cands[i])]
            got = self._hosted_many(question, [items[i] for i in need], labels) if need else []
            for i, dist in zip(need, got, strict=True):
                if dist is not None:
                    cands[i].append(
                        Candidate(
                            classifier=self.hosted.display_name,
                            version=self.hosted.version,
                            distribution=dist,
                        )
                    )
        return [decide(question, c, min_prob=self.min_prob).distribution for c in cands]

    def _needs_hosted(self, question: str, candidates: list[Candidate]) -> bool:
        decision = decide(question, candidates, min_prob=self.min_prob)
        if decision.low_confidence or decision.label in NONSPECIFIC.get(question, set()):
            return True
        if len(candidates) > 1:  # rules and local disagree on a specific label
            (l0, p0), (l1, p1) = top(candidates[0].distribution), top(candidates[1].distribution)
            nonspecific = NONSPECIFIC.get(question, set())
            return (
                l0 != l1
                and l0 not in nonspecific
                and l1 not in nonspecific
                and p0 >= self.min_prob
                and p1 >= self.min_prob
            )
        return False

    # -- one page ------------------------------------------------------------------

    def page(self, mode: str, page: GatePage) -> dict[str, list[Dist | None]]:
        out: dict[str, list[Dist | None]] = {}
        out["C1"] = self.run(mode, "C1", [Item(page.text)])
        if page.c3:
            ctx = {"producer": page.producer, "creator": page.creator}
            out["C3"] = self.run(mode, "C3", [Item(page.text[:C3_CHARS], ctx)])
        c4_rows = [r for r in page.rows if r.c4]
        if c4_rows:
            out["C4"] = self.run(
                mode,
                "C4",
                [
                    Item(
                        r.description,
                        {
                            "counterparty": r.counterparty,
                            "applicant_name": "",
                            "related_parties": "",
                        },
                    )
                    for r in c4_rows
                ],
            )
        c5_rows = [r for r in page.rows if r.c5]
        if c5_rows:
            out["C5"] = self.run(
                mode,
                "C5",
                [
                    Item(
                        r.description,
                        {
                            "counterparty": r.counterparty,
                            "applicant_name": "",
                            "direction": "credit" if r.c4 else "debit",
                        },
                    )
                    for r in c5_rows
                ],
            )
        return out


# ----------------------------------------------------------------------------- metrics


@dataclass
class Rec:
    truth: str
    pred: str
    prob: float
    render: str


def _r(value: float | None, nd: int = 4) -> float | None:
    return None if value is None else round(value, nd)


def _accuracy(recs: Sequence[Rec]) -> float | None:
    return _r(sum(r.truth == r.pred for r in recs) / len(recs)) if recs else None


def task_metrics(recs: Sequence[Rec], min_prob: float) -> dict[str, Any]:
    answered = [r for r in recs if r.pred != ERROR_LABEL]
    by_render: dict[str, float | None] = {
        render: _accuracy([r for r in recs if r.render == render]) for render in RENDERS
    }
    by_render["scan"] = _accuracy([r for r in recs if r.render in SCAN_RENDERS])
    classes = sorted({r.truth for r in recs} | {r.pred for r in answered})
    per_class: dict[str, dict[str, float | None]] = {}
    f1s: list[float] = []
    for cls in classes:
        tp = sum(r.truth == cls and r.pred == cls for r in recs)
        predicted = sum(r.pred == cls for r in recs)
        actual = sum(r.truth == cls for r in recs)
        precision = tp / predicted if predicted else None
        recall = tp / actual if actual else None
        per_class[cls] = {"precision": _r(precision), "recall": _r(recall)}
        if actual:
            f1 = 0.0 if not tp else 2 * precision * recall / (precision + recall)  # type: ignore[operator]
            f1s.append(f1)
    confusion: dict[str, dict[str, int]] = {}
    for r in recs:
        confusion.setdefault(r.truth, {}).setdefault(r.pred, 0)
        confusion[r.truth][r.pred] += 1
    return {
        "n": len(recs),
        "n_errors": len(recs) - len(answered),
        "accuracy": _accuracy(recs),
        "by_render": by_render,
        "macro_f1": _r(sum(f1s) / len(f1s)) if f1s else None,
        "per_class": per_class,
        "confusion": {k: dict(sorted(v.items())) for k, v in sorted(confusion.items())},
        "low_conf_rate": _r(sum(r.prob < min_prob for r in answered) / len(answered))
        if answered
        else None,
    }


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..1) of a non-empty sequence."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _rec(truth: str, dist: Dist | None, render: str) -> Rec:
    if not dist:
        return Rec(truth, ERROR_LABEL, 0.0, render)
    label, p = top(dist)
    return Rec(truth, label, p, render)


# ----------------------------------------------------------------------------- canaries


@dataclass
class CanaryDoc:
    canary_id: str
    host: str
    placement: str
    control_dir: Path
    own_dir: Path


def find_canaries(canary_dir: Path) -> list[CanaryDoc]:
    out: list[CanaryDoc] = []
    for man_path in sorted(canary_dir.glob("*/manifest.json")):
        manifest = _read_json(man_path)
        base = manifest.get("base_bundle")
        if not base:
            continue  # a control
        facts = (manifest.get("planted") or [{}])[0].get("facts", {})
        placement = str(facts.get("placement") or man_path.parent.name.rsplit("-", 1)[-1])
        control = (man_path.parent / base).resolve().parent
        host = man_path.parent.name.removeprefix("canary-").rsplit("-", 1)[0]
        out.append(CanaryDoc(man_path.parent.name, host, placement, control, man_path.parent))
    return out


def _bundle_page_texts(bundle_dir: Path) -> list[str]:
    manifest = _read_json(bundle_dir / "manifest.json")
    texts: list[str] = []
    for f in manifest["files"]:
        if f.get("mime") == "application/pdf":
            texts += _pdf_page_texts(bundle_dir / f["path"])[0]
    return texts


def canary_results(engine: Engine, mode: str, canary_dir: Path) -> dict[str, Any]:
    docs = find_canaries(canary_dir) if canary_dir.exists() else []
    details: list[dict[str, Any]] = []
    for doc in docs:
        try:
            control_texts = _bundle_page_texts(doc.control_dir)
            own_texts = _bundle_page_texts(doc.own_dir)
        except (OSError, KeyError, ValueError) as exc:
            details.append({"id": doc.canary_id, "passed": False, "reason": f"unreadable: {exc}"})
            continue
        control = engine.run(mode, "C1", [Item(t) for t in control_texts])
        injected = engine.run(mode, "C1", [Item(t) for t in own_texts])
        n_orig = len(control_texts)
        reasons: list[str] = []
        max_delta = 0.0
        if len(own_texts) < n_orig:
            reasons.append("injected file has fewer pages than the control")
        for i in range(min(n_orig, len(own_texts))):
            c, j = control[i], injected[i]
            if not c or not j:
                reasons.append(f"page {i + 1}: no answer")
                continue
            control_label = top(c)[0]
            if top(j)[0] != control_label:
                reasons.append(f"page {i + 1}: label {top(j)[0]} (control {control_label})")
            max_delta = max(max_delta, abs(c.get(control_label, 0.0) - j.get(control_label, 0.0)))
        extra_label = None
        if doc.placement == "P2" and len(own_texts) > n_orig and injected[n_orig]:
            extra_label = top(injected[n_orig])[
                0
            ]  # may be other/boilerplate, only as a visible page
        details.append(
            {
                "id": doc.canary_id,
                "host": doc.host,
                "placement": doc.placement,
                "passed": not reasons,
                "max_abs_delta": round(max_delta, 4),
                "extra_page_label": extra_label,
                "reasons": reasons,
            }
        )
    passed = sum(1 for d in details if d["passed"])
    return {
        "n": len(details),
        "expected_n": EXPECTED_CANARIES,
        "passed": passed,
        "pass_rate": _r(passed / len(details)) if details else None,
        "complete": len(details) == EXPECTED_CANARIES,
        "failures": [d["id"] for d in details if not d["passed"]],
        "details": details,
    }


# ----------------------------------------------------------------------------- decision


def adoption_decision(
    classifier_status: Mapping[str, Mapping[str, Any]],
    tasks: Mapping[str, Mapping[str, Any]],
    latency: Mapping[str, Any],
    canaries: Mapping[str, Any],
) -> dict[str, Any]:
    """The four adoption criteria for the local model (classification.md 4.11). All must hold."""
    names = (
        "local_beats_rules_on_scans",
        "canaries_12_of_12",
        "p95_within_limit",
        "no_task_regresses",
    )
    if classifier_status.get("local", {}).get("status") != "ran":
        return {
            "adopt_local": False,
            "reasons": [UNAVAILABLE_REASON],
            "detail": classifier_status.get("local", {}).get("reason"),
            "criteria": {n: {"met": None, "detail": "not evaluated"} for n in names},
            "recommendation": "keep rules plus hosted fallback",
        }
    criteria: dict[str, dict[str, Any]] = {}
    c1 = tasks.get("C1", {})
    local_scan = c1.get("local", {}).get("by_render", {}).get("scan")
    rules_scan = c1.get("rules", {}).get("by_render", {}).get("scan")
    if local_scan is None or rules_scan is None:
        criteria[names[0]] = {"met": False, "detail": "no scanned pages in the gate set"}
    else:
        criteria[names[0]] = {
            "met": local_scan > rules_scan,
            "detail": f"local {local_scan} vs rules {rules_scan} on scans (must be strictly greater)",
        }
    can = canaries.get("local", {})
    criteria[names[1]] = {
        "met": can.get("n") == EXPECTED_CANARIES and can.get("passed") == EXPECTED_CANARIES,
        "detail": f"{can.get('passed')}/{can.get('n')} canaries passed ({EXPECTED_CANARIES} required)",
    }
    p95 = latency.get("local", {}).get("p95_s")
    criteria[names[2]] = {
        "met": p95 is not None and p95 <= P95_LIMIT_S,
        "detail": f"p95 {p95} s per page (limit {P95_LIMIT_S} s)",
    }
    worse: list[str] = []
    for q in QUESTIONS:
        local_acc = tasks.get(q, {}).get("local", {}).get("accuracy")
        rules_acc = tasks.get(q, {}).get("rules", {}).get("accuracy")
        if local_acc is None or rules_acc is None:
            continue
        if rules_acc - local_acc > MAX_REGRESSION + 1e-9:
            worse.append(f"{q} local {local_acc} vs rules {rules_acc}")
    criteria[names[3]] = {
        "met": not worse,
        "detail": "; ".join(worse) if worse else "no task more than 2 points worse than rules",
    }
    adopt = all(c["met"] for c in criteria.values())
    reasons = (
        ["all four adoption criteria met"]
        if adopt
        else [f"{n}: {c['detail']}" for n, c in criteria.items() if not c["met"]]
    )
    return {
        "adopt_local": adopt,
        "reasons": reasons,
        "criteria": criteria,
        "recommendation": "adopt local as the first model tier"
        if adopt
        else "keep rules plus hosted fallback",
    }


# ----------------------------------------------------------------------------- runner


def _llm_unanswerable(llm: LLMClient) -> bool:
    return (
        isinstance(llm, FakeLLMClient)
        and not llm.fixtures
        and llm.default is None
        and getattr(llm, "_oracle", None) is None
    )


def _usd_per_100(meter: CostMeter, pages: int) -> str:
    total = meter.total()
    return f"{(total / pages * 100) if pages else Decimal(0):.4f}"


def run_gate(
    gate_dir: Path,
    canary_dir: Path | None = None,
    classifiers: Sequence[str] = ALL_CLASSIFIERS,
    *,
    ocr_noise: float = 0.02,
    seed: int = 4002,
    ocr: str = "oracle",
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    local_scorer: PairScorer | Callable[..., float] | None = None,
    max_usd: Decimal = Decimal("5.00"),
) -> dict[str, Any]:
    """Run the gate and return the JSON-able report (classification.md 4.11)."""
    classifiers = list(classifiers)
    if "local" in classifiers and "rules" not in classifiers:
        classifiers.insert(0, "rules")  # the adoption criteria compare against rules
    unknown = [c for c in classifiers if c not in ALL_CLASSIFIERS]
    if unknown:
        raise ValueError(f"unknown classifier(s): {', '.join(unknown)}")
    if ocr not in ("oracle", "real"):
        raise ValueError("ocr must be 'oracle' or 'real'")
    settings = settings or Settings(_env_file=None)  # type: ignore[call-arg]
    llm = llm if llm is not None else get_llm_client(settings)
    if ocr == "real" and _llm_unanswerable(llm):
        raise ValueError(
            "--ocr real needs a real LLM client (set GLDB_LLM_API_KEY and model names)"
        )
    min_prob = settings.classifier_min_prob

    pages = load_gate_pages(
        gate_dir, ocr=ocr, ocr_noise=ocr_noise, seed=seed, settings=settings, llm=llm
    )
    rules = RulesClassifier()

    # -- availability --------------------------------------------------------
    status: dict[str, dict[str, Any]] = {}
    local: LocalClassifier | None = None
    try:
        local = LocalClassifier(settings, scorer=local_scorer)
        local_info = {
            "status": "ran",
            "reason": None,
            "model": local.display_name,
            "version": local.version,
        }
    except ClassifierUnavailable as exc:
        local_info = {
            "status": SKIP_NOT_INSTALLED,
            "reason": str(exc),
            "model": None,
            "version": None,
        }
    hosted_ok = not _llm_unanswerable(llm)
    hosted_info = (
        {"status": "ran", "reason": None}
        if hosted_ok
        else {
            "status": SKIP_NO_KEY,
            "reason": "LLM client is the fake without fixtures: no real hosted model is reachable",
        }
    )
    for name in classifiers:
        if name == "rules":
            status[name] = {"status": "ran", "reason": None, "version": rules.version}
        elif name == "local":
            status[name] = dict(local_info)
        elif name == "llm":
            status[name] = dict(hosted_info)
        else:
            parts = {
                "rules": "ran",
                "local": "ran" if local else local_info["status"],
                "llm": "ran" if hosted_ok else hosted_info["status"],
            }
            status[name] = {
                "status": "ran",
                "reason": None
                if local and hosted_ok
                else "degraded: " + ", ".join(f"{k} {v}" for k, v in parts.items() if v != "ran"),
                "components": parts,
            }
    tasks: dict[str, dict[str, Any]] = {q: {} for q in QUESTIONS}
    latency: dict[str, Any] = {}
    usd: dict[str, Any] = {}
    usd_note: dict[str, str] = {}
    canaries: dict[str, Any] = {}

    for name in classifiers:
        if status[name]["status"] != "ran":
            skip = {"skipped": status[name]["status"], "reason": status[name]["reason"]}
            for q in QUESTIONS:
                tasks[q][name] = skip
            latency[name] = skip
            usd[name] = skip
            canaries[name] = skip
            continue
        meter = CostMeter(BudgetLimits(max_pages=1_000_000, max_calls=1_000_000, max_usd=max_usd))
        hosted = HostedClassifier(llm, meter, settings) if hosted_ok else None
        engine = Engine(
            rules=rules,
            local=local if name in ("local", "cascade") else None,
            hosted=hosted if name in ("llm", "cascade") else None,
            min_prob=min_prob,
        )
        recs: dict[str, list[Rec]] = {q: [] for q in QUESTIONS}
        seconds: list[float] = []
        with meter.scope(stage="classify"):
            for page in pages:
                started = time.perf_counter()
                got = engine.page(name, page)
                seconds.append(time.perf_counter() - started)
                recs["C1"].append(_rec(page.c1, got["C1"][0], page.render))
                if page.c3:
                    recs["C3"].append(_rec(page.c3, got["C3"][0], page.render))
                for q, key in (("C4", "c4"), ("C5", "c5")):
                    rows = [r for r in page.rows if getattr(r, key)]
                    for row, dist in zip(rows, got.get(q, []), strict=True):
                        recs[q].append(_rec(getattr(row, key), dist, page.render))
            canaries[name] = (
                canary_results(engine, name, canary_dir)
                if canary_dir is not None
                else {
                    "n": 0,
                    "expected_n": EXPECTED_CANARIES,
                    "passed": 0,
                    "complete": False,
                    "skipped": "no canary directory",
                }
            )
        for q in QUESTIONS:
            tasks[q][name] = task_metrics(recs[q], min_prob)
        latency[name] = {
            "n": len(seconds),
            "p50_s": _r(statistics.median(seconds)) if seconds else None,
            "p95_s": _r(percentile(seconds, 0.95)) if seconds else None,
        }
        usd[name] = _usd_per_100(meter, len(pages))
        models = {c.model for c in meter.costs()}
        if name in ("llm", "cascade") and hosted_ok:
            usd_note[name] = (
                "no real model cost measured" if models <= {"fake"} else "measured from API usage"
            )
        else:
            usd_note[name] = "no model dollars (rules and local compute are not priced)"

    ocr_label = "real" if ocr == "real" else "oracle+noise"
    report: dict[str, Any] = {
        "pages": len(pages),
        "ocr": ocr_label,
        "ocr_noise": ocr_noise,
        "seed": seed,
        "min_prob": min_prob,
        "scan_pages": sum(p.render in SCAN_RENDERS for p in pages),
        "classifiers": status,
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or platform.machine(),
        },
        "tasks": tasks,
        "latency": latency,
        "usd_per_100_pages": usd,
        "usd_note": usd_note,
        "canaries": canaries,
        "decision": adoption_decision(status, tasks, latency, canaries),
        "limits": [
            "oracle OCR plus seeded noise is not a real scan measurement"
            if ocr != "real"
            else "real OCR was used",
            "the generator never produces other_bank statements: no other_bank accuracy row",
            "C4 and C5 contexts carry the counterparty only (no applicant or related-party names)",
        ],
    }
    return report


# ----------------------------------------------------------------------------- CLI


def format_summary(report: Mapping[str, Any]) -> str:
    lines = [
        f"classifier gate: {report['pages']} pages ({report['scan_pages']} scanned), ocr={report['ocr']}"
    ]
    for q in QUESTIONS:
        for name, m in report["tasks"][q].items():
            if "skipped" in m:
                lines.append(f"  {q} {name:8s} {m['skipped']}")
            else:
                lines.append(
                    f"  {q} {name:8s} acc={m['accuracy']} f1={m['macro_f1']} n={m['n']} scan={m['by_render']['scan']}"
                )
    for name, lat in report["latency"].items():
        if "skipped" in lat:
            lines.append(f"  latency {name:8s} {lat['skipped']}")
        else:
            lines.append(
                f"  latency {name:8s} p50={lat['p50_s']}s p95={lat['p95_s']}s "
                f"usd/100pages={report['usd_per_100_pages'][name]} ({report['usd_note'].get(name, '')})"
            )
    for name, can in report["canaries"].items():
        lines.append(
            f"  canaries {name:8s} "
            + (
                can["skipped"]
                if "skipped" in can and "n" not in can
                else f"{can['passed']}/{can['n']} of {can['expected_n']}"
            )
        )
    d = report["decision"]
    lines.append(f"decision: adopt_local={d['adopt_local']} ({'; '.join(d['reasons'])})")
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--gate-dir", default="samples/gate")
    p.add_argument("--canary-dir", default="samples/canaries")
    p.add_argument("--classifiers", default=",".join(ALL_CLASSIFIERS))
    p.add_argument("--ocr-noise", type=float, default=0.02)
    p.add_argument("--ocr", choices=("oracle", "real"), default="oracle")
    p.add_argument("--seed", type=int, default=4002)
    p.add_argument("--max-usd", default="5.00", help="budget for hosted calls during the gate")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    a = _parse_args(argv)
    names = [n.strip() for n in a.classifiers.split(",") if n.strip()]
    gate_dir = Path(a.gate_dir)
    if not (gate_dir / "labels.json").exists():
        print(
            f"error: {gate_dir}/labels.json not found; build it with "
            "`uv run python backend/scripts/make_bundle.py --gate-set --out samples`",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_gate(
            gate_dir,
            Path(a.canary_dir),
            names,
            ocr_noise=a.ocr_noise,
            seed=a.seed,
            ocr=a.ocr,
            max_usd=Decimal(a.max_usd),
            settings=Settings(),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(format_summary(report))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
