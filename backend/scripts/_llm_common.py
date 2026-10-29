"""Shared helpers of ``llm_smoke.py`` and ``llm_eval.py`` (docs/local-llm.md section 7).

Both scripts talk to the owner's local OpenAI-compatible server. They never print the API key and
they turn "the server is off / unreachable / key missing" into one clear instruction instead of a
traceback.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import Settings  # noqa: E402
from app.core.llm import OpenAICompatClient  # noqa: E402

BOT = "@your_llm_gateway_bot"
LOCAL_MODEL = "qwen3.8-27b"
LOCAL_BASE_URL = "http://llm-host:8080/v1"
EXIT_OK, EXIT_FAILED, EXIT_UNAVAILABLE = 0, 1, 2

START_INSTRUCTION = (
    f"Send /start to the Telegram bot {BOT}, wait about a minute for the model to load, "
    "then send /newkey gldb 30 and put the key in .env as GLDB_LLM_API_KEY."
)
TAILNET_HINT = (
    "This machine must be signed in to the same Tailscale tailnet as llm-host "
    "(base URL http://llm-host:8080/v1, or http://100.64.0.1:8080/v1 without MagicDNS)."
)


class Unavailable(Exception):  # noqa: N818 - a state: the server cannot be used right now
    """The server is stopped, unreachable, or the key is missing/invalid. ``str`` is the reason."""


def abort_if_unavailable(exc: Exception) -> None:
    """Re-raise a "stopped / unreachable" failure as :class:`Unavailable` so a script stops at once
    instead of recording one failed call per item. Other errors are left for the caller to count."""
    from app.core.llm import LLMServerStopped, LLMTransportError

    if isinstance(exc, LLMServerStopped):
        raise Unavailable(str(exc)) from None
    if isinstance(exc, LLMTransportError) and str(exc).startswith("transport error"):
        raise Unavailable(f"the server did not answer ({exc})") from None
    kind, _, message = str(exc).partition(":")
    if kind == "transport" and (
        "transport error" in message or "is stopped" in message
    ):  # PageReadError("transport", ...)
        raise Unavailable(message.strip()) from None


def instruction(reason: str) -> str:
    return (
        f"LOCAL LLM SERVER NOT AVAILABLE: {reason}\n  -> {START_INSTRUCTION}\n  -> {TAILNET_HINT}"
    )


def server_root(base_url: str) -> str:
    """``http://host:8080/v1`` -> ``http://host:8080`` (where ``/health`` lives)."""
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def model_name(settings: Settings) -> str:
    return settings.llm_model_cheap or LOCAL_MODEL


def with_model(settings: Settings, model: str | None = None) -> Settings:
    """Settings with the model names filled in (the scripts default to the local model)."""
    name = model or model_name(settings)
    return settings.model_copy(
        update={"llm_model_cheap": name, "llm_model_strong": settings.llm_model_strong or name}
    )


def load_settings(base_url: str | None = None, model: str | None = None) -> tuple[Settings, str]:
    """Settings for a local-server script: the app settings (``.env``), but never the hosted default
    URL. Returns (settings, note) where ``note`` says when the local default URL was substituted."""
    settings = Settings()
    note = ""
    if base_url:
        settings = settings.model_copy(update={"llm_base_url": base_url})
    elif settings.llm_base_url == Settings.model_fields["llm_base_url"].default:
        settings = settings.model_copy(update={"llm_base_url": LOCAL_BASE_URL})
        note = f"GLDB_LLM_BASE_URL is not set; using the local server {LOCAL_BASE_URL}"
    return with_model(settings, model), note


def make_http(transport: httpx.BaseTransport | None, timeout: float) -> httpx.Client:
    return httpx.Client(transport=transport, timeout=timeout)


def make_client(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> OpenAICompatClient:
    return OpenAICompatClient(settings, http_client=make_http(transport, settings.llm_timeout_s))


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..1); ``None`` for an empty sequence."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def preflight(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> tuple[list[str], list[str]]:
    """Check ``/health`` (no key), then ``/models`` (key). Returns (report lines, model ids).
    Raises :class:`Unavailable` with a reason when the server cannot be used."""
    base = settings.llm_base_url.rstrip("/")
    root = server_root(base)
    lines: list[str] = []
    with make_http(transport, 15.0) as http:
        try:
            health = http.get(f"{root}/health")
        except httpx.HTTPError as exc:
            raise Unavailable(f"cannot reach {root}/health ({type(exc).__name__})") from None
        if health.status_code == 503:
            what = "is stopped" if "stopped" in health.text.lower() else "is stopped or loading"
            raise Unavailable(f"{root}/health answered 503: the local LLM server {what}")
        if health.status_code != 200:
            raise Unavailable(f"{root}/health answered HTTP {health.status_code}")
        lines.append(f"health      OK   {root}/health -> 200")
        if not settings.api_key_value:
            raise Unavailable("GLDB_LLM_API_KEY is empty (no key to authenticate with)")
        try:
            models = http.get(
                f"{base}/models", headers={"Authorization": f"Bearer {settings.api_key_value}"}
            )
        except httpx.HTTPError as exc:
            raise Unavailable(f"cannot reach {base}/models ({type(exc).__name__})") from None
    if models.status_code in (401, 403):
        raise Unavailable(
            f"the API key was rejected (HTTP {models.status_code}: expired or revoked)"
        )
    if models.status_code == 503:
        raise Unavailable("the server answered 503 on /v1/models: it is stopped or loading")
    if models.status_code != 200:
        raise Unavailable(f"/v1/models answered HTTP {models.status_code}")
    try:
        ids = [m["id"] for m in models.json().get("data", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        ids = []
    lines.append(f"models      OK   {', '.join(ids) or '(none listed)'}")
    wanted = model_name(settings)
    if ids and wanted not in ids:
        lines.append(f"models      WARN configured model {wanted!r} is not in that list")
    return lines, ids
