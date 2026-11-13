"""Public-exposure guard rails (docs/deploy.md).

The Next.js server is the only public entry point; it signs users in with a shared password and
forwards ``/api/*`` to this backend with an ``X-Gldb-Proxy-Secret`` header (and the real client
address in ``X-Forwarded-For``). This module is the second line of defence, installed from
``main.py`` as one pure-ASGI middleware:

* **Proxy secret.** When ``GLDB_PROXY_SECRET`` is set, every request without the matching header is
  rejected with 401 (constant-time compare). The only exception is ``GET /health`` from a loopback
  peer, so a local status script keeps working. Unset = no gate (local development, tests).
* **Who is signed in.** The proxy sends the signed-in account in ``X-Gldb-Reviewer``. It is believed
  only when the proxy secret matched, then stored as ``request.state.reviewer``; the review, check
  catalogue and delete endpoints record that name and ignore any name typed in the request. Without a
  proxy secret (local development, tests) the header is ignored.
* **Which accounts exist.** On ``GET /spending`` only, the proxy also sends the names (never the
  passwords) of every configured account in ``X-Gldb-Accounts``, believed under the same rule and
  stored as ``request.state.accounts``, so the spending page can list an account that has not used the
  model yet instead of only the ones that have.
* **Per-client rate limits** (sliding window, in memory, single process): ``POST /cases`` at most
  ``GLDB_RATE_CASES_PER_HOUR`` per hour, review ``POST /cases/{id}/review`` at most
  ``GLDB_RATE_REVIEWS_PER_MIN`` per minute. The check catalogue writes (``PUT`` and ``DELETE``
  on ``/rules/{rule}/override``) share the review limiter, and so do the two custom-rule routes that
  call the model (``POST /custom-rules/drafts`` and ``/custom-rules/drafts/{id}/turns``), and the
  incident report (``POST /monitoring/incidents``, which notifies the alert channels). Over the
  limit: 429 ``rate_limited`` with ``Retry-After``.
* **Upload size.** ``POST /cases`` and ``/cases/{id}/files`` whose ``Content-Length`` exceeds
  ``GLDB_MAX_FILES_PER_CASE`` times ``GLDB_MAX_UPLOAD_MB`` plus 1 MB get 413 ``file_too_large`` before
  the body is read. The form itself is read with a cap on the number of parts (``api/_common.py``).
  The limit is counted per signed-in account when the proxy sent one (so a room of people on one
  Wi-Fi each get their own allowance), otherwise per client address: the ``X-Forwarded-For`` value
  sent by the proxy, but only when the proxy secret matched (otherwise the header is
  attacker-controlled and the peer address is used).
* **Job cap.** Requests that start analysis work (``POST /cases``, ``/cases/{id}/files``,
  ``/cases/{id}/rerun``) get 503 ``busy`` with ``Retry-After`` while ``GLDB_MAX_ACTIVE_JOBS`` jobs
  are queued or running, so strangers cannot queue unlimited work. It is a soft cap (checked, not
  reserved), which is enough for a single-process demo.

A limit of 0 disables that limit. Error bodies use the API's one error envelope (docs/api.md).
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("gldb.access")

PROXY_SECRET_HEADER = "x-gldb-proxy-secret"
REVIEWER_HEADER = "x-gldb-reviewer"
ACCOUNTS_HEADER = "x-gldb-accounts"
MAX_LISTED_ACCOUNTS = 200
_REVIEWER_OK = re.compile(r"^[a-z0-9._@-]{3,64}$")
FORWARDED_FOR_HEADER = "x-forwarded-for"
REQUEST_ID_HEADER = "X-Request-Id"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})
MIN_PROXY_SECRET_CHARS = 16
MAX_TRACKED_CLIENTS = 10_000

_REVIEW_PATH = re.compile(r"^/cases/[^/]+/review/?$")
_OVERRIDE_PATH = re.compile(r"^/rules/[^/]+/override/?$")
_JOB_PATH = re.compile(r"^/cases/[^/]+/(files|rerun)/?$")
_CUSTOM_DRAFT_PATH = re.compile(r"^/custom-rules/drafts(/[^/]+/turns)?/?$")
_ASK_PATH = re.compile(r"^/cases/[^/]+/ask/?$")
_INCIDENT_PATH = re.compile(r"^/monitoring/incidents/?$")
_UPLOAD_PATH = re.compile(r"^/cases(/[^/]+/files)?/?$")
REQUEST_OVERHEAD_BYTES = 1024 * 1024  # form fields and multipart framing around the files


class AccessSettings(BaseSettings):
    """Own settings class so the main ``Settings`` stays untouched. Unknown ``GLDB_*`` variables
    (the whole main ``Settings`` set shares the prefix and the ``.env`` file) are ignored."""

    model_config = SettingsConfigDict(
        env_prefix="GLDB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    proxy_secret: SecretStr = SecretStr("")
    rate_cases_per_hour: int = Field(default=10, ge=0)
    rate_reviews_per_min: int = Field(default=30, ge=0)
    max_active_jobs: int = Field(default=6, ge=0)

    @property
    def secret_value(self) -> str:
        return self.proxy_secret.get_secret_value().strip()


class SlidingWindowLimiter:
    """At most ``limit`` hits per ``window_s`` seconds per key. ``limit == 0`` never blocks."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """Record an attempt. Returns ``(allowed, retry_after_seconds)``; a blocked attempt is
        not recorded, so hammering does not extend the block."""
        if self.limit <= 0:
            return True, 0
        now = self._clock()
        with self._lock:
            if len(self._hits) >= MAX_TRACKED_CLIENTS and key not in self._hits:
                self._prune(now)
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(self.window_s - (now - q[0])) + 1)
            q.append(now)
            return True, 0

    def _prune(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window_s]:
            del self._hits[k]


def _is_loopback(scope: Scope) -> bool:
    client = scope.get("client")
    return bool(client) and client[0] in LOOPBACK_HOSTS


def _client_key(scope: Scope, headers: Headers, *, trust_forwarded: bool) -> str:
    """Rate-limit key. The proxy sends one address; with several, the last one is the hop our own
    trusted proxy appended, so the earlier (client-supplied) ones are ignored."""
    if trust_forwarded:
        forwarded = headers.get(FORWARDED_FOR_HEADER, "")
        last = forwarded.split(",")[-1].strip() if forwarded else ""
        if last:
            return last[:64]
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


class AccessMiddleware:
    def __init__(
        self, app: ASGIApp, access: AccessSettings, *, clock: Callable[[], float] | None = None
    ):
        self.app = app
        self.access = access
        self._secret = access.secret_value.encode()
        clock = clock or time.monotonic
        self.cases_limiter = SlidingWindowLimiter(access.rate_cases_per_hour, 3600, clock)
        self.reviews_limiter = SlidingWindowLimiter(access.rate_reviews_per_min, 60, clock)

    # ------------------------------------------------------------------ helpers

    def _secret_ok(self, headers: Headers) -> bool:
        given = headers.get(PROXY_SECRET_HEADER, "").encode()
        return secrets.compare_digest(given, self._secret)

    @staticmethod
    def _rid(headers: Headers) -> str:
        return headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]

    def _reject(
        self,
        headers: Headers,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> JSONResponse:
        extra = {REQUEST_ID_HEADER: self._rid(headers)}
        if retry_after is not None:
            extra["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "details": details or {}}},
            headers=extra,
        )

    @staticmethod
    def _active_jobs(scope: Scope) -> int:
        app = scope.get("app")
        db = getattr(getattr(app, "state", None), "db", None)
        if db is None:
            return 0
        with db.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')"
            ).fetchone()
        return int(row[0])

    def _check_body_size(self, scope: Scope, headers: Headers) -> JSONResponse | None:
        """413 before the body is read when the declared size cannot fit the upload limits (the most
        files times the biggest file, plus a little for the form). A missing or malformed length is left
        to the parser."""
        cfg = getattr(getattr(scope.get("app"), "state", None), "settings", None)
        max_files = getattr(cfg, "max_files_per_case", None)
        max_mb = getattr(cfg, "max_upload_mb", None)
        if not isinstance(max_files, int) or not isinstance(max_mb, int):
            return None
        cap = max_files * max_mb * 1024 * 1024 + REQUEST_OVERHEAD_BYTES
        try:
            declared = int(headers.get("content-length", ""))
        except ValueError:
            return None
        if declared <= cap:
            return None
        return self._reject(
            headers,
            413,
            "file_too_large",
            f"The upload is too large: at most {max_files} files of {max_mb} MB each.",
            {"max_mb": max_mb, "max_files": max_files, "max_request_bytes": cap},
        )

    # ------------------------------------------------------------------ ASGI

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        method = scope.get("method", "GET")
        path = scope.get("path", "")

        trusted = False
        if self._secret:
            if self._secret_ok(headers):
                trusted = True
                who = headers.get(REVIEWER_HEADER, "").strip().lower()
                if _REVIEWER_OK.match(who):
                    scope.setdefault("state", {})["reviewer"] = who
                listed = {
                    n
                    for n in (
                        p.strip().lower() for p in headers.get(ACCOUNTS_HEADER, "").split(",")
                    )
                    if _REVIEWER_OK.match(n)
                }
                if listed:
                    scope.setdefault("state", {})["accounts"] = tuple(
                        sorted(listed)[:MAX_LISTED_ACCOUNTS]
                    )
            elif (
                scope["type"] == "http"
                and method == "GET"
                and path == "/health"
                and _is_loopback(scope)
            ):
                await self.app(scope, receive, send)
                return
            else:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                log.warning("rejected %s %s: missing or wrong proxy secret", method, path)
                response = self._reject(
                    headers, 401, "unauthorized", "Missing or invalid proxy credential."
                )
                await response(scope, receive, send)
                return

        if scope["type"] == "http" and (
            method == "POST"
            or (method in ("PUT", "DELETE") and _OVERRIDE_PATH.match(path) is not None)
        ):
            blocked = self._check_post(scope, headers, path, trusted)
            if blocked is not None:
                await blocked(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _check_post(
        self, scope: Scope, headers: Headers, path: str, trusted: bool
    ) -> JSONResponse | None:
        is_create = path in ("/cases", "/cases/")
        is_job = is_create or bool(_JOB_PATH.match(path))
        is_review = bool(
            _REVIEW_PATH.match(path)
            or _OVERRIDE_PATH.match(path)
            or _CUSTOM_DRAFT_PATH.match(path)
            or _ASK_PATH.match(path)
            or _INCIDENT_PATH.match(path)
        )
        if not (is_job or is_review):
            return None
        if scope.get("method") == "POST" and _UPLOAD_PATH.match(path):
            too_big = self._check_body_size(scope, headers)
            if too_big is not None:
                return too_big
        reviewer = (scope.get("state") or {}).get("reviewer") if trusted else None
        client = (
            f"user:{reviewer}" if reviewer else _client_key(scope, headers, trust_forwarded=trusted)
        )
        if is_create:
            ok, wait = self.cases_limiter.hit(client)
            if not ok:
                return self._reject(
                    headers,
                    429,
                    "rate_limited",
                    f"Too many cases created from this account. Try again in {wait} seconds.",
                    {
                        "scope": "cases",
                        "limit": self.access.rate_cases_per_hour,
                        "window_s": 3600,
                        "retry_after_s": wait,
                    },
                    retry_after=wait,
                )
        if is_review:
            ok, wait = self.reviews_limiter.hit(client)
            if not ok:
                return self._reject(
                    headers,
                    429,
                    "rate_limited",
                    f"Too many review actions from this account. Try again in {wait} seconds.",
                    {
                        "scope": "reviews",
                        "limit": self.access.rate_reviews_per_min,
                        "window_s": 60,
                        "retry_after_s": wait,
                    },
                    retry_after=wait,
                )
        cap = self.access.max_active_jobs
        if is_job and cap > 0:
            active = self._active_jobs(scope)
            if active >= cap:
                return self._reject(
                    headers,
                    503,
                    "busy",
                    "The analysis queue is full. Try again in a minute.",
                    {"active_jobs": active, "max_active_jobs": cap, "retry_after_s": 30},
                    retry_after=30,
                )
        return None


def install_access(app: Any, access: AccessSettings | None = None) -> AccessSettings:
    """Add the middleware to ``app`` (a FastAPI app) and return the settings it uses.

    Install it before the request-id middleware so that wrapper adds ``X-Request-Id`` to the
    rejections too."""
    access = access or AccessSettings()
    secret = access.secret_value
    if secret and len(secret) < MIN_PROXY_SECRET_CHARS:
        raise ValueError(
            f"GLDB_PROXY_SECRET is shorter than {MIN_PROXY_SECRET_CHARS} characters; "
            "generate one with: openssl rand -base64 32"
        )
    app.add_middleware(AccessMiddleware, access=access)
    app.state.access = access
    if secret:
        log.info("proxy-secret gate enabled")
    return access
