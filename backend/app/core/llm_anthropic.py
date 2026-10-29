"""Anthropic backend (docs/anthropic-haiku.md): the official ``anthropic`` SDK, Claude Haiku 5.5 only.

Same public interface and behaviour as ``OpenAICompatClient`` (``complete_json``, response cache,
cost telemetry, shared concurrency gate, schema validation with one retry, budget guard, vision), but
the request is the Messages API shape of Haiku 5.5:

* ``thinking={"type": "disabled"}`` with ``output_config={"effort": "low"}`` (disabled thinking is
  only accepted at effort ``high`` or below; the default effort is medium);
* no ``temperature``/``top_p``/``top_k`` (non-default values are a 400 on this model) and no
  assistant prefill (a 400);
* structured output is one user tool whose ``input_schema`` is the JSON schema, ``tool_choice``
  ``auto`` (forced choice is not used) plus an instruction to call it; no ``strict``;
* the SDK does its own 429/5xx retries (``max_retries=2``); there is no second retry layer here.

Only ``ALLOWED_ANTHROPIC_MODELS`` can be sent: checked at settings validation, at construction and
again on every call. Thinking blocks are never read or stored. The API key lives in a ``SecretStr``
and in the SDK client; it is never part of a repr, log line or error message.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Literal

import anthropic

from app.core.cache import ResponseCache, cache_key
from app.core.config import ALLOWED_ANTHROPIC_MODELS, Settings
from app.core.costs import CostMeter
from app.core.errors import (
    LLMAuthError,
    LLMModelNotAllowed,
    LLMRefusal,
    LLMSchemaError,
    LLMTransportError,
    LLMWorkspaceRequired,
)
from app.core.llm import (
    LLMRequest,
    LLMResult,
    LLMVisionUnsupported,
    OpenAICompatClient,
    _get,
    _log_call,
    _record,
    concurrency_gate,
    estimate_tokens,
    file_sha_from_extra,
    validate_json_schema,
)

ANTHROPIC_CLIENT_VERSION = "a1"  # bump when message construction or parsing changes (cache key)
ANTHROPIC_BASE_URL = "https://api.anthropic.com"  # fixed: the paid key never goes anywhere else
SDK_MAX_RETRIES = 2  # the SDK retries 408/409/429/5xx and connection errors itself
WORKSPACE_HEADER = "anthropic-workspace-id"
TOOL_DESCRIPTION = "Return the structured result."
SYSTEM_SUFFIX = "Reply only by calling the tool {name} once."

_TOOL_NAME_BAD = re.compile(r"[^A-Za-z0-9_-]")
_KEY_LIKE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def _tool_name(schema_name: str) -> str:
    return _TOOL_NAME_BAD.sub("_", schema_name)[:64] or "result"


class AnthropicLLMClient:
    """Implements ``LLMClient`` over ``anthropic.Anthropic().messages.create``."""

    def __init__(
        self,
        settings: Settings,
        *,
        cache: ResponseCache | None = None,
        sdk: Any | None = None,
        http_client: Any | None = None,
    ):
        settings.validate_anthropic()  # SettingsError: key missing or a model that is not allowed
        self.settings = settings
        self.cache = cache
        self._gate = concurrency_gate("anthropic", settings.llm_max_concurrency)
        self._secrets = [settings.anthropic_api_key_value]
        if sdk is None:
            extra: dict[str, Any] = {}
            if http_client is not None:
                extra["http_client"] = http_client
            workspace = settings.anthropic_workspace_id.strip()
            sdk = anthropic.Anthropic(
                api_key=settings.anthropic_api_key_value,
                base_url=ANTHROPIC_BASE_URL,
                timeout=settings.anthropic_timeout_s,
                max_retries=SDK_MAX_RETRIES,
                default_headers={WORKSPACE_HEADER: workspace} if workspace else None,
                **extra,
            )
        self._sdk = sdk

    def __repr__(self) -> str:  # never the key, never the SDK client
        workspace = "set" if self.settings.anthropic_workspace_id.strip() else "unset"
        return f"AnthropicLLMClient(model={self.settings.anthropic_model!r}, workspace={workspace})"

    __str__ = __repr__

    @property
    def max_output_tokens(self) -> int:
        return self.settings.llm_max_output_tokens

    @property
    def client_version(self) -> str:
        return ANTHROPIC_CLIENT_VERSION

    def model_for(self, role: Literal["cheap", "strong"]) -> str:
        return self.settings.anthropic_model  # one model for both roles

    @staticmethod
    def assert_model_allowed(model: str) -> None:
        if model not in ALLOWED_ANTHROPIC_MODELS:
            raise LLMModelNotAllowed(
                f"model {model!r} is not allowed on the anthropic backend "
                f"(only {', '.join(sorted(ALLOWED_ANTHROPIC_MODELS))})"
            )

    # -------------------------------------------------------------------- request

    def _content(self, req: LLMRequest, feedback: str | None) -> list[dict]:
        if req.images and not self.settings.llm_supports_vision:
            raise LLMVisionUnsupported(
                f"{req.purpose}: request has {len(req.images)} image(s) but "
                "GLDB_LLM_SUPPORTS_VISION=false"
            )
        blocks: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(img).decode("ascii"),
                },
            }
            for img in req.images
        ]
        blocks.append({"type": "text", "text": req.user})
        if feedback:  # same user turn: the conversation must end with a user message (no prefill)
            blocks.append(
                {
                    "type": "text",
                    "text": f"Your previous reply was not valid: {feedback[:200]}. "
                    "Call the tool again.",
                }
            )
        return blocks

    def _kwargs(self, req: LLMRequest, model: str, feedback: str | None) -> dict[str, Any]:
        name = _tool_name(req.schema_name)
        return {
            "model": model,
            "max_tokens": self.max_output_tokens,
            "system": req.system + "\n\n" + SYSTEM_SUFFIX.format(name=name),
            "messages": [{"role": "user", "content": self._content(req, feedback)}],
            "tools": [
                {"name": name, "description": TOOL_DESCRIPTION, "input_schema": req.json_schema}
            ],
            "tool_choice": {"type": "auto"},
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "low"},
        }

    # -------------------------------------------------------------------- transport

    def _clean(self, exc: Exception) -> str:
        """The API's error message, one line, with anything key-shaped removed."""
        body = getattr(exc, "body", None)
        error = body.get("error") if isinstance(body, dict) else None
        detail = error.get("message") if isinstance(error, dict) else None
        text = str(detail or getattr(exc, "message", "") or exc)
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return " ".join(_KEY_LIKE.sub("[redacted]", text).split())[:200]

    def _translate(self, exc: anthropic.AnthropicError) -> LLMTransportError:
        """Map an SDK error (most specific first) onto the LLM error family. The message is ours
        plus the sanitised API text; never ``repr(exc)`` (headers) and never the key."""
        rid = getattr(exc, "request_id", None)
        suffix = f" (request id {rid})" if rid else ""
        if isinstance(exc, anthropic.AuthenticationError):
            return LLMAuthError(
                "anthropic rejected the API key (401); check GLDB_ANTHROPIC_API_KEY" + suffix
            )
        if isinstance(exc, anthropic.PermissionDeniedError):
            return LLMAuthError(
                "anthropic denied access (403): the key may not use this model or workspace; "
                f"check GLDB_ANTHROPIC_API_KEY and GLDB_ANTHROPIC_WORKSPACE_ID{suffix}"
            )
        if isinstance(exc, anthropic.NotFoundError):
            return LLMTransportError(
                f"anthropic status 404: model {self.settings.anthropic_model!r} not found for this key{suffix}"
            )
        if isinstance(exc, anthropic.BadRequestError):
            text = self._clean(exc)
            if "workspace" in text.lower():
                return LLMWorkspaceRequired(
                    "anthropic needs a workspace id for this key: set GLDB_ANTHROPIC_WORKSPACE_ID "
                    f"(the key is user-scoped and the API requires the {WORKSPACE_HEADER} header)"
                    + suffix
                )
            return LLMTransportError(f"anthropic status 400: {text}{suffix}")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMTransportError(
                f"anthropic status 429 (rate limited; SDK retries used){suffix}"
            )
        if isinstance(exc, anthropic.APIStatusError):
            return LLMTransportError(f"anthropic status {exc.status_code}{suffix}")
        if isinstance(exc, anthropic.APITimeoutError):  # subclass of APIConnectionError: first
            return LLMTransportError("anthropic request timed out (GLDB_ANTHROPIC_TIMEOUT_S)")
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMTransportError("transport error: APIConnectionError")
        return LLMTransportError(f"transport error: {type(exc).__name__}")

    def _create(self, req: LLMRequest, model: str, feedback: str | None) -> Any:
        kwargs = self._kwargs(req, model, feedback)
        self.assert_model_allowed(kwargs["model"])  # the last gate before anything leaves
        try:
            with self._gate:  # at most GLDB_LLM_MAX_CONCURRENCY calls in flight, all threads
                return self._sdk.messages.create(**kwargs)
        except anthropic.AnthropicError as exc:
            raise self._translate(exc) from None

    # -------------------------------------------------------------------- parsing

    @staticmethod
    def _parse(
        response: Any, schema: dict | None = None, name: str = ""
    ) -> tuple[dict | None, str | None]:
        """(data, error). The tool_use input first (already a dict), then JSON found in text blocks
        (the model answered in text instead of calling the tool). Thinking blocks are skipped by
        type and never read. With a ``schema`` the first object that validates wins."""
        candidates: list[object] = []
        texts: list[object] = []
        for block in _get(response, "content") or []:
            kind = _get(block, "type")
            if kind == "tool_use":
                candidates.append(_get(block, "input"))
            elif kind == "text" and (_get(block, "text") or "").strip():
                texts.append(_get(block, "text"))
        candidates += texts
        if not candidates:
            return None, "no tool call and no content"
        first: dict | None = None
        error: str | None = None
        for raw in candidates:
            objects, err = OpenAICompatClient._objects_in(raw, name)
            error = error or err
            for obj in objects:
                if schema is None or validate_json_schema(obj, schema) is None:
                    return obj, None
                first = first or obj
        if first is not None:
            return first, None
        return None, error or "not valid JSON"

    def _check_stop(self, response: Any, req: LLMRequest) -> None:
        stop = _get(response, "stop_reason")
        if stop == "refusal":
            category = _get(_get(response, "stop_details"), "category") or "unspecified"
            raise LLMRefusal(f"{req.purpose}: the model refused the request (category: {category})")
        if stop == "max_tokens":
            raise LLMSchemaError(
                f"{req.purpose}: output cut off at max_tokens ({self.max_output_tokens}); "
                "raise GLDB_LLM_MAX_OUTPUT_TOKENS"
            )

    @staticmethod
    def _usage(response: Any, req: LLMRequest, ctx_costs: CostMeter | None) -> tuple[int, int]:
        """(prompt_tokens, completion_tokens). The prompt size is input + cache reads + cache
        writes: it decides the price tier and cache tokens are billed at the plain input rate."""
        usage = _get(response, "usage")
        base = _get(usage, "input_tokens")
        out = _get(usage, "output_tokens")
        if base is None or out is None:
            if ctx_costs is not None:
                ctx_costs.add_warning("usage_missing")
            blocks = [
                _get(b, "input") if _get(b, "type") == "tool_use" else _get(b, "text")
                for b in _get(response, "content") or []
                if _get(b, "type") in ("tool_use", "text")
            ]
            return estimate_tokens(req.system, req.user), estimate_tokens(
                json.dumps(blocks, default=str)
            )
        prompt = (
            int(base)
            + int(_get(usage, "cache_read_input_tokens", 0) or 0)
            + int(_get(usage, "cache_creation_input_tokens", 0) or 0)
        )
        return prompt, int(out)

    # -------------------------------------------------------------------- main entry

    def complete_json(self, req: LLMRequest, *, ctx_costs: CostMeter | None = None) -> LLMResult:
        model = self.model_for(req.model_role)
        self.assert_model_allowed(model)
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
                ctx_costs.reserve_call()  # raises LLMBudgetExceeded (per case or per day)
            started = time.perf_counter()
            response = self._create(req, model, feedback)
            seconds = time.perf_counter() - started

            prompt_tokens, completion_tokens = self._usage(response, req, ctx_costs)
            cost = _record(
                ctx_costs,
                req,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                seconds=seconds,
            )
            _log_call(req, cost)

            self._check_stop(response, req)  # refusal / max_tokens: billed above, never retried

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
