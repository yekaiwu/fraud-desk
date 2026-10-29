"""FastAPI application (docs/architecture.md sections 2 and 12, api.md section 1).

Routers auto-register: every module in ``app/api/`` that exposes ``router`` is included, and an
optional module-level ``init(app, settings)`` is called at startup (api-backend uses it to install
the audit log filter without editing this file). One error body for every non-2xx response, an
``X-Request-Id`` header on every response, no stack traces to clients.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.api as api_package
from app.access import AccessSettings, install_access
from app.core.config import APP_VERSION, Settings, get_settings
from app.core.db import Database
from app.core.errors import ApiError
from app.core.llm import LLMClient
from app.core.registry import MockRegistry
from app.pipeline import JobRunner

log = logging.getLogger("gldb.api")

REQUEST_ID_HEADER = "X-Request-Id"
HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    501: "not_implemented",
}


def discover_api_modules() -> list[ModuleType]:
    """Every module of ``app.api`` that exposes ``router`` (sorted by name)."""
    modules: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(api_package.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{api_package.__name__}.{info.name}")
        if hasattr(module, "router"):
            modules.append(module)
    return modules


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        request.state.request_id = rid
    return rid


def _error(
    request: Request, status: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
        headers={REQUEST_ID_HEADER: _request_id(request)},
    )


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in e.get("loc", ())[1:]),
                "message": str(e.get("msg", "invalid")),
                "type": str(e.get("type", "")),
            }
            for e in exc.errors()
        ]
        return _error(
            request, 422, "validation_error", "The request is not valid.", {"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_CODES.get(exc.status_code, "http_error")
        message = "Not found." if exc.status_code == 404 else str(exc.detail)
        return _error(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        log.error("unhandled error request_id=%s type=%s", rid, type(exc).__name__, exc_info=exc)
        return _error(
            request, 500, "internal_error", "An unexpected error occurred.", {"request_id": rid}
        )


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    llm: LLMClient | None = None,
    registry: MockRegistry | None = None,
) -> FastAPI:
    """Build the application. Startup work (settings validation, schema creation, recovery of
    interrupted jobs, module ``init`` hooks) happens here so that a ``TestClient`` without a
    lifespan context still has a working application."""
    settings = settings or get_settings()
    settings.validate_startup()  # fail fast with a clear message

    if db is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    recovered = db.recover_interrupted_jobs()
    if recovered:
        log.warning("marked %d interrupted job(s) as failed", len(recovered))
    runner = JobRunner(db, settings, llm=llm, registry=registry)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        runner.shutdown(wait=False)

    # Behind the gate (production) the interactive docs and the OpenAPI schema are off: a map of every route is
    # of no use to a logged-in visitor. Without a secret (local dev, tests) they stay at /docs and /redoc.
    gated = bool(AccessSettings().secret_value)
    app = FastAPI(
        title="GLDB document-fraud analysis tool",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url=None if gated else "/docs",
        redoc_url=None if gated else "/redoc",
        openapi_url=None if gated else "/openapi.json",
    )
    app.state.settings = settings
    app.state.db = db
    app.state.runner = runner

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Location"],
    )

    install_access(app)  # proxy-secret gate + rate limits + job cap (app/access.py); no-op unset

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = _request_id(request)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response

    _install_handlers(app)

    modules = discover_api_modules()
    for module in modules:
        app.include_router(module.router)
    for module in modules:
        init = getattr(module, "init", None)
        if callable(init):
            init(app, settings)
    app.state.api_modules = [m.__name__.rsplit(".", 1)[-1] for m in modules]
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazy ``app.main:app`` for ``uvicorn --app-dir backend app.main:app`` so that importing this
    module never touches the database or the filesystem."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
