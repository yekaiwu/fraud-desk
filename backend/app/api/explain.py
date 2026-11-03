"""GET /explain/method (api.md section 12): how the score, coverage and lanes are worked out.

Read-only and database free. Every number and rule name is read from the constants and functions
the engine itself uses, so what the interface explains cannot drift from what the engine does.
"""

from __future__ import annotations

from typing import Any, get_args

from fastapi import APIRouter

from app import queue, score_model
from app.api._common import SettingsDep
from app.checks.registry import all_checks
from app.core import models
from app.core.config import Settings
from app.core.models import Lane
from app.core.provenance import ruleset_version

router = APIRouter(tags=["explain"])


def _percent(ratio: float) -> str:
    return f"{ratio * 100:g}%"


_USE_CASE_NAMES = {"kyc": "KYC", "aml": "AML", "loan": "Loan"}


def _lane_how(lane: Lane) -> str:
    if lane == "fast":
        return (
            "Cases that meet every fast lane requirement are routed here. It means look at this "
            "last: a person still decides and nothing is approved automatically."
        )
    names = " and ".join(
        _USE_CASE_NAMES[uc]
        for uc in sorted(uc for uc, base in queue.BASE_LANE.items() if base == lane)
    )
    if lane == "aml":
        rules = ", ".join(sorted(queue.AML_ROUTING_RULES))
        return (
            f"{names} cases are routed here, and so is any other case with a scoring finding from "
            f"an AML routing rule ({rules}), unless the fast lane applies."
        )
    return (
        f"{names} cases are routed here unless a scoring finding comes from an AML routing rule "
        "or the fast lane applies."
    )


def score_method(settings: Settings | None) -> dict[str, Any]:
    """How the score is made, read from the model in force (``GLDB_SCORE_MODEL``)."""
    section: dict[str, Any] = {
        "weights": dict(models.SCORE_WEIGHTS),
        "cap": models.SCORE_CAP,
        "bands": {
            "medium_from": models.BAND_THRESHOLDS["medium"],
            "high_from": models.BAND_THRESHOLDS["high"],
        },
        "counting": "Each finding counts once, however many flags or documents it covers.",
    }
    if settings is None:
        return section
    model = score_model.describe_model(settings)
    section["model"] = model["name"]
    section["model_version"] = model["version"]
    section["note"] = score_model.model_note(settings)
    section["ordering_signal_only"] = True
    if model["name"] == score_model.CALIBRATED:
        section["combination"] = "noisy_or"
        section["counting"] = (
            "Each finding counts once. Findings are combined as separate pieces of evidence "
            "(1 minus the product of what each leaves unexplained), findings of one family of "
            "checks count less each time and the family is capped, so the score approaches 100 "
            "without a handful of flags reaching it. A finding's weight is the points it would "
            "score alone; the weights do not add up to the score."
        )
        section["family_discount"] = score_model.WITHIN_FAMILY_DISCOUNT
        section["family_cap"] = score_model.FAMILY_CAP
        section["weights_version"] = model["weights_version"]
        section["weights_source"] = model["weights_source"]
        section["fitted_on_synthetic"] = model["fitted_on_synthetic"]
    else:
        section["combination"] = "sum"
    return section


def build_method(settings: Settings | None = None) -> dict[str, Any]:
    fast_min = queue.FAST_LANE_MIN_COVERAGE
    return {
        "score": score_method(settings),
        "coverage": {
            "low_coverage_ratio": queue.LOW_COVERAGE_RATIO,
            "fast_lane_min_coverage": fast_min,
            "definition": "Checks that ran divided by checks that apply to the documents received.",
        },
        "fast_lane": {
            "max_score": queue.FAST_LANE_MAX_SCORE,
            "min_coverage": fast_min,
            "requires": [
                "The analysis is complete, not partial",
                f"Score of {queue.FAST_LANE_MAX_SCORE} or less",
                f"At least {_percent(fast_min)} of applicable checks completed, counting pages "
                "that could not be checked as not completed",
                "No finding that blocks the fast lane, and no hard-stop check switched off in the "
                "check catalogue",
                "No document that no rule could read but that carries printed money figures",
                "No page whose text is written as an instruction to the reader",
            ],
        },
        "hard_stop_rules": sorted(queue.HARD_STOP_RULES),
        "hard_stop_effect": "A hard stop never changes the score; it only blocks the fast lane.",
        "aml_routing_rules": sorted(queue.AML_ROUTING_RULES),
        "lanes": [
            {
                "lane": lane,
                "suggested_verdict": queue.LANE_SUGGESTED_VERDICT[lane],
                "how": _lane_how(lane),
            }
            for lane in get_args(Lane)
        ],
        "ruleset_version": ruleset_version(all_checks()),
    }


@router.get("/explain/method")
def explain_method(settings: SettingsDep) -> dict[str, Any]:
    return build_method(settings)
