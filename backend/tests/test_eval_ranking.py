"""Ranking test (docs 4.5): measures on hand-made queues, assignment structure, and the claim that the
eval's own priority order equals ``Database.list_cases(sort="priority")``."""

from __future__ import annotations

from datetime import timedelta

from app.core.db import Database, new_case_id
from app.core.models import Case, Coverage, Score
from app.core.testing import FIXED_NOW, FIXED_TODAY
from scripts._eval import metrics as mx
from scripts._eval import ranking as rk
from scripts._gen.schema import QueueFile, QueueSlot


def case(
    i: int, score: int, fraud: bool, upload: int | None = None, flags: int = 1, cov: float = 0.9
) -> dict:
    return {
        "slot": i,
        "fraud": fraud,
        "score": score,
        "coverage_ratio": cov,
        "upload_index": i if upload is None else upload,
        "n_flags": flags,
    }


def test_perfect_queue_puts_every_fraud_case_first() -> None:
    queue = [case(i, 100 - i, i < 6) for i in range(30)]
    r = rk.evaluate_queue(queue, share=0.2)
    assert r["top5_fraud"] == 5 and r["top10_fraud"] == 6
    assert r["P@5"] == 1.0 and r["P@5/ideal"] == 1.0 and r["R@10"] == 1.0
    assert r["lift@5"] == 5.0 and r["auroc"] == 1.0
    assert r["first_top_down"] == 1


def test_inverted_queue_puts_fraud_last_and_upload_order_is_independent() -> None:
    queue = [case(i, i, i >= 24, upload=i) for i in range(30)]
    queue = [dict(q, score=29 - q["score"]) for q in queue]  # fraud (slots 24..29) now score lowest
    r = rk.evaluate_queue(queue, share=0.2)
    assert r["top5_fraud"] == 0 and r["auroc"] == 0.0
    assert r["first_top_down"] == 25 and r["first_upload"] == 25  # fraud last in both orders here
    assert r["sim_minutes_top_down"] == r["sim_minutes_upload"]


def test_simulated_minutes_use_the_assumed_model() -> None:
    queue = [case(0, 10, False, flags=2), case(1, 5, True, flags=4), case(2, 1, False, flags=0)]
    r = rk.evaluate_queue(queue, share=0.33)
    # top-down: case 0 (2 flags) then case 1 (4 flags) = (4 + 1.5) + (4 + 3.0)
    assert r["sim_minutes_top_down"] == 4.0 + 0.75 * 2 + 4.0 + 0.75 * 4
    assert r["first_top_down"] == 2


def test_assignments_cover_all_shares_with_nested_fraud_sets() -> None:
    assigns = rk.assignments(4002, 30)
    assert len(assigns) == 4 * 5
    k = {a.share: len(a.fraud_slots) for a in assigns}
    assert k == {0.1: 3, 0.2: 6, 0.3: 9, 0.5: 15}
    for rep in range(5):
        by_share = {a.share: a for a in assigns if a.rep == rep}
        assert (
            set(by_share[0.1].fraud_slots)
            <= set(by_share[0.2].fraud_slots)
            <= set(by_share[0.5].fraud_slots)
        )
        kinds = set(by_share[0.5].kind_by_slot.values())
        assert kinds == {"deck", "forger", "single"}


def test_slot_plan_mix_and_determinism() -> None:
    plan = rk.slot_plan(4002)
    names = [s for _i, s in plan]
    assert names.count("loan") == 12 and names.count("kyc") == 9 and names.count("aml") == 9
    assert plan == rk.slot_plan(4002) and plan != rk.slot_plan(7)


def test_sweep_resamples_cached_results_and_reports_every_share() -> None:
    slots = [
        QueueSlot(
            slot=i,
            scenario="loan",
            company_index=100 + i,
            upload_index=i,
            cases={},
            single_tamper="T01",
        )
        for i in range(10)
    ]
    qf = QueueFile(
        queue_id="q1",
        seed=1,
        slots=slots,
        shares=[0.1, 0.5],
        reps=2,
        assignments=rk.assignments(1, 10, [0.1, 0.5], 2),
    )

    def res(score: int) -> dict:
        return {
            "score": score,
            "coverage_ratio": 0.9,
            "flags": [{"severity": "high"}] * (1 if score else 0),
        }

    results = {
        i: {"clean": res(0), "deck": res(90), "forger": res(10), "single": res(60)}
        for i in range(10)
    }
    sweep = rk.run_sweep(results, qf)
    assert [s["share"] for s in sweep["shares"]] == [0.1, 0.5]
    for row in sweep["shares"]:
        assert (
            row["all"]["top5_fraud"]["n"] == 2
            and row["detectable"]["fraud"]["mean"] <= row["all"]["fraud"]["mean"]
        )
    assert len(rk.movement_lines(sweep)) == 2
    assert rk.CAVEATS and "upper bound" in rk.CAVEATS[0]


def test_eval_priority_order_equals_database_priority_sort(db: Database) -> None:
    specs = [(40, 0.9), (90, 0.9), (40, 0.5), (40, 0.5), (None, None), (90, 0.2)]
    ids = []
    cases = []
    for i, (score, cov) in enumerate(specs):
        cid = new_case_id()
        c = Case(
            id=cid,
            use_case="loan",
            today=FIXED_TODAY,
            created_at=FIXED_NOW + timedelta(minutes=i),
            updated_at=FIXED_NOW + timedelta(minutes=i),
            status="open",
        )
        if score is not None:
            c.score = Score(value=score, band="low")
            c.coverage = Coverage(applicable=10, ran=int(cov * 10), ratio=cov)
        db.create_case(c)
        db.save_case(c, touch=False)
        ids.append(cid)
        cases.append({"score": score, "coverage_ratio": cov, "upload_index": i})
    rows, _total = db.list_cases(sort="priority", page_size=50)
    assert [r["id"] for r in rows] == [ids[i] for i in mx.priority_order(cases)]
