"""Markdown and CSV writers: validation-report sections, acceptance.md, CSVs, ``latest.json``.

Every number printed here comes from a result dict produced by the suites (never typed by hand);
a metric that could not be run prints "not run" with a reason, or "rule not implemented".
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from scripts._eval import metrics as mx
from scripts._eval.common import HEADER, ORACLE_SENTENCE, write_json_file

MARKERS = [
    "scope",
    "recall",
    "false_alarms",
    "extraction",
    "classification",
    "canaries",
    "cost",
    "ranking",
    "fairness",
    "acceptance",
    "burden",
    "limits",
    "changelog",
]
NOT_RUN = "_Not run yet._"


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(v: Any) -> str:
        return "-" if v is None else str(v).replace("|", "/")

    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def f2(v: float | None, nd: int = 2) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def rate(k: int, n: int) -> str:
    return mx.rate_text(k, n)


# -------------------------------------------------------------------------------- scope


def fmt_extraction(e: Any) -> str:
    if isinstance(e, dict):
        by: dict[str, list[str]] = {}
        for render, mode in e.items():
            by.setdefault(mode, []).append(render)
        return "; ".join(f"{mode} for {', '.join(sorted(r))}" for mode, r in sorted(by.items()))
    return str(e)


def section_scope(R: dict[str, Any]) -> str:
    g = R["generated_with"]
    lines = [
        HEADER,
        "",
        f"- Ruleset version: `{g.get('ruleset_version')}`; app version `{g.get('app_version')}`.",
        f"- Today (injected, never the wall clock): {g['today']}; generator seed {g['seed']}.",
        f"- Extraction: {fmt_extraction(g['extraction'])}; classifier: `{g['classifier']}`; language model: `{g['llm_backend']}` (no API key, no network).",
        f"- Renders requested: {', '.join(g['renders'])}; instances per tamper (digital): {g['n']}; degraded instances per scenario: {g['n_degraded']} ({g['degradation_policy']}).",
        f"- Mode: {'quick' if g.get('quick') else 'full'}. Clean pool: {g['clean_pool']}; ranking slots: {g['ranking_slots']} x 4 forms x {g['ranking_reps']} repetitions; fairness set: {g['fairness_mode']}.",
    ]
    return "\n".join(lines)


# -------------------------------------------------------------------------------- recall


def recall_rows_md(rows: list[dict[str, Any]], renders: list[str]) -> str:
    out = []
    for r in rows:
        if r.get("status") != "measured":
            out.append(
                [
                    r["tamper_id"],
                    r["channel"],
                    r["render"],
                    "0",
                    "-",
                    "-",
                    "-",
                    r["status"],
                    ", ".join(r.get("expected", [])) or "(none)",
                    r["edit"][:70],
                ]
            )
            continue
        flags = (
            ",".join(
                f
                for f, on in (
                    ("hard", r["hard"]),
                    ("known miss", r["known_miss"]),
                    ("weak", r["weak"]),
                )
                if on
            )
            or "-"
        )
        loc = (
            "-"
            if not r["localisable"] and not r["localised"]
            else f"{r['localised']}/{r['localisable']}"
        )
        out.append(
            [
                r["tamper_id"],
                r["channel"],
                r["render"],
                r["n"],
                f"{r['caught']}/{r['n']}",
                f"{r['wilson_lo']:.2f}-{r['wilson_hi']:.2f}",
                loc,
                flags,
                ", ".join(r["expected"]) or "(none)",
                (
                    f"{r['missed_ran']} ran / {r['missed_not_run']} not run"
                    + (f" ({'; '.join(r['not_run_reasons'])})" if r.get("not_run_reasons") else "")
                )
                if r["n"] - r["caught"]
                else "-",
            ]
        )
    return md_table(
        [
            "tamper",
            "channel",
            "render",
            "n",
            "caught",
            "Wilson 95%",
            "localised",
            "flags",
            "expected rule(s)",
            "misses (ran / not run)",
        ],
        out,
    )


def caught_via(R: dict[str, Any], tid: str, render: str) -> str:
    """Which expected rules produced the catches of ``tid`` (from the per-instance records)."""
    counts: dict[str, int] = {}
    for rec in R.get("recall_recs") or []:
        if rec["tamper_id"] == tid and rec["render"] == render and rec.get("caught"):
            for rule in rec.get("caught_rules", []):
                counts[rule] = counts.get(rule, 0) + 1
    return ", ".join(f"`{k}` in {v}" for k, v in sorted(counts.items()))


def section_recall(R: dict[str, Any]) -> str:
    t = R["recall_tables"]
    rows_all = t["rows"]
    lines = [
        "A tamper instance counts as caught only when an expected rule fires on a flag that is new relative to the untampered base run of the same render. `weak`, `hard` and `known miss` rows are listed apart and excluded from core recall. `localised` = the flag points at the edited row, field or box (overlap of at least 0.30), over caught instances that have a row, field or box target. The full table, one row per tamper and render, is `docs/eval-results/recall.csv`. "
        + ORACLE_SENTENCE,
        "",
        "### Core recall (pooled over non-weak, non-hard, non-known-miss tampers)",
        "",
    ]
    rows = []
    for render, c in sorted(t["core_recall"].items()):
        lo, hi = c["wilson"]
        rows.append(
            [
                render,
                c["n"],
                c["caught"],
                f"{c['recall']:.2f} [{lo:.2f}-{hi:.2f}]" if c["recall"] is not None else "-",
                f"{c['localised']}/{c['localisable']}",
            ]
        )
    lines.append(
        md_table(["render", "instances", "caught", "core recall", "localised"], rows)
        if rows
        else "_not run_"
    )
    hard = [r for r in rows_all if (r["hard"] or r["known_miss"]) and r.get("status") == "measured"]
    lines += ["", "**Hard case: consistent recomputation (stated either way) and known miss**", ""]
    for r in hard:
        word = "CAUGHT" if r["caught"] else "MISSED"
        extra = " (known miss: content checks cannot see it)" if r["known_miss"] else " (hard case)"
        via = caught_via(R, r["tamper_id"], r["render"])
        lines.append(
            f"- {r['tamper_id']} on {r['render']}: {word} in {r['caught']}/{r['n']} instances, Wilson {r['wilson_lo']:.2f}-{r['wilson_hi']:.2f}{extra}"
            + (f"; caught by {via}" if via else "")
            + "."
        )
    measured = [
        r for r in rows_all if r.get("status") == "measured" and not (r["hard"] or r["known_miss"])
    ]
    digital_miss = [r for r in measured if r["render"] == "digital" and r["caught"] < r["n"]]
    lines += [
        "",
        "**Digital rows with a miss (every other digital row caught every instance)**",
        "",
    ]
    lines.append(
        recall_rows_md(digital_miss, R["generated_with"]["renders"]) if digital_miss else "_none_"
    )
    lines.append("")
    for render in sorted({r["render"] for r in measured if r["render"] != "digital"}):
        rr = [r for r in measured if r["render"] == render]
        miss = [r for r in rr if r["caught"] < r["n"]]
        lines += [
            f"- `{render}` ({len(rr)} tamper rows, one to three instances each): "
            + (
                "; ".join(f"{r['tamper_id']} {r['caught']}/{r['n']}" for r in miss)
                if miss
                else "every instance caught"
            )
            + (f". The other {len(rr) - len(miss)} rows caught every instance." if miss else "."),
        ]
    already = [
        f"{r['tamper_id']} {r['render']} ({r['already_firing_on_base']})"
        for r in measured
        if r.get("already_firing_on_base")
    ]
    if already:
        lines += [
            "- The expected rule was already firing on the untampered base (so no catch is credited): "
            + ", ".join(already)
            + ".",
        ]
    locmiss = [
        r
        for r in measured
        if r["caught"]
        and r["localisable"]
        and r["localised"] < r["localisable"]
        and r["render"] == "digital"
    ]
    if locmiss:
        lines += [
            "- Caught but not localised on digital (the flag did not point at the edited row, field or box): "
            + ", ".join(f"{r['tamper_id']} {r['localised']}/{r['localisable']}" for r in locmiss)
            + ".",
        ]
    notrun = [r for r in measured if r.get("not_run_reasons") and r["render"] == "digital"]
    for r in notrun:
        lines.append(
            f"- {r['tamper_id']} missed because the check did not run in {r['missed_not_run']} instance(s): "
            + "; ".join(r["not_run_reasons"])
            + "."
        )
    other = [r for r in rows_all if r.get("status") != "measured"]
    sk = [s for s in t["skipped"] if "scenario" in s]
    if other or sk:
        lines += [
            "- Not measured: "
            + "; ".join(sorted({f"{s['tamper_id']} ({s['status'][:90]})" for s in [*other, *sk]})),
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- false alarms


_FORENSIC_RULES = {"ela_region", "copy_move", "noise_mismatch"}


def _diag_tail(diag: dict[str, Any]) -> str:
    """What the image-only-PDF diagnostic shows, read from the data (never a fixed claim)."""
    fired = {rule for row in diag.get("rows", []) for rule, _sev in row.get("false_alarms", [])}
    if not diag.get("flags_ge_medium"):
        return "the statement-chain rules and the image-forensics rules are both silent on clean image-only PDFs."
    if fired & _FORENSIC_RULES:
        return (
            "the statement-chain rules stop firing, the image-forensics rules ("
            + ", ".join(sorted(fired & _FORENSIC_RULES))
            + ") do not."
        )
    return "the rules still firing are: " + ", ".join(sorted(fired)) + "."


def section_false_alarms(R: dict[str, Any]) -> str:
    fa = R["false_alarms"]
    lines = [
        "False alarms are high or medium flags on a clean bundle; a rule a quirk's `may_trigger` allows is listed but not counted; `info` and `low` flags are never false alarms (low flags are a secondary figure).",
        "",
    ]
    rows = []
    for render, e in sorted(fa["by_render"].items()):
        rows.append(
            [
                render,
                e["n_clean"],
                e["flags_ge_medium"],
                f2(e["per_10"]),
                f"{e['bundles_with_false_alarm']}/{e['n_clean']}",
                e["low_flags"],
                e["allowed_by_quirk"],
                f"{e['fast_lane']}/{e['n_clean']}",
            ]
        )
    lines.append(
        md_table(
            [
                "render",
                "clean bundles",
                "high/medium flags",
                "false alarms per 10",
                "bundles with a false alarm",
                "low flags",
                "allowed by quirk",
                "fast lane",
            ],
            rows,
        )
        if rows
        else "_not run_"
    )
    by_rules = [
        f"`{render}`: "
        + ", ".join(sorted({a for row in e["rows"] for a, _s in row["false_alarms"]}))
        for render, e in sorted(fa["by_render"].items())
        if e["flags_ge_medium"]
    ]
    if by_rules:
        lines += ["", "Rules firing on clean bundles, by render: " + "; ".join(by_rules) + "."]
    diag = fa["by_render"].get("scan_pdf")
    hq = fa["by_render"].get("scan_hq")
    if diag and hq:
        lines += [
            "",
            f"Diagnostic (not part of recall): `scan_hq` uploads every page as its own JPEG, so a statement spread over several files cannot be put back together; `scan_pdf` keeps each document in one image-only PDF. High or medium flags on the same three clean bundles: {hq['flags_ge_medium']} on `scan_hq` against {diag['flags_ge_medium']} on `scan_pdf` ({diag['per_10']:.1f} per 10); {_diag_tail(diag)}",
        ]
    if fa["hard_quirk"]:
        for e in fa["hard_quirk"].values():
            lines += [
                "",
                "Hard-quirk bundles (reported apart from the headline rate): "
                + "; ".join(
                    f"{b['bundle']} {len(b['false_alarms'])} false alarms, {b['allowed_by_quirk']} allowed by quirk, score {b['score']}, lane {b['lane']}"
                    for b in e["rows"]
                )
                + ".",
            ]
    if fa["errors"]:
        lines += [
            "",
            "Analyses that failed: "
            + "; ".join(f"{e['bundle']} {e['render']}: {e['error']}" for e in fa["errors"]),
        ]
    if any(r != "digital" for r in fa["by_render"]):
        lines += ["", ORACLE_SENTENCE]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- extraction


def section_extraction(R: dict[str, Any]) -> str:
    ex = R["extraction"]
    lines = [
        "Row and field extraction on the clean bundles, against the generator's ground truth. Rows are aligned with `difflib.SequenceMatcher` over `(date, debit, credit, balance)`.",
        "",
    ]
    rows = []
    for mode, t in sorted(ex["statements"].items()):
        rows.append(
            [
                mode,
                t["docs"],
                f"{t['aligned']}/{t['truth_rows']}",
                f"{t['aligned']}/{t['extracted']}",
                f"{t['exact']}/{t['truth_rows']}",
                f"{t['chain_valid']}/{t['docs']}",
                f"{t['chain_match']}/{t['docs']}",
            ]
        )
    lines.append(
        md_table(
            [
                "extraction mode",
                "statements",
                "row recall",
                "row precision",
                "row exact (with description)",
                "chain valid",
                "chain valid matches truth",
            ],
            rows,
        )
        if rows
        else "_not run_"
    )
    nf = R.get("never_fix", {})
    if nf.get("n"):
        lines += [
            "",
            f"The extractor must never fix a tampered chain (plan 8.5): on T01 digital cases the printed chain is broken by construction and the extracted chain stayed broken in {nf['kept_broken']}/{nf['n']} statements.",
        ]
    if ex["fields"]:
        by_type: dict[str, list[int]] = {}
        for k, (a, b) in ex["fields"].items():
            by_type.setdefault(k.split(".")[0], [0, 0])
            by_type[k.split(".")[0]][0] += a
            by_type[k.split(".")[0]][1] += b
        total_a = sum(a for a, _b in by_type.values())
        total_b = sum(b for _a, b in by_type.values())
        weak = [f"{t} {a}/{b}" for t, (a, b) in sorted(by_type.items()) if a < b]
        worst = sorted(
            ((a / b, k, a, b) for k, (a, b) in ex["fields"].items() if b and a < b),
            key=lambda x: x[0],
        )[:6]
        lines += [
            "",
            f"Field accuracy (canonical-equal over fields present in the truth): {total_a}/{total_b} overall across {len(by_type)} document types; document types below 1.00: "
            + (", ".join(weak) or "none")
            + ". Weakest fields: "
            + ("; ".join(f"`{k}` {a}/{b}" for _r, k, a, b in worst) or "none")
            + ".",
        ]
    if ex["tables"]:
        tot_a = sum(a for a, _b in ex["tables"].values())
        tot_b = sum(b for _a, b in ex["tables"].values())
        below = [f"{k} {a}/{b}" for k, (a, b) in ex["tables"].items() if a < b]
        lines += [
            f"Table row recall by key column: {tot_a}/{tot_b} over {len(ex['tables'])} tables"
            + (f"; below 1.00: {', '.join(below)}" if below else "; every table complete")
            + "."
        ]
    lines += ["", "Digital rows are read from the text layer. " + ORACLE_SENTENCE]
    return "\n".join(lines)


# ------------------------------------------------------------------------- classification


def section_classification(R: dict[str, Any]) -> str:
    c = R["classification"]
    lines = [
        "Classification C1 (page type), C3 (statement issuer), C4 (credit-row type), C5 (counterparty business) measured on the clean bundles with the `rules` classifier; the local and hosted classifiers are not available in this run (see the gate result below). C4 and C5 are scored over rows aligned to the truth rows.",
        "",
    ]
    rows = []
    for render, t in sorted(c["rules"].items()):

        def acc(d: dict[str, Any]) -> str:
            return rate(d["correct"], d["n"])

        rows.append(
            [
                render,
                acc(t["C1"]) + f" (macro-F1 {f2(t['C1']['macro_f1'])})",
                acc(t["C3"]),
                acc(t["C4"]),
                acc(t["C5"]),
                f2(t["low_conf_rate"]),
            ]
        )
    lines.append(
        md_table(
            [
                "render",
                "C1 pages",
                "C3 issuer",
                "C4 credits",
                "C5 rows",
                "low-confidence page rate",
            ],
            rows,
        )
        if rows
        else "_not run_"
    )
    for render, t in sorted(c["rules"].items()):
        errs = t["c1_errors"][:6]
        if errs:
            lines.append(
                f"\nC1 confusions on {render} (truth, predicted): "
                + "; ".join(f"{a} -> {b}" for a, b in errs)
            )
    g = c.get("gate", {})
    lines += [
        "",
        "### Classifier gate (imported from `docs/eval-results/classifier-gate.json`, not re-implemented)",
        "",
    ]
    if g.get("status") != "imported":
        lines.append(g.get("status", "not run"))
    else:
        lines.append(
            f"{g['pages']} labelled pages; OCR: {g['ocr']}. Decision recorded by the gate: {g.get('decision')}. Classifier availability: "
            + ", ".join(f"{k}: {v}" for k, v in g["classifiers"].items())
            + "."
        )
        grows = []
        for task, per in sorted(g["tasks"].items()):
            for cl, d in sorted(per.items()):
                if d.get("accuracy") is None or (
                    cl == "cascade" and per.get("rules", {}).get("accuracy") == d.get("accuracy")
                ):
                    continue  # classifiers that did not run, and a cascade identical to rules, are named above
                grows.append(
                    [
                        task,
                        cl,
                        f2(d.get("accuracy"), 3),
                        f2(d.get("macro_f1"), 3) if d.get("macro_f1") is not None else "-",
                        d.get("n"),
                    ]
                )
        if grows:
            lines += ["", md_table(["task", "classifier", "accuracy", "macro-F1", "n"], grows)]
    lines += [
        "",
        "C2 (packet boundaries) is a stretch item and is not run. `other_bank` statements are never generated, so there is no accuracy row for them.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------- canaries


def section_canaries(R: dict[str, Any]) -> str:
    c = R["canaries"]
    rows = [
        [
            r["host"],
            r["placement"],
            "PASS" if r["passed"] else "FAIL",
            r.get("extra_page_label") or "-",
            "; ".join(r["reasons"]) or "-",
        ]
        for r in c["rows"]
    ]
    head = f"Canary pass rate: {rate(c['passed'], c['total'])} (classifier `{c['classifier']}`). Pass means: original page labels unchanged, flags a superset of the control's (P2: the extra page labelled other or boilerplate only with a `doc_unclassified` info flag and coverage not higher), score not lower."
    return (
        head
        + "\n\n"
        + md_table(
            [
                "host",
                "placement",
                "result",
                "extra page label",
                "failure (with the label it received)",
            ],
            rows,
        )
    )


# ------------------------------------------------------------------------------- cost


def section_cost(R: dict[str, Any]) -> str:
    c = R["cost"]
    lines = []
    if c["no_model_cost_measured"]:
        lines += [
            "**No real model cost measured.** The language model client is the fake client, so every dollar figure is zero by construction and is not a result. Seconds below are real wall-clock times of the offline pipeline on this machine.",
            "",
        ]
    s = c["seconds"]
    lines.append(
        f"Digital bundles analysed: {c['bundles']} ({c['pages']} pages). Seconds per bundle: mean {f2(s['mean'])}, p50 {f2(s['p50'])}, p95 {f2(s['p95'])} (nearest-rank)."
    )
    lines += [
        "Mean seconds per stage: "
        + ", ".join(f"{k} {v['mean']:.2f}" for k, v in c["stage_seconds"].items())
        + " (dollars per page by stage: no real model cost measured).",
    ]
    a = c["assumptions"]
    lines += [
        "",
        "Cost per correctly handled bundle: "
        + (
            f"{c['cost_per_correct_bundle']:.4f} USD"
            if c["cost_per_correct_bundle"]
            else "not a result (no model dollars were measured and no false flag had to be priced, so the formula gives zero)"
        )
        + f" over {c['n_correct']} correctly handled bundles. The formula is model dollars plus analyst time on false flags at {a['minutes_per_false_flag']} minutes per false flag and {a['analyst_rate_usd_per_hour']} USD per hour; both are PLACEHOLDER assumptions until `timed-test.csv` has data (timed test: {c['timed_test']['mode']}).",
    ]
    lines += [
        "",
        "Cost experiments (`docs/eval-results/cost_experiments.json`): "
        + ", ".join(f"{k} = {v['status']}" for k, v in c["experiments"].items())
        + ".",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------- ranking


def section_ranking(R: dict[str, Any]) -> str:
    r = R["ranking"]
    sw = r["sweep"]
    m = r["sim_model"]
    lines = [
        f"{r['n_slots']} generated cases per queue (slots), each slot in four forms (clean, deck fraud, forger, one core tamper), digital renders. Fraud share swept over {', '.join(f'{x:.0%}' for x in (s['share'] for s in sw['shares']))} with {sw['reps']} repetitions each (mean, min..max). Simulated minutes per case = {m['base_minutes']} + {m['per_flag_minutes']} x non-info flags (**{m['source']}**).",
        "",
    ]

    def mm(d: dict[str, Any], nd: int = 1) -> str:
        return (
            "-"
            if d["mean"] is None
            else f"{d['mean']:.{nd}f} ({d['min']:.{nd}f}..{d['max']:.{nd}f})"
        )

    rows = []
    for s in sw["shares"]:
        a = s["all"]
        rows.append(
            [
                f"{s['share']:.0%}",
                s["k"],
                mm(a["top5_fraud"]),
                mm(a["top10_fraud"]),
                mm(a["P@5"], 2),
                mm(a["R@5"], 2),
                mm(a["lift@5"], 2),
                mm(a["auroc"], 2),
                mm(a["first_top_down"]),
                mm(a["first_upload"]),
                f"{a['random_first_position']['mean']:.1f}"
                if a["random_first_position"]["mean"]
                else "-",
                mm(a["sim_minutes_top_down"]),
                mm(a["sim_minutes_upload"]),
            ]
        )
    lines += [
        "**All fraud forms (deck, forger, single)**",
        "",
        md_table(
            [
                "fraud share",
                "fraud cases",
                "top-5 fraud",
                "top-10 fraud",
                "P@5",
                "R@5",
                "lift@5",
                "AUROC",
                "first fraud top-down",
                "first fraud upload order",
                "random expectation",
                "sim. minutes top-down",
                "sim. minutes upload order",
            ],
            rows,
        ),
        "",
        "**Detectable subset (forger cases removed from the queue)**",
        "",
    ]
    for s in sw["shares"]:
        d = s["detectable"]
        lines.append(
            f"- {s['share']:.0%}: {mm(d['fraud'])} fraud cases; top-5 {mm(d['top5_fraud'])}; P@5 {mm(d['P@5'], 2)}; AUROC {mm(d['auroc'], 2)}; first fraud top-down {mm(d['first_top_down'])} against {mm(d['first_upload'])} in upload order (random {f2(d['random_first_position']['mean'], 1)})."
        )
    lines.append("")
    lines += ["", "**How the result moves with the fraud share**", ""] + [
        f"- {x}" for x in r["movement"]
    ]
    lines += ["", "**Caveats**", ""] + [f"- {x}" for x in r["caveats"]]
    if r["errors"]:
        lines += [
            "",
            f"Cases that failed to analyse: {len(r['errors'])} (slots excluded from the queue).",
        ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ fairness


def section_fairness(R: dict[str, Any]) -> str:
    f = R["fairness"]
    rows = []
    for g in f["groups"]:
        ff, wm = g["false_flag_ci"], g["wrong_match_ci"]
        rows.append(
            [
                g["group"],
                f"{g['false_flags']}/{g['n_same']} = {f2(g['false_flag_rate'])} [{ff[0]:.2f}-{ff[1]:.2f}]"
                if ff
                else "-",
                f"{g['wrong_matches']}/{g['n_diff']} = {f2(g['wrong_match_rate'])} [{wm[0]:.2f}-{wm[1]:.2f}]"
                if wm
                else "-",
                f"{f2(g.get('matcher_false_flag_rate'))} / {f2(g.get('matcher_wrong_match_rate'))}",
            ]
        )
    c = f["criteria_proposed"]
    lines = [
        f"Name-matching slice test ({f['mode']}, {f['n_pairs']} pairs) through the real pipeline. False-flag rate = same-person pairs for which a name rule (`x_directors`, `x_ownership_vs_registry`, `x_entity`) fired; wrong-match rate = different-person (near-spelled) pairs for which none fired. Last column: the public matcher in `backend/app/names.py` run directly (false-flag rate / wrong-match rate).",
        "",
        md_table(["name group", "false-flag rate", "wrong-match rate", "matcher only"], rows),
        "",
        f"Disparity (max minus min false-flag rate): {f2(f['disparity'])}. Proposed pass criteria (ours, not from the plan): false-flag rate <= {c['false_flag_rate']}, wrong-match rate <= {c['wrong_match_rate']}, disparity <= {c['disparity']}. Outcome: **{f['overall']}** (criteria met: "
        + ", ".join(f"{k} {'yes' if v else 'no'}" for k, v in f["criteria_met"].items())
        + "). A FAIL is a finding, not a build failure. The groups are our categories, not a validated taxonomy; the intervals are wide.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ acceptance


def section_acceptance(R: dict[str, Any]) -> str:
    a = R["acceptance"]
    rows = []
    for r in a["digital"]:
        s = r.get("summary") or {}
        rows.append(
            [
                r["bundle_id"],
                "PASS" if r["pass"] else "FAIL",
                s.get("lane"),
                s.get("band"),
                s.get("score"),
                f2(s.get("coverage_ratio")),
                "yes" if r.get("reproducible") else "no",
                "; ".join(r["reasons"]) or "-",
            ]
        )
    lines = [
        f"Deck acceptance, digital (gating): {sum(1 for r in a['digital'] if r['pass'])}/{len(a['digital'])} bundles pass. The scripted analyst confirms every high flag with a reason and posts the expected verdict through the API; a row passes only when every `must_fire` is met (each any-of group, at the minimum severity), no `must_not_fire` fired, lane and band are in bounds, the case is not partial, the verdict is accepted (201, case closed) and `flags_digest` is reproducible. The tool never decides: this shows the evidence is there and the human path exists.",
        "",
        md_table(
            [
                "bundle",
                "result",
                "lane",
                "band",
                "score",
                "coverage",
                "reproducible",
                "missing, forbidden or other failure",
            ],
            rows,
        ),
    ]
    lines += [
        "",
        f"Reproducibility (M10): {rate(a['reproducible'], a['reproducible_total'])} bundles gave the same `flags_digest` and flag ids on rerun.",
    ]
    eff = [r for r in a["digital"] if r.get("effectiveness")]
    if eff:
        e_ok = sum(
            1 for r in eff if not any("effectiveness" in x or "pack" in x for x in r["reasons"])
        )
        lines.append(
            f"Row A:effectiveness: effectiveness counts and the case pack are consistent with the analyst's actions in {e_ok}/{len(eff)} bundles."
        )
    if a["scan_hq"]:
        n_pass = sum(1 for r in a["scan_hq"] if r["pass"])
        parts = []
        for r in a["scan_hq"]:
            sm_ = r.get("summary") or {}
            parts.append(
                f"{r['bundle_id']} {'PASS' if r['pass'] else 'FAIL'} (band {sm_.get('band')}, score {sm_.get('score')}"
                + ("" if r["pass"] else "; " + "; ".join(r["reasons"])[:110])
                + ")"
            )
        lines += [
            "",
            f"Scan renders (`scan_hq`, oracle extraction), reported and not gating: {n_pass}/{len(a['scan_hq'])} pass. "
            + ORACLE_SENTENCE
            + " "
            + " | ".join(parts),
        ]
    return "\n".join(lines)


# -------------------------------------------------------------------------------- burden


def section_burden(R: dict[str, Any]) -> str:
    b = R["burden"]
    tt = R["cost"]["timed_test"]
    rows = []
    for name, key in (("clean bundles", "clean"), ("deck-fraud bundles", "deck_fraud")):
        s = b[key]["flags_per_bundle"]
        rows.append([name, s["n"], f2(s["mean"]), s["median"], s["p95"]])
    lines = [md_table(["population", "bundles", "mean non-info flags", "median", "p95"], rows), ""]
    if tt["mode"] == "measured":
        lines.append(
            f"Timed analyst test: {tt['rows']} rows in `timed-test.csv`; median seconds to first fraud {tt.get('median_seconds_to_first_fraud')}; minutes per flag {tt.get('minutes_per_flag')}."
        )
    else:
        lines.append(
            f"Timed analyst test: **not run** ({tt['reason']}). The protocol and header are in `docs/eval-results/timed-test.csv`; the simulated minutes in the prioritisation section use an assumed model, not a measurement."
        )
    return "\n".join(lines)


# -------------------------------------------------------------------------------- limits


def section_limits(R: dict[str, Any]) -> str:
    L = R["limits"]
    lines = [
        "**Missed or weak in this run** (generated from the results above; per-row detail in `docs/eval-results/recall.csv`)",
        "",
    ]
    core = [x for x in L["measured"] if "(core)" in x]
    other = [x for x in L["measured"] if "(core)" not in x]
    if core:
        lines.append(
            f"- {len(core)} core tamper rows missed at least one instance (digital and degraded renders); the digital ones are listed in the recall section."
        )
    lines += [f"- {x}" for x in other] or ([] if core else ["- none"])
    lines += ["", "**Gaps (plan 0.6)**", ""] + [f"- {x}" for x in R["gaps"]]
    lines += [
        "",
        "**Fixed limits**",
        "",
        "- The generator is ours (documents, layouts and the answer sheet): every number is an upper bound. Scan and photo results use oracle extraction, not OCR or a vision model.",
        "- Four invented statement layouts; no real bank templates; `other_bank`, hanzi names and the plan 0.4 circle items are not generated. The mock registry is not ACRA data; mock signatures carry no cryptography.",
        "- Weak and statistical tampers (T08, T09, T17, T35) have thresholds only the eval can set and are kept out of core recall. The fraud share in the prioritisation test is our choice.",
        "- The fairness set is small and its groups are our categories. Dollar figures are zero with the fake client; real cost exists only after a keyed run.",
    ]
    return "\n".join(lines)


def section_changelog(R: dict[str, Any], existing: str = "") -> str:
    """One row per ruleset version, appended; a row for this version is replaced, never duplicated."""
    row = R["changelog_row"]
    rows: dict[str, str] = {}
    for line in existing.splitlines():
        m = re.match(r"\| (rs-[^ |]+|[^ |]+) \|", line)
        if (
            m
            and line.startswith("| ")
            and not line.startswith("| ruleset")
            and not line.startswith("|---")
        ):
            rows[m.group(1)] = line
    rows[row["ruleset_version"]] = (
        f"| {row['ruleset_version']} | {row['summary']} | {row['deltas']} |"
    )
    head = "| ruleset version | summary | deltas against the baseline |\n|---|---|---|"
    return head + "\n" + "\n".join(rows[k] for k in rows)


SECTION_FNS = {
    "scope": section_scope,
    "recall": section_recall,
    "false_alarms": section_false_alarms,
    "extraction": section_extraction,
    "classification": section_classification,
    "canaries": section_canaries,
    "cost": section_cost,
    "ranking": section_ranking,
    "fairness": section_fairness,
    "acceptance": section_acceptance,
    "burden": section_burden,
    "limits": section_limits,
}


# ------------------------------------------------------------------------ report writing


def fill_markers(text: str, sections: dict[str, str]) -> str:
    """Replace the content between ``eval:<id>`` marker pairs; everything outside is untouched."""
    for name, body in sections.items():
        pat = re.compile(rf"(<!-- BEGIN eval:{name} -->\n)(.*?)(\n<!-- END eval:{name} -->)", re.S)
        if not pat.search(text):
            raise ValueError(f"marker pair eval:{name} not found")
        text = pat.sub(lambda m, b=body: m.group(1) + b + m.group(3), text, count=1)
    return text


def existing_marker(text: str, name: str) -> str:
    m = re.search(rf"<!-- BEGIN eval:{name} -->\n(.*?)\n<!-- END eval:{name} -->", text, re.S)
    return m.group(1) if m else ""


def write_validation_report(path: Path, R: dict[str, Any]) -> bool:
    """Fill ``docs/validation-report.md`` between the markers and rewrite the ``Status:`` line.
    Idempotent. Returns True when the file changed."""
    text = path.read_text(encoding="utf-8")
    sections = {n: fn(R) for n, fn in SECTION_FNS.items()}
    sections["changelog"] = section_changelog(
        R,
        existing_marker(text, "changelog") if existing_marker(text, "changelog") != NOT_RUN else "",
    )
    new = fill_markers(text, sections)
    g = R["generated_with"]
    status = f"Status: NOT VALIDATED. Evaluated for ruleset {g['ruleset_version']} on {g['today']} (synthetic data; upper bound; thresholds are proposed defaults; sign-off pending)"
    new = re.sub(r"^Status: .*$", status, new, count=1, flags=re.M)
    if new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def csv_text(headers: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue()


def write_outputs(out: Path, R: dict[str, Any], extra_csv: dict[str, str]) -> None:
    """``latest.json``, the CSVs and ``acceptance.md``."""
    from scripts._eval import acceptance as acc

    out.mkdir(parents=True, exist_ok=True)
    write_json_file(out / "latest.json", R["latest"])
    rows = [
        [
            r["tamper_id"],
            r["edit"],
            r["channel"],
            r["render"],
            r["n"],
            r["caught"],
            r.get("recall"),
            r.get("wilson_lo"),
            r.get("wilson_hi"),
            r.get("extraction_mode"),
            r.get("localised"),
            r.get("localisable"),
            r["status"],
        ]
        for r in R["recall_tables"]["rows"]
    ]
    (out / "recall.csv").write_text(
        csv_text(
            [
                "tamper_id",
                "edit",
                "channel",
                "render",
                "n",
                "caught",
                "recall",
                "wilson_lo",
                "wilson_hi",
                "extraction_mode",
                "localised",
                "localisable",
                "status",
            ],
            rows,
        ),
        encoding="utf-8",
    )
    fa = []
    for render, e in sorted(R["false_alarms"]["by_render"].items()):
        fa.append(
            [
                render,
                e["n_clean"],
                e["flags_ge_medium"],
                e["per_10"],
                e["low_flags"],
                e["bundles_with_false_alarm"],
            ]
        )
    for render, e in sorted(R["false_alarms"]["hard_quirk"].items()):
        fa.append(
            [
                f"{render} (hard quirk)",
                e["n_clean"],
                e["flags_ge_medium"],
                e["per_10"],
                e["low_flags"],
                e["bundles_with_false_alarm"],
            ]
        )
    (out / "false_alarms.csv").write_text(
        csv_text(
            [
                "render",
                "n_clean",
                "flags_ge_medium",
                "per_10",
                "low_flags",
                "bundles_with_false_alarm",
            ],
            fa,
        ),
        encoding="utf-8",
    )
    ex = [
        [
            m,
            t["docs"],
            t["truth_rows"],
            t["extracted"],
            t["aligned"],
            t["exact"],
            t["chain_valid"],
            t["chain_match"],
        ]
        for m, t in sorted(R["extraction"]["statements"].items())
    ]
    (out / "extraction.csv").write_text(
        csv_text(
            [
                "extraction_mode",
                "statements",
                "truth_rows",
                "extracted_rows",
                "aligned",
                "exact",
                "chain_valid",
                "chain_valid_matches_truth",
            ],
            ex,
        ),
        encoding="utf-8",
    )
    cl = []
    for render, t in sorted(R["classification"]["rules"].items()):
        for task in ("C1", "C3", "C4", "C5"):
            cl.append(["rules", render, task, t[task]["correct"], t[task]["n"]])
    (out / "classification.csv").write_text(
        csv_text(["classifier", "render", "task", "correct", "n"], cl), encoding="utf-8"
    )
    c = R["cost"]
    (out / "cost.csv").write_text(
        csv_text(
            ["stage", "mean_seconds", "p95_seconds", "usd_per_page"],
            [
                [k, v["mean"], v["p95"], "no real model cost measured"]
                for k, v in c["stage_seconds"].items()
            ],
        ),
        encoding="utf-8",
    )
    for name, text in extra_csv.items():
        (out / name).write_text(text, encoding="utf-8")
    (out / "acceptance.md").write_text(
        acc.acceptance_table_md(R["acceptance"]["digital"], R["acceptance"]["scan_hq"]),
        encoding="utf-8",
    )
    write_json_file(out / "name-slice.json", R["fairness"])
    write_json_file(
        out / "cost_experiments.json", {"status": "not_run: no api key", **c["experiments"]}
    )
