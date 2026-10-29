"""Pure metric functions (docs/test-data-and-eval.md section 4.4): no I/O, no app imports.

Every rate carries its denominator; ``wilson`` gives the 95% interval used below 100 samples.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

SEV_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
BAND_RANK = {"low": 0, "medium": 1, "high": 2}
Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for ``k`` successes out of ``n``; ``(0.0, 1.0)`` when ``n == 0``."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate_text(k: int, n: int) -> str:
    """``4/5 = 0.80 [0.38-0.96]``; the interval is printed below 100 samples."""
    if n == 0:
        return "0/0 (no samples)"
    text = f"{k}/{n} = {k / n:.2f}"
    if n < 100:
        lo, hi = wilson(k, n)
        text += f" [{lo:.2f}-{hi:.2f}]"
    return text


def percentile_nearest_rank(values: Sequence[float], q: float) -> float | None:
    """``sorted[ceil(q x n) - 1]`` (docs 4.4 M8)."""
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return s[idx]


def mean(values: Iterable[float]) -> float | None:
    v = list(values)
    return sum(v) / len(v) if v else None


# ---------------------------------------------------------------------------- M1 caught


def flag_key(flag: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """``(rule, sorted document keys)``: cross-document flags compare by the set of documents."""
    return (flag["rule"], tuple(sorted(set(flag.get("doc_keys") or []))))


def new_flags(
    tampered: Sequence[dict[str, Any]], base: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flags of the tampered case whose ``(rule, document keys)`` is absent in the base run."""
    seen = {flag_key(f) for f in base}
    return [f for f in tampered if flag_key(f) not in seen]


def caught_flags(
    tampered: Sequence[dict[str, Any]],
    base: Sequence[dict[str, Any]],
    expected_any_of: Sequence[str],
    info_rules: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """The new flags (relative to the base) whose rule is in ``expected_any_of``. A rule that
    already fired on the untampered base is not a catch (docs 4.4 M1). ``info`` flags are not
    catches, except for ``info_rules``: rules registered at severity ``info`` by design (e.g.
    ``ai_provenance_hint``), whose only possible flag is an info flag."""
    want = set(expected_any_of)
    info_ok = set(info_rules)
    return [
        f
        for f in new_flags(tampered, base)
        if f["rule"] in want and (f["severity"] != "info" or f["rule"] in info_ok)
    ]


# ----------------------------------------------------------------------- M2 localisation


def overlap_fraction(region: dict[str, Any], box: dict[str, Any]) -> float:
    """``area(region and box) / area(box)`` for boxes given as fractions of a page."""
    x1 = max(region["x"], box["x"])
    y1 = max(region["y"], box["y"])
    x2 = min(region["x"] + region["w"], box["x"] + box["w"])
    y2 = min(region["y"] + region["h"], box["y"] + box["h"])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    area = box["w"] * box["h"]
    return ((x2 - x1) * (y2 - y1)) / area if area > 0 else 0.0


def localisation_applicable(target: dict[str, Any]) -> bool:
    """File-level and page-level targets have no row, field or box and are excluded."""
    return bool(target.get("row") is not None or target.get("field") or target.get("box"))


def is_localised(
    flags: Sequence[dict[str, Any]],
    target: dict[str, Any],
    *,
    target_doc_keys: Sequence[str],
    target_row_sig: dict[str, str | None] | None = None,
    target_box: dict[str, Any] | None = None,
    target_box_sha: str | None = None,
    min_overlap: float = 0.30,
) -> bool:
    """True when some flag points at the edited row, field or box (docs 4.4 M2).

    ``target_row_sig`` (the printed cells of the target row) lets a row target match even when the
    extractor numbers rows differently from the generator (e.g. a printed "brought forward" row).
    """
    docs = set(target_doc_keys)
    for f in flags:
        if docs and not (set(f.get("doc_keys") or []) & docs):
            continue
        same_table = f.get("table") == target.get("table") or not target.get("table")
        if target.get("row") is not None and f.get("row") is not None and same_table:
            if f["row"] == target["row"]:
                return True
            sig = f.get("row_sig")
            if target_row_sig and sig and _sig_match(sig, target_row_sig):
                return True
        if target.get("field") and f.get("field") == target["field"]:
            return True
        if target_box:
            for reg in f.get("regions") or []:
                if target_box_sha and reg.get("sha") != target_box_sha:
                    continue
                if reg.get("page") != target_box["page"]:
                    continue
                if overlap_fraction(reg, target_box) >= min_overlap:
                    return True
    return False


def _sig_match(a: dict[str, str | None], b: dict[str, str | None]) -> bool:
    keys = [k for k in ("date", "debit", "credit", "balance") if b.get(k) is not None]
    if not keys:
        return False
    return all(_norm(a.get(k)) == _norm(b.get(k)) for k in keys)


def _norm(v: str | None) -> str:
    return "" if v is None else str(v).strip()


# ----------------------------------------------------------------------- M3 false alarms


def false_alarm_flags(
    flags: Sequence[dict[str, Any]], may_trigger: Iterable[str] = ()
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(false alarms, allowed by a quirk)``: high or medium flags are false alarms on a clean
    bundle unless a quirk's ``may_trigger`` names the rule. ``info`` and ``low`` never count."""
    allowed_rules = set(may_trigger)
    bad: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    for f in flags:
        if SEV_RANK.get(f["severity"], 0) >= SEV_RANK["medium"]:
            (allowed if f["rule"] in allowed_rules else bad).append(f)
    return bad, allowed


def per_ten(total_flags: int, n_clean: int) -> float | None:
    return None if n_clean == 0 else 10.0 * total_flags / n_clean


# ------------------------------------------------------------------------------- ranking


def priority_order(cases: Sequence[dict[str, Any]]) -> list[int]:
    """Indices in queue order: score descending, coverage ascending, upload index ascending (all
    cases are ``open``, so the status rank is equal; docs 4.5, architecture section 6)."""

    def key(i: int) -> tuple[float, float, int]:
        c = cases[i]
        score = c.get("score")
        cov = c.get("coverage_ratio")
        return (
            -(score if score is not None else -1),
            cov if cov is not None else 1.0,
            c["upload_index"],
        )

    return sorted(range(len(cases)), key=key)


def precision_at_k(is_fraud_in_order: Sequence[bool], k: int) -> float:
    k = min(k, len(is_fraud_in_order))
    return sum(is_fraud_in_order[:k]) / k if k else 0.0


def recall_at_k(is_fraud_in_order: Sequence[bool], k: int) -> float:
    total = sum(is_fraud_in_order)
    return sum(is_fraud_in_order[:k]) / total if total else 0.0


def auroc(scored: Sequence[tuple[tuple[float, float], bool]]) -> float | None:
    """Probability that a random fraud case outranks a random clean one; ties count 0.5.
    ``scored`` is ``(rank key, is_fraud)`` with a larger key meaning a higher queue position."""
    pos = [k for k, y in scored if y]
    neg = [k for k, y in scored if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


def first_position(flags: Sequence[bool]) -> int | None:
    for i, y in enumerate(flags, start=1):
        if y:
            return i
    return None


def random_first_position(n: int, k: int) -> float:
    """Expected 1-based position of the first fraud case in a random order: (n + 1) / (k + 1)."""
    return (n + 1) / (k + 1)
