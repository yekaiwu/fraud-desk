"""What every verification-source adapter shares: the three kinds of answer, a time budget, an HTTP
helper with retries that never puts a secret in a message, and an on-disk answer cache."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from app.core.crypto import DecryptionError, Vault, ctx_registry, plaintext_vault

log = logging.getLogger("gldb.sources")

USER_AGENT = "fraud-desk/0.1 (document-fraud research prototype)"
TIMEOUT_S = 12.0
RETRIES = 2
BACKOFF_S = 1.5
HIT_TTL_S = 7 * 86400
MISS_TTL_S = 86400
TRANSIENT_STATUSES = (429, 500, 502, 503, 504)


class SourceUnavailable(Exception):  # noqa: N818 - a state, not an error class
    """The source could not be asked (network, rate limit, refused, bad answer). Never a flag."""


@dataclass(frozen=True)
class SourceOutcome:
    """What the pipeline learned from one source for one case, read by the checks.

    ``ok``: ``data`` holds the answer (``None`` = the source has no such record). ``not_configured`` and
    ``blocked`` (private mode) are outside coverage; ``unavailable`` is configured but unreachable, which
    counts as not run and marks the case partial. ``reason`` never contains a secret."""

    status: Literal["ok", "not_configured", "blocked", "unavailable"]
    reason: str = ""
    data: Any = None


class Deadline:
    """One total time budget shared by every call a case makes to the sources."""

    def __init__(self, budget_s: float, clock: Any = time.monotonic):
        self._clock = clock
        self._end = clock() + budget_s

    def remaining(self) -> float:
        return self._end - self._clock()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise SourceUnavailable("time budget for the verification sources is used up")


def make_client(timeout_s: float = TIMEOUT_S) -> httpx.Client:
    return httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT})


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    deadline: Deadline | None = None,
    allow_404: bool = False,
    retries: int = RETRIES,
    **kwargs: Any,
) -> tuple[int, Any]:
    """``(status, parsed json)``. 404 returns ``(404, None)`` only with ``allow_404``. Rate limits and
    server errors are retried with a back-off; refusals (401/403) and other client errors are not (asking
    again changes nothing). Messages carry the status or the exception class, never a URL, a header or a
    response body: a secret cannot travel through them."""
    last = "no answer"
    for attempt in range(retries + 1):
        if deadline is not None:
            deadline.check()
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        else:
            code = resp.status_code
            if code == 404 and allow_404:
                return 404, None
            if code in TRANSIENT_STATUSES:
                last = f"answered {code}"
            elif code in (401, 403):
                raise SourceUnavailable(f"refused the request ({code})")
            elif code >= 400:
                raise SourceUnavailable(f"answered {code}")
            else:
                try:
                    return code, resp.json()
                except ValueError:
                    raise SourceUnavailable("answered something that is not JSON") from None
        if attempt < retries:
            time.sleep(BACKOFF_S * (attempt + 1))
    raise SourceUnavailable(last)


class AnswerCache:
    """``<dir>/<source>-<sha of key>.json``: ``{"at": epoch, "value": ...}`` sealed by the vault. A miss
    is cached too (``value`` None) with a shorter lifetime; an unavailable answer is never stored."""

    def __init__(self, cache_dir: Path | None, source: str, vault: Vault | None = None):
        self.dir = cache_dir
        self.source = source
        self._vault = vault if vault is not None else plaintext_vault()

    def _path(self, key: str) -> tuple[Path, str] | None:
        if self.dir is None:
            return None
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.dir / f"{self.source}-{digest}.json", f"{self.source}:{digest}"

    def get(
        self, key: str, now: float, hit_ttl: float = HIT_TTL_S, miss_ttl: float = MISS_TTL_S
    ) -> tuple[bool, Any]:
        found = self._path(key)
        if found is None or not found[0].is_file():
            return False, None
        path, ident = found
        try:
            blob = json.loads(self._vault.open(path.read_bytes(), ctx_registry(ident)))
            value = blob["value"]
            if now - float(blob["at"]) > (hit_ttl if value is not None else miss_ttl):
                return False, None
            return True, value
        except (OSError, ValueError, KeyError, TypeError, DecryptionError):
            return False, None

    def put(self, key: str, value: Any, now: float) -> None:
        found = self._path(key)
        if found is None:
            return
        path, ident = found
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"at": now, "value": value}).encode("utf-8")
            path.write_bytes(self._vault.seal(payload, ctx_registry(ident)))
        except OSError:
            log.warning("could not cache an answer of the %s source", self.source)
