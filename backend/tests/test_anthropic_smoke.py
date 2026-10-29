"""anthropic_smoke.py offline: the real SDK over a mock transport, no network, no real key."""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from scripts import anthropic_smoke
from tests.test_llm_anthropic import KEY, Server, api_error, message, tool_use


def http(server: Server) -> anthropic.DefaultHttpxClient:
    return anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(server.handler))


def good_message() -> dict:
    body = message(
        [tool_use({"capital": "Paris", "letters": 5})],
        usage={"input_tokens": 1000, "output_tokens": 20},
    )
    return body


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLDB_ANTHROPIC_API_KEY", KEY)


def test_success_prints_tokens_dollars_seconds_and_request_id(
    with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Server(good_message())
    assert anthropic_smoke.main([], http_client=http(server)) == anthropic_smoke.EXIT_OK
    out = capsys.readouterr().out
    assert "claude-haiku-5-5" in out and "input       1000 tokens" in out
    assert "output      20 tokens" in out and "$0.000110" in out  # 1000*0.10/1e6 + 20*0.50/1e6
    assert "request id  req_test_1" in out and "seconds" in out
    assert KEY not in out
    assert server.bodies[0]["max_tokens"] == 256 and server.calls == 1


def test_missing_key_exits_2_with_a_clear_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = Server(good_message())
    assert anthropic_smoke.main([], http_client=http(server)) == anthropic_smoke.EXIT_UNUSABLE
    out = capsys.readouterr().out
    assert "GLDB_ANTHROPIC_API_KEY" in out and server.calls == 0


def test_wrong_model_exits_2_without_a_call(
    with_key: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLDB_ANTHROPIC_MODEL", "claude-opus-5-5")
    server = Server(good_message())
    assert anthropic_smoke.main([], http_client=http(server)) == anthropic_smoke.EXIT_UNUSABLE
    assert "claude-haiku-5-5" in capsys.readouterr().out and server.calls == 0


def test_workspace_header_required_exits_2_and_says_what_to_set(
    with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Server((400, api_error("anthropic-workspace-id header is required")))
    assert anthropic_smoke.main([], http_client=http(server)) == anthropic_smoke.EXIT_UNUSABLE
    out = capsys.readouterr().out
    assert "GLDB_ANTHROPIC_WORKSPACE_ID" in out and KEY not in out


def test_rejected_key_exits_2(with_key: None, capsys: pytest.CaptureFixture[str]) -> None:
    server = Server((401, api_error(f"invalid key {KEY}", "authentication_error")))
    assert anthropic_smoke.main([], http_client=http(server)) == anthropic_smoke.EXIT_UNUSABLE
    assert KEY not in capsys.readouterr().out


def test_other_failures_exit_1(with_key: None, capsys: pytest.CaptureFixture[str]) -> None:
    server = Server((404, api_error("model: not found", "not_found_error")))
    assert anthropic_smoke.main([], http_client=http(server)) == anthropic_smoke.EXIT_FAILED
    wrong = Server(message([tool_use({"capital": "Lyon", "letters": 4})]))
    assert anthropic_smoke.main([], http_client=http(wrong)) == anthropic_smoke.EXIT_FAILED
    assert "WRONG" in capsys.readouterr().out


def test_dry_run_sends_nothing_and_shows_no_key(
    with_key: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLDB_ANTHROPIC_WORKSPACE_ID", "wrkspc_01ABC")
    server = Server(good_message())
    assert anthropic_smoke.main(["--dry-run"], http_client=http(server)) == anthropic_smoke.EXIT_OK
    out = capsys.readouterr().out
    assert server.calls == 0 and "nothing was sent" in out
    assert KEY not in out and "wrkspc_01ABC" not in out and "workspace id set" in out
