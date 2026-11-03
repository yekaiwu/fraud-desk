"""Fit the per-rule weights of the calibrated score model from labelled outcomes.

The risk score orders the analyst queue; it is never a decision and never a probability of fraud.
This script measures how often each check was right (precision) and writes a versioned weights file
that the app loads (``GLDB_SCORE_MODEL=calibrated``, ``GLDB_SCORE_WEIGHTS_PATH``). See
docs/scoring-and-queue.md section 4.4a.

Outcomes come from one of two places in the same format (``gldb-score-outcomes/1``):

* the synthetic evaluation: bundles from our own generator, analysed offline with the fake LLM
  (no network, no money). Fit on the DEV seed only; the held-out seeds are never fitted.
* real reviewer verdicts: the review log (confirmed / dismissed per flag, as the effectiveness view
  reads them) once the tool is in use.

    # fit on the dev seed, write the file, evaluate on two seeds the fit never saw
    uv run python backend/scripts/fit_score_weights.py --source synthetic --seed 4002 --slots 60 \\
        --eval-seed 4101 --eval-seed 4102 --out backend/app/score_weights.default.json

    # the same fit from stored datasets
    uv run python backend/scripts/fit_score_weights.py --dataset dev.json --eval-dataset held.json

    # real verdicts from the database
    uv run python backend/scripts/fit_score_weights.py --source review-log --db data/gldb.sqlite3 \\
        --out data/score_weights.json

Weights fitted on synthetic data calibrate the score to that generator: the app says so wherever it
explains the score. Exit code 0 on success, 2 on a refused request (for example fitting on a seed
that is also an evaluation seed).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import score_model as sm  # noqa: E402
from scripts._eval import score_data as sd  # noqa: E402
from scripts._eval import score_fit as sf  # noqa: E402
from scripts._eval.common import DEFAULT_SEED  # noqa: E402


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--source", choices=["synthetic", "dataset", "review-log"], default=None)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="DEV seed to fit on (synthetic)")
    p.add_argument(
        "--slots", type=int, default=60, help="companies in the fit queue (4 cases each)"
    )
    p.add_argument("--render", default="digital", help="digital or scan_hq (synthetic)")
    p.add_argument("--dataset", action="append", default=[], help="outcome dataset(s) to fit on")
    p.add_argument("--db", help="SQLite database for --source review-log")
    p.add_argument("--eval-seed", type=int, action="append", default=[], help="held-out seed(s)")
    p.add_argument("--eval-slots", type=int, default=30)
    p.add_argument(
        "--eval-dataset", action="append", default=[], help="held-out outcome dataset(s)"
    )
    p.add_argument("--work", default="docs/eval-results/score", help="samples, cache and datasets")
    p.add_argument("--out", help="write the weights file here")
    p.add_argument("--report-json", help="write the metrics and calibration tables here")
    p.add_argument("--version-label", default="")
    p.add_argument("--prevalence", type=float, default=sf.DEFAULT_PREVALENCE)
    p.add_argument("--prior-strength", type=float, default=sf.DEFAULT_PRIOR_STRENGTH)
    p.add_argument("--reps", type=int, default=400, help="resampled queues per evaluation")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--regenerate", action="store_true", help="ignore stored synthetic datasets")
    return p.parse_args(argv)


def _synthetic(work: Path, seed: int, slots: int, render: str, a: argparse.Namespace) -> dict:
    path = work / f"outcomes-{render}-{seed}-{slots}.json"
    if path.exists() and not a.regenerate:
        return sd.load_dataset(path)
    data = sd.build_synthetic(
        samples=work / "samples",
        out=work / "cache",
        seed=seed,
        n_slots=slots,
        render=render,
        workers=a.workers,
        log=lambda m: print(m, file=sys.stderr),
    )
    if data["errors"]:
        raise SystemExit(f"harness errors in seed {seed}: {data['errors'][:3]}")
    sd.save_dataset(path, data)
    return data


def _review_log(db_path: str | None) -> dict:
    from app.core.config import Settings
    from app.core.db import Database

    settings = Settings(_env_file=None)
    path = Path(db_path) if db_path else Path(settings.database_path)
    if not path.exists():
        raise SystemExit(f"no database at {path}")
    db = Database(str(path), data_dir=settings.data_dir)
    return sd.outcomes_from_review_log(db)


def run(argv: Sequence[str] | None = None) -> int:
    a = parse_args(argv)
    work = Path(a.work)
    source = a.source or ("dataset" if a.dataset else "synthetic")

    if source == "synthetic":
        fit_sets = [_synthetic(work, a.seed, a.slots, a.render, a)]
    elif source == "dataset":
        if not a.dataset:
            raise SystemExit("--source dataset needs --dataset FILE")
        fit_sets = [sd.load_dataset(Path(p)) for p in a.dataset]
    else:
        fit_sets = [_review_log(a.db)]

    fit_seeds = {d.get("seed") for d in fit_sets if d.get("seed") is not None}
    overlap = fit_seeds & set(a.eval_seed)
    if overlap:
        print(
            f"refused: seed(s) {sorted(overlap)} are in both the fit and the evaluation",
            file=sys.stderr,
        )
        return 2

    fitted = sf.fit_weights(
        fit_sets,
        prior_strength=a.prior_strength,
        prevalence=a.prevalence,
        today=date.today(),
        label=a.version_label,
    )
    # the same loader and validation the app uses: a file this script writes is a file the app reads
    raw = (json.dumps(fitted, indent=1, sort_keys=False) + "\n").encode("utf-8")
    loaded = sm.parse_weights(raw, origin="(fitted)")
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        loaded = sm.load_weights(out)
        print(f"wrote {out}  version={fitted['version']}  sha256={loaded.sha256[:16]}")

    src = fitted["source"]
    print(
        f"\nFIT on {src['kind']} {src.get('seeds') or ''}: {src['n_cases']} cases "
        f"({src['n_tampered']} tampered, {src['n_clean']} clean), {len(fitted['rules'])} rule/severity "
        f"cells fitted; every other rule keeps its severity default."
    )
    print(sf.weights_table(fitted))

    report: dict[str, Any] = {"weights_version": fitted["version"], "evaluations": {}}
    dev_label = f"DEV seed {sorted(fit_seeds) or 'log'} (fitted on: optimistic)"
    evals: list[tuple[str, str, list[dict]]] = [(dev_label, "dev", sf.merge(fit_sets))]
    for seed in a.eval_seed:
        d = _synthetic(work, seed, a.eval_slots, a.render, a)
        evals.append((f"HELD-OUT seed {seed} ({a.render})", a.render, d["cases"]))
    for path in a.eval_dataset:
        d = sd.load_dataset(Path(path))
        if d.get("seed") in fit_seeds:
            print(f"refused: {path} has the fit seed {d.get('seed')}", file=sys.stderr)
            return 2
        evals.append(
            (f"HELD-OUT {Path(path).name} ({d.get('render')})", str(d.get("render")), d["cases"])
        )

    held: dict[str, list[dict]] = {}
    for label, render, cases in evals:
        labelled = [c for c in cases if c.get("tampered") is not None]
        if not labelled:
            continue
        ev = sf.evaluate(labelled, loaded, share=a.prevalence, reps=a.reps)
        report["evaluations"][label] = ev
        print("\n" + sf.metrics_table(label, ev))
        if label.startswith("HELD-OUT"):
            held.setdefault(render, []).extend(labelled)
    pooled_groups = dict(held)
    if len(held) > 1:
        pooled_groups["all renders"] = [c for cs in held.values() for c in cs]
    for render, cases in pooled_groups.items():
        n_sets = sum(
            1
            for lbl, r, _ in evals
            if lbl.startswith("HELD-OUT") and (render == "all renders" or r == render)
        )
        if n_sets < 2 and render != "all renders":
            continue
        ev = sf.evaluate(cases, loaded, share=a.prevalence, reps=a.reps)
        label = f"HELD-OUT pooled ({render})"
        report["evaluations"][label] = ev
        print("\n" + sf.metrics_table(label, ev))
        for model in ("legacy", "calibrated"):
            print(
                "\n"
                + sf.calibration_text(f"{model}, {label}", ev[model]["calibration"], a.prevalence)
            )
    print(
        "\nNOT a probability: the weights calibrate the score to "
        + ("our own synthetic generator" if fitted["fitted_on_synthetic"] else "reviewer agreement")
        + "; the score orders the queue and decides nothing."
    )
    if a.report_json:
        Path(a.report_json).write_text(
            json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
