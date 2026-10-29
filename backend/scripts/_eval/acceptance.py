"""Deck acceptance (M13, docs 4.10): the nine bundles analysed, then a scripted analyst acts
through the API test client (confirms every high flag with a reason, posts the expected verdict).

The tool never decides: this proves only that the evidence is there and the human path exists.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from scripts._eval.metrics import BAND_RANK, SEV_RANK
from scripts._eval.runner import (
    analyse_manifest,
    extraction_mode_for,
    make_settings,
    manifest_registry,
    new_db,
    summarise_case,
)

BUNDLES = [f"{s}-{v}" for s in ("kyc", "aml", "loan") for v in ("clean", "fraud", "forger")]
ALLOWED_VERDICTS = {
    "approve",
    "request_documents",
    "refer_edd",
    "escalate_aml",
    "reject_or_escalate",
}


def check_row(summary: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Reasons a bundle fails its ``manifest.expected`` (empty = all met). Works on a case summary:
    flags carry ``rule`` and ``severity``; ``lane``, ``band``, ``partial`` are plain fields."""
    reasons: list[str] = []
    best: dict[str, int] = {}
    for f in summary["flags"]:
        best[f["rule"]] = max(best.get(f["rule"], -1), SEV_RANK[f["severity"]])
    for entry in expected.get("must_fire", []):
        rules = entry["rule"] if isinstance(entry["rule"], list) else [entry["rule"]]
        floor = SEV_RANK[entry.get("min_severity") or "info"]
        if not any(best.get(r, -1) >= floor for r in rules):
            label = rules[0] if len(rules) == 1 else "any of " + "/".join(rules)
            reasons.append(f"missing {label} (>= {entry.get('min_severity') or 'info'})")
    for rule in expected.get("must_not_fire", []):
        if best.get(rule, 0) > 0:
            reasons.append(f"forbidden rule fired: {rule}")
    lane = summary.get("lane")
    if expected.get("lane") and lane != expected["lane"]:
        reasons.append(f"lane {lane} != {expected['lane']}")
    if expected.get("lane_any_of") and lane not in expected["lane_any_of"]:
        reasons.append(f"lane {lane} not in {expected['lane_any_of']}")
    band = summary.get("band")
    if expected.get("band_min") and (
        band is None or BAND_RANK[band] < BAND_RANK[expected["band_min"]]
    ):
        reasons.append(f"band {band} below {expected['band_min']}")
    if expected.get("band_max") and (
        band is None or BAND_RANK[band] > BAND_RANK[expected["band_max"]]
    ):
        reasons.append(f"band {band} above {expected['band_max']}")
    limit = expected.get("max_flags_ge_medium")
    if limit is not None:
        n = sum(1 for f in summary["flags"] if SEV_RANK[f["severity"]] >= SEV_RANK["medium"])
        if n > limit:
            reasons.append(f"{n} flags >= medium (max {limit})")
    if summary.get("partial"):
        reasons.append(f"case partial: {summary.get('partial_reasons')}")
    return reasons


def verdict_for(expected: dict[str, Any]) -> str:
    v = expected.get("verdict")
    return v if v in ALLOWED_VERDICTS else "request_documents"


def analyst_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry: analyse, act as the analyst through the API, read effectiveness and the
    pack, then rerun for reproducibility. ``job``: manifest, render, today, gating."""
    from fastapi.testclient import TestClient

    import app.main as main
    from app.core.llm import FakeLLMClient

    root = Path(tempfile.mkdtemp(prefix="gldb-accept-"))
    manifest = Path(job["manifest"])
    render = job["render"]
    today = date.fromisoformat(job["today"])
    man = json.loads(manifest.read_text(encoding="utf-8"))
    try:
        settings = make_settings(root)
        db = new_db(settings)
        case = analyse_manifest(manifest, render=render, db=db, settings=settings, today=today)
        mode = extraction_mode_for(render, "auto")
        summary = summarise_case(case, manifest, render=render, mode=mode)
        expected = man.get("expected", {})
        out: dict[str, Any] = {
            "ok": True,
            "bundle_id": man["bundle_id"],
            "render": render,
            "summary": summary,
            "reasons": check_row(summary, expected),
            "extraction_mode": mode,
        }
        if render == "digital":
            llm = FakeLLMClient()
            registry = manifest_registry(manifest)
            app = main.create_app(settings, db=db, llm=llm, registry=registry)
            client = TestClient(app, raise_server_exceptions=False)
            out["human_path"] = _act_as_analyst(client, case.id, expected)
            out["reasons"] += out["human_path"].pop("reasons")
            out["effectiveness"] = _effectiveness(client, case.id, out["human_path"])
            out["reasons"] += out["effectiveness"].pop("reasons")
            # reproducibility (M10): analyse again in a fresh database
            root2 = Path(tempfile.mkdtemp(prefix="gldb-accept2-"))
            try:
                s2 = make_settings(root2)
                d2 = new_db(s2)
                c2 = analyse_manifest(manifest, render=render, db=d2, settings=s2, today=today)
                same = c2.provenance.flags_digest == case.provenance.flags_digest
                ids_same = sorted(f.id for f in c2.flags) == sorted(f.id for f in case.flags)
            finally:
                shutil.rmtree(root2, ignore_errors=True)
            out["reproducible"] = bool(same and ids_same)
            if not out["reproducible"]:
                out["reasons"].append("flags_digest not reproducible on rerun")
        out["pass"] = not out["reasons"]
        return out
    except Exception as exc:
        return {
            "ok": False,
            "bundle_id": man.get("bundle_id"),
            "render": render,
            "error": f"{type(exc).__name__}: {exc}",
            "pass": False,
            "reasons": [f"harness error: {exc}"],
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _act_as_analyst(client: Any, case_id: str, expected: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    view = client.get(f"/cases/{case_id}").json()
    high = [f["id"] for f in view["flags"] if f["severity"] == "high"]
    confirmed = 0
    if high:
        r = client.post(
            f"/cases/{case_id}/review",
            json={
                "kind": "flag_decision",
                "decision": "confirm",
                "flag_ids": high,
                "reviewer": "eval.analyst",
                "reason": "Scripted analyst: evidence checked against the source document.",
                "case_version": view["version"],
            },
        )
        if r.status_code != 201:
            reasons.append(f"flag_decision confirm failed: {r.status_code} {r.text[:160]}")
        else:
            confirmed = len(high)
    verdict = verdict_for(expected)
    allowed = view.get("allowed_verdicts", [])
    if verdict not in allowed:
        reasons.append(f"verdict {verdict} not in allowed_verdicts {allowed}")
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "verdict",
            "decision": verdict,
            "reviewer": "eval.analyst",
            "reason": "Scripted analyst verdict for the acceptance run.",
            "case_version": view["version"],
        },
    )
    closed = None
    if r.status_code != 201:
        reasons.append(f"verdict post returned {r.status_code}: {r.text[:160]}")
    else:
        closed = client.get(f"/cases/{case_id}").json()
        if closed["status"] != "closed" or closed.get("decision") != verdict:
            reasons.append(
                f"case not closed with decision {verdict}: {closed['status']} / {closed.get('decision')}"
            )
    return {
        "confirmed_high_flags": confirmed,
        "verdict": verdict,
        "verdict_status": r.status_code,
        "closed": bool(closed and closed["status"] == "closed"),
        "reasons": reasons,
    }


def _effectiveness(client: Any, case_id: str, human: dict[str, Any]) -> dict[str, Any]:
    """Row ``A:effectiveness``: counts consistent with what the analyst did and the pack complete."""
    reasons: list[str] = []
    eff = client.get("/rules/effectiveness").json()
    pack = client.get(f"/cases/{case_id}/pack")
    case = client.get(f"/cases/{case_id}").json()
    if pack.status_code != 200:
        reasons.append(f"pack returned {pack.status_code}")
        return {"reasons": reasons}
    p = pack.json()
    fired = sum(r["times_fired"] for r in eff["rules"])
    confirmed = sum(r["confirmed"] for r in eff["rules"])
    if fired != len(case["flags"]):
        reasons.append(f"effectiveness times_fired {fired} != case flags {len(case['flags'])}")
    if confirmed != human["confirmed_high_flags"]:
        reasons.append(f"effectiveness confirmed {confirmed} != {human['confirmed_high_flags']}")
    if len(p["flags"]) != len(case["flags"]):
        reasons.append("pack flag count differs from the case")
    if not p.get("review"):
        reasons.append("pack has no review entries")
    if eff["cases_decided"] != 1:
        reasons.append(f"effectiveness cases_decided {eff['cases_decided']} != 1")
    return {
        "reasons": reasons,
        "times_fired": fired,
        "confirmed": confirmed,
        "pack_flags": len(p["flags"]),
        "pack_review_entries": len(p.get("review", [])),
        "cases_decided": eff["cases_decided"],
    }


def acceptance_table_md(rows: list[dict[str, Any]], scan_rows: list[dict[str, Any]]) -> str:
    def line(r: dict[str, Any]) -> str:
        s = r.get("summary") or {}
        cov = s.get("coverage_ratio")
        return (
            f"| {r['bundle_id']} | {'PASS' if r['pass'] else 'FAIL'} | {s.get('lane')} | {s.get('band')} | {s.get('score')} | "
            f"{'-' if cov is None else format(cov, '.2f')} | {'; '.join(r['reasons']) or '-'} |"
        )

    head = "| bundle | result | lane | band | score | coverage | missing or forbidden |\n|---|---|---|---|---|---|---|"
    out = ["# Deck acceptance", "", "## digital (gating)", head, *[line(r) for r in rows]]
    if scan_rows:
        out += [
            "",
            "## scan_hq, oracle extraction (reported, not gating)",
            head,
            *[line(r) for r in scan_rows],
        ]
    return "\n".join(out) + "\n"
