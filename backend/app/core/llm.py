"""LLM access (docs/architecture.md section 5, ingest-and-extraction.md sections 3.4 and 5).

One ``LLMClient`` protocol over an OpenAI-compatible API plus a ``FakeLLMClient`` for tests and for
the no-API-key environment. Model names are settings, never constants. Document text is untrusted
data: instructions go in ``system`` (fixed, hashed into provenance), document text goes in ``user``
between ``wrap_untrusted`` delimiters, and the model has no tools of its own.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from app.core.cache import ResponseCache, cache_key
from app.core.config import Settings, SettingsError
from app.core.costs import CostMeter, LLMBudgetExceeded
from app.core.errors import LLMError, LLMNoFixture, LLMSchemaError, LLMTransportError
from app.core.models import Cost, StageName, Strict

__all__ = [
    "LLMBudgetExceeded",
    "LLMError",
    "LLMNoFixture",
    "LLMSchemaError",
    "LLMServerStopped",
    "LLMTransportError",
    "LLMVisionUnsupported",
]

log = logging.getLogger("gldb.llm")

LLM_CLIENT_VERSION = "1"  # bump when message construction or parsing changes (cache key)
MAX_OUTPUT_TOKENS = 8000  # default of GLDB_LLM_MAX_OUTPUT_TOKENS (the cache key uses the setting)
UNTRUSTED_MAX_CHARS = 30_000
SYSTEM_SUFFIX = "Reply only by calling the function {name} once."

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

STOPPED_MESSAGE = (
    "The local LLM server is stopped. Send /start to the Telegram bot "
    "(@your_llm_gateway_bot), wait about a minute, then retry."
)


class LLMServerStopped(LLMTransportError):
    """The local server's gateway answered 503 "The local LLM server is stopped". Retrying will not
    help until the owner sends /start to the Telegram bot; the message says so."""


class LLMVisionUnsupported(LLMError):
    """A request carrying images reached a client configured with GLDB_LLM_SUPPORTS_VISION=false.
    The callers never build such a request; this is the last line of defence, not a normal path."""


# --------------------------------------------------------------------------------------
# Request / result / protocol
# --------------------------------------------------------------------------------------


class LLMRequest(Strict):
    purpose: str  # "extract:bank_statement", "classify:C1", "ocr:page" ...
    model_role: Literal["cheap", "strong"] = "cheap"
    system: str  # fixed instructions; never contains document text
    user: str  # document text, wrapped in explicit data delimiters
    images: list[bytes] = Field(default_factory=list)  # PNG bytes
    schema_name: str
    json_schema: dict
    cache_extra: str = ""  # file sha256 + page (+ mode), appended to the cache key


class LLMResult(Strict):
    data: dict
    model: str
    cost: Cost
    cache_hit: bool = False


class LLMClient(Protocol):
    def complete_json(
        self, req: LLMRequest, *, ctx_costs: CostMeter | None = None
    ) -> LLMResult: ...


# --------------------------------------------------------------------------------------
# Prompt registry (provenance hashes the registered system prompts)
# --------------------------------------------------------------------------------------

_prompts: dict[str, str] = {}
_prompts_lock = threading.Lock()


def register_prompt(purpose: str, system: str) -> str:
    """Store a system prompt for provenance; returns its sha256 hex. The same purpose twice with
    different text is a ``ValueError`` (a prompt must have one definition)."""
    with _prompts_lock:
        existing = _prompts.get(purpose)
        if existing is not None and existing != system:
            raise ValueError(f"prompt for purpose {purpose!r} already registered with other text")
        _prompts[purpose] = system
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


def prompt_registry() -> dict[str, str]:
    """purpose -> system text, sorted by purpose."""
    with _prompts_lock:
        return {k: _prompts[k] for k in sorted(_prompts)}


# --------------------------------------------------------------------------------------
# Untrusted text
# --------------------------------------------------------------------------------------


def truncate_untrusted(text: str, limit: int = UNTRUSTED_MAX_CHARS) -> tuple[str, bool]:
    """Cut at a line boundary and append a final ``[truncated]`` line. Returns (text, truncated).
    The caller adds the warning ``text_truncated: page N``."""
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut + "\n[truncated]", True


def wrap_untrusted(text: str, *, label: str) -> str:
    """Wrap document text in delimiters whose nonce is a hash of the enclosed text, so the text
    cannot forge its own end marker. No escaping is done (MRZ ``<`` reaches the model unchanged)."""
    text, _ = truncate_untrusted(text)
    nonce = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    end = f"[[[DOC-{nonce} END]]]"
    if end in text:
        raise ValueError("document text contains its own end marker")
    safe_label = re.sub(r"[^A-Za-z0-9_.:\-]", "_", label)[:80]
    return f"[[[DOC-{nonce} BEGIN label={safe_label}]]]\n{text}\n{end}"


# --------------------------------------------------------------------------------------
# JSON schema validation (the subset Pydantic emits; no new dependency)
# --------------------------------------------------------------------------------------


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True  # unknown type keywords are ignored


def _resolve_ref(ref: str, root: dict) -> dict | None:
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _validate(value: Any, schema: dict, root: dict, path: str) -> str | None:
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        if target is not None:
            err = _validate(value, target, root, path)
            if err:
                return err
    for sub in schema.get("allOf", []):
        err = _validate(value, sub, root, path)
        if err:
            return err
    if "anyOf" in schema:
        errors = [_validate(value, sub, root, path) for sub in schema["anyOf"]]
        if all(e is not None for e in errors):
            return f"{path}: does not match any allowed alternative"
    if "const" in schema and value != schema["const"]:
        return f"{path}: must equal {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: value not in enum"
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, t) for t in types):
            return f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                return f"{path}.{name}: required property missing"
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    return f"{path}.{name}: additional property not allowed"
        for name, sub in props.items():
            if name in value:
                err = _validate(value[name], sub, root, f"{path}.{name}")
                if err:
                    return err
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            err = _validate(item, schema["items"], root, f"{path}[{i}]")
            if err:
                return err
    return None


def validate_json_schema(data: object, schema: dict) -> str | None:
    """``None`` if valid, else the first error as ``"path: message"``. Supports ``type`` (and type
    lists), ``properties``, ``required``, ``additionalProperties: false``, ``items``, ``enum``,
    ``const``, ``anyOf``, ``allOf`` and ``$ref`` into ``#/$defs``. Unknown keywords are ignored."""
    return _validate(data, schema, schema, "$")


# --------------------------------------------------------------------------------------
# Helpers shared by the clients
# --------------------------------------------------------------------------------------


def stage_for_purpose(purpose: str) -> StageName:
    head = purpose.split(":", 1)[0]
    if head == "ocr":
        return "ingest"
    if head == "classify":
        return "classify"
    return "extract"


def estimate_tokens(*texts: str) -> int:
    return math.ceil(sum(len(t) for t in texts) / 4)


def file_sha_from_extra(purpose: str, cache_extra: str) -> str | None:
    """Cached ``ocr:*``/``extract:*`` values contain page text, so they carry the file hash for
    deletion with the case (cost-efficiency.md 5.4)."""
    if not purpose.startswith(("ocr:", "extract:")):
        return None
    head = cache_extra.split(":", 1)[0]
    return head if _SHA_RE.match(head) else None


def _record(
    ctx_costs: CostMeter | None,
    req: LLMRequest,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    seconds: float,
    cache_hit: bool = False,
    cached_prompt_tokens: int = 0,
) -> Cost:
    if ctx_costs is not None:
        return ctx_costs.record(
            purpose=req.purpose,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            seconds=seconds,
            cache_hit=cache_hit,
            cached_prompt_tokens=cached_prompt_tokens,
        )
    return Cost(
        stage=stage_for_purpose(req.purpose),
        purpose=req.purpose,
        model=model,
        prompt_tokens=0 if cache_hit else prompt_tokens,
        completion_tokens=0 if cache_hit else completion_tokens,
        usd=Decimal("0"),
        seconds=seconds,
        cache_hit=cache_hit,
    )


def _log_call(req: LLMRequest, cost: Cost) -> None:
    # No document text, no key, no filenames: ids, counts and money only.
    log.info(
        "llm_call purpose=%s model=%s role=%s prompt_tokens=%s completion_tokens=%s usd=%s "
        "seconds=%.3f cache_hit=%s doc=%s page=%s",
        req.purpose,
        cost.model,
        req.model_role,
        cost.prompt_tokens,
        cost.completion_tokens,
        format(cost.usd, "f"),
        cost.seconds,
        cost.cache_hit,
        cost.document_id,
        cost.page,
    )


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_TAG = re.compile(r"</?tool_call>", re.IGNORECASE)


def _json_objects(text: str) -> list[dict]:
    """Every top-level JSON object found in ``text``, in order. Tolerates markdown fences, prose
    before and after, and ``<think>``/``<tool_call>`` wrappers (llama-server with a Qwen template
    may leave the call in the content or add trailing text after the arguments)."""
    text = _TOOL_CALL_TAG.sub(" ", _THINK_BLOCK.sub(" ", text))
    out: list[dict] = []
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return out
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, dict):
            out.append(value)
        index = end


def _unwrap_call(data: dict, name: str) -> dict:
    """``{"name": <schema_name>, "arguments": {...}}`` (the shape a model prints when the server
    did not turn its call into ``tool_calls``) becomes the arguments; anything else is unchanged."""
    if data.get("name") == name:
        inner = data.get("arguments", data.get("parameters"))
        if isinstance(inner, str):
            found = _json_objects(inner)
            inner = found[0] if found else None
        if isinstance(inner, dict):
            return inner
    return data


_semaphores: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_semaphores_lock = threading.Lock()


def concurrency_gate(base_url: str, limit: int) -> threading.BoundedSemaphore:
    """One semaphore per (server, limit), shared by every client object and thread in the process:
    a local server runs one request at a time, so with a limit of 1 the page workers queue here
    instead of all sitting in the server's queue until their HTTP timeout fires."""
    key = (base_url.rstrip("/"), max(1, limit))
    with _semaphores_lock:
        gate = _semaphores.get(key)
        if gate is None:
            gate = _semaphores[key] = threading.BoundedSemaphore(key[1])
        return gate


def _retry_after_seconds(exc: Exception) -> float | None:
    headers = _get(getattr(exc, "response", None), "headers")
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _error_text(exc: Exception) -> str:
    """Lower-cased text of an HTTP error (message and parsed body), for classifying a 503."""
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(json.dumps(body, default=str) if not isinstance(body, str) else body)
    return " ".join(parts).lower()


# --------------------------------------------------------------------------------------
# OpenAI-compatible client
# --------------------------------------------------------------------------------------


class OpenAICompatClient:
    """Implements ``LLMClient`` over an OpenAI-compatible chat completions API.

    Structured output is one function tool with ``tool_choice="auto"`` (a forced choice is
    rejected by OpenRouter for Claude models), then we verify a tool call came back and fall back
    to parsing JSON text. Output is validated against the schema; one retry on parse failure.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ResponseCache | None = None,
        sdk: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        http_client: Any | None = None,
    ):
        self.settings = settings
        self.cache = cache
        self._sleep = sleep
        self._gate = concurrency_gate(settings.llm_base_url, settings.llm_max_concurrency)
        # visible to scripts/tests: how often a 503 was retried and how long we waited
        self.stats = {"retries_503": 0, "retry_wait_s": 0.0}
        self._stats_lock = threading.Lock()
        if sdk is None:
            import openai

            extra: dict[str, Any] = {}
            if http_client is not None:
                extra["http_client"] = http_client
            sdk = openai.OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.api_key_value,
                timeout=settings.llm_timeout_s,
                max_retries=0,
                **extra,
            )
        self._sdk = sdk

    @property
    def max_output_tokens(self) -> int:
        return self.settings.llm_max_output_tokens

    @property
    def client_version(self) -> str:
        """Cache-key component: a thinking-off answer is a different answer from a thinking-on one."""
        version = (
            LLM_CLIENT_VERSION if self.settings.llm_enable_thinking else LLM_CLIENT_VERSION + "-nt"
        )
        effort = self.settings.llm_reasoning_effort
        return f"{version}-re{effort}" if effort else version

    def model_for(self, role: Literal["cheap", "strong"]) -> str:
        return self.settings.llm_model_strong if role == "strong" else self.settings.llm_model_cheap

    # -------------------------------------------------------------------- messages

    def _messages(self, req: LLMRequest, model: str, feedback: str | None) -> list[dict]:
        system_text = req.system + "\n\n" + SYSTEM_SUFFIX.format(name=req.schema_name)
        system_content: Any = system_text
        if model.startswith("anthropic/"):  # provider prompt cache marker (lever 7)
            system_content = [
                {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
            ]
        parts: list[dict] = [{"type": "text", "text": req.user}]
        if req.images and not self.settings.llm_supports_vision:
            # text-only server: an image part would be rejected or silently ignored. Callers must
            # use OCR text instead (ingest/ocr.py); reaching this line is a bug, so fail loudly.
            raise LLMVisionUnsupported(
                f"{req.purpose}: request has {len(req.images)} image(s) but "
                "GLDB_LLM_SUPPORTS_VISION=false"
            )
        for img in req.images:
            b64 = base64.b64encode(img).decode("ascii")
            parts.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": parts},
        ]
        if feedback:
            messages.append(
                {
                    "role": "user",
                    "content": f"Your previous reply was not valid: {feedback[:200]}. "
                    "Call the function again.",
                }
            )
        return messages

    # -------------------------------------------------------------------- transport

    @staticmethod
    def _status_of(exc: Exception) -> int | None:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = _get(getattr(exc, "response", None), "status_code")
        return status if isinstance(status, int) else None

    def _create(self, model: str, messages: list[dict], req: LLMRequest) -> Any:
        cfg = self.settings
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": req.schema_name,
                        "description": "Return the structured result.",
                        "parameters": req.json_schema,
                    },
                }
            ],
            "tool_choice": "auto",
        }
        if not cfg.llm_enable_thinking:
            # llama-server (--jinja) reads this from the request body; the OpenAI SDK merges
            # extra_body into the top level of the JSON it posts
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if cfg.llm_reasoning_effort:
            kwargs["reasoning_effort"] = cfg.llm_reasoning_effort
        attempt = 0
        while True:
            try:
                with self._gate:  # at most GLDB_LLM_MAX_CONCURRENCY calls in flight, all threads
                    return self._sdk.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below, message sanitised
                status = self._status_of(exc)
                stopped = False
                if status == 503:
                    text = _error_text(exc)
                    stopped = "server is stopped" in text or "is stopped" in text
                    limit = cfg.llm_retry_503_attempts
                    if stopped:  # waiting does not start it: one quick re-check, then fail clearly
                        limit = min(limit, 1)
                else:
                    limit = 2
                retryable = status is None or status == 429 or status >= 500
                if retryable and attempt < limit:
                    wait = self._wait_before_retry(attempt, status, stopped, exc)
                    if status == 503:
                        with self._stats_lock:
                            self.stats["retries_503"] += 1
                            self.stats["retry_wait_s"] += wait
                    self._sleep(wait)
                    attempt += 1
                    continue
                if stopped:
                    raise LLMServerStopped(STOPPED_MESSAGE) from None
                if status == 503:
                    raise LLMTransportError(
                        f"status 503 (server busy or still loading the model; "
                        f"gave up after {attempt} retries)"
                    ) from None
                if status is not None:
                    raise LLMTransportError(f"status {status}") from None
                raise LLMTransportError(f"transport error: {type(exc).__name__}") from None

    def _wait_before_retry(
        self, attempt: int, status: int | None, stopped: bool, exc: Exception
    ) -> float:
        """Seconds to wait. 503/429 honour ``Retry-After`` (capped); otherwise 1, 3 s (hosted
        default) or ``backoff * 3**n`` for a 503 (``GLDB_LLM_RETRY_503_BACKOFF_S``)."""
        cfg = self.settings
        if status == 503:
            backoff = cfg.llm_retry_503_backoff_s * 3**attempt
            if not stopped:
                hinted = _retry_after_seconds(exc)
                if hinted is not None:
                    backoff = hinted
            return min(cfg.llm_retry_503_max_wait_s, backoff)
        if status == 429:
            hinted = _retry_after_seconds(exc)
            if hinted is not None:
                return min(cfg.llm_retry_503_max_wait_s, hinted)
        return float(3**attempt)

    # -------------------------------------------------------------------- parsing

    @staticmethod
    def _text_candidates(message: Any) -> list[str]:
        content = _get(message, "content")
        if isinstance(content, list):  # content parts
            content = "".join(_get(p, "text", "") or "" for p in content)
        # reasoning_content (thinking) is deliberately never read: it is not the answer
        return [content] if isinstance(content, str) and content.strip() else []

    @staticmethod
    def _objects_in(raw: object, name: str) -> tuple[list[dict], str | None]:
        """(objects, error) from one tool-call argument value or one content string."""
        if isinstance(raw, dict):
            return [_unwrap_call(raw, name)], None
        if not isinstance(raw, str):
            return [], "not valid JSON: arguments are not text"
        stripped = _strip_fence(raw)
        try:
            whole = json.loads(stripped)
        except ValueError:
            found = [_unwrap_call(d, name) for d in _json_objects(raw)]
            if found:
                return found, None
            return [], f"not valid JSON: {stripped[:80]!r}"
        if isinstance(whole, dict):
            return [_unwrap_call(whole, name)], None
        return [], "JSON is not an object"

    @classmethod
    def _parse(
        cls, response: Any, schema: dict | None = None, name: str = ""
    ) -> tuple[dict | None, str | None]:
        """(data, error). Tool-call arguments first (a JSON string, possibly fenced or followed by
        text), then JSON found in the message content (a model that answers in text instead of
        calling the tool, or an unparsed ``<tool_call>`` block). With a ``schema``, the first
        object that validates wins; otherwise the first object found is returned so the caller
        reports the schema error."""
        choices = _get(response, "choices") or []
        if not choices:
            return None, "no choices in response"
        message = _get(choices[0], "message")
        candidates: list[object] = []
        for call in _get(message, "tool_calls") or []:
            args = _get(_get(call, "function"), "arguments")
            if args is not None:
                candidates.append(args)
        candidates += cls._text_candidates(message)
        if not candidates:
            if _get(choices[0], "finish_reason") == "length":
                return None, (
                    "output cut off at max_tokens (thinking used the budget?); raise "
                    "GLDB_LLM_MAX_OUTPUT_TOKENS or set GLDB_LLM_ENABLE_THINKING=false"
                )
            return None, "no tool call and no content"
        first: dict | None = None
        error: str | None = None
        for raw in candidates:
            objects, err = cls._objects_in(raw, name)
            error = error or err
            for obj in objects:
                if schema is None or validate_json_schema(obj, schema) is None:
                    return obj, None
                first = first or obj
        if first is not None:
            return first, None
        if _get(choices[0], "finish_reason") == "length":
            return None, "output cut off at max_tokens: " + (error or "no JSON")[:100]
        return None, error or "not valid JSON"

    # -------------------------------------------------------------------- main entry

    def complete_json(self, req: LLMRequest, *, ctx_costs: CostMeter | None = None) -> LLMResult:
        model = self.model_for(req.model_role)
        key = cache_key(req, model, self.client_version, self.max_output_tokens)

        if self.cache is not None:
            started = time.perf_counter()
            hit = self.cache.get(key)
            if hit is not None:
                cost = _record(
                    ctx_costs,
                    req,
                    model=hit.model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    seconds=time.perf_counter() - started,
                    cache_hit=True,
                )
                _log_call(req, cost)
                return LLMResult(data=hit.data, model=hit.model, cost=cost, cache_hit=True)

        attempts = 1 + max(0, self.settings.llm_schema_retries)
        feedback: str | None = None
        last_error = "unknown"
        for _attempt in range(attempts):
            if ctx_costs is not None:
                ctx_costs.reserve_call()  # raises LLMBudgetExceeded
            messages = self._messages(req, model, feedback)
            started = time.perf_counter()
            response = self._create(model, messages, req)
            seconds = time.perf_counter() - started

            usage = _get(response, "usage")
            prompt_tokens = _get(usage, "prompt_tokens")
            completion_tokens = _get(usage, "completion_tokens")
            if prompt_tokens is None or completion_tokens is None:
                prompt_tokens = estimate_tokens(req.system, req.user)
                completion_tokens = estimate_tokens(json.dumps(self._safe_content(response)))
                if ctx_costs is not None:
                    ctx_costs.add_warning("usage_missing")
            cached_tokens = _get(_get(usage, "prompt_tokens_details"), "cached_tokens", 0) or 0
            cost = _record(
                ctx_costs,
                req,
                model=model,
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                seconds=seconds,
                cached_prompt_tokens=int(cached_tokens),
            )
            _log_call(req, cost)

            data, error = self._parse(response, req.json_schema, req.schema_name)
            if data is not None:
                error = validate_json_schema(data, req.json_schema)
            if data is not None and error is None:
                if self.cache is not None:
                    self.cache.put(
                        key,
                        data,
                        model=model,
                        purpose=req.purpose,
                        prompt_tokens=cost.prompt_tokens,
                        completion_tokens=cost.completion_tokens,
                        usd=cost.usd,
                        file_sha256=file_sha_from_extra(req.purpose, req.cache_extra),
                    )
                return LLMResult(data=data, model=model, cost=cost, cache_hit=False)
            last_error = error or "invalid"
            feedback = last_error
        raise LLMSchemaError(
            f"model output rejected after {attempts} attempt(s): {last_error[:200]}"
        )

    @staticmethod
    def _safe_content(response: Any) -> str:
        try:
            message = _get(_get(response, "choices")[0], "message")
            calls = _get(message, "tool_calls") or []
            if calls:
                return str(_get(_get(calls[0], "function"), "arguments") or "")
            return str(_get(message, "content") or "")
        except Exception:  # noqa: BLE001
            return ""


# --------------------------------------------------------------------------------------
# Fake client
# --------------------------------------------------------------------------------------

Fixture = str | dict | Callable[[LLMRequest], dict]


class FakeLLMClient:
    """Canned JSON by purpose; the default client when no API key is set.

    Lookup order for a request: ``purpose:<sha256(images[0] or user)[:12]>``,
    ``purpose:<file_sha[:12]>:<page>`` (parsed from ``cache_extra``), ``purpose:<model_role>``,
    ``purpose``, ``default``; otherwise ``LLMNoFixture``. A refused (budget) request is not
    recorded in ``calls``; every other request is recorded before lookup.
    """

    def __init__(
        self,
        fixtures: dict[str, Fixture] | None = None,
        default: dict | None = None,
        *,
        cache: ResponseCache | None = None,
        oracle: _GroundTruthOracle | None = None,
    ):
        self.fixtures: dict[str, Fixture] = dict(fixtures or {})
        self.default = default
        self.cache = cache
        self._oracle = oracle
        self.calls: list[LLMRequest] = []
        self._lock = threading.Lock()

    def model_for(self, role: str) -> str:
        return "fake"

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    @classmethod
    def from_ground_truth(cls, manifest_path: str | Path, **kwargs: Any) -> FakeLLMClient:
        """Oracle mode: answers ``ocr:page`` and ``extract:<doc_type>`` from the generator's
        ``labels.json`` / ``ground_truth.json`` next to ``manifest.json``. Never answers
        ``classify:*`` (those fall back to the rules result). It measures rules and forensics, not
        OCR: extraction is perfect by construction."""
        return cls(oracle=_GroundTruthOracle(Path(manifest_path)), **kwargs)

    # -------------------------------------------------------------------- lookup

    def _lookup(self, req: LLMRequest) -> dict:
        p = req.purpose
        basis = req.images[0] if req.images else req.user.encode("utf-8")
        candidates = [f"{p}:{hashlib.sha256(basis).hexdigest()[:12]}"]
        sha, _, rest = req.cache_extra.partition(":")
        page = rest.split(":", 1)[0]
        if _SHA_RE.match(sha) and page:
            candidates.append(f"{p}:{sha[:12]}:{page}")
        candidates += [f"{p}:{req.model_role}", p]
        for name in candidates:
            if name in self.fixtures:
                return self._materialise(self.fixtures[name], req)
        if self._oracle is not None:
            answer = self._oracle.answer(req)  # raises LLMNoFixture when it cannot answer
            if answer is not None:
                return answer
        if self.default is not None:
            return self.default
        raise LLMNoFixture(p)

    @staticmethod
    def _materialise(fixture: Fixture, req: LLMRequest) -> dict:
        if callable(fixture):
            return fixture(req)
        if isinstance(fixture, str):
            try:
                return json.loads(fixture)
            except ValueError as exc:
                raise LLMSchemaError(f"fixture for {req.purpose} is not JSON") from exc
        return fixture

    def complete_json(self, req: LLMRequest, *, ctx_costs: CostMeter | None = None) -> LLMResult:
        model = "fake"
        key = cache_key(req, model, LLM_CLIENT_VERSION, MAX_OUTPUT_TOKENS) if self.cache else ""
        if self.cache is not None:
            started = time.perf_counter()
            hit = self.cache.get(key)
            if hit is not None:
                cost = _record(
                    ctx_costs,
                    req,
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    seconds=time.perf_counter() - started,
                    cache_hit=True,
                )
                return LLMResult(data=hit.data, model=model, cost=cost, cache_hit=True)
        if ctx_costs is not None:
            ctx_costs.reserve_call()  # counts toward max_calls like a real call
        with self._lock:
            self.calls.append(req)
        started = time.perf_counter()
        data = self._lookup(req)
        error = validate_json_schema(data, req.json_schema)
        if error is not None:
            raise LLMSchemaError(f"fixture for {req.purpose} rejected: {error[:200]}")
        cost = _record(
            ctx_costs,
            req,
            model=model,
            prompt_tokens=estimate_tokens(req.system, req.user),
            completion_tokens=estimate_tokens(json.dumps(data)),
            seconds=time.perf_counter() - started,
        )
        if self.cache is not None:
            self.cache.put(
                key,
                data,
                model=model,
                purpose=req.purpose,
                prompt_tokens=cost.prompt_tokens,
                completion_tokens=cost.completion_tokens,
                usd=cost.usd,
                file_sha256=file_sha_from_extra(req.purpose, req.cache_extra),
            )
        return LLMResult(data=data, model=model, cost=cost, cache_hit=False)


class _GroundTruthOracle:
    """Answers OCR and extraction requests from a generated bundle's ground truth."""

    def __init__(self, manifest_path: Path):
        base = manifest_path.parent
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.labels = json.loads((base / "labels.json").read_text(encoding="utf-8"))
        self.truth = json.loads((base / "ground_truth.json").read_text(encoding="utf-8"))
        self._page_row: dict[tuple[str, int], dict] = {
            (p["sha256"], int(p["page"])): p for p in self.labels.get("pages", [])
        }
        self._docs: dict[str, dict] = {d["key"]: d for d in self.truth.get("documents", [])}
        self._lines: dict[tuple[str, int], list[dict]] = {
            (p["file"], int(p["page"])): p.get("lines", []) for p in self.truth.get("pages", [])
        }

    def answer(self, req: LLMRequest) -> dict | None:
        kind, _, doc_type = req.purpose.partition(":")
        if kind == "classify" or kind not in ("ocr", "extract"):
            raise LLMNoFixture(req.purpose)
        sha, _, rest = req.cache_extra.partition(":")
        try:
            page = int(rest.split(":", 1)[0])
        except ValueError:
            raise LLMNoFixture(f"{req.purpose}: cache_extra has no page") from None
        row = self._page_row.get((sha, page))
        if row is None:
            raise LLMNoFixture(f"{req.purpose}: unknown (sha256, page)")
        if kind == "ocr":
            lines = self._lines.get((row["file"], page), [])
            return {"lines": [{k: line[k] for k in ("text", "x", "y", "w", "h")} for line in lines]}
        doc = self._docs.get(row["doc_key"])
        if doc is None or doc.get("doc_type") != doc_type:
            raise LLMNoFixture(f"{req.purpose}: no ground truth document {row['doc_key']}")
        # `page` is the page inside the file; for one image per page it is always 1, so the page of the
        # DOCUMENT that file holds (`doc_page`) decides which rows and fields the answer carries.
        return self._extract_page(doc, doc_type, int(row.get("doc_page", page)))

    @staticmethod
    def _raw(cell: Any) -> str | None:
        if not isinstance(cell, dict):
            return None
        raw = cell.get("raw")
        return raw if raw is not None else cell.get("value")

    def _extract_page(self, doc: dict, doc_type: str, page: int) -> dict:
        from app.core.schemas import DOC_SCHEMAS

        schema = DOC_SCHEMAS[doc_type]
        gt_fields = doc.get("fields", {})
        fields: dict[str, str | None] = {}
        for name in schema.fields:
            cell = gt_fields.get(name)
            on_page = isinstance(cell, dict) and int(cell.get("page", 1)) == page
            fields[name] = self._raw(cell) if on_page else None
        tables: dict[str, list[dict[str, str | None]]] = {}
        for tname, tspec in schema.tables.items():
            gt_table = doc.get("tables", {}).get(tname, {})
            rows = []
            for r in gt_table.get("rows", []):
                if int(r.get("page", 1)) != page:
                    continue
                cells = r.get("cells", {})
                out: dict[str, str | None] = {}
                for col in tspec.columns:
                    if doc_type == "financial_statement" and col == "is_subtotal":
                        continue
                    cell = cells.get(col)
                    value = self._raw(cell)
                    if doc_type == "financial_statement" and col in ("line", "section"):
                        value = cell.get("value") if isinstance(cell, dict) else None
                    out[col] = value
                rows.append(out)
            tables[tname] = rows
        result: dict[str, Any] = {"fields": fields, "tables": tables}
        if doc_type == "bank_statement":
            marker = None
            for r in doc.get("tables", {}).get("page_chain", {}).get("rows", []):
                cells = r.get("cells", {})
                if int(r.get("page", 1)) == page or (
                    str(self._raw(cells.get("page"))) == str(page)
                ):
                    marker = (
                        f"Page {self._raw(cells.get('page_x'))} of {self._raw(cells.get('page_y'))}"
                    )
                    break
            result["page_marker"] = marker
        return result


# --------------------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------------------


def get_llm_client(settings: Settings, *, cache: ResponseCache | None = None) -> LLMClient:
    """``GLDB_LLM_BACKEND``: ``fake`` -> FakeLLMClient (no cache); ``openai`` -> OpenAICompatClient
    (clear startup error if key or model names are empty); ``anthropic`` -> AnthropicLLMClient
    (official SDK, Claude Haiku 5.5 only); ``auto`` -> anthropic when its key is set, else openai
    when ``GLDB_LLM_API_KEY`` is set, else the Fake (the default in this project: no key)."""
    backend = settings.effective_llm_backend()
    if backend == "fake":
        return FakeLLMClient()
    if backend == "anthropic":
        from app.core.llm_anthropic import AnthropicLLMClient  # imports the SDK only when used

        return AnthropicLLMClient(settings, cache=cache if settings.cache_enabled else None)
    missing = [
        name
        for name, value in (
            ("GLDB_LLM_API_KEY", settings.api_key_value),
            ("GLDB_LLM_MODEL_CHEAP", settings.llm_model_cheap),
            ("GLDB_LLM_MODEL_STRONG", settings.llm_model_strong),
        )
        if not value.strip()
    ]
    if missing:
        raise SettingsError(
            "LLM backend 'openai' needs these settings, which are empty: " + ", ".join(missing)
        )
    return OpenAICompatClient(settings, cache=cache if settings.cache_enabled else None)
