"""The OpenAI-compatible client against a local llama-server shaped API (no network).

These tests use the real ``openai`` SDK over ``httpx.MockTransport`` so the JSON actually posted,
the status/``Retry-After`` handling and the response parsing are those of the real library.
"""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from types import SimpleNamespace as NS

import httpx
import pytest

from app.core.cache import cache_key
from app.core.config import Settings
from app.core.costs import BudgetLimits, CostMeter, PriceTable, summarise_costs
from app.core.llm import (
    LLMRequest,
    LLMSchemaError,
    LLMServerStopped,
    LLMTransportError,
    LLMVisionUnsupported,
    OpenAICompatClient,
    concurrency_gate,
)
from app.core.models import Case
from app.core.testing import FIXED_NOW, FIXED_TODAY

SCHEMA = {
    "type": "object",
    "properties": {"total": {"type": "string"}},
    "required": ["total"],
    "additionalProperties": False,
}
MODEL = "qwen3.8-27b"
STOPPED_BODY = {
    "error": {"message": "The local LLM server is stopped. Send /start to the Telegram bot."}
}


def settings(**kw) -> Settings:  # noqa: ANN003
    base = dict(
        _env_file=None,
        llm_backend="openai",
        llm_base_url="http://llm-host:8080/v1",
        llm_api_key="sk-llm-test",
        llm_model_cheap=MODEL,
        llm_model_strong=MODEL,
        llm_enable_thinking=False,
        llm_supports_vision=False,
        llm_max_concurrency=1,
    )
    base.update(kw)
    return Settings(**base)


def request(**kw) -> LLMRequest:  # noqa: ANN003
    base = dict(
        purpose="extract:invoice",
        system="Extract.",
        user="[[[DOC]]]",
        schema_name="Page",
        json_schema=SCHEMA,
        cache_extra="a" * 64 + ":1:text",
    )
    base.update(kw)
    return LLMRequest(**base)


def completion(message: dict, *, finish: str = "stop", usage=(120, 30)) -> dict:  # noqa: ANN001
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL,
        "choices": [
            {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}
        ],
        "usage": {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": sum(usage),
        },
    }


def tool_message(arguments: str) -> dict:
    return {
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Page", "arguments": arguments}}
        ],
    }


class Server:
    """A scripted fake llama-server gateway: replies in order, records every request body."""

    def __init__(self, *replies: httpx.Response | dict):
        self.replies = list(replies)
        self.bodies: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


def make_client(server: Server, sleeps: list[float] | None = None, **kw) -> OpenAICompatClient:  # noqa: ANN003
    sleeps = sleeps if sleeps is not None else []
    http = httpx.Client(transport=httpx.MockTransport(server))
    return OpenAICompatClient(settings(**kw), http_client=http, sleep=sleeps.append)


# ------------------------------------------------------------------------------- request shape


def test_thinking_off_posts_chat_template_kwargs_at_top_level() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    result = make_client(server, llm_enable_thinking=False).complete_json(request())
    body = server.bodies[0]
    assert result.data == {"total": "1"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["model"] == MODEL and body["tool_choice"] == "auto" and body["temperature"] == 0
    assert server.headers[0]["authorization"] == "Bearer sk-llm-test"
    assert body["tools"][0]["function"]["name"] == "Page"


def test_thinking_on_sends_no_template_override_and_a_bigger_token_budget() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server, llm_enable_thinking=True, llm_max_output_tokens=16000).complete_json(
        request()
    )
    body = server.bodies[0]
    assert "chat_template_kwargs" not in body and body["max_tokens"] == 16000


def test_defaults_are_unchanged_for_hosted_use() -> None:
    s = Settings(_env_file=None)
    assert s.llm_enable_thinking is True and s.llm_supports_vision is True
    assert s.llm_max_concurrency == 4 and s.llm_max_output_tokens == 8000
    assert (s.llm_retry_503_attempts, s.llm_retry_503_backoff_s) == (2, 1.0)
    assert s.ocr_backend == "auto" and s.llm_timeout_s == 60
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server, llm_enable_thinking=True).complete_json(request())
    assert server.bodies[0]["max_tokens"] == 8000


def test_cache_key_depends_on_thinking() -> None:
    on = make_client(Server({}), llm_enable_thinking=True)
    off = make_client(Server({}), llm_enable_thinking=False)
    req = request()
    assert cache_key(req, MODEL, on.client_version, 8000) != cache_key(
        req, MODEL, off.client_version, 8000
    )


def test_images_are_refused_when_the_model_is_text_only() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    client = make_client(server, llm_supports_vision=False)
    with pytest.raises(LLMVisionUnsupported):
        client.complete_json(request(images=[b"\x89PNG"]))
    assert server.bodies == []  # nothing was sent


def test_images_are_sent_when_vision_is_on() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server, llm_supports_vision=True).complete_json(request(images=[b"\x89PNG"]))
    parts = server.bodies[0]["messages"][1]["content"]
    assert any(p["type"] == "image_url" for p in parts)


def test_text_only_requests_contain_no_image_part() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server).complete_json(request())
    parts = server.bodies[0]["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["text"]


# ------------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "arguments",
    [
        '{"total": "7"}',
        '```json\n{"total": "7"}\n```',
        'Here is the result:\n{"total": "7"}\nHope that helps!',
        '{"total": "7"} {"ignored": true}',
        '<tool_call>{"name": "Page", "arguments": {"total": "7"}}</tool_call>',
    ],
)
def test_tool_call_arguments_in_llama_server_shapes(arguments: str) -> None:
    server = Server(completion(tool_message(arguments)))
    assert make_client(server).complete_json(request()).data == {"total": "7"}


@pytest.mark.parametrize(
    "content",
    [
        'Sure.\n```json\n{"total": "8"}\n```',
        '{"total": "8"}',
        '<think>draft {"total": "no"}</think>\n{"total": "8"}',
        '<tool_call>\n{"name": "Page", "arguments": "{\\"total\\": \\"8\\"}"}\n</tool_call>',
        'Answer: {"unrelated": 1} then {"total": "8"}',
    ],
)
def test_text_answer_instead_of_a_tool_call_is_parsed_and_validated(content: str) -> None:
    server = Server(completion({"content": content, "tool_calls": None}))
    assert make_client(server).complete_json(request()).data == {"total": "8"}


def test_reasoning_content_is_ignored() -> None:
    message = {
        "content": '{"total": "5"}',
        "reasoning_content": 'I think the answer is {"total": "WRONG"} because...',
    }
    assert make_client(Server(completion(message))).complete_json(request()).data == {"total": "5"}


def test_tool_call_with_broken_arguments_falls_back_to_content() -> None:
    message = tool_message("{not json")
    message["content"] = '{"total": "6"}'
    assert make_client(Server(completion(message))).complete_json(request()).data == {"total": "6"}


def test_invalid_output_is_retried_once_then_rejected() -> None:
    bad = completion({"content": '{"wrong": 1}'})
    server = Server(bad)
    with pytest.raises(LLMSchemaError):
        make_client(server).complete_json(request())
    assert len(server.bodies) == 2  # one retry only, as before
    assert "not valid" in server.bodies[1]["messages"][-1]["content"]


def test_truncated_thinking_names_the_fix() -> None:
    cut = completion({"content": "", "reasoning_content": "hmm " * 50}, finish="length")
    with pytest.raises(LLMSchemaError, match="GLDB_LLM_MAX_OUTPUT_TOKENS"):
        make_client(Server(cut), llm_schema_retries=0).complete_json(request())


def test_schema_text_fallback_prefers_the_object_that_validates() -> None:
    message = completion({"content": '{"total": 1} and {"total": "ok"}'})
    assert make_client(Server(message)).complete_json(request()).data == {"total": "ok"}


# ------------------------------------------------------------------------------- 503 handling


def test_loading_503_honours_retry_after_then_succeeds() -> None:
    loading = httpx.Response(
        503, json={"error": {"message": "Loading model"}}, headers={"Retry-After": "7"}
    )
    server = Server(loading, loading, completion(tool_message('{"total": "1"}')))
    sleeps: list[float] = []
    client = make_client(server, sleeps, llm_retry_503_attempts=5)
    assert client.complete_json(request()).data == {"total": "1"}
    assert sleeps == [7.0, 7.0] and len(server.bodies) == 3
    assert client.stats == {"retries_503": 2, "retry_wait_s": 14.0}


def test_loading_503_without_header_uses_the_backoff_schedule_and_the_cap() -> None:
    loading = httpx.Response(503, json={"error": {"message": "Loading model"}})
    server = Server(loading, loading, loading, completion(tool_message('{"total": "1"}')))
    sleeps: list[float] = []
    make_client(
        server,
        sleeps,
        llm_retry_503_attempts=5,
        llm_retry_503_backoff_s=5.0,
        llm_retry_503_max_wait_s=20.0,
    ).complete_json(request())
    assert sleeps == [5.0, 15.0, 20.0]


def test_retry_after_is_capped() -> None:
    loading = httpx.Response(
        503, json={"error": {"message": "Loading model"}}, headers={"Retry-After": "900"}
    )
    server = Server(loading, completion(tool_message('{"total": "1"}')))
    sleeps: list[float] = []
    make_client(
        server, sleeps, llm_retry_503_attempts=3, llm_retry_503_max_wait_s=30
    ).complete_json(request())
    assert sleeps == [30.0]


def test_loading_503_gives_up_with_status_503() -> None:
    loading = httpx.Response(503, json={"error": {"message": "Loading model"}})
    server = Server(loading)
    sleeps: list[float] = []
    with pytest.raises(LLMTransportError, match="status 503") as err:
        make_client(server, sleeps, llm_retry_503_attempts=3).complete_json(request())
    assert not isinstance(err.value, LLMServerStopped)
    assert len(server.bodies) == 4 and len(sleeps) == 3


def test_stopped_server_fails_fast_and_names_the_telegram_command() -> None:
    stopped = httpx.Response(503, json=STOPPED_BODY, headers={"Retry-After": "120"})
    server = Server(stopped)
    sleeps: list[float] = []
    with pytest.raises(LLMServerStopped) as err:
        make_client(server, sleeps, llm_retry_503_attempts=8).complete_json(request())
    message = str(err.value)
    assert "/start" in message and "Telegram" in message and "stopped" in message
    assert len(server.bodies) == 2  # one quick re-check, not eight waits of Retry-After
    assert sleeps == [1.0]  # never the 120 s the gateway suggests
    assert isinstance(
        err.value, LLMTransportError
    )  # callers that catch transport errors still work


def test_stopped_without_retries_configured_fails_on_the_first_call() -> None:
    server = Server(httpx.Response(503, json=STOPPED_BODY))
    with pytest.raises(LLMServerStopped):
        make_client(server, llm_retry_503_attempts=0).complete_json(request())
    assert len(server.bodies) == 1


def test_401_is_not_retried_and_does_not_leak_the_key() -> None:
    server = Server(httpx.Response(401, json={"error": {"message": "API key expired sk-llm-test"}}))
    sleeps: list[float] = []
    with pytest.raises(LLMTransportError) as err:
        make_client(server, sleeps).complete_json(request())
    assert str(err.value) == "status 401" and sleeps == [] and "sk-llm" not in str(err.value)


# ------------------------------------------------------------------------------- concurrency


class _Probe:
    """Stub SDK that records how many calls are inside ``create`` at once."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **_kw):  # noqa: ANN003, ANN202
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        return NS(
            choices=[
                NS(message=NS(tool_calls=None, content='{"total": "1"}'), finish_reason="stop")
            ],
            usage=NS(prompt_tokens=1, completion_tokens=1),
        )


def _run_threads(limit: int, n: int = 6) -> int:
    probe = _Probe()
    client = OpenAICompatClient(
        settings(llm_max_concurrency=limit, llm_base_url=f"http://host-{limit}-{id(probe)}/v1"),
        sdk=probe,
    )
    threads = [
        threading.Thread(target=client.complete_json, args=(request(user=f"u{i}"),))
        for i in range(n)
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return probe.peak


def test_concurrency_limit_one_serialises_page_workers() -> None:
    assert _run_threads(1) == 1


def test_concurrency_limit_allows_parallelism_when_raised() -> None:
    assert 1 < _run_threads(3) <= 3


def test_gate_is_shared_by_client_objects_of_the_same_server() -> None:
    a = OpenAICompatClient(settings(llm_base_url="http://same/v1"), sdk=_Probe())
    b = OpenAICompatClient(settings(llm_base_url="http://same/v1/"), sdk=_Probe())
    assert a._gate is b._gate is concurrency_gate("http://same/v1", 1)


# ------------------------------------------------------------------------------- costs


def _meter(table: PriceTable | None, max_usd: str = "2") -> CostMeter:
    return CostMeter(
        BudgetLimits(max_pages=10, max_calls=1000, max_usd=Decimal(max_usd)),
        table if table is not None else PriceTable.load(None),
    )


def test_local_model_costs_zero_but_tokens_and_seconds_are_recorded() -> None:
    server = Server(completion(tool_message('{"total": "1"}'), usage=(3000, 400)))
    meter = _meter(None)
    with meter.scope(stage="extract"):
        result = make_client(server).complete_json(request(), ctx_costs=meter)
    assert result.cost.usd == Decimal("0") and meter.drain_warnings() == []  # known, not unknown
    assert (result.cost.prompt_tokens, result.cost.completion_tokens) == (3000, 400)
    assert result.cost.seconds >= 0


def test_unknown_model_cannot_bypass_the_dollar_ceiling() -> None:
    meter = _meter(None, max_usd="0.50")
    with meter.scope(stage="extract"):
        for _ in range(4):  # 0.15 USD estimated per call: the guard trips on the fifth
            meter.reserve_call()
            meter.record(
                purpose="p",
                model="typo-model",
                prompt_tokens=8000,
                completion_tokens=400,
                seconds=1,
            )
        assert meter.total() == Decimal("0")  # no invented bill is recorded ...
        with pytest.raises(Exception, match="max_usd"):
            meter.reserve_call()  # ... but the guard counted the call at the pessimistic rate


def test_local_model_never_trips_the_dollar_guard() -> None:
    meter = _meter(None, max_usd="0.01")
    with meter.scope(stage="extract"):
        for _ in range(50):
            meter.reserve_call()
            meter.record(
                purpose="p", model=MODEL, prompt_tokens=200_000, completion_tokens=9000, seconds=60
            )
    assert meter.total() == Decimal("0")


def test_cost_summary_labels_local_models() -> None:
    meter = _meter(None)
    with meter.scope(stage="extract"):
        meter.record(purpose="p", model=MODEL, prompt_tokens=10, completion_tokens=2, seconds=1)
    case = Case(
        id="c_test00000000",
        use_case="loan",
        today=FIXED_TODAY,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        costs=meter.costs(),
    )
    summary = summarise_costs(case)
    assert summary["total_usd"] == "0.000000"
    assert summary["pricing_note"] == "qwen3.8-27b: local, $0 marginal cost"
    hosted = Case(
        id="c_test00000001",
        use_case="loan",
        today=FIXED_TODAY,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    assert "pricing_note" not in summarise_costs(hosted)  # hosted output keeps its shape
