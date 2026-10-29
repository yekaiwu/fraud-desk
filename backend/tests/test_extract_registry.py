"""Extractor registry, discovery and prompt registration (ingest-and-extraction.md 3.3)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

import app.extract as extract_pkg
from app.core.llm import prompt_registry
from app.core.schemas import DOC_SCHEMAS
from app.extract import registry
from app.extract.base import ExtractionResult, LayoutNotRecognised, make_page_model
from app.extract.registry import discover, get_extractors, has_extractor, register_extractor


@pytest.fixture(autouse=True)
def _passport_has_no_text_layer_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests need one recognised type that nobody extracts. Passport was that type until
    extract-kyc (wave 2) registered ``kyc_ids``; hide that owner here (integration-loan)."""
    from app.extract import registry as _registry

    _registry.discover()
    llm_only = {"llm": _registry._EXTRACTORS["passport"]["llm"]}
    monkeypatch.setitem(_registry._EXTRACTORS, "passport", llm_only)
    monkeypatch.setitem(_registry._HINTS, "passport", [])


def test_discover_registers_both_backends_for_the_fin_types() -> None:
    discover()
    discover()  # idempotent
    for dt in ("bank_statement", "invoice", "financial_statement", "contract", "purchase_order"):
        got = get_extractors(dt)
        assert set(got) == {"text_layer", "llm"}, dt
        assert got["text_layer"].backend == "text_layer"
        assert got["llm"].backend == "llm"
        assert has_extractor(dt)


def test_llm_class_alone_does_not_make_a_type_extractable() -> None:
    discover()
    # KYC types: the generic LLM class is registered, but nobody owns them yet
    assert "llm" in get_extractors("passport")
    assert has_extractor("passport") is False
    assert has_extractor("boilerplate") is False


def test_prompts_are_registered_per_extractable_type() -> None:
    discover()
    reg = prompt_registry()
    for dt in ("bank_statement", "invoice", "financial_statement", "contract", "purchase_order"):
        text = reg[f"extract:{dt}"]
        assert "untrusted" in text
        assert "Copy each value exactly as printed" in text


def test_page_model_follows_the_doc_schema() -> None:
    model = make_page_model("bank_statement")
    schema = model.model_json_schema()
    assert set(schema["properties"]) == {"fields", "tables", "page_marker"}
    text = str(make_page_model("invoice").model_json_schema())
    assert all(key in text for key in DOC_SCHEMAS["invoice"].fields)
    assert "page_marker" not in text  # only bank statements carry a page marker


def test_a_module_dropped_into_the_folder_is_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "zz_dummy_extractor.py").write_text(
        textwrap.dedent(
            """
            from app.extract.base import ExtractionResult, LayoutNotRecognised
            from app.extract.registry import register_extractor

            @register_extractor(doc_types={"passport"}, backend="text_layer")
            class DummyPassport:
                doc_types = frozenset({"passport"})
                backend = "text_layer"
                name = "dummy"
                version = "9"

                def extract(self, doc, ctx):
                    raise LayoutNotRecognised("dummy")
            """
        )
    )
    monkeypatch.setattr(extract_pkg, "__path__", [*extract_pkg.__path__, str(tmp_path)])
    monkeypatch.setitem(
        registry._EXTRACTORS, "passport", dict(registry._EXTRACTORS.get("passport", {}))
    )
    try:
        discover()
        found = get_extractors("passport")
        assert found["text_layer"].version == "9"
        assert has_extractor("passport")
        assert "llm" in found  # the generic backend is still there
    finally:
        registry.unregister_extractor("passport", "text_layer")
        sys.modules.pop("app.extract.zz_dummy_extractor", None)
    assert has_extractor("passport") is False


def test_duplicate_registration_by_another_class_is_refused() -> None:
    discover()
    with pytest.raises(ValueError, match="already registered"):

        @register_extractor(doc_types={"invoice"}, backend="text_layer")
        class Other:  # noqa: B903
            def extract(self, doc, ctx) -> ExtractionResult:  # pragma: no cover
                raise LayoutNotRecognised("x")


def test_unknown_backend_is_refused() -> None:
    with pytest.raises(ValueError):
        register_extractor(doc_types={"invoice"}, backend="magic")  # type: ignore[arg-type]
