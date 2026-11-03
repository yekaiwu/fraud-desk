"""Fit and evaluate the calibrated score model (docs/scoring-and-queue.md 4.4a, 8).

Everything here works on a ``gldb-score-outcomes/1`` dataset (``_eval/score_data.py``): findings and
flags as the engine produced them, a case-level label and a confirmed / dismissed outcome per flag.
Nothing is re-analysed, no model is called, and the same code serves synthetic outcomes and real
reviewer verdicts.

Per-rule precision: for every ``(rule, severity)`` count the cases in which it fired and a flag was
confirmed or dismissed (a rule counts once per case, however many flags it raised). The synthetic
sample is reweighted to a stated fraud prevalence (real prevalence is unknown), a Beta prior
centred on the rule's current default keeps rare rules near that default, and rules never seen keep
it exactly. ``p`` = shrunk precision x ``SINGLE_FINDING_CEILING``.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

from app import score_model as sm
from app.checks.registry import get_check
from app.core.models import SCORE_WEIGHTS
from scripts._eval import metrics as mx
from scripts._eval import ranking as rk

# 50 pseudo-observations: chosen on the dev seed among 4, 8, 20, 50, 100 (ranking is the same for
# all; only the order of cases by how many manipulations were planted improves, towards the severity
# defaults). Synthetic data has no realistic false alarms, so the fit stays close to the human prior.
DEFAULT_PRIOR_STRENGTH = 50.0
DEFAULT_PREVALENCE = 0.10
# Zero false alarms in N clean cases does not mean a rule never raises one. When the sample is
# reweighted to a prevalence, every rule gets this many extra false alarms among the clean cases
# (a Jeffreys half-count), so a rule seen only a few times cannot claim a high precision.
CLEAN_PSEUDO_FALSE_ALARMS = 0.5
MIN_CASES_LISTED = 1  # a rule seen in fewer cases is not written: it keeps its default
FRAUD_KINDS = ("deck", "forger", "single")
BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("0", 0, 0),
    ("1-19", 1, 19),
    ("20-29", 20, 29),
    ("30-44", 30, 44),
    ("45-59", 45, 59),
    ("60-74", 60, 74),
    ("75-89", 75, 89),
    ("90-99", 90, 99),
    ("100", 100, 100),
)


def merge(datasets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for d in datasets:
        cases.extend(d["cases"])
    return cases


# ------------------------------------------------------------------------------------ counts


def key_of(rule: str, severity: str) -> str:
    return f"{rule}:{severity}"


def case_outcomes(case: dict[str, Any]) -> dict[str, str]:
    """``{"rule:severity": "confirmed" | "dismissed"}`` for one case: confirmed when any flag of
    that rule and severity was confirmed, dismissed when all decided ones were dismissed. Info and
    undecided flags are not counted."""
    seen: dict[str, set[str]] = defaultdict(set)
    for f in case["flags"]:
        if f["severity"] == "info" or f["outcome"] not in ("confirmed", "dismissed"):
            continue
        seen[key_of(f["rule"], f["severity"])].add(f["outcome"])
    return {k: "confirmed" if "confirmed" in v else "dismissed" for k, v in seen.items()}


def count_outcomes(cases: Sequence[dict[str, Any]]) -> dict[str, Counter]:
    """Per key: ``confirmed_t`` / ``dismissed_t`` in tampered cases, ``dismissed_c`` in clean ones
    (a labelled-clean case can only dismiss). Cases without a label (``tampered`` None) count as
    tampered when a flag was confirmed, so a review-log dataset needs no reweighting."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for case in cases:
        clean = case.get("tampered") is False
        for k, outcome in case_outcomes(case).items():
            counts[k][f"{outcome}_{'c' if clean else 't'}"] += 1
    return counts


def class_sizes(cases: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """``(tampered cases, clean cases)`` among the labelled cases."""
    nt = sum(1 for c in cases if c.get("tampered") is True)
    nc = sum(1 for c in cases if c.get("tampered") is False)
    return nt, nc


def shrunk_precision(
    key: str,
    c: Counter,
    *,
    n_tampered: int,
    n_clean: int,
    prevalence: float | None,
    prior_strength: float,
    ceiling: float,
) -> tuple[float, int, int]:
    """``(posterior mean precision, confirmed, dismissed)`` for one key. Beta prior centred on the
    key's default (default p / ceiling) with ``prior_strength`` pseudo-observations."""
    severity = key.rpartition(":")[2]
    conf = c["confirmed_t"] + c["confirmed_c"]
    dism = c["dismissed_t"] + c["dismissed_c"]
    n = conf + dism
    prior_mean = min(sm.MAX_P, sm.default_p(severity) / ceiling)
    a0, b0 = prior_strength * prior_mean, prior_strength * (1 - prior_mean)
    tp = float(conf)
    if prevalence is not None and n_tampered and n_clean:
        s_t, s_c = prevalence / n_tampered, (1 - prevalence) / n_clean
        tp_eff = conf * s_t
        fp_eff = c["dismissed_t"] * s_t + (c["dismissed_c"] + CLEAN_PSEUDO_FALSE_ALARMS) * s_c
        total = tp_eff + fp_eff
        if total > 0:  # keep the number of observations, change only the mix
            tp = n * tp_eff / total
    return (a0 + tp) / (a0 + b0 + n), conf, dism


def fit_weights(
    datasets: Sequence[dict[str, Any]],
    *,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    prevalence: float | None = DEFAULT_PREVALENCE,
    ceiling: float = sm.SINGLE_FINDING_CEILING,
    today: date | None = None,
    label: str = "",
) -> dict[str, Any]:
    """The weights file (a dict ready for ``json.dumps``) fitted on ``datasets``."""
    cases = merge(datasets)
    kinds = {d.get("source") for d in datasets}
    if len(kinds) != 1:
        raise ValueError(f"mixed sources in one fit: {sorted(map(str, kinds))}")
    source = kinds.pop()
    synthetic = source == "synthetic"
    n_t, n_c = class_sizes(cases)
    reweight = prevalence if synthetic else None  # real verdicts keep their own prevalence
    counts = count_outcomes(cases)
    rules: dict[str, Any] = {}
    retired: list[str] = []
    for key in sorted(counts):
        rule, _, sev = key.rpartition(":")
        if sev not in sm.SEVERITIES:
            continue
        if get_check(rule) is None:  # a retired rule in an old log: the app would refuse the file
            retired.append(key)
            continue
        prec, conf, dism = shrunk_precision(
            key,
            counts[key],
            n_tampered=n_t,
            n_clean=n_c,
            prevalence=reweight,
            prior_strength=prior_strength,
            ceiling=ceiling,
        )
        if conf + dism < MIN_CASES_LISTED:
            continue
        p = max(0.005, min(sm.MAX_P, ceiling * prec))
        if synthetic and sev == "high":
            # On synthetic data a rare rule's low precision is missing evidence (the pseudo false
            # alarm), not evidence against it: the fit may raise a high rule but never takes it
            # below the legacy 30, so one high finding keeps landing in the medium band.
            p = max(p, sm.default_p("high"))
        rules[key] = {
            "p": round(p, 4),
            "precision": round(prec, 4),
            "confirmed": conf,
            "dismissed": dism,
        }
    today = today or date.today()
    digest = hashlib.sha256(
        json.dumps([d["cases"] for d in datasets], sort_keys=True).encode()
    ).hexdigest()
    seeds = sorted({d.get("seed") for d in datasets if d.get("seed") is not None})
    src: dict[str, Any] = {
        "kind": "synthetic" if synthetic else "review_log",
        "seeds": seeds,
        "n_cases": len(cases),
        "n_tampered": n_t,
        "n_clean": n_c,
        "dataset_sha256": digest,
    }
    if synthetic:
        src["renders"] = sorted({str(d.get("render")) for d in datasets})
        src["slots"] = sum(int(d.get("slots") or 0) for d in datasets)
        src["note"] = (
            "Our own generator's ranking queue (clean, deck, forger, single per company). "
            "A flag is confirmed when a tampered case raises it and its clean twin does not."
        )
    else:
        src["note"] = "Reviewer verdicts: confirmed or dismissed per flag (agreement, not truth)."
    return {
        "schema": sm.WEIGHTS_SCHEMA,
        "version": label
        or f"{today.isoformat()}-{src['kind']}-{'-'.join(map(str, seeds)) or 'log'}",
        "created": today.isoformat(),
        "fitted_on_synthetic": synthetic,
        "source": src,
        "method": {
            "estimator": "Beta posterior mean of per-(rule, severity) precision",
            "prior": "centred on the severity default (default p / ceiling)",
            "prior_strength": prior_strength,
            "prevalence": reweight,
            "clean_pseudo_false_alarms": CLEAN_PSEUDO_FALSE_ALARMS if reweight is not None else 0,
            "ceiling": ceiling,
            "family_discount": sm.WITHIN_FAMILY_DISCOUNT,
            "family_cap": sm.FAMILY_CAP,
            "high_floor": sm.default_p("high") if synthetic else None,
            "unseen_rules": "keep the severity default (high 0.30, medium 0.15, low 0.05)",
            "dropped_unregistered": retired,
        },
        "ruleset_version": next(
            (d["ruleset_version"] for d in datasets if d.get("ruleset_version")), None
        ),
        "rules": rules,
    }


# ----------------------------------------------------------------------------------- scoring


def finding_inputs(case: dict[str, Any]) -> list[sm.FindingInput]:
    sev_of = {f["id"]: (f["rule"], f["severity"]) for f in case["flags"]}
    return [
        sm.FindingInput(
            tuple(fd["rules"]),
            fd["severity"],
            tuple(sev_of[i] for i in fd["flag_ids"] if i in sev_of),
        )
        for fd in case["findings"]
    ]


def score_case(case: dict[str, Any], model: str, weights: sm.LoadedWeights | None) -> int:
    """Score of a stored case under ``model``: ``legacy``, ``noisy_default`` (the noisy-OR with
    severity defaults only, an ablation) or ``calibrated`` (needs ``weights``)."""
    if model == "legacy":
        return sm.legacy_value([fd["severity"] for fd in case["findings"]])
    items = finding_inputs(case)
    if model == "noisy_default":
        return sm.calibrated_value(items, None)
    if model == "calibrated":
        if weights is None:
            raise ValueError("calibrated needs weights")
        return sm.calibrated_value(items, weights)
    raise ValueError(model)


# ------------------------------------------------------------------------------------ metrics


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx_, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx_) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx_) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def _slot(case: dict[str, Any]) -> str:
    """The company a case belongs to: its id without the trailing form (``q4002-scan_hq-05``)."""
    return case["id"].rsplit("-", 1)[0]


def _pairwise_auroc(cases: Sequence[dict[str, Any]], scores: dict[str, int]) -> float | None:
    """Probability that a tampered case scores above a clean one, ties counting half: the score
    alone, with no queue tie-break (coverage, upload order) to hide a tie."""
    keyed = [
        ((scores[c["id"]], 0.0), bool(c["tampered"]))
        for c in cases
        if c.get("tampered") is not None
    ]
    return mx.auroc(keyed)


def _plain_metrics(cases: Sequence[dict[str, Any]], scores: dict[str, int]) -> dict[str, Any]:
    """The same measures for cases that are not a generated queue (reviewer verdicts): the real
    cases ordered as the app orders them, no resampling and no fraud-share sweep."""
    queue = [
        {
            "fraud": bool(c["tampered"]),
            "score": scores[c["id"]],
            "coverage_ratio": c.get("coverage_ratio"),
            "upload_index": i,
        }
        for i, c in enumerate(cases)
    ]
    n = len(queue)
    top_n = max(1, round(n * 0.10))
    order = mx.priority_order(queue)
    in_order = [queue[i]["fraud"] for i in order]
    scored = [((-rank, 0.0), queue[idx]["fraud"]) for rank, idx in enumerate(order)]
    tampered = [c for c in cases if c.get("tampered") is True]
    return {
        "slots": n,
        "fraud_per_queue": len(tampered),
        "top10_n": top_n,
        "auroc": mx.auroc(scored),
        "pairwise_auroc": _pairwise_auroc(cases, scores),
        "first_fraud_position": mx.first_position(in_order),
        "top10_tampered_share": sum(in_order[:top_n]) / top_n,
        "saturated_all": sum(1 for c in cases if scores[c["id"]] >= 100),
        "saturated_tampered": sum(1 for c in tampered if scores[c["id"]] >= 100),
        "tampered_cases": len(tampered),
        "clean_nonzero": sum(
            1 for c in cases if c.get("tampered") is False and scores[c["id"]] > 0
        ),
        "distinct_scores_tampered": len({scores[c["id"]] for c in tampered}),
        "spearman_score_vs_root_causes": None,
        "tampered_in_fast_by_score": sum(
            1
            for c in tampered
            if scores[c["id"]] <= 20
            and not c["partial"]
            and not any(fd["hard_stop"] for fd in c["findings"])
        ),
    }


def queue_metrics(
    cases: Sequence[dict[str, Any]],
    scores: dict[str, int],
    *,
    share: float = DEFAULT_PREVALENCE,
    reps: int = 400,
    seed: str = "score-fit",
) -> dict[str, Any]:
    """Ranking at a fraud ``share`` by resampling the stored cases, like the eval's ranking sweep:
    each repetition picks ``round(share x slots)`` fraud slots (kinds cycle deck, forger, single),
    the others stay clean, and the queue is ordered as the app orders it (score, then low coverage
    first, then upload order). Means over ``reps`` repetitions."""
    if any(
        c["kind"] not in ("clean", *FRAUD_KINDS) for c in cases
    ):  # a review log: one pass over the real queue
        return _plain_metrics(cases, scores)
    by_slot: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for c in cases:
        by_slot[_slot(c)][c["kind"]] = c
    slots = sorted(s for s, forms in by_slot.items() if set(forms) >= {"clean", *FRAUD_KINDS})
    n = len(slots)
    k = max(1, round(n * share))
    top_n = max(1, round(n * 0.10))
    rng = random.Random(f"{seed}:{n}:{share}")
    rows = []
    for _ in range(reps):
        perm = slots[:]
        rng.shuffle(perm)
        upload = list(range(n))
        rng.shuffle(upload)
        fraud = {s: FRAUD_KINDS[i % 3] for i, s in enumerate(perm[:k])}
        queue = []
        for idx, s in enumerate(slots):
            kind = fraud.get(s, "clean")
            c = by_slot[s][kind]
            queue.append(
                {
                    "fraud": kind != "clean",
                    "score": scores[c["id"]],
                    "coverage_ratio": c.get("coverage_ratio"),
                    "upload_index": upload[idx],
                    "n_flags": 0,
                }
            )
        res = rk.evaluate_queue(queue, share=share)
        order = mx.priority_order(queue)
        in_order = [queue[i]["fraud"] for i in order]
        rows.append(
            {
                "auroc": res["auroc"],
                "first": res["first_top_down"],
                "top10": sum(in_order[:top_n]) / top_n,
            }
        )
    tampered = [c for c in cases if c.get("tampered") is True]
    intensity = [c["n_root_causes"] for c in tampered if c.get("n_root_causes") is not None]
    t_scores = [scores[c["id"]] for c in tampered if c.get("n_root_causes") is not None]
    return {
        "slots": n,
        "fraud_per_queue": k,
        "top10_n": top_n,
        "auroc": statistics.fmean(r["auroc"] for r in rows if r["auroc"] is not None),
        "pairwise_auroc": _pairwise_auroc(cases, scores),
        "first_fraud_position": statistics.fmean(
            r["first"] for r in rows if r["first"] is not None
        ),
        "top10_tampered_share": statistics.fmean(r["top10"] for r in rows),
        "saturated_all": sum(1 for c in cases if scores[c["id"]] >= 100),
        "saturated_tampered": sum(1 for c in tampered if scores[c["id"]] >= 100),
        "tampered_cases": len(tampered),
        "clean_nonzero": sum(
            1 for c in cases if c.get("tampered") is False and scores[c["id"]] > 0
        ),
        "distinct_scores_tampered": len({scores[c["id"]] for c in tampered}),
        "spearman_score_vs_root_causes": spearman(intensity, t_scores),
        "tampered_in_fast_by_score": sum(
            1
            for c in tampered
            if scores[c["id"]] <= 20
            and not c["partial"]
            and not any(fd["hard_stop"] for fd in c["findings"])
        ),
    }


def calibration_table(
    cases: Sequence[dict[str, Any]],
    scores: dict[str, int],
    *,
    prevalence: float | None = DEFAULT_PREVALENCE,
) -> list[dict[str, Any]]:
    """Score bucket against the share of tampered bundles: as sampled, and (for a labelled sample)
    reweighted to ``prevalence`` so it reads as "at that fraud share"."""
    labelled = [c for c in cases if c.get("tampered") is not None]
    n_t, n_c = class_sizes(labelled)
    w_t = prevalence / n_t if prevalence is not None and n_t else 1.0
    w_c = (1 - prevalence) / n_c if prevalence is not None and n_c else 1.0
    rows = []
    for name, lo, hi in BUCKETS:
        inside = [c for c in labelled if lo <= scores[c["id"]] <= hi]
        t = sum(1 for c in inside if c["tampered"])
        cl = len(inside) - t
        wt, wc = t * w_t, cl * w_c
        rows.append(
            {
                "bucket": name,
                "cases": len(inside),
                "tampered": t,
                "share_tampered": t / len(inside) if inside else None,
                "share_tampered_at_prevalence": wt / (wt + wc) if wt + wc else None,
            }
        )
    return rows


def evaluate(
    cases: Sequence[dict[str, Any]],
    weights: sm.LoadedWeights,
    *,
    share: float = DEFAULT_PREVALENCE,
    reps: int = 400,
) -> dict[str, Any]:
    """Every model on the same cases: queue metrics and a calibration table each."""
    out: dict[str, Any] = {}
    for model in ("legacy", "noisy_default", "calibrated"):
        scores = {c["id"]: score_case(c, model, weights) for c in cases}
        out[model] = {
            "metrics": queue_metrics(cases, scores, share=share, reps=reps),
            "calibration": calibration_table(cases, scores, prevalence=share),
        }
    return out


# ------------------------------------------------------------------------------------ report

METRIC_ROWS = (
    ("AUROC (queue order)", "auroc", "{:.3f}"),
    ("AUROC (score alone, ties = half)", "pairwise_auroc", "{:.3f}"),
    ("first fraud case, position", "first_fraud_position", "{:.2f}"),
    ("tampered share of top 10% of queue", "top10_tampered_share", "{:.2f}"),
    ("tampered cases at 100", "saturated_tampered", "{}"),
    ("clean cases above 0", "clean_nonzero", "{}"),
    ("distinct scores among tampered", "distinct_scores_tampered", "{}"),
    ("Spearman, score vs root causes", "spearman_score_vs_root_causes", "{:.3f}"),
    ("tampered cases fast-lane by score", "tampered_in_fast_by_score", "{}"),
)


def _fmt(fmt: str, v: Any) -> str:
    return "n/a" if v is None else fmt.format(v)


def metrics_table(label: str, ev: dict[str, Any]) -> str:
    models = ["legacy", "noisy_default", "calibrated"]
    head = f"{label}  ({ev['legacy']['metrics']['slots']} slots, {ev['legacy']['metrics']['tampered_cases']} tampered)"
    lines = [head, f"  {'':38}" + "".join(f"{m:>15}" for m in models)]
    for name, key, fmt in METRIC_ROWS:
        lines.append(
            f"  {name:38}" + "".join(f"{_fmt(fmt, ev[m]['metrics'][key]):>15}" for m in models)
        )
    return "\n".join(lines)


def calibration_text(label: str, rows: Sequence[dict[str, Any]], prevalence: float | None) -> str:
    lines = [
        f"{label}: score bucket vs share of tampered bundles "
        f"(second share reweighted to {prevalence:.0%} fraud)",
        f"  {'bucket':8}{'cases':>7}{'tampered':>10}{'share':>8}{'@prev':>8}",
    ]
    for r in rows:
        lines.append(
            f"  {r['bucket']:8}{r['cases']:>7}{r['tampered']:>10}"
            f"{_fmt('{:.2f}', r['share_tampered']):>8}{_fmt('{:.2f}', r['share_tampered_at_prevalence']):>8}"
        )
    return "\n".join(lines)


def weights_table(fitted: dict[str, Any], limit: int = 40) -> str:
    rows = sorted(fitted["rules"].items(), key=lambda kv: (-kv[1]["p"], kv[0]))
    lines = [
        f"  {'rule:severity':38}{'n':>5}{'conf':>6}{'dism':>6}{'prec':>7}{'p':>7}{'default':>9}"
    ]
    for key, r in rows[:limit]:
        sev = key.rpartition(":")[2]
        lines.append(
            f"  {key:38}{r['confirmed'] + r['dismissed']:>5}{r['confirmed']:>6}{r['dismissed']:>6}"
            f"{r['precision']:>7.2f}{r['p']:>7.3f}{SCORE_WEIGHTS[sev] / 100:>9.2f}"
        )
    if len(rows) > limit:
        lines.append(f"  ... {len(rows) - limit} more")
    return "\n".join(lines)
