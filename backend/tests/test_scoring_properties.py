"""Property test 18: for random flag sets the score is explained by the flags
(scoring-and-queue.md section 6). Seeded ``random``; no wall clock."""

from __future__ import annotations

import random

import pytest

from app.checks.registry import make_flag
from app.core.models import SCORE_WEIGHTS, CheckRun, Flag, Region
from app.core.testing import make_ctx, make_doc
from app.findings import FAMILIES
from app.queue import lane_for
from app.score import run_stage

SEVERITIES = ["high", "medium", "low", "info"]
RULES = sorted({r for fam in FAMILIES for r in fam.rules}) + ["bs_dates", "x_other_rule"]
DISCS = [None, None, "lim", "tan", "acme"]


def random_flags(rng: random.Random, count: int) -> list[Flag]:
    flags: dict[str, Flag] = {}
    for n in range(count):
        docs = rng.sample([f"d{k}" for k in range(1, 7)], rng.randint(0, 2))
        region = None
        if rng.random() < 0.5:
            region = Region(
                file_id=rng.choice(["f1", "f2"]),
                page=rng.randint(1, 2),
                x=rng.choice([0.0, 0.25, 0.5]),
                y=rng.choice([0.0, 0.25, 0.5]),
                w=rng.choice([0.25, 0.5]),
                h=rng.choice([0.25, 0.5]),
            )
        flag = make_flag(
            rng.choice(RULES),
            docs=docs,
            evidence=f"evidence {n}",
            explanation=f"Explanation {n}. More text.",
            row=rng.choice([None, 0, 1, 2]),
            table="transactions",
            region=region,
            severity=rng.choice(SEVERITIES),  # type: ignore[arg-type]
            discriminator=rng.choice(DISCS),
        )
        flags.setdefault(flag.id, flag)  # the runner makes ids unique; keep the generator honest
    return list(flags.values())


@pytest.mark.parametrize("seed", range(40))
def test_every_flag_is_counted_once_and_score_traces_to_flags(seed: int) -> None:
    rng = random.Random(seed)
    flags = random_flags(rng, rng.randint(0, 25))
    docs = [make_doc("invoice", id=f"d{k}", file_id=f"f{1 + k % 2}") for k in range(1, 7)]
    ctx = make_ctx(docs, check_runs=[CheckRun(rule="r", stage="rules", status="ran")])
    ctx.case.flags = flags
    run_stage(ctx)
    case = ctx.case
    assert case.score is not None

    members = [fid for f in case.findings for fid in f.flag_ids]
    assert sorted(members) == sorted(f.id for f in flags)  # exactly one finding per flag
    assert case.score.value == min(100, sum(f.weight for f in case.findings))
    by_id = {f.id: f for f in flags}
    for finding in case.findings:
        assert finding.flag_ids, "no finding without flags"
        top = min(SEVERITIES.index(by_id[i].severity) for i in finding.flag_ids)
        assert finding.severity == SEVERITIES[top]
        assert finding.weight == SCORE_WEIGHTS[finding.severity]
    if not any(f.severity != "info" for f in flags):
        assert case.score.value == 0  # no score point without a weighted flag
    assert [f.id for f in case.findings] == [f"fnd_{n}" for n in range(1, len(case.findings) + 1)]
    if case.lane == "fast":
        assert case.score.value <= 20 and not any(f.hard_stop for f in case.findings)
    assert lane_for(case) == case.lane


@pytest.mark.parametrize("seed", range(10))
def test_shuffling_never_changes_the_result(seed: int) -> None:
    rng = random.Random(1000 + seed)
    flags = random_flags(rng, 20)

    def run(order: list[Flag]):  # noqa: ANN202
        ctx = make_ctx([make_doc("invoice", id="d1")], check_runs=[])
        ctx.case.flags = order
        run_stage(ctx)
        return [f.model_dump() for f in ctx.case.findings], ctx.case.score, ctx.case.top_finding

    base = run(flags)
    for _ in range(3):
        shuffled = flags[:]
        rng.shuffle(shuffled)
        assert run(shuffled) == base
