"""Proxy-secret gate, per-client rate limits and the job cap (app/access.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.access import AccessSettings, SlidingWindowLimiter
from app.core.cases import new_case
from app.core.config import Settings
from tests.test_api_support import tiny_pdf

SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
H = {"X-Gldb-Proxy-Secret": SECRET}
LOOPBACK = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.9", 50000)


def make_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, client=LOOPBACK, **env: str
) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(f"GLDB_{key.upper()}", value)
    return TestClient(main.create_app(settings), client=client)


def assert_envelope(body: dict, code: str) -> dict:
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["error"]["details"], dict)
    return body["error"]


# --------------------------------------------------------------------------- proxy secret


def test_no_secret_configured_means_no_gate(settings, monkeypatch) -> None:
    c = make_client(settings, monkeypatch)
    assert c.get("/health").status_code == 200
    assert c.get("/rules").status_code == 200


def test_secret_required_for_every_route(settings, monkeypatch) -> None:
    c = make_client(settings, monkeypatch, client=REMOTE, proxy_secret=SECRET)
    for method, path in [
        ("GET", "/rules"),
        ("GET", "/cases"),
        ("POST", "/cases"),
        ("GET", "/docs"),
    ]:
        r = c.request(method, path)
        assert r.status_code == 401, path
        assert_envelope(r.json(), "unauthorized")
        assert "x-request-id" in r.headers
    assert c.get("/rules", headers={"X-Gldb-Proxy-Secret": "wrong"}).status_code == 401
    assert c.get("/rules", headers={"X-Gldb-Proxy-Secret": ""}).status_code == 401
    assert c.get("/rules", headers={"X-Gldb-Proxy-Secret": SECRET + "x"}).status_code == 401
    assert c.get("/rules", headers=H).status_code == 200


def test_non_ascii_secret_header_is_rejected_not_a_500(settings, monkeypatch) -> None:
    c = make_client(settings, monkeypatch, client=REMOTE, proxy_secret=SECRET)
    r = c.get("/rules", headers={"X-Gldb-Proxy-Secret": "café".encode("latin-1")})
    assert r.status_code == 401


def test_health_exception_is_loopback_get_only(settings, monkeypatch) -> None:
    local = make_client(settings, monkeypatch, client=LOOPBACK, proxy_secret=SECRET)
    assert local.get("/health").status_code == 200
    assert local.post("/health").status_code == 401  # not GET
    assert local.get("/health/x").status_code == 401  # not exactly /health
    assert local.get("/rules").status_code == 401
    ipv6 = TestClient(local.app, client=("::1", 1))
    assert ipv6.get("/health").status_code == 200
    remote = TestClient(local.app, client=REMOTE)
    assert remote.get("/health").status_code == 401
    assert remote.get("/health", headers=H).status_code == 200


def test_short_proxy_secret_is_refused_at_startup(settings, monkeypatch) -> None:
    monkeypatch.setenv("GLDB_PROXY_SECRET", "short")
    with pytest.raises(ValueError, match="GLDB_PROXY_SECRET"):
        main.create_app(settings)


def test_unknown_gldb_env_vars_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("GLDB_DATA_DIR", "/tmp/x")
    monkeypatch.setenv("GLDB_SOMETHING_NEW", "1")
    assert AccessSettings(_env_file=None).rate_cases_per_hour == 10


def test_defaults(monkeypatch) -> None:
    a = AccessSettings(_env_file=None)
    assert (a.rate_cases_per_hour, a.rate_reviews_per_min, a.secret_value) == (10, 30, "")


# --------------------------------------------------------------------------- limiter unit


def test_sliding_window_limiter() -> None:
    now = [0.0]
    lim = SlidingWindowLimiter(2, 60, clock=lambda: now[0])
    assert lim.hit("a") == (True, 0)
    now[0] = 10
    assert lim.hit("a") == (True, 0)
    allowed, wait = lim.hit("a")
    assert not allowed and 50 <= wait <= 51
    assert lim.hit("b") == (True, 0)  # other client unaffected
    now[0] = 61  # first hit left the window
    assert lim.hit("a") == (True, 0)
    assert lim.hit("a")[0] is False
    assert SlidingWindowLimiter(0, 60).hit("a") == (True, 0)  # 0 disables


# --------------------------------------------------------------------------- rate limits


def test_cases_rate_limit_429_envelope_and_retry_after(settings, monkeypatch) -> None:
    c = make_client(
        settings, monkeypatch, client=REMOTE, proxy_secret=SECRET, rate_cases_per_hour="3"
    )
    hdr = {**H, "X-Forwarded-For": "198.51.100.1"}
    for _ in range(3):
        assert c.post("/cases", headers=hdr, data={"use_case": "loan"}).status_code == 422
    r = c.post("/cases", headers=hdr, data={"use_case": "loan"})
    assert r.status_code == 429
    err = assert_envelope(r.json(), "rate_limited")
    assert err["details"]["scope"] == "cases" and err["details"]["limit"] == 3
    assert 1 <= int(r.headers["retry-after"]) <= 3600
    assert err["details"]["retry_after_s"] == int(r.headers["retry-after"])
    # another client (X-Forwarded-For forwarded by the proxy) has its own budget
    other = {**H, "X-Forwarded-For": "198.51.100.2"}
    assert c.post("/cases", headers=other, data={"use_case": "loan"}).status_code == 422
    # reads are never limited
    for _ in range(5):
        assert c.get("/cases", headers=hdr).status_code == 200


def test_forwarded_for_spoofing_cannot_evade_the_limit(settings, monkeypatch) -> None:
    c = make_client(
        settings, monkeypatch, client=REMOTE, proxy_secret=SECRET, rate_cases_per_hour="1"
    )
    # the trusted proxy appends the real peer last; attacker-supplied leading entries are ignored
    assert c.post("/cases", headers={**H, "X-Forwarded-For": "1.1.1.1, 9.9.9.9"}).status_code == 422
    r = c.post("/cases", headers={**H, "X-Forwarded-For": "2.2.2.2, 9.9.9.9"})
    assert r.status_code == 429


def test_without_the_secret_forwarded_for_is_not_trusted(settings, monkeypatch) -> None:
    c = make_client(settings, monkeypatch, client=REMOTE, rate_cases_per_hour="1")
    assert c.post("/cases", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 422
    assert c.post("/cases", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_review_rate_limit(settings, monkeypatch) -> None:
    c = make_client(
        settings, monkeypatch, client=REMOTE, proxy_secret=SECRET, rate_reviews_per_min="2"
    )
    body = {"kind": "verdict", "decision": "approve", "case_version": 1}
    for _ in range(2):
        assert c.post("/cases/c_x/review", headers=H, json=body).status_code in (404, 422)
    r = c.post("/cases/c_x/review", headers=H, json=body)
    assert r.status_code == 429
    err = assert_envelope(r.json(), "rate_limited")
    assert err["details"]["scope"] == "reviews"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    # GET of the review log is not limited and POST /cases has its own bucket
    assert c.get("/cases/c_x/review", headers=H).status_code == 404
    assert c.post("/cases", headers=H, data={"use_case": "loan"}).status_code == 422


def test_limits_can_be_disabled_with_zero(settings, monkeypatch) -> None:
    c = make_client(
        settings, monkeypatch, client=REMOTE, proxy_secret=SECRET, rate_cases_per_hour="0"
    )
    for _ in range(15):
        assert c.post("/cases", headers=H, data={"use_case": "loan"}).status_code == 422


# --------------------------------------------------------------------------- job cap


def test_active_job_cap(settings, monkeypatch) -> None:
    c = make_client(
        settings,
        monkeypatch,
        client=REMOTE,
        proxy_secret=SECRET,
        max_active_jobs="1",
        rate_cases_per_hour="0",
    )
    db = c.app.state.db
    case = new_case(settings, "loan")
    db.create_case(case)
    db.create_job(case.id)  # state "queued" counts as active
    for method, path in [
        ("POST", "/cases"),
        ("POST", "/cases/c_1/files"),
        ("POST", "/cases/c_1/rerun"),
    ]:
        r = c.request(method, path, headers=H)
        assert r.status_code == 503, path
        err = assert_envelope(r.json(), "busy")
        assert err["details"]["max_active_jobs"] == 1
        assert int(r.headers["retry-after"]) >= 1
    # other writes and all reads still work while busy
    assert c.get("/cases", headers=H).status_code == 200
    assert c.post("/cases/c_1/review", headers=H, json={}).status_code != 503
    # finished jobs free the slot
    db.update_job(db.latest_job(case.id).id, state="succeeded")
    assert c.post("/cases", headers=H, data={"use_case": "loan"}).status_code == 422


def test_real_upload_passes_through_the_gate(settings, monkeypatch) -> None:
    c = make_client(settings, monkeypatch, client=REMOTE, proxy_secret=SECRET)
    files = [("files", ("a.pdf", tiny_pdf(), "application/pdf"))]
    assert c.post("/cases", data={"use_case": "loan"}, files=files).status_code == 401
    r = c.post("/cases", headers=H, data={"use_case": "loan"}, files=files)
    assert r.status_code == 202
    assert r.headers["x-request-id"]
