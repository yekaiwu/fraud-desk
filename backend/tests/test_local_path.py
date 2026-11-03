"""The local-model path (docs/local-llm.md section 12): thinking really off, rules before the model on scans,
and tables returned as rows-of-arrays. No network; the model is the Fake client."""

from __future__ import annotations

import copy
from pathlib import Path

from app.core.config import Settings
from app.core.llm import FakeLLMClient, validate_json_schema
from app.core.schemas import (
    DOC_SCHEMAS,
    compact_table_columns,
    expand_compact_tables,
    response_json_schema,
    response_json_schema_compact,
    response_model_for,
)
from app.extract.cascade import extract_document
from app.extract.llm_extractor import system_prompt_for
from app.extract.registry import discover
from tests.extract_helpers import (
    StatementSpec,
    bank_page_json,
    default_txns,
    make_case,
    page_of,
    render_statement,
)
from tests.test_llm_local import MODEL, Server, completion, make_client, request, tool_message

discover()
RENDER = lambda *a, **k: b"\x89PNG"  # noqa: E731


def S(tmp_path: Path, **kw) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake", **kw)


# ------------------------------------------------------------------------------ thinking off for real


def test_thinking_off_also_sends_reasoning_effort_none() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server, llm_enable_thinking=False).complete_json(request())
    body = server.bodies[0]
    assert body["reasoning_effort"] == "none"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_an_explicit_effort_wins_and_thinking_on_sends_nothing() -> None:
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server, llm_enable_thinking=False, llm_reasoning_effort="low").complete_json(
        request()
    )
    assert server.bodies[0]["reasoning_effort"] == "low"
    server = Server(completion(tool_message('{"total": "1"}')))
    make_client(server, llm_enable_thinking=True).complete_json(request())
    assert "reasoning_effort" not in server.bodies[0]
    assert MODEL  # the model name is the one the local profile uses


# ------------------------------------------------------------------------------ rules before the model


def scan_spec(**kw) -> StatementSpec:  # noqa: ANN003
    return StatementSpec(layout="dbs", txns=default_txns(12), rows_per_page=12, **kw)


def per_page(spec: StatementSpec):  # noqa: ANN201
    return lambda req: bank_page_json(spec, page_of(req))


def test_a_scan_the_parser_can_read_needs_no_model_call_when_rules_first(tmp_path: Path) -> None:
    spec = scan_spec()
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        settings=S(tmp_path, scan_rules_first=True),
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert llm.calls == []
    assert meta.extractor == "text_layer@1" and meta.status == "ok"
    assert any(w.startswith("scan_rules:") for w in result.warnings)
    assert len(result.tables["transactions"].rows) == 12


def test_the_default_still_sends_a_scan_to_the_model(tmp_path: Path) -> None:
    spec = scan_spec()
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=S(tmp_path)
    )
    result, _ = extract_document(doc, ctx, render=RENDER)
    assert [c.model_role for c in llm.calls] == ["cheap"]
    assert not any(w.startswith("scan_rules") for w in result.warnings)


def test_a_scan_whose_arithmetic_fails_goes_to_the_model_even_when_rules_first(
    tmp_path: Path,
) -> None:
    spec = scan_spec(balance_edits={4: "58,210.75"})  # the words themselves break the balance chain
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        settings=S(tmp_path, scan_rules_first=True),
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert llm.calls, "the parser's reading did not add up, so the model must read the page"
    assert any(w.startswith("scan_rules_declined: page 1 balance_chain") for w in result.warnings)
    assert "text_layer" not in meta.extractor


def test_a_layout_the_parser_does_not_know_goes_to_the_model(tmp_path: Path) -> None:
    spec = scan_spec()
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        keep_words=False,  # no OCR words at all: nothing for the parser to read
        settings=S(tmp_path, scan_rules_first=True),
    )
    result, _ = extract_document(doc, ctx, render=RENDER)
    assert [c.model_role for c in llm.calls] == ["cheap"]
    assert any(w.startswith("scan_rules_declined") for w in result.warnings)


def test_settings_hash_moves_only_when_the_local_switches_are_on() -> None:
    base = Settings(_env_file=None).result_settings()
    assert "scan_rules_first" not in base and "llm_compact_tables" not in base
    on = Settings(_env_file=None, scan_rules_first=True, llm_compact_tables=True).result_settings()
    assert on["scan_rules_first"] == "on" and on["llm_compact_tables"] == "on"


# ------------------------------------------------------------------------------ rows as arrays


def test_compact_schema_has_array_rows_and_the_plain_one_is_untouched() -> None:
    plain_before = copy.deepcopy(response_json_schema("bank_statement"))
    compact = response_json_schema_compact("bank_statement")
    assert response_json_schema("bank_statement") == plain_before
    rows = compact["$defs"]["Tables_bank_statement"]["properties"]["transactions"]
    assert rows["items"]["type"] == "array"
    ok = {
        "fields": {k: None for k in DOC_SCHEMAS["bank_statement"].fields},
        "tables": {"transactions": [["01 Jul", "X", None, "5.00", "10.00"]], "page_chain": []},
        "page_marker": None,
    }
    assert validate_json_schema(ok, compact) is None
    assert validate_json_schema(ok, plain_before) is not None  # the object form would not accept it


def test_the_financial_statement_keeps_object_rows() -> None:
    assert compact_table_columns("financial_statement") == {}
    assert response_json_schema_compact("financial_statement") == response_json_schema(
        "financial_statement"
    )


def test_expand_pads_short_rows_cuts_long_ones_and_says_so() -> None:
    cols = compact_table_columns("bank_statement")["transactions"]
    data = {
        "fields": {},
        "tables": {"transactions": [["a", "b", "c", "d", "e"], ["a", "b"], list("abcdefg")]},
    }
    out, notes = expand_compact_tables("bank_statement", data)
    rows = out["tables"]["transactions"]
    assert rows[0] == dict(zip(cols, "abcde", strict=True))
    assert rows[1]["debit"] is None and rows[1]["description"] == "b"
    assert len(rows[2]) == 5
    assert len(notes) == 2 and notes[0].startswith("compact_row_length: transactions[1]")


def test_compact_prompt_names_the_column_order_and_the_plain_one_does_not_change() -> None:
    plain = system_prompt_for("bank_statement")
    assert plain == system_prompt_for("bank_statement", False)
    compact = system_prompt_for("bank_statement", True)
    assert "exactly 5 strings or nulls" in compact and "strings or nulls" not in plain
    assert "table transactions: date, description, debit, credit, balance" in plain


def test_a_compact_answer_is_read_like_an_object_answer(tmp_path: Path) -> None:
    spec = scan_spec()

    def answer(req):  # noqa: ANN001, ANN202
        page = bank_page_json(spec, page_of(req))
        cols = compact_table_columns("bank_statement")["transactions"]
        page["tables"]["transactions"] = [
            [r.get(c) for c in cols] for r in page["tables"]["transactions"]
        ]
        return page

    seen: list = []

    def fake(req):  # noqa: ANN001, ANN202
        seen.append(req)
        return answer(req)

    llm = FakeLLMClient({"extract:bank_statement": fake})
    compact_ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        settings=S(tmp_path, llm_compact_tables=True),
    )
    got, _ = extract_document(doc, compact_ctx, render=RENDER)
    llm2 = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    plain_ctx, doc2, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm2, is_scan=True, settings=S(tmp_path)
    )
    want, _ = extract_document(doc2, plain_ctx, render=RENDER)
    assert seen and seen[0].json_schema != response_json_schema("bank_statement")
    assert seen[0].cache_extra.endswith("+compact")
    assert [{k: c.raw for k, c in r.cells.items()} for r in got.tables["transactions"].rows] == [
        {k: c.raw for k, c in r.cells.items()} for r in want.tables["transactions"].rows
    ]
    response_model_for("bank_statement")  # the plain model is what validates the expanded answer
