"""LLM error family (re-exported from ``app.core.llm``; lives here to avoid an import cycle)."""

from __future__ import annotations


class LLMError(Exception):
    """Base class of every model-call failure."""


class LLMSchemaError(LLMError):
    """Model output did not parse or validate after ``GLDB_LLM_SCHEMA_RETRIES`` retries."""


class LLMRefusal(LLMSchemaError):
    """The model declined the request (``stop_reason == "refusal"``). Not retried; handled like any
    other failed extraction (an info flag and a partial score), with the category in the message."""


class LLMNoFixture(LLMError):
    """FakeLLMClient has no fixture for the request."""


class LLMTransportError(LLMError):
    """Timeout / 5xx / 429 after retries, or 400/401/403. Message is sanitised (never the key)."""


class LLMAuthError(LLMTransportError):
    """The provider rejected the key (401) or the key may not use the model/workspace (403)."""


class LLMWorkspaceRequired(LLMTransportError):
    """The Anthropic API wants the ``anthropic-workspace-id`` header (user-scoped key):
    set ``GLDB_ANTHROPIC_WORKSPACE_ID``."""


class LLMModelNotAllowed(LLMError):
    """A request named a model outside ``ALLOWED_ANTHROPIC_MODELS``. Nothing was sent."""


class ApiError(Exception):
    """Raised by routers; ``main.py`` renders it as ``{"error": {"code", "message", "details"}}``.

    Never carries a stack trace or document text to the client."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ):
        super().__init__(f"{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def not_implemented(what: str = "this endpoint") -> ApiError:
    return ApiError(501, "not_implemented", f"{what} is not implemented yet.")
