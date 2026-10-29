"""Smoke test of the Anthropic backend (docs/anthropic-haiku.md section 9).

    uv run python backend/scripts/anthropic_smoke.py [--dry-run]

Makes ONE tiny schema-constrained call (max_tokens 256) to Claude Haiku 5.5 with the settings of the
app (``.env``: GLDB_ANTHROPIC_API_KEY, GLDB_ANTHROPIC_MODEL, GLDB_ANTHROPIC_WORKSPACE_ID). It does not
need ``GLDB_LLM_BACKEND=anthropic`` to be set yet: that is what it lets you check first. It costs a
fraction of a cent. It prints the model, input and output tokens, the dollars computed from the price
table, the seconds and the request id; it never prints the key. ``--dry-run`` checks the settings and
prints what would be sent without sending anything.

Exit codes: 0 the call worked; 1 the call failed or the answer was wrong; 2 the setup is not usable:
the key is missing or rejected, the model is not claude-haiku-5-5, or the API wants a workspace id.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import Settings, SettingsError  # noqa: E402
from app.core.costs import PriceTable  # noqa: E402
from app.core.errors import (  # noqa: E402
    LLMAuthError,
    LLMError,
    LLMTransportError,
    LLMWorkspaceRequired,
)
from app.core.llm import LLMRequest  # noqa: E402
from app.core.llm_anthropic import AnthropicLLMClient  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_UNUSABLE = 0, 1, 2
MAX_TOKENS = 256  # a forced-schema tool call alone costs ~75 output tokens; 64 truncates it
SCHEMA = {
    "type": "object",
    "properties": {"capital": {"type": "string"}, "letters": {"type": "integer"}},
    "required": ["capital", "letters"],
    "additionalProperties": False,
}
REQUEST = LLMRequest(
    purpose="extract:smoke",
    system="You are a precise assistant.",
    user="What is the capital of France, and how many letters does its name have? "
    "Reply by calling the tool.",
    schema_name="smoke_capital",
    json_schema=SCHEMA,
)


def unusable(message: str) -> int:
    print(f"ANTHROPIC BACKEND NOT USABLE: {message}")
    return EXIT_UNUSABLE


def main(argv: Sequence[str] | None = None, *, http_client: Any | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="check the settings, send nothing")
    args = ap.parse_args(argv)

    try:
        # keyword arguments beat the environment: the backend is forced, the output kept tiny
        settings = Settings(llm_backend="anthropic", llm_max_output_tokens=MAX_TOKENS)
        client = AnthropicLLMClient(settings, http_client=http_client)
    except SettingsError as exc:
        return unusable(f"{exc}. Put GLDB_ANTHROPIC_API_KEY in .env (docs/anthropic-haiku.md).")

    workspace = "set" if settings.anthropic_workspace_id.strip() else "unset"
    print(f"model       {settings.anthropic_model}")
    print(f"key         set (not shown)   workspace id {workspace}")
    print(
        f"limits      max_tokens={MAX_TOKENS} timeout={settings.anthropic_timeout_s:g}s "
        f"per case ${settings.max_usd_per_case} per day ${settings.max_usd_per_day}"
    )
    if args.dry_run:
        print("dry run     nothing was sent")
        return EXIT_OK

    model = client.model_for("cheap")
    started = time.perf_counter()
    try:
        response = client._create(REQUEST, model, None)
    except LLMWorkspaceRequired as exc:
        return unusable(str(exc))
    except LLMAuthError as exc:
        return unusable(str(exc))
    except LLMTransportError as exc:
        print(f"CALL FAILED: {exc}")
        return EXIT_FAILED
    except LLMError as exc:
        print(f"CALL FAILED: {type(exc).__name__}: {exc}")
        return EXIT_FAILED
    seconds = time.perf_counter() - started

    prompt_tokens, completion_tokens = client._usage(response, REQUEST, None)
    usd, known = PriceTable.load(None).cost(model, prompt_tokens, completion_tokens)
    data, error = client._parse(response, SCHEMA, REQUEST.schema_name)
    request_id = getattr(response, "_request_id", None) or "n/a"
    print(f"input       {prompt_tokens} tokens")
    print(f"output      {completion_tokens} tokens")
    print(f"dollars     ${usd:f} ({'price table' if known else 'price UNKNOWN'})")
    print(f"seconds     {seconds:.2f}")
    print(f"request id  {request_id}")
    print(f"stop reason {getattr(response, 'stop_reason', None)}")
    ok = (
        data is not None
        and error is None
        and str(data.get("capital", "")).strip().lower() == "paris"
        and data.get("letters") == 5
    )
    print(f"answer      {'OK  ' if ok else 'WRONG'} {data if data is not None else error}")
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
