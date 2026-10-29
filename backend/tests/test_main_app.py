"""Application factory, router discovery, error body, request ids and the 501 stubs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings, SettingsError
from app.core.db import Database
from app.core.errors import ApiError

STUB_MODULES = [
    "cases",
    "effectiveness",
    "explain",
    "files",
    "health",
    "jobs",
    "pack",
    "queue",
    "review",
    "rules",
]
STUB_ROUTES = [
    ("POST", "/cases"),
    ("GET", "/cases"),
    ("GET", "/cases/c_1"),
    ("DELETE", "/cases/c_1"),
    ("POST", "/cases/c_1/files"),
    ("POST", "/cases/c_1/rerun"),
    ("GET", "/cases/c_1/versions"),
    ("GET", "/cases/c_1/documents/d1/words"),
    ("GET", "/jobs/j_1"),
    ("GET", "/cases/c_1/pages/1"),
    ("GET", "/cases/c_1/files/f1"),
    ("POST", "/cases/c_1/review"),
    ("GET", "/cases/c_1/review"),
    ("GET", "/cases/c_1/audit"),
    ("GET", "/cases/c_1/pack"),
    ("GET", "/rules/effectiveness"),
    ("GET", "/rules"),
    ("POST", "/admin/retention/purge"),
]


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(main.create_app(settings))


def test_router_discovery_lists_all_ten_modules(settings: Settings) -> None:
    modules = [m.__name__.rsplit(".", 1)[-1] for m in main.discover_api_modules()]
    assert modules == STUB_MODULES
    app_ = main.create_app(settings)
    assert app_.state.api_modules == STUB_MODULES


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["version"] == "0.1.0" and body["llm_backend"] == "fake"
    assert body["ruleset_version"].startswith("rs-") and body["checks_registered"] >= 3
    assert "api_key" not in r.text.lower()
    assert r.headers["x-request-id"]


@pytest.mark.parametrize(("method", "path"), STUB_ROUTES)
def test_every_api_route_is_implemented(client: TestClient, method: str, path: str) -> None:
    """The foundation answered 501 here; the routers are now real, so no route may be a stub."""
    r = client.request(method, path)
    assert r.status_code != 501, (method, path)
    assert r.status_code < 500, (method, path, r.text)
    if r.status_code >= 400:
        err = r.json()["error"]
        assert err["code"] != "not_implemented" and err["message"]
    assert "Traceback" not in r.text and r.headers["x-request-id"]


def test_unknown_route_and_wrong_method_use_the_single_error_body(client: TestClient) -> None:
    r = client.get("/definitely/not/here")
    assert r.status_code == 404 and r.json() == {
        "error": {"code": "not_found", "message": "Not found.", "details": {}}
    }
    r = client.put("/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_request_id_is_echoed(client: TestClient) -> None:
    r = client.get("/health", headers={"X-Request-Id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


def test_cors_headers(client: TestClient) -> None:
    r = client.options(
        "/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_validation_and_unexpected_errors_never_leak(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = APIRouter()

    @router.get("/t/int/{n}")
    def need_int(n: int) -> dict:
        return {"n": n}

    @router.get("/t/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail /etc/passwd")

    @router.get("/t/api")
    def api_error() -> None:
        raise ApiError(409, "case_not_open", "Not open.", {"status": "closed"})

    module = SimpleNamespace(router=router, __name__="app.api.zz_test")
    monkeypatch.setattr(main, "discover_api_modules", lambda: [module])
    c = TestClient(main.create_app(settings), raise_server_exceptions=False)
    r = c.get("/t/int/abc")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"]["errors"][0]["field"] == "n"
    r = c.get("/t/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "secret" not in r.text and "Traceback" not in r.text and r.headers["x-request-id"]
    assert r.json()["error"]["details"]["request_id"] == r.headers["x-request-id"]
    r = c.get("/t/api")
    assert r.status_code == 409 and r.json() == {
        "error": {"code": "case_not_open", "message": "Not open.", "details": {"status": "closed"}}
    }


def test_module_init_hooks_run_at_startup(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, object]] = []
    module = SimpleNamespace(
        router=APIRouter(), init=lambda app_, s: calls.append((app_, s)), __name__="app.api.zz_hook"
    )
    monkeypatch.setattr(main, "discover_api_modules", lambda: [module])
    app_ = main.create_app(settings)
    assert calls == [(app_, settings)]


def test_startup_recovers_interrupted_jobs_and_fails_fast_on_bad_config(
    settings: Settings, db: Database
) -> None:
    from app.core.cases import new_case

    case = new_case(settings, "loan")
    db.create_case(case)
    job = db.create_job(case.id)
    db.update_job(job.id, state="running")
    main.create_app(settings, db=db)
    assert db.get_job(job.id).state == "failed"  # type: ignore[union-attr]
    assert db.load_case(case.id).status == "error"
    bad = Settings(_env_file=None, llm_backend="openai", llm_api_key="k")
    with pytest.raises(SettingsError):
        main.create_app(bad)


def test_importing_main_does_not_touch_the_filesystem(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import importlib

    monkeypatch.chdir(tmp_path)
    importlib.reload(main)
    assert list(tmp_path.iterdir()) == []  # the lazy `app` attribute is built on first access
    with pytest.raises(AttributeError):
        main.not_a_thing  # noqa: B018


def test_lazy_app_attribute_builds_an_application(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from app.core.config import get_settings

    monkeypatch.setenv("GLDB_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("GLDB_LLM_BACKEND", "fake")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "_app", None)
    try:
        application = main.app
        assert TestClient(application).get("/health").status_code == 200
        assert (tmp_path / "d" / "gldb.sqlite3").exists()
    finally:
        get_settings.cache_clear()
        monkeypatch.setattr(main, "_app", None)
