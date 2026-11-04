"""Proxy-secret gate, per-client rate limits and the job cap (app/access.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

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


def test_limit_is_per_signed_in_account_not_per_address(settings, monkeypatch) -> None:
    """A room of people on one Wi-Fi shares one address; each signed-in account has its own allowance."""
    c = make_client(
        settings, monkeypatch, client=REMOTE, proxy_secret=SECRET, rate_cases_per_hour="2"
    )
    room = {**H, "X-Forwarded-For": "198.51.100.1"}
    for _ in range(2):
        assert (
            c.post(
                "/cases", headers={**room, "X-Gldb-Reviewer": "user1"}, data={"use_case": "loan"}
            ).status_code
            == 422
        )
    r = c.post("/cases", headers={**room, "X-Gldb-Reviewer": "user1"}, data={"use_case": "loan"})
    assert r.status_code == 429
    for who in ("user2", "user3"):
        assert (
            c.post(
                "/cases", headers={**room, "X-Gldb-Reviewer": who}, data={"use_case": "loan"}
            ).status_code
            == 422
        )


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


def test_an_account_that_used_its_daily_budget_cannot_start_another_analysis(
    settings, monkeypatch
) -> None:
    from decimal import Decimal

    from app.core.costs import DailySpend

    paid = settings.model_copy(
        update={
            "llm_backend": "anthropic",
            "anthropic_api_key": SecretStr("sk-ant-test-fake"),
            "max_usd_per_user_per_day": Decimal("1.00"),
        }
    )
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    app = main.create_app(paid)
    c = TestClient(app, client=REMOTE)
    DailySpend(app.state.db, Decimal("5"), account="user1", user_cap=Decimal("1")).add(Decimal("1"))
    r = c.post("/cases", headers={**H, "X-Gldb-Reviewer": "user1"}, data={"use_case": "loan"})
    assert r.status_code == 429
    err = assert_envelope(r.json(), "user_budget_used")
    assert err["details"]["limit_usd"] == "1.00" and "midnight UTC" in err["message"]
    # another account is not blocked by it (it gets as far as the missing-files validation)
    ok = c.post("/cases", headers={**H, "X-Gldb-Reviewer": "user2"}, data={"use_case": "loan"})
    assert ok.status_code == 422


def test_spending_is_for_administrator_accounts_only(settings, monkeypatch) -> None:
    from decimal import Decimal

    from app.core.costs import DailySpend

    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    monkeypatch.setenv("GLDB_ADMIN_USERS", "boss")
    s = settings.model_copy(update={"admin_users": "boss"})
    app = main.create_app(s)
    c = TestClient(app, client=REMOTE)
    DailySpend(app.state.db, Decimal("5"), account="user1", user_cap=Decimal("1")).add(
        Decimal("0.25")
    )
    DailySpend(app.state.db, Decimal("5"), account="boss", user_cap=Decimal("1")).add(
        Decimal("0.5")
    )
    DailySpend(app.state.db, Decimal("5")).add(Decimal("0.1"))  # started with no account
    for who in ("user1", None):
        hdr = {**H, **({"X-Gldb-Reviewer": who} if who else {})}
        r = c.get("/spending", headers=hdr)
        assert r.status_code == 403
        assert_envelope(r.json(), "forbidden")
    assert c.get("/me", headers={**H, "X-Gldb-Reviewer": "boss"}).json() == {
        "reviewer": "boss",
        "admin": True,
    }
    assert c.get("/me", headers={**H, "X-Gldb-Reviewer": "user1"}).json()["admin"] is False
    body = c.get("/spending", headers={**H, "X-Gldb-Reviewer": "boss"}).json()
    assert body["viewer"] == "boss"
    assert {a["account"]: a["cap_usd"] for a in body["accounts"]} == {"boss": "0", "user1": "0"}
    assert [(a["account"], a["today_usd"]) for a in body["accounts"]] == [
        ("boss", "0.500000"),
        ("user1", "0.250000"),
    ]
    assert body["site_today_usd"] == "0.850000" and body["unattributed_today_usd"] == "0.100000"
    assert len(body["history"]) == 7 and body["history"][0]["site_usd"] == "0.850000"


def test_one_account_can_have_its_own_daily_budget(settings, monkeypatch) -> None:
    from decimal import Decimal

    from app.core.costs import DailySpend

    paid = settings.model_copy(
        update={
            "llm_backend": "anthropic",
            "anthropic_api_key": SecretStr("sk-ant-test-fake"),
            "max_usd_per_user_per_day": Decimal("1.00"),
            "max_usd_per_user_overrides": "Officer=5.00",
        }
    )
    assert paid.user_cap_for("officer") == Decimal("5.00")
    assert paid.user_cap_for("user1") == Decimal("1.00")
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    app = main.create_app(paid)
    c = TestClient(app, client=REMOTE)
    for who in ("officer", "user1"):
        DailySpend(app.state.db, Decimal("50"), account=who, user_cap=Decimal("9")).add(
            Decimal("2")
        )
    post = lambda who: c.post(  # noqa: E731
        "/cases", headers={**H, "X-Gldb-Reviewer": who}, data={"use_case": "loan"}
    )
    assert post("user1").status_code == 429  # $2 spent, $1 allowed
    assert (
        post("officer").status_code == 422
    )  # $2 spent, $5 allowed: gets as far as the missing files


def test_a_malformed_override_stops_the_application_at_start(settings) -> None:
    import pytest

    bad = settings.model_copy(update={"max_usd_per_user_overrides": "officer=five"})
    with pytest.raises(Exception, match="MAX_USD_PER_USER_OVERRIDES"):
        bad.validate_startup()
