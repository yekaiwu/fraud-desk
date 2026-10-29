"""Prioritisation test (M11, docs 4.5, plan 9.3 "Prioritisation", deck slide 3).

30 slots (12 loan, 9 kyc, 9 aml; company ``100 + slot``), each generated in four forms: clean, deck
(the fraud variant), forger and single (one core tamper). Each of the 120 cases is analysed once;
the fraud-share sweep and the repetitions are pure resampling over the cached results.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from scripts._eval import metrics as mx
from scripts._eval.common import DEFAULT_SEED, write_json_file
from scripts._gen.schema import (
    FIXED_TODAY,
    Assignment,
    BundleSpec,
    QueueFile,
    QueueSlot,
)

SHARES = [0.1, 0.2, 0.3, 0.5]
REPS = 5
TOP_KS = (5, 10)
SINGLE_POOL = {
    "loan": ["T01", "T10", "T11", "T16a", "T20a", "T30b", "T32a", "T19"],
    "kyc": ["T50a", "T53", "T56a", "T57", "T41"],
    "aml": ["T58", "T59", "T60", "T10", "T01"],
}
FRAUD_KINDS = ["deck", "forger", "single"]
SIM_BASE_MINUTES = 4.0
SIM_PER_FLAG_MINUTES = 0.75
CAVEATS = [
    "The generator is ours: the fraud we planted is the fraud we look for, so these numbers are an upper bound.",
    "`forger` cases keep every internal identity consistent and can legitimately sit low; the second column set drops them (the detectable subset).",
    "The queue is only as good as `SCORE_WEIGHTS` and the fast-lane thresholds, which are recalibrated from the recall and false-alarm results; this test is run after any recalibration and before the freeze.",
    "The fraud share is our choice (real prevalence at GLDB is unknown); the table shows four shares and five repetitions each.",
    "Minutes to first fraud come from an assumed model, not a measurement, unless `timed-test.csv` has data.",
]


def slot_plan(seed: int, n_slots: int = 30) -> list[tuple[int, str]]:
    """``(slot, scenario)``: 12 loan, 9 kyc, 9 aml in a seeded shuffle (scaled for small ``n_slots``)."""
    names = (["loan"] * 12 + ["kyc"] * 9 + ["aml"] * 9)[:30]
    if n_slots < 30:
        names = [("loan", "kyc", "aml")[i % 3] for i in range(n_slots)]
    rng = random.Random(f"{seed}:ranking:scenarios")
    rng.shuffle(names)
    return list(enumerate(names))


def assignments(
    seed: int, n_slots: int, shares: Sequence[float] = SHARES, reps: int = REPS
) -> list[Assignment]:
    out: list[Assignment] = []
    for rep in range(reps):
        perm = list(range(n_slots))
        random.Random(f"{seed}:ranking:fraud:{rep}").shuffle(perm)
        for share in shares:
            k = round(n_slots * share)
            fraud = perm[:k]
            kinds = {str(s): FRAUD_KINDS[i % 3] for i, s in enumerate(fraud)}
            out.append(Assignment(share=share, rep=rep, fraud_slots=fraud, kind_by_slot=kinds))  # type: ignore[arg-type]
    return out


def upload_orders(seed: int, n_slots: int, reps: int = REPS) -> list[list[int]]:
    """``order[rep][slot] = upload index`` (a seeded permutation per repetition)."""
    out = []
    for rep in range(reps):
        perm = list(range(n_slots))
        random.Random(f"{seed}:ranking:upload:{rep}").shuffle(perm)
        out.append(perm)
    return out


def build_cases(
    samples: Path,
    *,
    seed: int = DEFAULT_SEED,
    n_slots: int = 30,
    today=FIXED_TODAY,  # noqa: ANN001
    log: Callable[[str], None] = print,
) -> tuple[QueueFile, dict[int, dict[str, Path]]]:
    """Generate the four forms of every slot under ``samples/ranking/q<seed>/cases/`` and write
    ``queue.json`` and the queue's own registry."""
    from scripts import make_bundle
    from scripts._eval.common import load_manifest
    from scripts._eval.tamper_core import TamperNotApplicable
    from scripts.make_tampered import apply_tamper

    qdir = samples / "ranking" / f"q{seed}"
    cases_dir = qdir / "cases"
    plan = slot_plan(seed, n_slots)
    uploads = upload_orders(seed, n_slots, 1)[0]
    paths: dict[int, dict[str, Path]] = {}
    slots: list[QueueSlot] = []
    manifests = []
    for slot, scenario in plan:
        idx = 100 + slot
        forms: dict[str, Path] = {}
        singles = list(SINGLE_POOL[scenario])
        random.Random(f"{seed}:ranking:single:{slot}").shuffle(singles)
        for kind, variant in (("clean", "clean"), ("deck", "fraud"), ("forger", "forger")):
            spec = BundleSpec(
                scenario=scenario, variant=variant, company_index=idx, seed=seed, today=today
            )  # type: ignore[arg-type]
            out = cases_dir / f"{slot:02d}-{kind}"
            m = make_bundle.build_bundle(spec, out)
            manifests.append(m)
            forms[kind] = out
        base_dir = forms["clean"]
        base = load_manifest(base_dir / "manifest.json")
        chosen = None
        for tid in singles:
            out = cases_dir / f"{slot:02d}-single"
            try:
                apply_tamper(
                    base,
                    tid,
                    seed=seed,
                    instance=slot,
                    out_dir=out,
                    render="digital",
                    base_dir=base_dir,
                )
            except TamperNotApplicable as exc:
                log(f"ranking slot {slot}: {tid} not applicable ({exc})")
                continue
            chosen = tid
            forms["single"] = out
            break
        if chosen is None:
            raise RuntimeError(f"no applicable single tamper for slot {slot} ({scenario})")
        paths[slot] = forms
        slots.append(
            QueueSlot(
                slot=slot,
                scenario=scenario,  # type: ignore[arg-type]
                company_index=idx,
                upload_index=uploads[slot],
                cases={k: str((v / "manifest.json").relative_to(qdir)) for k, v in forms.items()},  # type: ignore[misc]
                single_tamper=chosen,
            )
        )
    make_bundle.build_registry(qdir / "registry.json", manifests)
    qf = QueueFile(
        queue_id=f"q{seed}", seed=seed, slots=slots, assignments=assignments(seed, n_slots)
    )
    write_json_file(qdir / "queue.json", qf.model_dump(mode="json"))
    return qf, paths


# ------------------------------------------------------------------------------ resampling


def sim_minutes(
    case: dict[str, Any], base: float = SIM_BASE_MINUTES, per_flag: float = SIM_PER_FLAG_MINUTES
) -> float:
    return base + per_flag * case["n_flags"]


def evaluate_queue(
    queue: Sequence[dict[str, Any]],
    *,
    share: float,
    base: float = SIM_BASE_MINUTES,
    per_flag: float = SIM_PER_FLAG_MINUTES,
) -> dict[str, Any]:
    """Measures for one queue. Each case: ``fraud`` (bool), ``score``, ``coverage_ratio``,
    ``upload_index``, ``n_flags`` (non-info). ``share`` is the nominal fraud share (for lift)."""
    n = len(queue)
    order = mx.priority_order(queue)
    in_order = [queue[i]["fraud"] for i in order]
    upload = sorted(range(n), key=lambda i: queue[i]["upload_index"])
    in_upload = [queue[i]["fraud"] for i in upload]
    k_fraud = sum(in_order)
    res: dict[str, Any] = {"n": n, "fraud": k_fraud}
    for k in TOP_KS:
        kk = min(k, n)
        top = sum(in_order[:kk])
        res[f"top{k}_fraud"] = top
        p = mx.precision_at_k(in_order, k)
        res[f"P@{k}"] = p
        res[f"R@{k}"] = mx.recall_at_k(in_order, k)
        ideal = min(kk, k_fraud)
        res[f"P@{k}/ideal"] = top / ideal if ideal else None
        res[f"lift@{k}"] = p / share if share else None
    scored = [((-(i_rank), 0.0), queue[idx]["fraud"]) for i_rank, idx in enumerate(order)]
    res["auroc"] = mx.auroc(scored)
    res["first_top_down"] = mx.first_position(in_order)
    res["first_upload"] = mx.first_position(in_upload)
    res["random_first_position"] = mx.random_first_position(n, k_fraud) if k_fraud else None

    def minutes_to_first(seq: list[int], flags: list[bool]) -> float | None:
        total = 0.0
        for idx, is_f in zip(seq, flags, strict=True):
            total += sim_minutes(queue[idx], base, per_flag)
            if is_f:
                return total
        return None

    res["sim_minutes_top_down"] = minutes_to_first(order, in_order)
    res["sim_minutes_upload"] = minutes_to_first(upload, in_upload)
    return res


def run_sweep(
    results: dict[int, dict[str, dict[str, Any]]],
    qf: QueueFile,
    *,
    base: float = SIM_BASE_MINUTES,
    per_flag: float = SIM_PER_FLAG_MINUTES,
) -> dict[str, Any]:
    """``results[slot][kind]`` = case summary (``score``, ``coverage_ratio``, ``flags``). Returns
    per-share rows for the full queue and for the detectable subset (no forger cases)."""
    uploads = upload_orders(qf.seed, len(qf.slots), qf.reps)
    out: dict[str, Any] = {"shares": [], "n_slots": len(qf.slots), "reps": qf.reps}
    by_share: dict[float, list[Assignment]] = {}
    for a in qf.assignments:
        by_share.setdefault(a.share, []).append(a)
    for share in qf.shares:
        reps_all: list[dict[str, Any]] = []
        reps_det: list[dict[str, Any]] = []
        for a in by_share[share]:
            queue_all, queue_det = [], []
            for s in qf.slots:
                kind = a.kind_by_slot.get(str(s.slot)) if s.slot in a.fraud_slots else "clean"
                res = results[s.slot].get(kind or "clean")
                if res is None:
                    continue
                item = {
                    "slot": s.slot,
                    "kind": kind,
                    "fraud": kind != "clean",
                    "score": res.get("score"),
                    "coverage_ratio": res.get("coverage_ratio"),
                    "upload_index": uploads[a.rep][s.slot],
                    "n_flags": sum(1 for f in res.get("flags", []) if f["severity"] != "info"),
                }
                queue_all.append(item)
                if kind != "forger":
                    queue_det.append(item)
            reps_all.append(evaluate_queue(queue_all, share=share, base=base, per_flag=per_flag))
            reps_det.append(evaluate_queue(queue_det, share=share, base=base, per_flag=per_flag))
        out["shares"].append(
            {
                "share": share,
                "k": round(len(qf.slots) * share),
                "all": summarise(reps_all),
                "detectable": summarise(reps_det),
            }
        )
    return out


def summarise(reps: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [k for k in reps[0] if isinstance(reps[0][k], (int, float)) or reps[0][k] is None]
    out: dict[str, Any] = {}
    for k in keys:
        vals = [r[k] for r in reps if r[k] is not None]
        out[k] = (
            {"mean": statistics.fmean(vals), "min": min(vals), "max": max(vals), "n": len(vals)}
            if vals
            else {"mean": None, "min": None, "max": None, "n": 0}
        )
    return out


def movement_lines(sweep: dict[str, Any]) -> list[str]:
    """One sentence per share on how the result moves (plan 9.4)."""
    lines = []
    for row in sweep["shares"]:
        a = row["all"]
        f = row["share"]
        t5 = a["top5_fraud"]["mean"]
        ft, fu = a["first_top_down"]["mean"], a["first_upload"]["mean"]
        rnd = a["random_first_position"]["mean"]
        det = row["detectable"]["top5_fraud"]["mean"]
        lines.append(
            f"At a fraud share of {f:.0%} ({row['k']} of {sweep['n_slots']} cases) the first five queue places held "
            f"{t5:.1f} fraud cases on average (an ideal queue would hold {min(5, row['k'])}); the first fraud case sat at "
            f"position {ft:.1f} top-down against {fu:.1f} in upload order (random expectation {rnd:.1f}); "
            f"without forger cases the top five held {det:.1f}."
        )
    return lines
