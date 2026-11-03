"""Labelled outcomes for the score model: the same JSON shape from two sources.

``gldb-score-outcomes/1`` holds, per case, the findings and flags exactly as the engine produced
them (so a scoring model can be re-run offline without re-analysing anything), a case-level label
(``tampered``) and a per-flag ``outcome`` (``confirmed`` / ``dismissed``).

* ``synthetic``: the ranking queue of the eval (``_eval/ranking.py``): per slot a clean bundle and
  three tampered forms (deck, forger, single). A flag of a tampered case is *confirmed* when it is
  new against the clean twin of the same company (the same rule ``metrics.new_flags`` uses for
  recall), *dismissed* when the clean twin raises it too; every flag of a clean case is dismissed.
  Fictitious data from our own generator: weights fitted on it calibrate the score to that generator.
* ``review_log``: real reviewer verdicts (confirm / dismiss per flag, as ``app.effectiveness``
  reads them). Same shape, so the fit script needs no change when the tool is in use.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from scripts._eval import metrics as mx
from scripts._eval import ranking as rk
from scripts._eval.common import read_json
from scripts._eval.suites import Config, job, run_cached
from scripts._gen.schema import FIXED_TODAY

SCHEMA = "gldb-score-outcomes/1"
KINDS = ("clean", "deck", "forger", "single")


def finding_outcome(flag_outcomes: list[str]) -> str:
    """``confirmed`` when any member flag is, ``dismissed`` when all are, else ``unknown``."""
    if "confirmed" in flag_outcomes:
        return "confirmed"
    if flag_outcomes and all(o == "dismissed" for o in flag_outcomes):
        return "dismissed"
    return "unknown"


def _planted_kinds(manifest_path: Path) -> tuple[int, int]:
    """``(n_planted, n_root_causes)``: planted manipulations in a generated bundle and the number
    of distinct kinds among them (a single tamper is one). A proxy for how bad a case is."""
    man = read_json(manifest_path)
    planted = man.get("planted") or []
    tampers = man.get("tampers") or []
    kinds = {p.get("kind") for p in planted} | {t.get("tamper_id") for t in tampers}
    return len(planted) + len(tampers), len(kinds)


def case_record(
    summary: dict[str, Any],
    *,
    case_id: str,
    kind: str,
    scenario: str,
    clean_flags: list[dict[str, Any]] | None,
    manifest: Path,
) -> dict[str, Any]:
    tampered = kind != "clean"
    base_keys = {mx.flag_key(f) for f in (clean_flags or [])}
    flags = []
    outcome_of: dict[str, str] = {}
    for f in summary["flags"]:
        if f["severity"] == "info":
            outcome = "info"
        elif tampered and mx.flag_key(f) not in base_keys:
            outcome = "confirmed"
        else:
            outcome = "dismissed"
        outcome_of[f["id"]] = outcome
        flags.append(
            {"id": f["id"], "rule": f["rule"], "severity": f["severity"], "outcome": outcome}
        )
    findings = []
    for fd in summary["findings_detail"]:
        findings.append(
            {
                "group_key": fd["group_key"],
                "severity": fd["severity"],
                "rules": fd["rules"],
                "flag_ids": fd["flag_ids"],
                "hard_stop": fd["hard_stop"],
                "outcome": finding_outcome([outcome_of.get(i, "unknown") for i in fd["flag_ids"]]),
            }
        )
    n_planted, n_causes = _planted_kinds(manifest)
    return {
        "id": case_id,
        "kind": kind,
        "scenario": scenario,
        "tampered": tampered,
        "n_planted": n_planted,
        "n_root_causes": n_causes if tampered else 0,
        "partial": bool(summary.get("partial")),
        "coverage_ratio": summary.get("coverage_ratio"),
        "legacy_score": summary.get("score"),
        "findings": findings,
        "flags": flags,
    }


def build_synthetic(
    *,
    samples: Path,
    out: Path,
    seed: int,
    n_slots: int,
    render: str = "digital",
    today: date = FIXED_TODAY,
    workers: int = 6,
    use_cache: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Generate the ranking queue for ``seed`` and analyse every case offline (fake LLM, no
    network, no money), then label it. Analyses are cached under ``out/.cache``."""
    cfg = Config(
        samples=samples,
        out=out,
        seed=seed,
        today=today,
        renders=[render],
        workers=workers,
        use_cache=use_cache,
        ranking_slots=n_slots,
        log=log,
    )
    qf, paths = rk.build_cases(
        samples, seed=seed, n_slots=n_slots, today=today, log=log, render=render
    )
    qname = qf.queue_id
    meta, jobs = [], []
    for slot, forms in sorted(paths.items()):
        for kind, d in forms.items():
            meta.append((slot, kind))
            jobs.append(
                job(
                    cfg,
                    d / "manifest.json",
                    render,
                    registry=str(samples / "ranking" / qname / "registry.json"),
                )
            )
    res = run_cached(cfg, jobs, label=f"score data seed {seed}")
    by_slot: dict[int, dict[str, dict[str, Any]]] = {}
    errors = []
    for (slot, kind), r in zip(meta, res, strict=True):
        if not r.get("ok"):
            errors.append({"slot": slot, "kind": kind, "error": r.get("error")})
            continue
        by_slot.setdefault(slot, {})[kind] = r
    scenario_of = {s.slot: s.scenario for s in qf.slots}
    cases = []
    ruleset = None
    for slot in sorted(by_slot):
        forms = by_slot[slot]
        if len(forms) != len(KINDS):
            continue
        clean_flags = forms["clean"]["flags"]
        for kind in KINDS:
            r = forms[kind]
            ruleset = ruleset or r.get("ruleset_version")
            cases.append(
                case_record(
                    r,
                    case_id=f"{qname}-{slot:02d}-{kind}",
                    kind=kind,
                    scenario=scenario_of[slot],
                    clean_flags=None if kind == "clean" else clean_flags,
                    manifest=paths[slot][kind] / "manifest.json",
                )
            )
    return {
        "schema": SCHEMA,
        "source": "synthetic",
        "seed": seed,
        "slots": len(by_slot),
        "render": render,
        "today": today.isoformat(),
        "ruleset_version": ruleset,
        "errors": errors,
        "note": (
            "Fictitious bundles from our own generator (ranking queue, four forms per slot). "
            "A flag is confirmed when a tampered case raises it and its clean twin does not."
        ),
        "cases": cases,
    }


def outcomes_from_review_log(db: Any) -> dict[str, Any]:
    """Real reviewer verdicts: the latest version of each case, ``confirmed`` / ``dismissed`` per
    flag as ``app.effectiveness`` reads them. Measures reviewer agreement with a rule, not ground
    truth. A case is ``tampered`` when a reviewer confirmed a non-info flag and ``None`` (unlabelled,
    left out of evaluation) when no flag was decided."""
    from app.review import flag_dispositions, load_entries

    with db.conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM cases ORDER BY id").fetchall()]
    cases = []
    ruleset = None
    for case_id in ids:
        case = db.load_case(case_id)
        if case.score is None:
            continue
        disp = flag_dispositions(case, load_entries(db, case_id))
        outcome_of = {}
        for f in case.flags:
            d = disp.get(f.id, "open")
            outcome_of[f.id] = (
                "info" if f.severity == "info" else d if d in ("confirmed", "dismissed") else "open"
            )
        decided = [o for o in outcome_of.values() if o in ("confirmed", "dismissed")]
        ruleset = ruleset or (case.provenance.ruleset_version if case.provenance else None)
        cases.append(
            {
                "id": case.id,
                "kind": "review",
                "scenario": case.use_case,
                "tampered": (any(o == "confirmed" for o in decided) if decided else None),
                "n_planted": None,
                "n_root_causes": None,
                "partial": bool(case.score.partial),
                "coverage_ratio": case.coverage.ratio if case.coverage else None,
                "legacy_score": None,
                "findings": [
                    {
                        "group_key": fd.group_key,
                        "severity": fd.severity,
                        "rules": list(fd.rules),
                        "flag_ids": list(fd.flag_ids),
                        "hard_stop": fd.hard_stop,
                        "outcome": finding_outcome(
                            [outcome_of.get(i, "open") for i in fd.flag_ids]
                        ),
                    }
                    for fd in case.findings
                ],
                "flags": [
                    {
                        "id": f.id,
                        "rule": f.rule,
                        "severity": f.severity,
                        "outcome": outcome_of[f.id],
                    }
                    for f in case.flags
                ],
            }
        )
    return {
        "schema": SCHEMA,
        "source": "review_log",
        "seed": None,
        "ruleset_version": ruleset,
        "note": "Reviewer verdicts (confirmed / dismissed per flag). Agreement with a rule, not ground truth.",
        "cases": cases,
    }


def save_dataset(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8")


def load_dataset(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} file (schema={data.get('schema')!r})")
    return data
