"""The calibrated score model (scoring-and-queue.md 4.4a): saturation, double counting, hard stops,
fast lane, partial cases, legacy goldens, the weights file and determinism.

Tests build their own small weights file, so they do not depend on the numbers in the file shipped
with the app (which a refit changes); one test checks that the shipped file loads."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import score_model as sm
from app.checks.registry import make_flag
from app.core.config import Settings, SettingsError
from app.core.models import SCORE_WEIGHTS, CheckRun, Flag
from app.core.provenance import build_provenance, settings_hash
from app.core.testing import make_ctx
from app.findings import group_flags
from app.score import compute_score, run_stage, score_findings

# eight rules from eight different score families, all high by default
EIGHT_FAMILIES = [
    "x_directors",  # identity
    "bs_totals",  # arithmetic
    "id_checksum",  # id_document
    "pdf_bytes_after_signature",  # file_metadata
    "pdf_text_vs_render",  # pixel_forensics
    "inv_duplicate",  # reuse
    "x_invoice_vs_receipts",  # patterns
    "x_sof_support",  # activity
]


def weights_doc(rules: dict[str, float] | None = None, **over: Any) -> dict[str, Any]:
    """A valid weights file. ``rules`` maps ``"rule:severity"`` to p."""
    doc: dict[str, Any] = {
        "schema": sm.WEIGHTS_SCHEMA,
        "version": "test-weights",
        "created": "2026-11-03",
        "fitted_on_synthetic": True,
        "source": {"kind": "synthetic", "seeds": [1]},
        "method": {"estimator": "test"},
        "ruleset_version": None,
        "rules": {
            k: {
                "p": p,
                "precision": min(1.0, p / sm.SINGLE_FINDING_CEILING)
                if isinstance(p, float) and p > 0
                else 0.5,
                "confirmed": 5,
                "dismissed": 1,
            }
            for k, p in (rules or {}).items()
        },
    }
    doc.update(over)
    return doc


def write_weights(tmp_path: Path, rules: dict[str, float] | None = None, **over: Any) -> Path:
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(weights_doc(rules, **over)), encoding="utf-8")
    return path


def settings_for(tmp_path: Path, model: str, weights: Path | None = None) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="fake",
        score_model=model,
        score_weights_path=weights,
    )


def fl(
    rule: str, sev: str | None = None, doc: str = "d1", disc: str | None = None, n: int = 0
) -> Flag:
    return make_flag(
        rule,
        docs=[doc],
        evidence=f"evidence {rule} {doc} {n}",
        explanation=f"Explanation of {rule} {doc} {n}. More.",
        severity=sev,  # type: ignore[arg-type]
        discriminator=disc,
    )


def run(flags: list[Flag], settings: Settings, **kw: Any):  # noqa: ANN202
    ctx = make_ctx(
        [],
        settings=settings,
        check_runs=[CheckRun(rule=f"r{i:02d}", stage="rules", status="ran") for i in range(10)],
        **kw,
    )
    ctx.case.flags = list(flags)
    run_stage(ctx)
    return ctx


@pytest.fixture
def cal(tmp_path: Path) -> Settings:
    return settings_for(tmp_path, "calibrated", write_weights(tmp_path))


@pytest.fixture
def legacy(tmp_path: Path) -> Settings:
    return settings_for(tmp_path, "legacy")


# ------------------------------------------------------------------------------- saturation


def test_four_and_eight_high_findings_both_saturate_in_legacy_but_not_calibrated(
    cal: Settings, legacy: Settings
) -> None:
    four = [fl(r, "high") for r in EIGHT_FAMILIES[:4]]
    eight = [fl(r, "high") for r in EIGHT_FAMILIES]
    # legacy: the cap hides the difference (golden: this is today's behaviour)
    assert run(four, legacy).case.score.value == 100  # type: ignore[union-attr]
    assert run(eight, legacy).case.score.value == 100  # type: ignore[union-attr]
    one, two, v4, v8 = (run(fs, cal).case.score.value for fs in ([four[0]], four[:2], four, eight))  # type: ignore[union-attr]
    assert 0 < one < two < v4 < v8 <= 100
    assert v4 < 100 and v8 < 100  # neither reaches the cap, so a bad case differs from a worse one


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8])
def test_score_rises_with_every_extra_independent_finding(cal: Settings, n: int) -> None:
    fewer = run([fl(r, "high") for r in EIGHT_FAMILIES[: n - 1]], cal).case.score.value  # type: ignore[union-attr]
    more = run([fl(r, "high") for r in EIGHT_FAMILIES[:n]], cal).case.score.value  # type: ignore[union-attr]
    assert more > fewer


def test_pure_function_ranks_four_below_eight_and_stays_below_the_cap() -> None:
    def val(count: int) -> int:
        items = [sm.FindingInput((r,), "high", ((r, "high"),)) for r in EIGHT_FAMILIES[:count]]
        return sm.calibrated_value(items, None)

    values = [val(n) for n in range(0, 9)]
    assert values == sorted(set(values))  # strictly increasing
    assert values[0] == 0 and values[-1] < 100


def test_one_finding_is_never_high_and_one_family_cannot_reach_high() -> None:
    assert sm.SINGLE_FINDING_CEILING < 0.60 and sm.FAMILY_CAP < 0.60
    # even ten identity findings at the largest allowed p stay below the High band
    fams = {"identity": [sm.MAX_P] * 10}
    assert sm.score_value(sm.combine(fams)) < 60


# ----------------------------------------------------------------------- double counting


def test_one_name_error_is_one_finding_and_counts_once(cal: Settings) -> None:
    # the same person's name wrong in three places: grouped by discriminator into one finding
    flags = [
        fl("x_entity", "high", doc="d1", disc="acme"),
        fl("fs_entity", "medium", doc="d2", disc="acme"),
        fl("bs_holder", "high", doc="d3", disc="acme"),
    ]
    ctx = run(flags, cal)
    assert len(ctx.case.findings) == 1
    alone = run([fl("x_entity", "high", disc="acme")], cal).case.score.value  # type: ignore[union-attr]
    assert ctx.case.score.value == alone  # type: ignore[union-attr]


def test_several_name_findings_count_less_than_independent_evidence(cal: Settings) -> None:
    names = [fl("x_directors", "high", doc=f"d{n}", disc=f"person{n}") for n in range(3)]
    other = [fl(r, "high") for r in EIGHT_FAMILIES[:3]]
    same_family = run(names, cal).case.score.value  # type: ignore[union-attr]
    different = run(other, cal).case.score.value  # type: ignore[union-attr]
    assert run(names, cal).case.score.value > run(names[:1], cal).case.score.value  # type: ignore[union-attr]
    assert same_family < different  # three roots of one cause < three separate causes
    assert same_family < 60  # and never alone a High case


def test_family_discount_is_applied_in_order_of_strength() -> None:
    # two findings of one family: the second counts d of its strength
    p = sm.default_p("high")
    both = sm.combine({"identity": [p, p]})
    assert both == pytest.approx(1 - (1 - p) * (1 - p * sm.WITHIN_FAMILY_DISCOUNT))
    assert both < 1 - (1 - p) ** 2
    assert sm.combine({"a": [p], "b": [p]}) == pytest.approx(1 - (1 - p) ** 2)


def test_info_findings_add_nothing(cal: Settings) -> None:
    flags = [fl("x_directors", "high"), fl("ai_provenance_hint", "info")]
    base = run([fl("x_directors", "high")], cal).case.score.value  # type: ignore[union-attr]
    assert run(flags, cal).case.score.value == base  # type: ignore[union-attr]
    assert run([fl("ai_provenance_hint", "info")], cal).case.score.value == 0  # type: ignore[union-attr]


# ------------------------------------------------------- weights from the file, defaults


def test_rule_weight_from_file_and_default_for_unlisted_rules(tmp_path: Path) -> None:
    path = write_weights(tmp_path, {"x_directors:high": 0.50, "bs_dates:medium": 0.02})
    s = settings_for(tmp_path, "calibrated", path)
    assert run([fl("x_directors", "high")], s).case.score.value == 50  # type: ignore[union-attr]
    assert run([fl("bs_dates", "medium")], s).case.score.value == 2  # type: ignore[union-attr]
    # a rule the file does not name keeps today's points for its severity
    assert run([fl("bs_totals", "high")], s).case.score.value == SCORE_WEIGHTS["high"]  # type: ignore[union-attr]
    assert run([fl("inv_sequence", "low")], s).case.score.value == SCORE_WEIGHTS["low"]  # type: ignore[union-attr]
    # severity matters: the same rule at another severity falls back to that severity's default
    assert run([fl("x_directors", "medium")], s).case.score.value == SCORE_WEIGHTS["medium"]  # type: ignore[union-attr]


def test_finding_weight_is_standalone_points_and_never_zero_for_a_flag(tmp_path: Path) -> None:
    path = write_weights(tmp_path, {"bs_dates:medium": 0.005})
    s = settings_for(tmp_path, "calibrated", path)
    ctx = run([fl("bs_dates", "medium"), fl("x_directors", "high")], s)
    by_rule = {f.rules[0]: f.weight for f in ctx.case.findings}
    assert by_rule["bs_dates"] == 1  # at least 1: weight 0 would read as "no flag" elsewhere
    assert by_rule["x_directors"] == SCORE_WEIGHTS["high"]


def test_info_finding_keeps_weight_zero(cal: Settings) -> None:
    ctx = run([fl("ai_provenance_hint", "info")], cal)
    assert [f.weight for f in ctx.case.findings] == [0]


# ------------------------------------------------- hard stops, fast lane, partial, bands


def test_hard_stop_still_blocks_the_fast_lane_whatever_the_score(tmp_path: Path) -> None:
    # a weight so small that the score is 2: the hard-stop rule must still keep the case out of fast
    s = settings_for(tmp_path, "calibrated", write_weights(tmp_path, {"x_directors:medium": 0.02}))
    ctx = run([fl("x_directors", "medium")], s)
    assert ctx.case.score.value == 2  # type: ignore[union-attr]
    assert ctx.case.findings[0].hard_stop is True
    assert ctx.case.lane != "fast"
    # the same score from a plain rule is fast-lane eligible
    s2 = settings_for(tmp_path, "calibrated", write_weights(tmp_path, {"bs_dates:medium": 0.02}))
    assert run([fl("bs_dates", "medium")], s2).case.lane == "fast"


def test_fast_lane_threshold_and_semantics_are_unchanged(cal: Settings, legacy: Settings) -> None:
    for s in (cal, legacy):
        assert run([], s).case.lane == "fast"
        assert run([fl("x_directors", "high")], s).case.lane != "fast"
    # four low findings: legacy 20 (still fast), calibrated at default 0.05 each also within 20
    lows = [fl(r, "low") for r in ("inv_sequence", "bs_round_numbers", "font_mix", "pdf_modified")]
    assert run(lows, legacy).case.score.value == 20  # type: ignore[union-attr]
    assert run(lows, cal).case.score.value <= 20  # type: ignore[union-attr]


def test_aml_routing_and_semantic_gate_follow_weight_not_score(tmp_path: Path) -> None:
    s = settings_for(
        tmp_path, "calibrated", write_weights(tmp_path, {"bs_pass_through:medium": 0.005})
    )
    ctx = run([fl("bs_pass_through", "medium")], s, use_case="loan")
    assert ctx.case.findings[0].weight == 1
    assert ctx.case.lane == "aml"


def test_partial_case_stays_partial_and_keeps_its_floor(cal: Settings) -> None:
    ctx = run([], cal, partial=True, partial_reasons=["stage_failed:rules"])
    sc = ctx.case.score
    assert sc is not None and sc.partial and sc.value == 0
    assert ctx.case.lane != "fast"  # a partial case never takes the fast lane
    # the queue sort still ranks a partial case as at least medium (db.PARTIAL_PRIORITY_FLOOR)
    from app.core.db import PARTIAL_PRIORITY_FLOOR

    assert PARTIAL_PRIORITY_FLOOR == 30


def test_bands_use_the_same_thresholds(cal: Settings) -> None:
    from app.score import band_for

    assert [band_for(v) for v in (0, 29, 30, 59, 60, 100)] == [
        "low",
        "low",
        "medium",
        "medium",
        "high",
        "high",
    ]
    ctx = run([fl(r, "high") for r in EIGHT_FAMILIES[:5]], cal)
    assert ctx.case.score.band == "high"  # type: ignore[union-attr]
    assert run([fl("x_directors", "high")], cal).case.score.band == "medium"  # type: ignore[union-attr]


# ------------------------------------------------------------------------ legacy goldens


@pytest.mark.parametrize(
    ("severities", "value"),
    [
        ([], 0),
        (["high"], 30),
        (["high", "medium"], 45),
        (["high", "high"], 60),
        (["medium"] * 4, 60),
        (["medium", "low", "low", "low"], 30),
        (["high"] * 4, 100),
        (["high"] * 8, 100),
    ],
)
def test_legacy_mode_is_todays_sum_and_cap(
    legacy: Settings, severities: list[str], value: int
) -> None:
    flags = [fl("bs_dates", sev, doc=f"d{n}", n=n) for n, sev in enumerate(severities)]
    ctx = run(flags, legacy)
    assert ctx.case.score.value == value  # type: ignore[union-attr]
    assert [f.weight for f in ctx.case.findings] == [
        SCORE_WEIGHTS[s] for s in sorted(severities, key=["high", "medium", "low"].index)
    ]


def test_legacy_stage_equals_compute_score_on_random_cases(legacy: Settings) -> None:
    rng = random.Random(7)
    rules = ["bs_dates", "bs_totals", "x_directors", "inv_sequence", "id_checksum", "x_entity"]
    for _ in range(40):
        flags = [
            fl(rng.choice(rules), rng.choice(["high", "medium", "low", "info"]), doc=f"d{i}", n=i)
            for i in range(rng.randint(0, 9))
        ]
        ctx = run(flags, legacy)
        findings = group_flags(ctx.case, flags)
        expected = compute_score(findings, partial=False, reasons=[])
        assert ctx.case.score.value == expected.value  # type: ignore[union-attr]
        again, same = score_findings(findings, flags, partial=False, reasons=[], settings=legacy)
        assert again == expected and same == findings  # legacy never rewrites a finding


def test_default_provenance_names_the_model(legacy: Settings) -> None:
    ctx = run([], legacy)
    prov = build_provenance(ctx)
    assert prov.score_model == {"name": "legacy", "version": "1"}


# ---------------------------------------------------------------------------- determinism


def test_order_of_flags_never_changes_the_calibrated_score(cal: Settings) -> None:
    rng = random.Random(3)
    rules = EIGHT_FAMILIES + ["bs_dates", "x_entity", "fs_entity", "bs_running_balance"]
    flags = [
        fl(rng.choice(rules), rng.choice(["high", "medium", "low"]), doc=f"d{i}", n=i)
        for i in range(14)
    ]
    results = set()
    for _ in range(12):
        rng.shuffle(flags)
        ctx = run(list(flags), cal)
        results.add(
            (ctx.case.score.value, tuple((f.group_key, f.weight) for f in ctx.case.findings))
        )  # type: ignore[union-attr]
    assert len(results) == 1


def test_rerun_is_identical(cal: Settings) -> None:
    flags = [fl(r, "high") for r in EIGHT_FAMILIES[:5]] + [fl("bs_dates", "low")]
    a = run(list(flags), cal).case
    b = run(list(flags), cal).case
    assert a.score == b.score and a.findings == b.findings


# ------------------------------------------------------------------------ weights file


def test_shipped_default_weights_load_and_say_synthetic() -> None:
    lw = sm.load_weights(None)
    assert lw.weights.schema_id == sm.WEIGHTS_SCHEMA
    assert lw.weights.fitted_on_synthetic is True
    assert lw.weights.source["kind"] == "synthetic"
    assert all(0 < rw.p <= sm.SINGLE_FINDING_CEILING for rw in lw.weights.rules.values())


def test_valid_file_loads_and_hash_follows_content(tmp_path: Path) -> None:
    a = sm.load_weights(write_weights(tmp_path, {"x_directors:high": 0.3}))
    path = write_weights(tmp_path, {"x_directors:high": 0.31})
    b = sm.load_weights(path)
    assert a.sha256 != b.sha256 and b.p("x_directors", "high") == 0.31
    assert b.p("x_directors", "medium") == SCORE_WEIGHTS["medium"] / 100


BAD_FILES: list[tuple[str, Any, str]] = [
    ("not json", b"{nope", "not valid JSON"),
    ("top level list", b"[]", "top level"),
    ("wrong schema", json.dumps(weights_doc(schema="other/9")).encode(), "unsupported schema"),
    (
        "missing field",
        json.dumps({k: v for k, v in weights_doc().items() if k != "version"}).encode(),
        "version",
    ),
    ("extra field", json.dumps(weights_doc(surprise=1)).encode(), "surprise"),
    ("bad key", json.dumps(weights_doc({"x_directors": 0.3})).encode(), "must be"),
    ("info severity", json.dumps(weights_doc({"x_directors:info": 0.3})).encode(), "must be"),
    (
        "unknown rule",
        json.dumps(weights_doc({"no_such_rule:high": 0.3})).encode(),
        "not a registered check",
    ),
    ("p zero", json.dumps(weights_doc({"x_directors:high": 0.0})).encode(), "p"),
    ("p too large", json.dumps(weights_doc({"x_directors:high": 0.99})).encode(), "p"),
    ("p negative", json.dumps(weights_doc({"x_directors:high": -0.1})).encode(), "p"),
    ("p not a number", json.dumps(weights_doc({"x_directors:high": "big"})).encode(), "p"),
    ("bad date", json.dumps(weights_doc(created="yesterday")).encode(), "created"),
    (
        "no synthetic flag",
        json.dumps({k: v for k, v in weights_doc().items() if k != "fitted_on_synthetic"}).encode(),
        "fitted_on_synthetic",
    ),
]


@pytest.mark.parametrize(("name", "raw", "needle"), BAD_FILES, ids=[b[0] for b in BAD_FILES])
def test_bad_weights_file_is_refused_loudly(
    tmp_path: Path, name: str, raw: bytes, needle: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(sm.ScoreWeightsError, match=needle):
        sm.load_weights(path)
    s = settings_for(tmp_path, "calibrated", path)
    with pytest.raises(SettingsError, match="GLDB_SCORE_MODEL=calibrated"):
        s.validate_startup()
    # and scoring itself stops rather than falling back to some other weights
    with pytest.raises(sm.ScoreWeightsError):
        score_findings([], [], partial=False, reasons=[], settings=s)


def test_missing_file_and_directory_are_refused(tmp_path: Path) -> None:
    with pytest.raises(sm.ScoreWeightsError, match="not found"):
        sm.load_weights(tmp_path / "absent.json")
    with pytest.raises(sm.ScoreWeightsError):
        sm.load_weights(tmp_path)  # a directory


def test_legacy_mode_never_reads_the_weights_file(tmp_path: Path) -> None:
    s = settings_for(tmp_path, "legacy", tmp_path / "absent.json")
    s.validate_startup()  # no error: the file is not used
    assert run([fl("x_directors", "high")], s).case.score.value == 30  # type: ignore[union-attr]


def test_edited_file_is_picked_up_without_a_restart(tmp_path: Path) -> None:
    path = write_weights(tmp_path, {"x_directors:high": 0.20})
    s = settings_for(tmp_path, "calibrated", path)
    assert run([fl("x_directors", "high")], s).case.score.value == 20  # type: ignore[union-attr]
    path.write_text(
        json.dumps(weights_doc({"x_directors:high": 0.4, "bs_dates:low": 0.01})), encoding="utf-8"
    )
    assert run([fl("x_directors", "high")], s).case.score.value == 40  # type: ignore[union-attr]


# --------------------------------------------------------------- provenance, settings, API


def test_provenance_records_model_and_weights(tmp_path: Path) -> None:
    path = write_weights(tmp_path, {"x_directors:high": 0.3})
    s = settings_for(tmp_path, "calibrated", path)
    prov = build_provenance(run([fl("x_directors", "high")], s))
    lw = sm.load_weights(path)
    assert prov.score_model == {
        "name": "calibrated",
        "version": sm.MODEL_VERSIONS["calibrated"],
        "weights_version": "test-weights",
        "weights_sha256": lw.sha256,
        "weights_source": "synthetic",
        "fitted_on_synthetic": True,
    }
    dumped = prov.model_dump(mode="json")
    assert dumped["score_model"]["name"] == "calibrated"


def test_settings_hash_changes_with_model_and_weights_only_when_not_legacy(tmp_path: Path) -> None:
    legacy = settings_for(tmp_path, "legacy")
    h_legacy = settings_hash(legacy)
    assert "score_model" not in legacy.result_settings()  # legacy hashes stay byte-identical
    c1 = settings_for(tmp_path, "calibrated", write_weights(tmp_path, {"x_directors:high": 0.3}))
    h1 = settings_hash(c1)
    c2 = settings_for(tmp_path, "calibrated", write_weights(tmp_path, {"x_directors:high": 0.31}))
    assert len({h_legacy, h1, settings_hash(c2)}) == 3


def test_explain_method_states_the_model_and_the_synthetic_note(tmp_path: Path) -> None:
    from app.api.explain import build_method

    cal = build_method(settings_for(tmp_path, "calibrated", write_weights(tmp_path)))["score"]
    assert cal["model"] == "calibrated" and cal["combination"] == "noisy_or"
    assert cal["fitted_on_synthetic"] is True and cal["ordering_signal_only"] is True
    assert "synthetic" in cal["note"] and "not as a probability" in cal["note"]
    assert cal["bands"] == {"medium_from": 30, "high_from": 60} and cal["cap"] == 100
    leg = build_method(settings_for(tmp_path, "legacy"))["score"]
    assert leg["model"] == "legacy" and leg["combination"] == "sum"
    assert "fitted_on_synthetic" not in leg and "not a probability" in leg["note"]
    # without settings the section keeps its original keys only
    assert set(build_method()["score"]) == {"weights", "cap", "bands", "counting"}


def test_monitoring_status_reports_model_and_broken_file(tmp_path: Path) -> None:
    ok = sm.status(settings_for(tmp_path, "calibrated", write_weights(tmp_path)))
    assert ok["ok"] and ok["name"] == "calibrated" and ok["fitted_on_synthetic"] is True
    assert ok["ordering_signal_only"] is True and "synthetic" in ok["note"]
    broken = sm.status(settings_for(tmp_path, "calibrated", tmp_path / "absent.json"))
    assert broken["ok"] is False and "not found" in broken["error"]
    assert sm.status(settings_for(tmp_path, "legacy"))["name"] == "legacy"


def test_explain_method_route_and_monitoring_route(tmp_path: Path) -> None:
    from app.main import create_app

    s = settings_for(tmp_path, "calibrated", write_weights(tmp_path))
    s = s.model_copy(update={"db_path": str(tmp_path / "t.sqlite3")})
    with TestClient(create_app(s)) as client:
        body = client.get("/explain/method").json()
        assert body["score"]["model"] == "calibrated"
        assert "synthetic" in body["score"]["note"]
        mon = client.get("/monitoring").json()
        assert mon["score_model"]["name"] == "calibrated"
        assert mon["score_model"]["fitted_on_synthetic"] is True


def test_empty_weights_path_in_the_environment_means_the_shipped_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GLDB_SCORE_WEIGHTS_PATH", "")
    monkeypatch.setenv("GLDB_SCORE_MODEL", "calibrated")
    s = Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake")
    assert s.score_weights_path is None and s.score_model == "calibrated"
    s.validate_startup()
    monkeypatch.setenv("GLDB_SCORE_MODEL", "nonsense")
    with pytest.raises(ValueError):
        Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake")


def test_the_documented_examples_hold_for_the_default_weights() -> None:
    """The table in docs/scoring-and-queue.md 4.4a (severity defaults, no weights file)."""

    def val(rules_sev: list[tuple[str, str]]) -> int:
        return sm.calibrated_value(
            [sm.FindingInput((r,), s, ((r, s),)) for r, s in rules_sev], None
        )

    fams = EIGHT_FAMILIES
    assert val([(fams[0], "high")]) == 30
    assert val([(fams[0], "high"), (fams[1], "high")]) == 51
    assert val([("x_directors", "high"), ("x_entity", "high")]) == 41  # one family
    assert val([(r, "high") for r in fams[:4]]) == 76
    assert val([(r, "high") for r in fams]) == 94
    assert val([(r, "medium") for r in fams[:4]]) == 48


def test_shipped_weights_keep_a_single_high_finding_in_the_medium_band() -> None:
    lw = sm.load_weights(None)
    for key in lw.weights.rules:
        if key.endswith(":high"):
            items = [
                sm.FindingInput(
                    (key.rpartition(":")[0],), "high", ((key.rpartition(":")[0], "high"),)
                )
            ]
            assert 30 <= sm.calibrated_value(items, lw) < 60, key
    # and no single finding of any kind reaches the High band
    assert max(rw.p for rw in lw.weights.rules.values()) < 0.60
