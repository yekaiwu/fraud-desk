"""AnthropicLLMClient and the anthropic settings, with the official SDK mocked at the transport.

The real ``anthropic.Anthropic`` client is built with an ``httpx2.MockTransport`` (the SDK 2.x runs on
httpx2), so request bodies, headers, SDK retries and typed exceptions are the real thing; nothing
touches the network and the key below is a made-up string.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from decimal import Decimal
from pathlib import Path

import anthropic
import httpx2
import pytest

from app.core.cache import ResponseCache
from app.core.config import (
    ALLOWED_ANTHROPIC_MODELS,
    ANTHROPIC_HAIKU_MODEL,
    Settings,
    SettingsError,
)
from app.core.costs import BudgetLimits, CostMeter, LLMBudgetExceeded, PriceTable
from app.core.db import Database
from app.core.errors import (
    LLMAuthError,
    LLMModelNotAllowed,
    LLMRefusal,
    LLMSchemaError,
    LLMTransportError,
    LLMWorkspaceRequired,
)
from app.core.llm import FakeLLMClient, LLMRequest, OpenAICompatClient, get_llm_client
from app.core.llm_anthropic import AnthropicLLMClient

KEY = "sk-ant-api03-FAKE-not-a-real-key-0123456789"
SCHEMA = {
    "type": "object",
    "properties": {"total": {"type": "string"}},
    "required": ["total"],
    "additionalProperties": False,
}
SHA = "b" * 64
PNG = b"\x89PNG\r\n\x1a\nfake"
FAST = {"retry-after-ms": "1"}  # make the SDK's own retries instant


def request(**kw) -> LLMRequest:  # noqa: ANN003
    base = dict(
        purpose="extract:invoice",
        system="Extract.",
        user="[[[DOC]]]",
        schema_name="extract_invoice",
        json_schema=SCHEMA,
        cache_extra=f"{SHA}:1:text",
    )
    base.update(kw)
    return LLMRequest(**base)


def message(
    content: list[dict],
    *,
    stop_reason: str = "end_turn",
    usage: dict | None = None,
    stop_details: dict | None = None,
) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": ANTHROPIC_HAIKU_MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage or {"input_tokens": 100, "output_tokens": 20},
    }


def tool_use(data: dict) -> dict:
    return {"type": "tool_use", "id": "toolu_1", "name": "extract_invoice", "input": data}


def text(t: str) -> dict:
    return {"type": "text", "text": t}


class Server:
    """Scripted Anthropic API: each item is a message dict (200) or (status, body[, headers])."""

    def __init__(self, *responses):  # noqa: ANN002
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []
        self.bodies: list[dict] = []
        self._lock = threading.Lock()

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        with self._lock:
            self.requests.append(request)
            self.bodies.append(json.loads(request.content))
            item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        if isinstance(item, tuple):
            status, body, *rest = item
            return httpx2.Response(status, json=body, headers=rest[0] if rest else None)
        return httpx2.Response(200, json=item, headers={"request-id": "req_test_1"})

    @property
    def calls(self) -> int:
        return len(self.requests)


def api_error(message_text: str, kind: str = "invalid_request_error") -> dict:
    return {"type": "error", "error": {"type": kind, "message": message_text}}


def make_settings(**kw) -> Settings:  # noqa: ANN003
    base = dict(_env_file=None, llm_backend="anthropic", anthropic_api_key=KEY)
    base.update(kw)
    return Settings(**base)


def make_client(server: Server, *, cache=None, **kw) -> AnthropicLLMClient:  # noqa: ANN001, ANN003
    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(server.handler))
    return AnthropicLLMClient(make_settings(**kw), cache=cache, http_client=http)


def make_meter(**kw) -> CostMeter:  # noqa: ANN003
    limits = BudgetLimits(
        max_pages=kw.pop("pages", 10),
        max_calls=kw.pop("calls", 10),
        max_usd=Decimal(kw.pop("usd", "5")),
    )
    return CostMeter(limits, PriceTable.load(None))


def run(client: AnthropicLLMClient, req: LLMRequest, meter: CostMeter | None = None):  # noqa: ANN201
    meter = meter or make_meter()
    with meter.scope(stage="extract", document_id="d1", page=1):
        return client.complete_json(req, ctx_costs=meter)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK backs off between its own retries; keep the tests fast."""
    real = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real(min(s, 0.001)))


# --------------------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------------------


def test_defaults_and_allow_list() -> None:
    s = Settings(_env_file=None)
    assert s.llm_backend == "auto" and s.effective_llm_backend() == "fake"
    assert s.anthropic_model == "claude-haiku-5-5" and s.anthropic_workspace_id == ""
    assert s.anthropic_api_key_value == "" and s.anthropic_timeout_s == 120
    assert s.max_usd_per_day == Decimal("5.00") and s.max_usd_per_case == Decimal("2.00")
    assert {"claude-haiku-5-5"} == ALLOWED_ANTHROPIC_MODELS


def test_auto_never_selects_anthropic() -> None:
    s = Settings(_env_file=None, llm_backend="auto", anthropic_api_key=KEY)
    assert s.effective_llm_backend() == "fake"
    both = Settings(_env_file=None, llm_backend="auto", anthropic_api_key=KEY, llm_api_key="sk-x")
    assert both.effective_llm_backend() == "openai"  # the openai rule is unchanged


def test_anthropic_backend_resolves_both_roles_to_haiku_with_vision() -> None:
    s = make_settings(
        llm_model_cheap="vendor/cheap", llm_model_strong="vendor/strong", llm_supports_vision=False
    )
    assert s.effective_llm_backend() == "anthropic"
    assert s.llm_model_cheap == s.llm_model_strong == "claude-haiku-5-5"
    assert s.llm_supports_vision is True
    s.validate_startup()


def test_startup_needs_the_key() -> None:
    with pytest.raises(SettingsError, match="GLDB_ANTHROPIC_API_KEY"):
        make_settings(anthropic_api_key="").validate_startup()
    with pytest.raises(SettingsError, match="GLDB_ANTHROPIC_API_KEY"):
        get_llm_client(make_settings(anthropic_api_key=""))


@pytest.mark.parametrize(
    "model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "Claude-Haiku-5-5", " "]
)
def test_only_haiku_5_5_is_accepted(model: str) -> None:
    s = make_settings(anthropic_model=model)
    with pytest.raises(SettingsError, match="claude-haiku-5-5"):
        s.validate_startup()
    with pytest.raises(SettingsError, match="claude-haiku-5-5"):
        get_llm_client(s)
    with pytest.raises(SettingsError, match="claude-haiku-5-5"):
        AnthropicLLMClient(s)


def test_a_wrong_model_is_not_checked_when_the_backend_is_not_anthropic() -> None:
    Settings(
        _env_file=None, llm_backend="fake", anthropic_model="claude-opus-5-5"
    ).validate_startup()


def test_other_new_setting_validation() -> None:
    with pytest.raises(SettingsError, match="GLDB_MAX_USD_PER_DAY"):
        Settings(_env_file=None, max_usd_per_day=Decimal("-1")).validate_startup()
    Settings(_env_file=None, max_usd_per_day=Decimal("0")).validate_startup()  # 0 disables
    with pytest.raises(SettingsError, match="GLDB_ANTHROPIC_TIMEOUT_S"):
        Settings(_env_file=None, anthropic_timeout_s=0).validate_startup()


def test_env_file_profile_loads(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        f"GLDB_LLM_BACKEND=anthropic\nGLDB_ANTHROPIC_API_KEY={KEY}\n"
        "GLDB_ANTHROPIC_MODEL=claude-haiku-5-5\nGLDB_ANTHROPIC_WORKSPACE_ID=wrkspc_01ABC\n"
        "GLDB_MAX_USD_PER_DAY=3.50\n"
    )
    s = Settings(_env_file=env)
    assert s.effective_llm_backend() == "anthropic" and s.anthropic_api_key_value == KEY
    assert s.anthropic_workspace_id == "wrkspc_01ABC" and s.max_usd_per_day == Decimal("3.50")
    s.validate_startup()


def test_tests_do_not_read_the_developers_dotenv(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """conftest turns the default env file off, so a .env in the working directory is ignored."""
    (tmp_path / ".env").write_text("GLDB_LLM_BACKEND=anthropic\nGLDB_MAX_USD_PER_DAY=99\n")
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.llm_backend == "auto" and s.max_usd_per_day == Decimal("5.00")


def test_provenance_hash_input_moves_only_for_non_default_new_settings() -> None:
    base = Settings(_env_file=None).result_settings()
    assert not {"anthropic_model", "max_usd_per_day"} & set(base)
    assert not any("api_key" in k or "workspace" in k for k in base)
    anth = make_settings().result_settings()
    assert anth["anthropic_model"] == "claude-haiku-5-5" and anth["llm_backend"] == "anthropic"
    assert anth["llm_model_cheap"] == anth["llm_model_strong"] == "claude-haiku-5-5"
    capped = Settings(_env_file=None, max_usd_per_day=Decimal("1.00")).result_settings()
    assert capped["max_usd_per_day"] == "1.00"


def test_factory_builds_the_anthropic_client_and_fake_stays_default() -> None:
    assert isinstance(get_llm_client(make_settings()), AnthropicLLMClient)
    assert isinstance(get_llm_client(Settings(_env_file=None)), FakeLLMClient)
    auto = Settings(_env_file=None, llm_api_key="k", llm_model_cheap="m", llm_model_strong="m")
    assert isinstance(get_llm_client(auto), OpenAICompatClient)


# --------------------------------------------------------------------------------------
# request shape
# --------------------------------------------------------------------------------------


def test_request_body_shape_for_haiku_5_5() -> None:
    server = Server(message([tool_use({"total": "1.00"})]))
    result = run(make_client(server), request())
    assert result.data == {"total": "1.00"} and result.model == "claude-haiku-5-5"
    body = server.bodies[0]
    assert set(body) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "tools",
        "tool_choice",
        "thinking",
        "output_config",
    }
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 8000
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"] == {"effort": "low"}
    assert body["tool_choice"] == {"type": "auto"}  # never forced
    assert not {"temperature", "top_p", "top_k", "stream", "metadata"} & set(body)
    (tool,) = body["tools"]
    assert tool == {
        "name": "extract_invoice",
        "description": "Return the structured result.",
        "input_schema": SCHEMA,
    }
    assert "strict" not in tool
    assert body["system"].startswith("Extract.") and "extract_invoice" in body["system"]
    (turn,) = body["messages"]  # one user turn, no assistant prefill
    assert turn["role"] == "user" and turn["content"] == [{"type": "text", "text": "[[[DOC]]]"}]
    assert "cache_control" not in json.dumps(body)


def test_max_output_tokens_setting_is_sent() -> None:
    server = Server(message([tool_use({"total": "1"})]))
    run(make_client(server, llm_max_output_tokens=1234), request())
    assert server.bodies[0]["max_tokens"] == 1234


def test_image_blocks_are_base64_png_before_the_text() -> None:
    import base64

    server = Server(message([tool_use({"total": "1"})]))
    run(make_client(server), request(images=[PNG, b"second"]))
    content = server.bodies[0]["messages"][0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(PNG).decode(),
        },
    }
    assert content[1]["source"]["data"] == base64.b64encode(b"second").decode()
    assert content[2] == {"type": "text", "text": "[[[DOC]]]"}


def test_workspace_header_present_only_when_configured() -> None:
    server = Server(message([tool_use({"total": "1"})]))
    run(make_client(server, anthropic_workspace_id="wrkspc_01ABC"), request())
    assert server.requests[0].headers["anthropic-workspace-id"] == "wrkspc_01ABC"
    server = Server(message([tool_use({"total": "1"})]))
    run(make_client(server), request())
    assert "anthropic-workspace-id" not in server.requests[0].headers
    server = Server(message([tool_use({"total": "1"})]))
    run(make_client(server, anthropic_workspace_id="   "), request())
    assert "anthropic-workspace-id" not in server.requests[0].headers


def test_request_goes_to_the_anthropic_messages_endpoint() -> None:
    server = Server(message([tool_use({"total": "1"})]))
    run(make_client(server), request())
    assert str(server.requests[0].url) == "https://api.anthropic.com/v1/messages"


def test_images_refused_when_vision_is_off_on_a_copied_settings_object() -> None:
    from app.core.llm import LLMVisionUnsupported

    server = Server(message([tool_use({"total": "1"})]))
    client = make_client(server)
    client.settings = client.settings.model_copy(update={"llm_supports_vision": False})
    with pytest.raises(LLMVisionUnsupported):
        run(client, request(images=[PNG]))
    assert server.calls == 0


# --------------------------------------------------------------------------------------
# Haiku only, at call time
# --------------------------------------------------------------------------------------


def test_call_time_guard_blocks_any_other_model(monkeypatch: pytest.MonkeyPatch) -> None:
    server = Server(message([tool_use({"total": "1"})]))
    client = make_client(server)
    monkeypatch.setattr(client, "model_for", lambda role: "claude-opus-5-5")
    with pytest.raises(LLMModelNotAllowed, match="claude-opus-5-5"):
        run(client, request())
    assert server.calls == 0


def test_call_time_guard_blocks_a_mutated_settings_object() -> None:
    server = Server(message([tool_use({"total": "1"})]))
    client = make_client(server)
    client.settings = client.settings.model_copy(update={"anthropic_model": "claude-sonnet-5-5"})
    for role in ("cheap", "strong"):
        with pytest.raises(LLMModelNotAllowed):
            run(client, request(model_role=role))
    with pytest.raises(LLMModelNotAllowed):
        client._create(request(), "claude-fable-5-1", None)
    assert server.calls == 0


def test_both_roles_use_haiku() -> None:
    server = Server(message([tool_use({"total": "1"})]))
    client = make_client(server)
    run(client, request(model_role="strong"))
    assert {b["model"] for b in server.bodies} == {"claude-haiku-5-5"}


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------


def test_text_json_fallback_when_the_model_answers_in_text() -> None:
    server = Server(message([text('Here you go:\n```json\n{"total": "7.00"}\n```')]))
    assert run(make_client(server), request()).data == {"total": "7.00"}


def test_text_call_wrapper_is_unwrapped() -> None:
    server = Server(message([text('{"name": "extract_invoice", "arguments": {"total": "9.00"}}')]))
    assert run(make_client(server), request()).data == {"total": "9.00"}


def test_thinking_blocks_are_never_read() -> None:
    wrong = '{"total": "FROM-THINKING"}'
    thinking = {"type": "thinking", "thinking": wrong, "signature": "sig"}
    redacted = {"type": "redacted_thinking", "data": "xxx"}
    server = Server(message([thinking, redacted, tool_use({"total": "right"})]))
    assert run(make_client(server), request()).data == {"total": "right"}
    # only thinking blocks: nothing to read, so the answer is rejected (and retried once)
    server = Server(message([thinking]))
    with pytest.raises(LLMSchemaError, match="no tool call and no content"):
        run(make_client(server), request())
    assert server.calls == 2


def test_tool_use_wins_over_text() -> None:
    server = Server(message([text('{"total": "from text"}'), tool_use({"total": "from tool"})]))
    assert run(make_client(server), request()).data == {"total": "from tool"}


def test_invalid_output_is_retried_once_with_feedback_and_no_prefill() -> None:
    server = Server(
        message([tool_use({"nope": 1})]),
        message([tool_use({"total": "2.00"})]),
    )
    meter = make_meter()
    result = run(make_client(server), request(), meter)
    assert result.data == {"total": "2.00"} and server.calls == 2
    second = server.bodies[1]["messages"]
    assert [m["role"] for m in second] == ["user"]  # still ends with a user turn
    feedback = second[0]["content"][-1]["text"]
    assert "not valid" in feedback and "total" in feedback
    assert len(meter.costs()) == 2 and meter.calls_used == 2  # both calls counted and billed


def test_invalid_twice_raises_schema_error_after_exactly_two_calls() -> None:
    server = Server(message([tool_use({"nope": 1})]))
    with pytest.raises(LLMSchemaError, match="after 2 attempt"):
        run(make_client(server), request())
    assert server.calls == 2


def test_schema_retries_setting_zero_means_one_call() -> None:
    server = Server(message([tool_use({"nope": 1})]))
    with pytest.raises(LLMSchemaError, match="after 1 attempt"):
        run(make_client(server, llm_schema_retries=0), request())
    assert server.calls == 1


# --------------------------------------------------------------------------------------
# refusal and max_tokens
# --------------------------------------------------------------------------------------


def test_refusal_is_not_retried_and_is_a_failed_extraction() -> None:
    refusal = message(
        [],
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "x"},
    )
    server = Server(refusal)
    meter = make_meter()
    with pytest.raises(LLMRefusal, match=r"category: cyber") as err:
        run(make_client(server), request(), meter)
    assert isinstance(err.value, LLMSchemaError)  # the pipeline already handles this type
    assert server.calls == 1
    assert len(meter.costs()) == 1  # the call is still recorded


def test_refusal_without_a_category() -> None:
    server = Server(message([], stop_reason="refusal"))
    with pytest.raises(LLMRefusal, match="unspecified"):
        run(make_client(server), request())


def test_refusal_becomes_a_page_read_error_the_extractor_reports(tmp_path: Path) -> None:
    """The extractor maps LLMSchemaError (so LLMRefusal) to PageReadError('schema'), which the
    cascade already turns into an unread page: partial coverage, never a crash."""
    from app.extract.llm_extractor import LLMExtractor, PageReadError
    from app.extract.registry import discover
    from tests.extract_helpers import StatementSpec, default_txns, make_case, render_statement

    discover()

    def refuse(req: LLMRequest) -> dict:
        raise LLMRefusal("extract:bank_statement: the model refused the request (category: bio)")

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="fake",
        extractor_mode="llm",
    )
    ctx, doc, _ = make_case(
        render_statement(StatementSpec(txns=default_txns(3))),
        "bank_statement",
        llm=FakeLLMClient({"extract:bank_statement": refuse}),
        settings=settings,
    )
    with pytest.raises(PageReadError) as err:
        LLMExtractor().read_page(doc, ctx, 1)
    assert err.value.kind == "schema" and "category: bio" in err.value.message


def test_max_tokens_names_the_setting_and_is_not_retried() -> None:
    server = Server(message([tool_use({"total": "1"})], stop_reason="max_tokens"))
    with pytest.raises(LLMSchemaError, match="GLDB_LLM_MAX_OUTPUT_TOKENS"):
        run(make_client(server), request())
    assert server.calls == 1


# --------------------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------------------


def test_400_mentioning_workspace_tells_the_owner_what_to_set() -> None:
    msg = "anthropic-workspace-id header is required for user-scoped API keys"
    server = Server((400, api_error(msg)))
    with pytest.raises(LLMWorkspaceRequired, match="GLDB_ANTHROPIC_WORKSPACE_ID") as err:
        run(make_client(server), request())
    assert isinstance(err.value, LLMTransportError) and server.calls == 1


def test_other_400_is_a_transport_error_with_the_api_message() -> None:
    server = Server((400, api_error("max_tokens: too large")))
    with pytest.raises(LLMTransportError, match="status 400: max_tokens: too large") as err:
        run(make_client(server), request())
    assert not isinstance(err.value, LLMWorkspaceRequired)


@pytest.mark.parametrize(
    ("status", "kind", "expected"),
    [
        (401, "authentication_error", LLMAuthError),
        (403, "permission_error", LLMAuthError),
        (404, "not_found_error", LLMTransportError),
        (422, "invalid_request_error", LLMTransportError),
        (413, "request_too_large", LLMTransportError),
    ],
)
def test_client_errors_are_mapped_without_a_retry(status, kind, expected) -> None:  # noqa: ANN001
    server = Server((status, api_error("nope", kind), {"request-id": "req_abc"}))
    with pytest.raises(expected, match=f"{status}") as err:
        run(make_client(server), request())
    assert server.calls == 1
    assert "req_abc" in str(err.value)


@pytest.mark.parametrize("status", [429, 500, 529])
def test_retryable_statuses_use_the_sdk_retries_then_map(status: int) -> None:
    server = Server((status, api_error("busy", "overloaded_error"), FAST))
    with pytest.raises(LLMTransportError, match=f"status {status}"):
        run(make_client(server), request())
    assert server.calls == 3  # 1 + the SDK's max_retries=2, and no second retry layer on top


def test_a_retried_429_that_recovers_is_one_logical_call() -> None:
    server = Server(
        (429, api_error("slow down", "rate_limit_error"), FAST), message([tool_use({"total": "1"})])
    )
    meter = make_meter()
    assert run(make_client(server), request(), meter).data == {"total": "1"}
    assert server.calls == 2 and meter.calls_used == 1


def test_connection_error_and_timeout_are_transport_errors() -> None:
    server = Server(httpx2.ConnectError("boom"))
    with pytest.raises(LLMTransportError, match="transport error"):
        run(make_client(server), request())
    assert server.calls == 3
    server = Server(httpx2.ReadTimeout("slow"))
    with pytest.raises(LLMTransportError, match="timed out"):
        run(make_client(server), request())


def test_error_messages_never_carry_the_key_even_if_the_server_echoes_it() -> None:
    echo = f"invalid x-api-key {KEY} (also {KEY[:20]}XYZ sk-ant-api03-OTHER-0000)"
    for status in (400, 401, 403, 404, 422):
        server = Server((status, api_error(echo)))
        with pytest.raises(LLMTransportError) as err:
            run(make_client(server), request())
        shown = "".join(traceback.format_exception(err.value)) + str(err.value) + repr(err.value)
        assert KEY not in shown and "OTHER-0000" not in shown


# --------------------------------------------------------------------------------------
# cache, telemetry, budget, concurrency
# --------------------------------------------------------------------------------------


def test_cache_hit_makes_zero_calls_and_costs_nothing() -> None:
    db = Database(":memory:")
    db.init_schema()
    cache = ResponseCache(db)
    server = Server(message([tool_use({"total": "5.00"})]))
    client = make_client(server, cache=cache)
    first = run(client, request())
    assert not first.cache_hit and server.calls == 1
    meter = make_meter()
    second = run(client, request(), meter)
    assert second.cache_hit and second.data == {"total": "5.00"} and server.calls == 1
    (cost,) = meter.costs()
    assert cost.cache_hit and cost.usd == 0 and meter.calls_used == 0


def test_cache_key_depends_on_page_prompt_and_client_version() -> None:
    db = Database(":memory:")
    db.init_schema()
    cache = ResponseCache(db)
    server = Server(message([tool_use({"total": "5.00"})]))
    client = make_client(server, cache=cache)
    run(client, request())
    run(client, request(cache_extra=f"{SHA}:2:text"))  # another page
    run(client, request(user="[[[OTHER]]]"))  # another prompt
    assert server.calls == 3
    run(client, request())
    assert server.calls == 3
    assert client.client_version == "a1" != "1"  # never shares keys with the openai client


def test_cost_recorded_at_haiku_prices() -> None:
    server = Server(
        message([tool_use({"total": "1"})], usage={"input_tokens": 1000, "output_tokens": 200})
    )
    meter = make_meter()
    result = run(make_client(server), request(), meter)
    assert result.cost.model == "claude-haiku-5-5"
    assert (result.cost.prompt_tokens, result.cost.completion_tokens) == (1000, 200)
    assert result.cost.usd == Decimal("0.000200")  # 1000*0.10/1e6 + 200*0.50/1e6


def test_cache_tokens_count_toward_prompt_size_and_are_billed_as_plain_input() -> None:
    usage = {
        "input_tokens": 60_000,
        "cache_read_input_tokens": 30_000,
        "cache_creation_input_tokens": 20_000,
        "output_tokens": 1_000,
    }
    server = Server(message([tool_use({"total": "1"})], usage=usage))
    result = run(make_client(server), request())
    assert result.cost.prompt_tokens == 110_000  # above 100K: the long tier decides the price
    # (110000 * 0.50 + 1000 * 2.50) / 1e6
    assert result.cost.usd == Decimal("0.057500")


def test_response_without_usage_is_estimated_with_a_warning() -> None:
    class NoUsage(AnthropicLLMClient):
        def _create(self, req, model, feedback):  # noqa: ANN001, ANN202
            resp = super()._create(req, model, feedback)
            resp.usage = None
            return resp

    server = Server(message([tool_use({"total": "1"})]))
    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(server.handler))
    client = NoUsage(make_settings(), http_client=http)
    meter = make_meter()
    result = run(client, request(), meter)
    assert result.cost.prompt_tokens > 0 and "usage_missing" in meter.drain_warnings()


def test_budget_guard_stops_the_retry() -> None:
    server = Server(message([tool_use({"nope": 1})]))
    with pytest.raises(LLMBudgetExceeded, match="max_calls"):
        run(make_client(server), request(), make_meter(calls=1))
    assert server.calls == 1  # the retry was refused before it left the machine


def test_dollar_budget_counts_real_haiku_dollars() -> None:
    usage = {"input_tokens": 100_000, "output_tokens": 0}  # exactly the low tier: $0.01 per call
    server = Server(message([tool_use({"total": "1"})], usage=usage))
    client = make_client(server)
    meter = make_meter(usd="0.015")
    run(client, request(user="a"), meter)
    run(client, request(user="b"), meter)  # total 0.01 < 0.015: allowed, crosses to 0.02
    with pytest.raises(LLMBudgetExceeded, match="max_usd"):
        run(client, request(user="c"), meter)
    assert server.calls == 2


def test_concurrency_gate_caps_in_flight_calls() -> None:
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(_request: httpx2.Request) -> httpx2.Response:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        threading.Event().wait(0.05)  # not time.sleep: the autouse fixture shortens that
        with lock:
            state["now"] -= 1
        return httpx2.Response(200, json=message([tool_use({"total": "1"})]))

    server = Server(slow)
    client = make_client(server, llm_max_concurrency=2)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            run(client, request(user=f"u{i}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and server.calls == 8
    assert 1 <= state["peak"] <= 2


# --------------------------------------------------------------------------------------
# the key never leaks
# --------------------------------------------------------------------------------------


def test_the_key_is_absent_from_every_rendering(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    settings = make_settings(anthropic_workspace_id="wrkspc_01ABC")
    server = Server(
        message([tool_use({"total": "1"})]),
        (401, api_error(f"bad key {KEY}", "authentication_error")),
    )
    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(server.handler))
    client = AnthropicLLMClient(settings, http_client=http)
    run(client, request())  # a successful call, logged at DEBUG everywhere
    with pytest.raises(LLMAuthError) as err:
        run(client, request(user="again"))

    shown = [
        repr(settings),
        str(settings),
        f"{settings!r}",
        settings.model_dump_json(),
        str(settings.model_dump()),
        json.dumps(settings.result_settings()),
        repr(client),
        str(client),
        f"{client!r} {client.settings!r}",
        str(err.value),
        repr(err.value),
        "".join(traceback.format_exception(err.value)),
        caplog.text,
    ]
    for text_ in shown:
        assert KEY not in text_
    assert "sk-ant-" not in repr(settings) + repr(client)
    assert settings.anthropic_api_key_value == KEY  # it is there for the client, and only there


def test_validation_error_does_not_echo_the_key() -> None:
    with pytest.raises(Exception) as err:  # noqa: B017 - pydantic ValidationError
        Settings(_env_file=None, anthropic_api_key=KEY, anthropic_timeout_s="not-a-number")
    assert KEY not in str(err.value)
