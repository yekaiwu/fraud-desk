"""The one place an agent talks to the model (docs/agents.md).

Same rules as the semantic checks: the prompt is registered for provenance, document text goes in the
``user`` message between untrusted markers, and any failure (transport, budget, malformed reply)
raises ``AgentFailed`` so the caller keeps the flags exactly as they were. An agent never turns a
failure into a verdict.
"""

from __future__ import annotations

from typing import Any

from app.core.context import CaseContext
from app.core.errors import LLMError
from app.core.llm import LLMRequest, register_prompt
from app.core.models import clean_text
from app.semantic.call import UNTRUSTED_NOTICE

PURPOSE_PREFIX = "agent:"
AGENTS_VERSION = (
    "1"  # bump when a prompt or the handling of a reply changes (part of the cache key)
)


class AgentFailed(Exception):  # noqa: N818 - a state, like SemanticNotRun
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def system_prompt(task: str) -> str:
    return f"{task.strip()}\n\n{UNTRUSTED_NOTICE}"


def ask(
    ctx: CaseContext,
    *,
    name: str,
    task: str,
    user: str,
    schema_name: str,
    json_schema: dict[str, Any],
) -> dict[str, Any]:
    system = system_prompt(task)
    sha = register_prompt(PURPOSE_PREFIX + name, system)
    req = LLMRequest(
        purpose=PURPOSE_PREFIX + name,
        model_role="cheap",
        system=system,
        user=user,
        schema_name=schema_name,
        json_schema=json_schema,
        cache_extra=f"agents{AGENTS_VERSION}:{sha[:16]}",
    )
    try:
        with ctx.costs.scope(stage="crossdoc"):
            return ctx.llm.complete_json(req, ctx_costs=ctx.costs).data
    except Exception as exc:  # noqa: BLE001 - every failure leaves the flags as they were
        kind = type(exc).__name__
        detail = f": {clean_text(str(exc), 200)}" if isinstance(exc, LLMError) else ""
        raise AgentFailed(kind + detail) from None
