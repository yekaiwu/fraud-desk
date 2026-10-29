"""Fairness slice test (M12, docs 4.7): name matching across six name groups through the real
pipeline. A FAIL is a reported finding, never a build failure; the table always has six rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts._eval import metrics as mx
from scripts._eval.common import DEFAULT_SEED, read_json
from scripts._gen.schema import FIXED_TODAY

GROUPS = [
    "western_order",
    "chinese_order",
    "malay_patronymic",
    "indian_patronymic",
    "mononym",
    "titles",
]
NAME_RULES = {"x_directors", "x_ownership_vs_registry", "x_entity"}
CRITERIA = {"false_flag_rate": 0.02, "wrong_match_rate": 0.05, "disparity": 0.05}


def ensure_fairness(samples: Path, seed: int = DEFAULT_SEED, today=FIXED_TODAY) -> Path:  # noqa: ANN001
    root = samples / "fairness"
    if not (root / "pairs.json").exists():
        from scripts._gen import kyc_fairness

        kyc_fairness.build_fairness(samples, seed=seed, today=today)
    return root


def select_pairs(pairs: list[dict[str, Any]], per_group: int | None) -> list[dict[str, Any]]:
    """All pairs, or the first ``per_group`` same-person pairs plus a third as many different-person
    pairs for each group (``--quick``)."""
    if per_group is None:
        return pairs
    out: list[dict[str, Any]] = []
    for g in GROUPS:
        same = [p for p in pairs if p["group"] == g and p["same_person"]][:per_group]
        diff = [p for p in pairs if p["group"] == g and not p["same_person"]][
            : max(1, per_group // 2)
        ]
        out += same + diff
    return out


def fired_name_rule(summary: dict[str, Any]) -> bool:
    return any(f["rule"] in NAME_RULES and f["severity"] != "info" for f in summary["flags"])


def matcher_verdicts(pairs: list[dict[str, Any]]) -> dict[str, str]:
    """The public matcher in ``app.names`` run directly on the same pairs (second column)."""
    from app.names import match_person

    return {p["pair_id"]: match_person(p["name_a"], p["name_b"]).verdict for p in pairs}


def score_groups(
    pairs: list[dict[str, Any]],
    fired: dict[str, bool],
    matcher: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Per group: false-flag rate (same person but a name rule fired), wrong-match rate (different
    person and none fired) with Wilson 95% intervals, and the matcher's own rates."""
    rows = []
    for g in GROUPS:
        same = [p for p in pairs if p["group"] == g and p["same_person"]]
        diff = [p for p in pairs if p["group"] == g and not p["same_person"]]
        ff = sum(1 for p in same if fired.get(p["pair_id"]))
        wm = sum(1 for p in diff if fired.get(p["pair_id"]) is False)
        row: dict[str, Any] = {
            "group": g,
            "n_same": len(same),
            "false_flags": ff,
            "false_flag_rate": ff / len(same) if same else None,
            "false_flag_ci": mx.wilson(ff, len(same)) if same else None,
            "n_diff": len(diff),
            "wrong_matches": wm,
            "wrong_match_rate": wm / len(diff) if diff else None,
            "wrong_match_ci": mx.wilson(wm, len(diff)) if diff else None,
        }
        if matcher is not None:
            mff = sum(1 for p in same if matcher.get(p["pair_id"]) in ("different",))
            mwm = sum(1 for p in diff if matcher.get(p["pair_id"]) in ("same", "probable"))
            row.update(
                matcher_false_flags=mff,
                matcher_false_flag_rate=mff / len(same) if same else None,
                matcher_wrong_matches=mwm,
                matcher_wrong_match_rate=mwm / len(diff) if diff else None,
            )
        rows.append(row)
    rates = [r["false_flag_rate"] for r in rows if r["false_flag_rate"] is not None]
    disparity = (max(rates) - min(rates)) if rates else None
    verdicts = {
        "false_flag_rate": all(
            (r["false_flag_rate"] or 0) <= CRITERIA["false_flag_rate"] for r in rows
        ),
        "wrong_match_rate": all(
            (r["wrong_match_rate"] or 0) <= CRITERIA["wrong_match_rate"] for r in rows
        ),
        "disparity": disparity is not None and disparity <= CRITERIA["disparity"],
    }
    return {
        "groups": rows,
        "disparity": disparity,
        "criteria_proposed": CRITERIA,
        "criteria_met": verdicts,
        "overall": "PASS" if all(verdicts.values()) else "FAIL",
        "note": "Criteria are ours (proposed defaults, not from the plan); a FAIL is a reported finding.",
    }


def load_pairs(samples: Path) -> list[dict[str, Any]]:
    return read_json(samples / "fairness" / "pairs.json")["pairs"]
