"""llm_smoke.py and llm_eval.py, offline: a scripted fake server and fakes, no network."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest

from app.classify.base import LABELS
from app.classify.llm import minimise
from app.core.config import Settings
from app.core.errors import LLMNoFixture
from app.core.llm import FakeLLMClient, LLMRequest, LLMResult
from scripts import llm_eval, llm_smoke
from scripts._llm_common import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNAVAILABLE,
    load_settings,
    percentile,
    server_root,
)

STOPPED = {"error": {"message": "The local LLM server is stopped. The owner has been notified."}}
MODEL = "qwen3.8-27b"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No .env from the repo root and no GLDB_* from the developer's shell."""
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("GLDB_"):
            monkeypatch.delenv(name)


def with_key(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("GLDB_LLM_API_KEY", "sk-llm-test")
    monkeypatch.setenv("GLDB_LLM_BASE_URL", "http://llm-host:8080/v1")
    monkeypatch.setenv("GLDB_LLM_RETRY_503_BACKOFF_S", "0")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


# ----------------------------------------------------------------------------- helpers


def test_helpers() -> None:
    assert server_root("http://h:8080/v1") == "http://h:8080"
    assert server_root("http://h:8080/v1/") == "http://h:8080"
    assert percentile([], 0.5) is None and percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([1, 2, 3, 4], 0.95) == 4


def test_load_settings_substitutes_the_local_url_only_for_the_hosted_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s, note = load_settings()
    assert s.llm_base_url == "http://llm-host:8080/v1" and "not set" in note
    assert s.llm_model_cheap == MODEL and s.llm_model_strong == MODEL
    monkeypatch.setenv("GLDB_LLM_BASE_URL", "http://100.64.0.1:8080/v1")
    s, note = load_settings()
    assert s.llm_base_url == "http://100.64.0.1:8080/v1" and note == ""


# ----------------------------------------------------------------------------- smoke: unavailable


def run_smoke(capsys: pytest.CaptureFixture[str], handler, *args: str) -> tuple[int, str]:  # noqa: ANN001
    code = llm_smoke.main(list(args), transport=httpx.MockTransport(handler))
    return code, capsys.readouterr().out


def assert_telegram_instruction(out: str) -> None:
    assert "/start" in out and "Telegram" in out and "/newkey gldb 30" in out
    assert "GLDB_LLM_API_KEY" in out and "sk-llm-test" not in out


def test_smoke_server_stopped_exits_2_with_the_telegram_instruction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)
    code, out = run_smoke(
        capsys, lambda r: httpx.Response(503, json=STOPPED, headers={"Retry-After": "120"})
    )
    assert code == EXIT_UNAVAILABLE and "is stopped" in out
    assert_telegram_instruction(out)


def test_smoke_unreachable_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    code, out = run_smoke(capsys, refuse)
    assert code == EXIT_UNAVAILABLE and "cannot reach" in out and "Tailscale" in out
    assert_telegram_instruction(out)


def test_smoke_without_a_key_says_how_to_get_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLDB_LLM_BASE_URL", "http://llm-host:8080/v1")
    code, out = run_smoke(capsys, lambda r: httpx.Response(200, json={"status": "ok"}))
    assert code == EXIT_UNAVAILABLE and "GLDB_LLM_API_KEY is empty" in out
    assert "/newkey gldb 30" in out


def test_smoke_rejected_key_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    code, out = run_smoke(capsys, handler)
    assert code == EXIT_UNAVAILABLE and "key was rejected" in out
    assert_telegram_instruction(out)


# ----------------------------------------------------------------------------- smoke: success


class FakeServer:
    """health + models + chat completions that answer the smoke prompts like a working Qwen."""

    def __init__(self, *, loading_once: bool = False, wrong_answer: bool = False):
        self.chat_bodies: list[dict] = []
        self.loading_once = loading_once
        self.wrong = wrong_answer

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/models":
            assert request.headers["authorization"] == "Bearer sk-llm-test"
            return httpx.Response(200, json={"data": [{"id": MODEL, "object": "model"}]})
        assert path == "/v1/chat/completions"
        body = json.loads(request.content)
        if self.loading_once and not self.chat_bodies:
            self.chat_bodies.append(body)
            return httpx.Response(
                503, json={"error": {"message": "Loading model"}}, headers={"Retry-After": "0"}
            )
        self.chat_bodies.append(body)
        user = body["messages"][1]["content"][0]["text"]
        name = body["tools"][0]["function"]["name"]
        if name == "smoke_capital":
            args = {"capital": "Lyon" if self.wrong else "Paris", "letters": 5}
        elif name == "smoke_speed":
            args = {"speed_kmh": 80}
        else:
            lines = len(re.findall(r"^\d{4} \d\d Sep 2025", user, re.MULTILINE))
            args = {
                "third_description": "FAST PAYMENT TO SUPPLIER 0003 PTE LTD",
                "line_count": lines,
            }
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "thinking...",
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 3100, "completion_tokens": 40, "total_tokens": 3140},
            },
        )


def test_smoke_passes_against_a_working_server_and_reports_503_handling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)
    server = FakeServer(loading_once=True)
    code, out = run_smoke(capsys, server)
    assert code == EXIT_OK, out
    assert "health      OK" in out and f"models      OK   {MODEL}" in out
    assert "schema call OK" in out and "thinking on OK" in out
    assert out.count("latency") >= 2 and "tok/s" in out
    assert "503 handling  retries=1" in out and "SMOKE PASSED" in out
    assert "sk-llm-test" not in out
    # thinking off sends the override, thinking on does not; the probe is thinking off
    flags = [b.get("chat_template_kwargs") for b in server.chat_bodies if True]
    assert {"enable_thinking": False} in flags and None in flags
    on = [b for b in server.chat_bodies if b["tools"][0]["function"]["name"] == "smoke_speed"][0]
    assert "chat_template_kwargs" not in on and on["max_tokens"] >= 16000
    # a text-only server: nothing but text parts were ever posted
    assert all(
        [p["type"] for p in b["messages"][1]["content"]] == ["text"] for b in server.chat_bodies
    )


def test_smoke_reports_a_wrong_answer_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)
    code, out = run_smoke(capsys, FakeServer(wrong_answer=True), "--skip-thinking", "--skip-probe")
    assert code == EXIT_FAILED and "SMOKE FAILED: schema call" in out


def test_smoke_stops_if_the_server_is_stopped_mid_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)
    ok = FakeServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(503, json=STOPPED)
        return ok(request)

    code, out = run_smoke(capsys, handler)
    assert code == EXIT_UNAVAILABLE
    assert_telegram_instruction(out)


# ----------------------------------------------------------------------------- eval: stopped


def test_eval_server_stopped_exits_2_before_doing_any_work(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with_key(monkeypatch)
    code = llm_eval.main(
        ["--out", "never.json"],
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json=STOPPED)),
    )
    out = capsys.readouterr().out
    assert code == EXIT_UNAVAILABLE and "is stopped" in out
    assert_telegram_instruction(out)
    assert not Path("never.json").exists()


def test_eval_stops_when_the_server_dies_after_preflight(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], samples_dir: Path
) -> None:
    with_key(monkeypatch)
    ok = FakeServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(503, json=STOPPED)
        return ok(request)

    code = llm_eval.main(
        ["--samples", str(samples_dir), "--out", "never.json", "--quick"],
        transport=httpx.MockTransport(handler),
    )
    out = capsys.readouterr().out
    assert code == EXIT_UNAVAILABLE
    assert_telegram_instruction(out)


def test_eval_without_samples_says_how_to_generate_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with_key(monkeypatch)
    code = llm_eval.main(
        ["--samples", str(tmp_path / "none"), "--no-preflight", "--quick"], llm=FakeLLMClient()
    )
    assert code == EXIT_FAILED and "make_bundle.py" in capsys.readouterr().out


# ----------------------------------------------------------------------------- eval: offline run


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The repo's samples/ when present, else a freshly generated loan-clean bundle + canaries."""
    repo = Path(__file__).resolve().parents[2] / "samples"
    if (repo / "bundles/loan-clean/manifest.json").exists() and (repo / "canaries").is_dir():
        return repo
    from scripts.make_bundle import main as make_bundle

    out = tmp_path_factory.mktemp("samples")
    assert make_bundle(["--scenario", "loan", "--variant", "clean", "--out", str(out)]) == 0
    assert make_bundle(["--canaries", "--out", str(out)]) == 0
    return out


class Router:
    """A fake model that knows the answers: extraction from the bundles' ground truth, C1 from the
    page labels, C4/C5 from the row labels (or deliberately wrong when ``rows_right`` is False)."""

    def __init__(self, samples: Path, *, rows_right: bool = True):
        self.rows_right = rows_right
        self.oracles = [
            FakeLLMClient.from_ground_truth(m)
            for m in sorted(samples.glob("bundles/loan-clean/manifest.json"))
            + sorted(samples.glob("canaries/*/manifest.json"))
        ]
        self.c1: dict[str, str] = {}
        self.rows: dict[str, tuple[str | None, str | None]] = {}
        for d in [samples / "bundles/loan-clean", *sorted(samples.glob("canaries/*"))]:
            for p in llm_eval.load_pages(d):
                self.c1[minimise(llm_eval.page_text(p.path, p.page)[: llm_eval.MODEL_CHARS])] = p.c1
        for r in llm_eval.load_rows(samples / "bundles/loan-clean"):
            self.rows[minimise(r.description)] = (r.c4, r.c5)
        self.calls: list[LLMRequest] = []
        self.images_seen = 0

    def model_for(self, role: str) -> str:
        return MODEL

    def complete_json(self, req: LLMRequest, *, ctx_costs=None) -> LLMResult:  # noqa: ANN001
        self.calls.append(req)
        self.images_seen += len(req.images)
        if req.purpose.startswith("extract:"):
            for oracle in self.oracles:
                try:
                    return oracle.complete_json(req, ctx_costs=ctx_costs)
                except LLMNoFixture:
                    continue
            raise LLMNoFixture(req.purpose)
        question = req.purpose.split(":")[1]
        labels = list(LABELS[question])
        fixed = {"C1": None, "C4": "refund", "C5": "gambling"}[question]

        def scores(label: str | None) -> dict[str, float]:
            return {k: (1.0 if k == label else 0.0) for k in labels}

        if question == "C1":
            hits = [k for k in self.c1 if k in req.user]
            label = self.c1[max(hits, key=len)] if hits else "other"  # longest = the page itself
            data: dict = {"scores": scores(label)}
        else:
            items = re.split(r"Item (\d+):\n", req.user)[1:]
            out = []
            for idx, body in zip(items[::2], items[1::2], strict=True):
                hits = [k for k in self.rows if k in body]
                truth = self.rows[max(hits, key=len)] if hits else (None, None)
                want = truth[0 if question == "C4" else 1] if self.rows_right else fixed
                out.append({"i": int(idx), "scores": scores(want or fixed or labels[-1])})
            data = {"items": out}
        cost = ctx_costs.record(
            purpose=req.purpose, model=MODEL, prompt_tokens=100, completion_tokens=10, seconds=0.5
        )
        return LLMResult(data=data, model=MODEL, cost=cost)


def test_sample_pages_is_stratified_seeded_and_deduplicated(samples_dir: Path) -> None:
    pages = llm_eval.load_pages(samples_dir / "bundles/loan-clean")
    a = llm_eval.sample_pages(pages + pages, 20, 4002)
    b = llm_eval.sample_pages(pages, 20, 4002)
    assert [(p.file, p.page) for p in a] == [(p.file, p.page) for p in b] and len(a) == 20
    kinds = [p.c1 for p in a]
    assert kinds.count("bank_statement") == 5 and kinds.count("invoice") == 5  # a quarter each
    assert len({(p.sha256, p.page) for p in a}) == 20
    assert llm_eval.sample_pages(pages, 20, 1) != a


def test_eval_offline_with_a_perfect_fake_is_adequate(
    samples_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out_file = tmp_path / "out" / "llm-local.json"
    router = Router(samples_dir)
    code = llm_eval.main(
        [
            "--samples",
            str(samples_dir),
            "--bundles",
            "loan-clean",
            "--n",
            "24",
            "--rows",
            "20",
            "--out",
            str(out_file),
        ],
        llm=router,
    )
    stdout = capsys.readouterr().out
    report = json.loads(out_file.read_text())
    assert code == EXIT_OK, stdout
    assert report["verdict"]["status"] == "adequate" and report["verdict"]["failing"] == []
    cls, ext = report["classification"], report["extraction"]
    assert cls["C1"]["llm_accuracy"] == 1.0 and cls["C1"]["n"] == 24
    assert cls["C4"]["llm_accuracy"] == 1.0 and cls["C5"]["llm_accuracy"] == 1.0
    assert cls["C1"]["rules_accuracy"] is not None  # baseline always reported
    bs, iv = ext["bank_statement"], ext["invoice"]
    assert bs["pages"] >= 4 and bs["row_match"] == 1.0 and bs["number_exact"] == 1.0
    assert bs["chain_valid_rate"] == 1.0 and bs["row_precision"] == 1.0
    assert iv["pages"] >= 4 and iv["field_accuracy"] == 1.0 and iv["line_row_match"] == 1.0
    can = report["canaries"]
    assert can["n"] == 12 and can["failures"] == []
    assert report["timing"]["calls"] > 0 and report["timing"]["usd"] == "0.000000"
    assert report["timing"]["seconds_p95"] is not None
    assert "local, $0 marginal cost" in report["timing"]["cost_note"]
    assert "sk-llm" not in out_file.read_text()
    # the stdout table and the explicit criteria
    assert "| classify C1 | 100.0%" in stdout and "Verdict: ADEQUATE" in stdout
    assert "Verdict criteria" in stdout and ">= 0.95" in stdout
    assert router.images_seen == 0  # a text-only evaluation never sends an image


def test_eval_flags_a_weak_classifier_and_names_the_failing_tasks(
    samples_dir: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out_file = tmp_path / "weak.json"
    code = llm_eval.main(
        [
            "--samples",
            str(samples_dir),
            "--bundles",
            "loan-clean",
            "--n",
            "12",
            "--rows",
            "12",
            "--skip-canary-extraction",
            "--out",
            str(out_file),
        ],
        llm=Router(samples_dir, rows_right=False),
    )
    stdout = capsys.readouterr().out
    report = json.loads(out_file.read_text())
    assert code == EXIT_FAILED
    assert report["verdict"]["status"] == "not_adequate"
    assert {"classify_C4", "classify_C5"} <= set(report["verdict"]["failing"])
    assert "NOT ADEQUATE" in stdout and "classify_C4" in stdout


# ----------------------------------------------------------------------------- eval: pure scoring


def _verdict_report(**over: object) -> dict:
    report = {
        "classification": {
            "C1": {"n": 10, "answered": 10, "llm_accuracy": 0.95},
            "C4": {"n": 10, "answered": 10, "llm_accuracy": 0.9},
            "C5": {"n": 10, "answered": 10, "llm_accuracy": 0.9},
        },
        "extraction": {
            "bank_statement": {
                "row_match": 0.97,
                "number_exact": 1.0,
                "chain_valid_rate": 1.0,
            },
            "invoice": {"field_accuracy": 0.99, "line_row_match": 1.0},
            "pages_attempted": 10,
            "errors": {},
        },
        "canaries": {"n": 12, "failures": []},
    }
    report.update(over)  # type: ignore[arg-type]
    return report


def test_verdict_adequate_when_every_criterion_holds() -> None:
    assert llm_eval.verdict(_verdict_report())["status"] == "adequate"


def test_verdict_names_the_failing_extraction_and_canary_criteria() -> None:
    report = _verdict_report()
    report["extraction"]["bank_statement"]["row_match"] = 0.90
    report["canaries"] = {"n": 12, "failures": ["statement-P3"]}
    v = llm_eval.verdict(report)
    assert v["status"] == "not_adequate"
    assert v["failing"] == ["statement_row_match", "canaries"]
    assert "statement_row_match" in v["text"]


def test_verdict_is_inconclusive_when_calls_failed_or_nothing_was_measured() -> None:
    report = _verdict_report()
    report["classification"]["C1"] = {"n": 10, "answered": 2, "llm_accuracy": 1.0}
    assert llm_eval.verdict(report)["status"] == "inconclusive"
    empty = _verdict_report(canaries=None)
    assert llm_eval.verdict(empty)["status"] == "inconclusive"
    assert "canaries" in llm_eval.verdict(empty)["unmeasured"]


def test_score_page_catches_a_changed_digit_and_a_broken_chain() -> None:
    from app.core.models import Cell, Row
    from app.extract.llm_extractor import PageReading

    def cell(raw: str, kind: str = "money") -> Cell:
        value = raw.replace(",", "") if kind == "money" else raw
        return Cell(value=value, raw=raw, kind=kind)  # type: ignore[arg-type]

    def row(i: int, date: str, debit: str | None, credit: str | None, bal: str) -> Row:
        cells = {
            "date": Cell(value=date, raw=date, kind="date"),
            "description": Cell(value="X", raw="X", kind="text"),
            "debit": cell(debit) if debit else Cell(value=None, kind="money"),
            "credit": cell(credit) if credit else Cell(value=None, kind="money"),
            "balance": cell(bal),
        }
        return Row(index=i, cells=cells)

    def truth_row(i: int, date: str, debit: str | None, credit: str | None, bal: str) -> dict:
        def c(raw: str | None) -> dict:
            return {"raw": raw, "value": None if raw is None else raw.replace(",", "")}

        return {
            "index": i,
            "page": 1,
            "cells": {
                "date": {"raw": date, "value": date},
                "description": {"raw": "X", "value": "X"},
                "debit": c(debit),
                "credit": c(credit),
                "balance": c(bal),
            },
        }

    truth = {
        "fields": {},
        "tables": {
            "transactions": {
                "rows": [
                    truth_row(0, "2025-07-01", None, "1,000.00", "11,000.00"),
                    truth_row(1, "2025-07-02", "500.00", None, "10,500.00"),
                ]
            }
        },
    }

    def reading(rows: list[Row]) -> PageReading:
        return PageReading(
            page=1, role="cheap", model="m", mode="text", fields={}, rows={"transactions": rows}
        )

    good = reading(
        [
            row(0, "2025-07-01", None, "1,000.00", "11,000.00"),
            row(1, "2025-07-02", "500.00", None, "10,500.00"),
        ]
    )
    s = llm_eval.score_page("bank_statement", good, truth, 1)
    assert (s["rows_matched"], s["truth_rows"], s["money_exact"], s["money_cells"]) == (2, 2, 4, 4)
    assert s["chain_valid"] is True
    # '10,500.00' re-typed as '10500.00': same value, different characters -> not copied exactly
    retyped = reading(
        [
            row(0, "2025-07-01", None, "1,000.00", "11,000.00"),
            row(1, "2025-07-02", "500.00", None, "10500.00"),
        ]
    )
    s = llm_eval.score_page("bank_statement", retyped, truth, 1)
    assert s["rows_matched"] == 2 and s["money_exact"] == 3
    # a changed digit: the row no longer matches and the balance chain breaks
    bad = reading(
        [
            row(0, "2025-07-01", None, "1,000.00", "11,000.00"),
            row(1, "2025-07-02", "500.00", None, "10,600.00"),
        ]
    )
    s = llm_eval.score_page("bank_statement", bad, truth, 1)
    assert s["rows_matched"] == 1 and s["chain_valid"] is False


def test_the_settings_object_is_not_mutated_by_the_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    with_key(monkeypatch)
    before = Settings().model_dump()
    llm_smoke.main(["--skip-thinking", "--skip-probe"], transport=httpx.MockTransport(FakeServer()))
    assert Settings().model_dump() == before
    assert "scripts" in sys.modules
