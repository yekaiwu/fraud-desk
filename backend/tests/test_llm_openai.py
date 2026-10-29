"""OpenAICompatClient against a stub SDK (no network)."""

import json
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings, SettingsError
from app.core.costs import BudgetLimits, CostMeter, LLMBudgetExceeded, ModelPrice, PriceTable
from app.core.db import Database
from app.core.llm import (
    FakeLLMClient,
    LLMRequest,
    LLMSchemaError,
    LLMTransportError,
    OpenAICompatClient,
    get_llm_client,
)

SCHEMA = {
    "type": "object",
    "properties": {"total": {"type": "string"}},
    "required": ["total"],
    "additionalProperties": False,
}
SHA = "a" * 64
SECRET = "sk-super-secret-key"


def request(**kw) -> LLMRequest:  # noqa: ANN003
    base = dict(
        purpose="extract:invoice",
        system="Extract.",
        user="[[[DOC]]]",
        schema_name="Page",
        json_schema=SCHEMA,
        cache_extra=f"{SHA}:1:text",
    )
    base.update(kw)
    return LLMRequest(**base)


def tool_response(args, usage=(100, 20), cached=None):  # noqa: ANN001, ANN201
    call = NS(
        function=NS(name="Page", arguments=args if isinstance(args, str) else json.dumps(args))
    )
    details = NS(cached_tokens=cached) if cached is not None else None
    u = NS(prompt_tokens=usage[0], completion_tokens=usage[1], prompt_tokens_details=details)
    return NS(choices=[NS(message=NS(tool_calls=[call], content=None))], usage=u)


def text_response(text, usage=(10, 5)):  # noqa: ANN001, ANN201
    u = NS(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
    return NS(choices=[NS(message=NS(tool_calls=None, content=text))], usage=u)


class StubSDK:
    def __init__(self, *responses):  # noqa: ANN002
        self.responses = list(responses)
        self.kwargs: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.kwargs.append(kwargs)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


class HttpError(Exception):
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status} {body}")
        self.status_code = status


def make_settings(**kw) -> Settings:  # noqa: ANN003
    base = dict(
        _env_file=None,
        llm_backend="openai",
        llm_api_key=SECRET,
        llm_model_cheap="vendor/cheap",
        llm_model_strong="vendor/strong",
    )
    base.update(kw)
    return Settings(**base)


def make_client(sdk, *, cache=None, sleeps=None, **kw):  # noqa: ANN001, ANN003, ANN201
    sleeps = sleeps if sleeps is not None else []
    return OpenAICompatClient(make_settings(**kw), cache=cache, sdk=sdk, sleep=sleeps.append)


def make_meter(limits=None, table=None) -> CostMeter:  # noqa: ANN001
    return CostMeter(
        limits or BudgetLimits(max_pages=10, max_calls=10, max_usd=Decimal("5")),
        table
        or PriceTable(
            {"vendor/cheap": ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("2"))}
        ),
    )


def test_tool_call_path_and_request_shape() -> None:
    sdk = StubSDK(tool_response({"total": "1.00"}))
    client = make_client(sdk)
    meter = make_meter()
    with meter.scope(stage="extract", document_id="d1", page=2):
        result = client.complete_json(request(images=[b"\x89PNG"]), ctx_costs=meter)
    assert (
        result.data == {"total": "1.00"} and result.model == "vendor/cheap" and not result.cache_hit
    )
    kw = sdk.kwargs[0]
    assert kw["tool_choice"] == "auto"  # never forced (OpenRouter rejects it for Claude models)
    assert kw["temperature"] == 0 and kw["max_tokens"] == 8000
    tool = kw["tools"][0]["function"]
    assert tool["name"] == "Page" and tool["parameters"] == SCHEMA
    system = kw["messages"][0]["content"]
    assert (
        system.startswith("Extract.") and "Reply only by calling the function Page once." in system
    )
    user_parts = kw["messages"][1]["content"]
    assert user_parts[0] == {"type": "text", "text": "[[[DOC]]]"}
    assert user_parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    cost = result.cost
    assert (cost.prompt_tokens, cost.completion_tokens) == (100, 20)
    assert cost.usd == Decimal("0.000140") and (cost.document_id, cost.page) == ("d1", 2)
    assert meter.calls_used == 1 and len(meter.costs()) == 1


def test_strong_role_uses_strong_model() -> None:
    sdk = StubSDK(tool_response({"total": "1"}))
    client = make_client(sdk)
    client.complete_json(request(model_role="strong"))
    assert sdk.kwargs[0]["model"] == "vendor/strong"
    assert client.model_for("cheap") == "vendor/cheap"


def test_fenced_json_text_fallback() -> None:
    sdk = StubSDK(text_response('```json\n{"total": "9"}\n```'))
    assert make_client(sdk).complete_json(request()).data == {"total": "9"}


def test_invalid_then_valid_records_two_costs_and_sends_feedback() -> None:
    sdk = StubSDK(tool_response({"wrong": 1}), tool_response({"total": "2"}))
    meter = make_meter()
    with meter.scope(stage="extract"):
        result = make_client(sdk).complete_json(request(), ctx_costs=meter)
    assert result.data == {"total": "2"}
    assert len(meter.costs()) == 2 and meter.calls_used == 2  # each attempt is a separate call
    retry_messages = sdk.kwargs[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert retry_messages[-1]["content"].startswith("Your previous reply was not valid:")
    assert "Call the function again." in retry_messages[-1]["content"]


def test_invalid_twice_raises_and_caches_nothing(mem_db: Database) -> None:
    cache = ResponseCache(mem_db)
    sdk = StubSDK(tool_response({"wrong": 1}))
    meter = make_meter()
    with meter.scope(stage="extract"), pytest.raises(LLMSchemaError):
        make_client(sdk, cache=cache).complete_json(request(), ctx_costs=meter)
    assert len(sdk.kwargs) == 2  # one retry only
    assert cache.stats().entries == 0
    sdk2 = StubSDK(text_response("not json at all"))
    with pytest.raises(LLMSchemaError):
        make_client(sdk2, llm_schema_retries=0).complete_json(request())
    assert len(sdk2.kwargs) == 1


def test_cache_hit_costs_zero_and_is_recorded(mem_db: Database) -> None:
    cache = ResponseCache(mem_db)
    sdk = StubSDK(tool_response({"total": "3"}))
    client = make_client(sdk, cache=cache)
    meter = make_meter()
    with meter.scope(stage="extract"):
        first = client.complete_json(request(), ctx_costs=meter)
        second = client.complete_json(request(), ctx_costs=meter)
    assert not first.cache_hit and second.cache_hit and second.data == first.data
    assert len(sdk.kwargs) == 1  # provider called once
    assert second.cost.cache_hit and second.cost.usd == 0 and second.cost.prompt_tokens == 0
    assert meter.calls_used == 1  # a hit consumes no call budget
    assert [c.cache_hit for c in meter.costs()] == [False, True]
    # the stored row carries the file hash so delete_case can remove it
    with mem_db.conn() as c:
        assert (
            json.loads(c.execute("SELECT value FROM llm_cache").fetchone()[0])["file_sha256"] == SHA
        )


def test_cache_disabled_calls_provider_twice(mem_db: Database) -> None:
    cache = ResponseCache(mem_db, enabled=False)
    sdk = StubSDK(tool_response({"total": "3"}))
    client = make_client(sdk, cache=cache)
    client.complete_json(request())
    client.complete_json(request())
    assert len(sdk.kwargs) == 2 and cache.stats().enabled is False


def test_classify_cache_rows_have_no_file_hash(mem_db: Database) -> None:
    cache = ResponseCache(mem_db)
    make_client(StubSDK(tool_response({"total": "1"})), cache=cache).complete_json(
        request(purpose="classify:C1", cache_extra="texthash")
    )
    with mem_db.conn() as c:
        assert (
            json.loads(c.execute("SELECT value FROM llm_cache").fetchone()[0])["file_sha256"]
            is None
        )


def test_missing_usage_is_estimated_and_warned() -> None:
    sdk = StubSDK(text_response('{"total": "1"}', usage=None))
    meter = make_meter()
    with meter.scope(stage="extract"):
        result = make_client(sdk).complete_json(request(), ctx_costs=meter)
    assert result.cost.prompt_tokens > 0
    assert meter.drain_warnings() == ["usage_missing"]


def test_429_then_200_sleeps_between_attempts() -> None:
    sdk = StubSDK(HttpError(429), HttpError(503), tool_response({"total": "1"}))
    sleeps: list[float] = []
    result = make_client(sdk, sleeps=sleeps).complete_json(request())
    assert result.data == {"total": "1"} and sleeps == [1, 3]


def test_5xx_exhausts_retries() -> None:
    sdk = StubSDK(HttpError(500))
    sleeps: list[float] = []
    with pytest.raises(LLMTransportError, match="status 500"):
        make_client(sdk, sleeps=sleeps).complete_json(request())
    assert sleeps == [1, 3] and len(sdk.kwargs) == 3


def test_timeout_without_status_is_retried_then_sanitised() -> None:
    sdk = StubSDK(TimeoutError(f"connection to host failed using {SECRET}"))
    with pytest.raises(LLMTransportError) as err:
        make_client(sdk, sleeps=[]).complete_json(request())
    assert SECRET not in str(err.value) and "TimeoutError" in str(err.value)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_do_not_retry_and_never_leak_the_key(status: int) -> None:
    sdk = StubSDK(HttpError(status, f"bad key {SECRET}"))
    sleeps: list[float] = []
    with pytest.raises(LLMTransportError) as err:
        make_client(sdk, sleeps=sleeps).complete_json(request())
    assert str(err.value) == f"status {status}" and SECRET not in str(err.value)
    assert sleeps == [] and len(sdk.kwargs) == 1


def test_budget_guard_runs_before_the_provider() -> None:
    sdk = StubSDK(tool_response({"total": "1"}))
    meter = make_meter(BudgetLimits(max_pages=1, max_calls=1, max_usd=Decimal("5")))
    client = make_client(sdk)
    with meter.scope(stage="extract"):
        client.complete_json(request(), ctx_costs=meter)
        with pytest.raises(LLMBudgetExceeded):
            client.complete_json(request(user="other"), ctx_costs=meter)
    assert len(sdk.kwargs) == 1


def test_anthropic_models_get_prompt_cache_markers_and_cached_tokens_price() -> None:
    sdk = StubSDK(tool_response({"total": "1"}, usage=(1_000_000, 0), cached=1_000_000))
    table = PriceTable(
        {
            "anthropic/claude": ModelPrice(
                input_per_mtok=Decimal("3"),
                output_per_mtok=Decimal("15"),
                cached_input_per_mtok=Decimal("0.3"),
            )
        }
    )
    meter = make_meter(table=table)
    with meter.scope(stage="extract"):
        result = make_client(sdk, llm_model_cheap="anthropic/claude").complete_json(
            request(), ctx_costs=meter
        )
    system = sdk.kwargs[0]["messages"][0]["content"]
    assert isinstance(system, list) and system[0]["cache_control"] == {"type": "ephemeral"}
    assert result.cost.usd == Decimal("0.300000")
    plain = StubSDK(tool_response({"total": "1"}))
    make_client(plain).complete_json(request())
    assert isinstance(plain.kwargs[0]["messages"][0]["content"], str)


def test_get_llm_client_selection() -> None:
    assert isinstance(get_llm_client(Settings(_env_file=None)), FakeLLMClient)
    assert isinstance(
        get_llm_client(Settings(_env_file=None, llm_backend="fake", llm_api_key="k")), FakeLLMClient
    )
    with pytest.raises(SettingsError) as err:
        get_llm_client(Settings(_env_file=None, llm_backend="openai", llm_api_key="k"))
    assert "GLDB_LLM_MODEL_CHEAP" in str(err.value)
    client = get_llm_client(make_settings())
    assert isinstance(client, OpenAICompatClient) and client.model_for("strong") == "vendor/strong"
