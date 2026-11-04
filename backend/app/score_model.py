"""Scoring models (docs/scoring-and-queue.md section 4.4a).

``legacy`` is the original rule: the score is ``min(100, sum of finding weights)`` (30 / 15 / 5 by
severity). Four high findings reach the cap and a bad case cannot be told from a catastrophic one.

``calibrated`` gives every finding a probability ``p`` and combines them with a noisy-OR:

    score = round(100 * (1 - prod(1 - P_family)))        P_family = 1 - prod(1 - p_k * d**k)

* ``p`` of a finding comes from the weights file: the per-rule precision measured on labelled
  outcomes, shrunk towards the severity default, times ``SINGLE_FINDING_CEILING`` (one finding is
  never read as proof, so a single finding stays in the medium band like a single high finding
  always did). A rule the file does not name keeps its severity default (high 0.30, medium 0.15,
  low 0.05 = the legacy points / 100).
* Findings of one rule family (names and ownership, arithmetic, metadata, pixel forensics, ...)
  are one root cause told several ways: inside a family they are sorted by ``p`` and the k-th
  counts ``d**k`` of its strength, and the family is capped at ``FAMILY_CAP``. So many findings of
  one family cannot dominate, and a finding that is already grouped is never counted twice.
* The result is still an integer 0 to 100, higher is worse, with the same band thresholds. It
  approaches 100 but separates 4 from 8 findings; it is an ORDERING signal for the queue. It is
  not a probability of fraud: the weights are only as good as the data they were fitted on, and
  the shipped file was fitted on our own synthetic generator.

Hard stops, the fast lane and the partial-case floor do not use anything in this module.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.models import SCORE_CAP, SCORE_WEIGHTS, Finding, Flag
from app.custom_rules.spec import is_custom_rule

LEGACY = "legacy"
CALIBRATED = "calibrated"
SCORE_MODELS: tuple[str, ...] = (LEGACY, CALIBRATED)
MODEL_VERSIONS: dict[str, str] = {LEGACY: "1", CALIBRATED: "1"}

WEIGHTS_SCHEMA = "gldb-score-weights/1"
SINGLE_FINDING_CEILING = (
    0.55  # p of a finding = measured precision x this; one finding stays below High
)
WITHIN_FAMILY_DISCOUNT = 0.5  # the k-th finding of a family counts d**k of its p
FAMILY_CAP = 0.58  # one rule family cannot reach the High band (60) on its own
MAX_P = 0.95  # largest p a weights file may carry
DEFAULT_WEIGHTS_FILE = Path(__file__).with_name("score_weights.default.json")

# Fitted on synthetic data? Said wherever the score is explained.
SYNTHETIC_NOTE = (
    "The weights behind this score were fitted on synthetic cases from this tool's own test "
    "generator, so they calibrate the score to that generator. Read the score as a way to order "
    "the queue, not as a probability of fraud. Real reviewer verdicts would replace them."
)
REVIEW_NOTE = (
    "The weights behind this score were fitted on reviewer verdicts (confirmed or dismissed "
    "flags). They measure how often reviewers agreed with a check, not proven fraud. Read the "
    "score as a way to order the queue, not as a probability of fraud."
)
LEGACY_NOTE = (
    "Points per finding by severity (high 30, medium 15, low 5), added up and capped at 100. "
    "The weights are starting guesses, not measured. The score orders the queue; it is not a "
    "probability of fraud."
)

_FAMILY_RULES: dict[str, tuple[str, ...]] = {
    # names, people and ownership that disagree between documents or with the registry
    "identity": (
        "x_ownership_vs_registry",
        "kyc_shareholdings",
        "x_directors",
        "x_entity",
        "x_entity_vs_registry",
        "fs_entity",
        "bs_holder",
        "x_remitter_vs_buyer",
        "inv_parties",
    ),
    # sums, balances and chains that do not add up
    "arithmetic": (
        "bs_totals",
        "bs_running_balance",
        "bs_page_chain",
        "bs_period_continuity",
        "inv_lines",
        "inv_gst",
        "inv_total",
        "fs_balance",
        "fs_subtotals",
        "fs_cash_tie",
        "fs_comparatives",
    ),
    "id_document": ("id_format", "id_checksum", "passport_mrz", "date_valid"),
    # how the file was made or changed
    "file_metadata": (
        "pdf_editor_software",
        "pdf_modified",
        "pdf_producer_unexpected",
        "image_editor_software",
        "image_modified",
        "pdf_bytes_after_signature",
        "pdf_signature_invalid",
        "ai_provenance_hint",
    ),
    # what the pixels and the text layer show
    "pixel_forensics": (
        "pdf_text_vs_render",
        "font_span_outlier",
        "font_mix",
        "ela_region",
        "copy_move",
        "noise_mismatch",
    ),
    "reuse": ("inv_duplicate", "signature_reuse", "file_reuse"),
    "patterns": (
        "x_invoice_vs_receipts",
        "x_invoice_vs_contract",
        "inv_sequence",
        "bs_round_numbers",
        "bs_benford",
        "bs_duplicates",
    ),
    # what the account activity says about the business
    "activity": (
        "x_revenue_vs_credits",
        "x_declared_vs_activity",
        "x_sof_support",
        "bs_pass_through",
    ),
}
RULE_FAMILY: dict[str, str] = {r: fam for fam, rules in _FAMILY_RULES.items() for r in rules}

SEVERITIES = ("high", "medium", "low")
# every custom-rule finding (docs/custom-rules.md) is one family, capped like any other
CUSTOM_FAMILY = "custom"


def family_of(rules: Sequence[str]) -> str:
    """Score family of a finding. A rule in no family is its own (``rule:<name>``); every custom rule is
    in the one ``custom`` family, so custom rules alone cannot reach the High band."""
    if any(is_custom_rule(r) for r in rules):
        return CUSTOM_FAMILY
    for rule in sorted(rules):
        return RULE_FAMILY.get(rule, f"rule:{rule}")
    return "rule:unknown"


def default_p(severity: str) -> float:
    """What a finding of this severity is worth when the weights file knows nothing better."""
    return SCORE_WEIGHTS.get(severity, 0) / 100


# ------------------------------------------------------------------------------ weights file


class ScoreWeightsError(ValueError):
    """The weights file is missing, unreadable or invalid. Never ignored: scoring stops."""


class RuleWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p: float = Field(gt=0, le=MAX_P)  # the finding probability used by the noisy-OR
    precision: float = Field(ge=0, le=1)  # posterior mean precision (p = precision x ceiling)
    confirmed: int = Field(ge=0)
    dismissed: int = Field(ge=0)


class ScoreWeights(BaseModel):
    """A versioned weights file (``fit_score_weights.py`` writes it). Keys of ``rules`` are
    ``"<rule>:<severity>"``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: str = Field(alias="schema")
    version: str = Field(min_length=1, max_length=120)
    created: date
    fitted_on_synthetic: bool
    source: dict[str, Any]
    method: dict[str, Any]
    ruleset_version: str | None = None
    rules: dict[str, RuleWeight]

    @field_validator("schema_id")
    @classmethod
    def _schema(cls, v: str) -> str:
        if v != WEIGHTS_SCHEMA:
            raise ValueError(f"unsupported schema {v!r} (this build reads {WEIGHTS_SCHEMA!r})")
        return v

    @field_validator("rules")
    @classmethod
    def _rules(cls, v: dict[str, RuleWeight]) -> dict[str, RuleWeight]:
        from app.checks.registry import get_check

        for key in v:
            rule, sep, severity = key.rpartition(":")
            if not sep or not rule or severity not in SEVERITIES:
                raise ValueError(
                    f"rule key {key!r} must be '<rule>:<severity>' with severity high, medium or low"
                )
            if get_check(rule) is None:
                raise ValueError(
                    f"rule {rule!r} (key {key!r}) is not a registered check; "
                    "refit the weights against this ruleset"
                )
        return v


@dataclass(frozen=True)
class LoadedWeights:
    weights: ScoreWeights
    sha256: str
    path: str

    def p(self, rule: str, severity: str) -> float:
        rw = self.weights.rules.get(f"{rule}:{severity}")
        return rw.p if rw is not None else default_p(severity)


def parse_weights(raw: bytes, *, origin: str) -> LoadedWeights:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScoreWeightsError(f"score weights {origin}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ScoreWeightsError(f"score weights {origin}: the top level must be an object")
    try:
        weights = ScoreWeights.model_validate(data)
    except ValidationError as exc:
        first = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise ScoreWeightsError(f"score weights {origin}: invalid ({first})") from None
    return LoadedWeights(weights, hashlib.sha256(raw).hexdigest(), origin)


@functools.lru_cache(maxsize=8)
def _load_cached(path: str, mtime_ns: int, size: int) -> LoadedWeights:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ScoreWeightsError(f"score weights {path}: cannot be read ({exc})") from None
    return parse_weights(raw, origin=path)


def load_weights(path: Path | str | None = None) -> LoadedWeights:
    """The weights file at ``path`` (``GLDB_SCORE_WEIGHTS_PATH``), or the file shipped with the
    app. Raises ``ScoreWeightsError`` (never falls back silently) when it is missing or invalid."""
    p = Path(path) if path else DEFAULT_WEIGHTS_FILE
    try:
        st = p.stat()
    except OSError as exc:
        raise ScoreWeightsError(
            f"score weights {p}: file not found or unreadable ({exc})"
        ) from None
    return _load_cached(str(p), st.st_mtime_ns, st.st_size)


# ------------------------------------------------------------------------------------ scoring


@dataclass(frozen=True)
class FindingInput:
    """What the noisy-OR needs from one finding: its rules, its severity and the ``(rule,
    severity)`` of its flags. Built from live findings or from a stored outcome dataset."""

    rules: tuple[str, ...]
    severity: str
    flags: tuple[tuple[str, str], ...] = ()


def finding_p(item: FindingInput, weights: LoadedWeights | None) -> float:
    """Probability of one finding: the largest of its flags' weights (a finding is as strong as
    its strongest member). ``info`` is worth nothing."""
    if item.severity == "info":
        return 0.0
    members = [(r, s) for r, s in item.flags if s != "info"] or [
        (r, item.severity) for r in item.rules
    ]
    if not members:
        return default_p(item.severity)
    return max(weights.p(r, s) if weights is not None else default_p(s) for r, s in members)


def combine(ps_by_family: Mapping[str, Sequence[float]]) -> float:
    """Noisy-OR across families, discounted noisy-OR inside a family. Deterministic: inputs are
    sorted first, so the order of the findings never changes the result."""
    survive = 1.0
    for family in sorted(ps_by_family):
        ps = sorted((p for p in ps_by_family[family] if p > 0), reverse=True)
        fam_survive = 1.0
        for k, p in enumerate(ps):
            fam_survive *= 1.0 - p * (WITHIN_FAMILY_DISCOUNT**k)
        survive *= 1.0 - min(1.0 - fam_survive, FAMILY_CAP)
    return 1.0 - survive


def score_value(probability: float) -> int:
    return min(SCORE_CAP, int(math.floor(100.0 * probability + 0.5)))


def calibrated_value(items: Sequence[FindingInput], weights: LoadedWeights | None) -> int:
    """The calibrated score for a list of findings (``weights=None``: severity defaults only)."""
    fams: dict[str, list[float]] = {}
    for item in items:
        p = finding_p(item, weights)
        if p > 0:
            fams.setdefault(family_of(item.rules), []).append(p)
    return score_value(combine(fams))


def legacy_value(severities: Sequence[str]) -> int:
    return min(SCORE_CAP, sum(SCORE_WEIGHTS[s] for s in severities))


def finding_inputs(findings: Sequence[Finding], flags: Sequence[Flag]) -> list[FindingInput]:
    by_id = {f.id: f for f in flags}
    out = []
    for fd in findings:
        members = tuple((by_id[i].rule, by_id[i].severity) for i in fd.flag_ids if i in by_id)
        out.append(FindingInput(tuple(fd.rules), fd.severity, members))
    return out


def standalone_points(item: FindingInput, weights: LoadedWeights | None) -> int:
    """Points one finding would score alone (at least 1 for anything but ``info``): the number
    shown as the finding's weight under the calibrated model."""
    p = finding_p(item, weights)
    if item.severity == "info":
        return 0
    return max(1, score_value(p))


def calibrated_points(
    findings: Sequence[Finding], flags: Sequence[Flag], weights: LoadedWeights
) -> tuple[int, list[int]]:
    """``(score, standalone points per finding)`` for live findings."""
    items = finding_inputs(findings, flags)
    return calibrated_value(items, weights), [standalone_points(i, weights) for i in items]


# --------------------------------------------------------------------------- description


def active_model(settings: Any) -> str:
    return getattr(settings, "score_model", LEGACY)


def describe_model(settings: Any) -> dict[str, Any]:
    """Name, version and (for the calibrated model) the weights behind a score: stored in the
    case's provenance and returned by ``GET /explain/method``. Raises if the weights file is bad."""
    name = active_model(settings)
    out: dict[str, Any] = {"name": name, "version": MODEL_VERSIONS[name]}
    if name == CALIBRATED:
        lw = load_weights(getattr(settings, "score_weights_path", None))
        w = lw.weights
        out |= {
            "weights_version": w.version,
            "weights_sha256": lw.sha256,
            "weights_source": w.source.get("kind"),
            "fitted_on_synthetic": w.fitted_on_synthetic,
        }
    return out


def model_note(settings: Any) -> str:
    if active_model(settings) == LEGACY:
        return LEGACY_NOTE
    lw = load_weights(getattr(settings, "score_weights_path", None))
    return SYNTHETIC_NOTE if lw.weights.fitted_on_synthetic else REVIEW_NOTE


def status(settings: Any) -> dict[str, Any]:
    """For ``GET /monitoring``: which model scores new cases and, if it is the calibrated one,
    which weights file and what they were fitted on. A broken weights file is reported, not hidden."""
    name = active_model(settings)
    try:
        out = describe_model(settings)
        out["note"] = model_note(settings)
        out["ok"] = True
    except ScoreWeightsError as exc:
        out = {"name": name, "version": MODEL_VERSIONS[name], "ok": False, "error": str(exc)}
    out["ordering_signal_only"] = True
    return out
