"""Turn a results file from ``run.py`` into the tables of docs/red-team.md.

uv run python backend/scripts/red_team/report.py results.json [before.json]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def classify(r: dict) -> str:
    """sanity | consistent | raster | counted"""
    if r["verdict"] == "error":
        return "error"
    if r["sanity"]:
        return "sanity"
    if r["consistent"]:
        return "consistent"
    if r["forensic_only"]:
        return "raster"
    return "counted"


def family_table(results: list[dict]) -> str:
    fams: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        k = classify(r)
        f = fams[r["family"]]
        f["attacks"] += 1
        if k in ("sanity", "consistent", "raster", "error"):
            f[k] += 1
            if k in ("consistent", "raster"):
                f[k + "_detected"] += int(r["verdict"] == "detected")
            continue
        f["counted"] += 1
        if r["verdict"] == "detected":
            f["detected"] += 1
            f["detected_fast"] += int(r["lane"] == "fast")
        else:
            f["evaded"] += 1
            f["free_pass"] += int(r["lane"] == "fast")
    head = (
        "| Family | Attacks | Calibration (detected) | Known miss: consistent (detected) | "
        "Raster, pixel checks only (detected) | Counted | Detected | Evaded | Free pass (evaded and fast lane) |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    tot: dict[str, int] = defaultdict(int)
    for name in sorted(fams, key=lambda n: int(n.split()[0][1:])):
        f = fams[name]
        rows.append(
            f"| {name} | {f['attacks']} | {f['sanity']} | {f['consistent']} ({f['consistent_detected']}) | "
            f"{f['raster']} ({f['raster_detected']}) | {f['counted']} | {f['detected']} | {f['evaded']} | {f['free_pass']} |"
        )
        for k, v in f.items():
            tot[k] += v
    rows.append(
        f"| **All** | **{tot['attacks']}** | {tot['sanity']} | {tot['consistent']} ({tot['consistent_detected']}) | "
        f"{tot['raster']} ({tot['raster_detected']}) | **{tot['counted']}** | **{tot['detected']}** | "
        f"**{tot['evaded']}** | **{tot['free_pass']}** |"
    )
    return head + "\n".join(rows)


def attack_table(results: list[dict]) -> str:
    out = [
        "| Id | Attack | Class | Verdict | Lane (control to forged) | Rules that fired |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(
        results, key=lambda r: (int("".join(c for c in r["id"] if c.isdigit())), r["id"])
    ):
        rules = sorted(
            {f"{f['rule']} ({f['severity']})" for f in r["attributable"] if f["severity"] != "info"}
        )
        title = r["title"].replace("|", "/")
        out.append(
            f"| {r['id']} | {title} | {classify(r)} | {r['verdict']} | {r['control_lane']} to {r['lane']} | "
            f"{', '.join(rules) or 'none'} |"
        )
    return "\n".join(out)


def summary(results: list[dict]) -> dict:
    counted = [r for r in results if classify(r) == "counted"]
    det = [r for r in counted if r["verdict"] == "detected"]
    ev = [r for r in counted if r["verdict"] == "evaded"]
    allx = [r for r in results if classify(r) in ("counted", "consistent", "raster")]
    return {
        "attacks": len(results),
        "counted": len(counted),
        "detected": len(det),
        "evaded": len(ev),
        "evasion_rate_counted": round(len(ev) / max(1, len(counted)), 3),
        "free_pass": sum(1 for r in ev if r["lane"] == "fast"),
        "detected_but_fast": sum(1 for r in det if r["lane"] == "fast"),
        "all_forgeries": len(allx),
        "all_evaded": sum(1 for r in allx if r["verdict"] == "evaded"),
        "control_medhigh_flags": sum(len(r["control_medhigh"]) for r in results),
        "sanity_detected": sum(
            1 for r in results if classify(r) == "sanity" and r["verdict"] == "detected"
        ),
        "sanity": sum(1 for r in results if classify(r) == "sanity"),
    }


def main(argv: list[str]) -> int:
    res = json.loads(Path(argv[0]).read_text())["results"]
    print(json.dumps(summary(res), indent=1))
    if len(argv) > 1:
        before = {r["id"]: r for r in json.loads(Path(argv[1]).read_text())["results"]}
        common = [r for r in res if r["id"] in before]
        flipped = [
            r["id"]
            for r in common
            if before[r["id"]]["verdict"] == "evaded" and r["verdict"] == "detected"
        ]
        lane = [
            r["id"]
            for r in common
            if before[r["id"]]["lane"] == "fast"
            and r["lane"] != "fast"
            and r["verdict"] == "evaded"
        ]
        print("evaded -> detected by the fixes:", flipped)
        print("evaded, no longer free pass:", lane)
    print(family_table(res))
    print()
    print(attack_table(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
