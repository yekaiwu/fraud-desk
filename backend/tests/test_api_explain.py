"""GET /explain/method: the scoring and routing method, read from the engine's own constants."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import queue
from app.checks.registry import all_checks
from app.core import models
from app.core.config import Settings
from app.core.provenance import ruleset_version

SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
REMOTE = ("203.0.113.9", 50000)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(main.create_app(settings))


def test_shape_and_values_come_from_the_constants(client: TestClient) -> None:
    r = client.get("/explain/method")
    assert r.status_code == 200 and "x-request-id" in r.headers
    body = r.json()
    assert list(body) == [
        "score",
        "coverage",
        "fast_lane",
        "hard_stop_rules",
        "hard_stop_effect",
        "aml_routing_rules",
        "lanes",
        "ruleset_version",
    ]
    assert body["score"] == {
        "weights": models.SCORE_WEIGHTS,
        "cap": models.SCORE_CAP,
        "bands": {
            "medium_from": models.BAND_THRESHOLDS["medium"],
            "high_from": models.BAND_THRESHOLDS["high"],
        },
        "counting": "Each finding counts once, however many flags or documents it covers.",
    }
    assert body["coverage"]["low_coverage_ratio"] == queue.LOW_COVERAGE_RATIO
    assert body["coverage"]["fast_lane_min_coverage"] == queue.FAST_LANE_MIN_COVERAGE
    assert body["fast_lane"]["max_score"] == queue.FAST_LANE_MAX_SCORE
    assert any("80%" in line for line in body["fast_lane"]["requires"])
    assert body["hard_stop_rules"] == sorted(queue.HARD_STOP_RULES)
    assert body["aml_routing_rules"] == sorted(queue.AML_ROUTING_RULES)
    assert body["ruleset_version"] == ruleset_version(all_checks())


def test_lanes_cover_every_lane_with_the_suggested_verdict(client: TestClient) -> None:
    lanes = client.get("/explain/method").json()["lanes"]
    assert [x["lane"] for x in lanes] == ["edd", "aml", "loan", "fast"]
    assert {x["lane"]: x["suggested_verdict"] for x in lanes} == {
        "edd": "refer_edd",
        "aml": "escalate_aml",
        "loan": "reject_or_escalate",
        "fast": "approve",
    }
    assert all(isinstance(x["how"], str) and x["how"] for x in lanes)
    by_lane = {x["lane"]: x["how"] for x in lanes}
    assert by_lane["loan"].startswith("Loan cases are routed here")
    assert "x_remitter_vs_buyer" in by_lane["aml"]


def test_the_endpoint_follows_the_constants(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(models.SCORE_WEIGHTS, "high", 41)
    monkeypatch.setitem(models.BAND_THRESHOLDS, "high", 70)
    monkeypatch.setattr(models, "SCORE_CAP", 90)
    monkeypatch.setattr(queue, "FAST_LANE_MIN_COVERAGE", 0.9)
    monkeypatch.setattr(queue, "FAST_LANE_MAX_SCORE", 10)
    monkeypatch.setattr(queue, "LOW_COVERAGE_RATIO", 0.5)
    monkeypatch.setattr(queue, "HARD_STOP_RULES", frozenset({"zz_rule", "aa_rule"}))
    monkeypatch.setattr(queue, "AML_ROUTING_RULES", frozenset({"zz_rule"}))
    monkeypatch.setitem(queue.LANE_SUGGESTED_VERDICT, "fast", "request_documents")
    body = client.get("/explain/method").json()
    assert body["score"]["weights"]["high"] == 41
    assert body["score"]["bands"]["high_from"] == 70
    assert body["score"]["cap"] == 90
    assert body["coverage"]["fast_lane_min_coverage"] == 0.9
    assert body["coverage"]["low_coverage_ratio"] == 0.5
    assert any("90%" in line for line in body["fast_lane"]["requires"])
    assert any("10 or less" in line for line in body["fast_lane"]["requires"])
    assert body["hard_stop_rules"] == ["aa_rule", "zz_rule"]
    assert body["aml_routing_rules"] == ["zz_rule"]
    assert {x["lane"]: x["suggested_verdict"] for x in body["lanes"]}["fast"] == "request_documents"


def test_score_cap_is_the_one_compute_score_uses() -> None:
    from app.core.models import Finding
    from app.score import compute_score

    def finding(i: int) -> Finding:
        return Finding(
            id=f"fnd_{i}",
            group_key=f"k{i}",
            title="t",
            severity="high",
            flag_ids=[],
            document_ids=[],
            rules=["r"],
            weight=models.SCORE_WEIGHTS["high"],
            summary="s",
            hard_stop=True,
        )

    score = compute_score([finding(i) for i in range(10)], partial=False, reasons=[])
    assert score.value == models.SCORE_CAP


def test_it_is_behind_the_access_gate_like_every_route(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    c = TestClient(main.create_app(settings), client=REMOTE)
    r = c.get("/explain/method")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    ok = c.get("/explain/method", headers={"X-Gldb-Proxy-Secret": SECRET})
    assert ok.status_code == 200 and ok.json()["score"]["cap"] == models.SCORE_CAP


def test_is_listed_as_a_router_and_needs_no_database(settings: Settings) -> None:
    app = main.create_app(settings)
    assert "explain" in app.state.api_modules
