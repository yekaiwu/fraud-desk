"""Real-OCR evaluation mode and its aggregation (backend/scripts/ext_eval/real_ocr*.py)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from app.extract import cascade
from app.extract.llm_extractor import PageState
from app.ingest.ocr_backends import vision_import_problem
from scripts.ext_eval import real_ocr, scan_sim
from scripts.ext_eval import real_ocr_eval as ev

# ------------------------------------------------------------------------------ the plug-in


def test_ocr_settings_force_local_ocr_and_deterministic_extractors(tmp_path: Path) -> None:
    s = real_ocr.make_ocr_settings(tmp_path)
    assert s.ocr_backend == "vision" and s.extractor_mode == "text_layer"
    assert s.llm_backend == "fake"  # no model, no key


def test_patch_reports_ocr_pages_as_text_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    def scan_state(doc: Any, ctx: Any, page: int) -> PageState:
        return PageState(page, [], True, False, None)

    monkeypatch.setattr(cascade, "page_state", scan_state)
    with real_ocr.ocr_words_as_text_layer():
        assert cascade.page_state is not scan_state
        assert cascade.page_state(None, None, 3).is_scan is False
        assert cascade.page_state(None, None, 3).page == 3
    assert cascade.page_state is scan_state  # restored, also after an exception
    with pytest.raises(RuntimeError), real_ocr.ocr_words_as_text_layer():
        raise RuntimeError("boom")
    assert cascade.page_state is scan_state


def test_raw_doc_key_map_reads_simulated_manifests(tmp_path: Path) -> None:
    man = {
        "files": [
            {"sha256": "aa", "pages": [{"page": 1, "doc_key": "x"}, {"page": 2, "doc_key": None}]}
        ],
        "upload_order": {
            "scan_sim_med": ["f.pdf"]
        },  # a render name the generator's literal rejects
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(man), encoding="utf-8")
    assert real_ocr.raw_doc_key_map(path) == {("aa", 1): "x", ("aa", 2): None}


@pytest.mark.skipif(vision_import_problem() is not None, reason="macOS Vision OCR not available")
def test_real_ocr_job_end_to_end_on_a_tiny_simulated_scan(tmp_path: Path) -> None:
    """A one-page image-only PDF goes through ingest, real OCR, classification and the rules."""
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    for i, line in enumerate(
        ["DBS-STYLE MOCK BANK", "Statement of Account - Current Account", "Account No: 1091318723"]
    ):
        pg.insert_text((60, 90 + i * 28), line, fontsize=16)
    pdf = doc.tobytes()
    doc.close()
    page = scan_sim.render_pdf_pages(pdf)[0]
    out, _ = scan_sim.degrade_page(page, "light", "e2e")
    jpg = scan_sim.jpeg_chain(out, scan_sim.LEVELS["light"].jpeg_chain, "SIMULATED e2e")
    scan_pdf = scan_sim.jpegs_to_pdf([jpg], "e2e")
    bdir = tmp_path / "b"
    (bdir / "scan_sim_light").mkdir(parents=True)
    (bdir / "scan_sim_light" / "s.pdf").write_bytes(scan_pdf)
    import hashlib

    sha = hashlib.sha256(scan_pdf).hexdigest()
    manifest = {
        "use_case": "loan",
        "registry_path": None,
        "upload_order": {"scan_sim_light": ["scan_sim_light/s.pdf"]},
        "files": [{"sha256": sha, "pages": [{"page": 1, "doc_key": "bank_statement:x"}]}],
        "quirks": [],
    }
    (bdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    res = real_ocr.run_job(
        {
            "manifest": str(bdir / "manifest.json"),
            "render": "scan_sim_light",
            "extraction": "ocr",
            "today": "2026-10-27",
            "detail": True,
        }
    )
    assert res["ok"], res.get("error")
    assert res["extraction_mode"] == "ocr"
    assert res["ocr"]["pages"] == 1 and res["ocr"]["pages_with_error"] == 0
    # OCR really read the page: classification ran on its text
    assert res["detail"]["pages"][0]["label"] == "bank_statement"


def test_run_job_reports_a_harness_error_instead_of_raising(tmp_path: Path) -> None:
    res = real_ocr.run_job(
        {
            "manifest": str(tmp_path / "missing.json"),
            "render": "scan_pdf",
            "extraction": "ocr",
            "today": "2026-10-27",
        }
    )
    assert res["ok"] is False and "error" in res


# ------------------------------------------------------------------------------ planning


def test_sim_render_names_round_trip() -> None:
    assert ev.sim_level_container("scan_sim_med") == ("med", "pdf")
    assert ev.sim_level_container("scan_sim_heavy_jpg") == ("heavy", "jpeg")
    assert ev.is_sim("scan_sim_light") and not ev.is_sim("scan_hq")
    assert set(ev.DEFAULT_RENDERS) == {"scan_hq", "scan_lq", "photo", "scan_pdf", *ev.SIM_RENDERS}


def test_jobs_are_deduplicated_and_bases_are_shared(tmp_path: Path) -> None:
    for name in ("clean", "t1", "t2"):
        d = tmp_path / name
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"upload_order": {"scan_pdf": ["a.pdf", "b.pdf", "c.pdf"]}}),
            encoding="utf-8",
        )
    items = [
        ev.Item("clean", "scan_pdf", "ocr", "clean", "loan", str(tmp_path / "clean")),
        ev.Item(
            "tamper",
            "scan_pdf",
            "ocr",
            "t1",
            "loan",
            str(tmp_path / "t1"),
            str(tmp_path / "clean"),
            "T01",
        ),
        ev.Item(
            "tamper",
            "scan_pdf",
            "ocr",
            "t2",
            "loan",
            str(tmp_path / "t2"),
            str(tmp_path / "clean"),
            "T03",
        ),
    ]
    jobs, refs = ev.jobs_for(items, date(2026, 10, 27), "salt", limit_files=2)
    assert len(jobs) == 3  # the base run is the clean run, shared
    assert refs[0][1] == refs[1][2] == refs[2][2]
    assert all(j["only_paths"] == ["a.pdf", "b.pdf"] for j in jobs)
    assert jobs[0]["detail"] is True and jobs[1]["detail"] is False


def test_salt_changes_only_with_result_affecting_code() -> None:
    assert ev.code_salt() == ev.code_salt() and len(ev.code_salt()) == 12


# ------------------------------------------------------------------------------ aggregation


def doc(
    key: str | None, rows: int, status: str = "ok", fields: dict | None = None, start: int = 0
) -> dict[str, Any]:
    return {
        "doc_key": key,
        "doc_type": "bank_statement",
        "status": status,
        "page_start": 1,
        "fields": fields or {},
        "tables": {},
        "rows": [
            {
                "date": "2025-07-01",
                "debit": None,
                "credit": "1.00",
                "balance": str(start + i),
                "description": f"r{start + i}",
            }
            for i in range(rows)
        ],
    }


def test_merge_documents_joins_fragments_of_one_statement() -> None:
    merged = ev.merge_documents(
        [
            doc("s:1", 2, fields={"bank_name": "EXAMPLE", "uen": None}),
            doc("s:1", 3, status="partial", fields={"bank_name": "X", "uen": "123"}),
            doc("other", 1),
            doc(None, 1),  # unkeyed documents stay separate
            doc(None, 1),
        ]
    )
    assert [d["doc_key"] for d in merged] == ["s:1", "other", None, None]
    first = merged[0]
    assert len(first["rows"]) == 5 and first["status"] == "partial"
    assert first["fields"] == {"bank_name": "EXAMPLE", "uen": "123"}  # first non-empty wins


def test_false_alarm_row_counts_medium_and_above_and_respects_quirks(tmp_path: Path) -> None:
    d = tmp_path / "b"
    d.mkdir()
    (d / "manifest.json").write_text(
        json.dumps({"quirks": [{"may_trigger": ["bs_dates"]}]}), encoding="utf-8"
    )
    it = ev.Item("clean", "scan_hq", "ocr", "b", "loan", str(d))
    res = {
        "flags": [
            {"rule": "bs_totals", "severity": "high"},
            {"rule": "bs_dates", "severity": "medium"},  # allowed by the quirk
            {"rule": "x", "severity": "low"},
            {"rule": "y", "severity": "info"},
        ],
        "score": 40,
        "lane": "loan",
        "coverage_ratio": 0.5,
        "partial": False,
    }
    row = ev.false_alarm_row([(it, res)])
    assert row["flags_ge_medium"] == 1 and row["bundles_with_false_alarm"] == (1, 1)
    assert row["rules"] == {"bs_totals": 1} and row["fast_lane"] == (0, 1)


def test_extraction_row_scores_rows_fields_and_pages(tmp_path: Path) -> None:
    d = tmp_path / "b"
    d.mkdir()
    cells = lambda i: {  # noqa: E731
        c: {"value": v}
        for c, v in zip(
            ("date", "description", "debit", "credit", "balance"),
            ("2025-07-01", f"r{i}", None, "1.00", str(i)),
            strict=True,
        )
    }
    truth = {
        "documents": [
            {
                "key": "s:1",
                "doc_type": "bank_statement",
                "fields": {"bank_name": {"value": "EXAMPLE"}, "uen": {"value": "123"}},
                "tables": {
                    "transactions": {"rows": [{"index": i, "cells": cells(i)} for i in range(4)]}
                },
            }
        ]
    }
    labels = {"pages": [{"sha256": "aa", "page": 1, "c1": "bank_statement"}]}
    for name, obj in (("ground_truth.json", truth), ("labels.json", labels)):
        (d / name).write_text(json.dumps(obj), encoding="utf-8")
    # two fragments of the statement: 3 of 4 rows read, one wrong field
    result = {
        "detail": {
            "documents": [
                doc("s:1", 2, fields={"bank_name": "EXAMPLE"}),
                doc("s:1", 1, fields={"uen": "999"}, start=2),
            ],
            "pages": [{"sha": "aa", "page": 1, "label": "bank_statement"}],
        },
        "ocr": {"pages": 2, "pages_with_error": 1},
    }
    it = ev.Item("clean", "scan_pdf", "ocr", "b", "loan", str(d))
    row = ev.extraction_row([(it, result)])
    assert row["row_recall"] == (3, 4) and row["row_precision"] == (3, 3)
    assert row["field_acc"] == (1, 2)
    assert row["c1_acc"] == (1, 1) and row["docs_found"] == (1, 1)
    assert row["pages_unreadable"] == (1, 2)


def test_markdown_report_states_the_limits_and_marks_smoke_runs() -> None:
    payload = {
        "extraction": {},
        "false_alarms": {},
        "recall": {},
        "errors": [{"bundle": "b", "render": "r", "mode": "ocr", "error": "boom"}],
        "skipped": [
            {"render": "scan_sim_med", "mode": "oracle", "why": "no oracle for simulated renders"}
        ],
        "limits": ev.LIMITS,
        "smoke": 4,
    }
    md = ev.render_markdown(payload)
    assert "SMOKE RUN" in md and "SIMULATED" in md and "boom" in md
    assert "internally consistent" in md  # the undetectable-forgery limit is always printed
    assert "no oracle for simulated renders" in md


def test_plan_pairs_tampered_cases_with_their_bases_and_reports_what_it_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # no real simulation here: the planner only needs a dir to look the render up in
    monkeypatch.setattr(ev, "ensure_sim", lambda srcs, *a, **k: {s: s for s in srcs})
    samples = tmp_path / "samples"
    clean = samples / "bundles" / "loan-clean"
    clean.mkdir(parents=True)
    (clean / "manifest.json").write_text(
        json.dumps({"use_case": "loan", "upload_order": {"scan_pdf": ["a.pdf"], "digital": []}}),
        encoding="utf-8",
    )
    with_render = samples / "tampered" / "T01-loan-clean-i0"
    without = samples / "tampered" / "T03-loan-clean-i0"
    for d, renders in ((with_render, {"scan_pdf": ["a.pdf"]}), (without, {"digital": []})):
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "use_case": "loan",
                    "base_bundle": "../../bundles/loan-clean/manifest.json",
                    "upload_order": renders,
                }
            ),
            encoding="utf-8",
        )
    items, skipped = ev.plan(
        samples,
        tmp_path / "sim",
        renders=["scan_pdf", "scan_sim_med"],
        modes=["oracle", "ocr"],
        scenarios=["loan", "kyc"],
        tamper_ids=["T01", "T03"],
        tamper_renders=["scan_pdf"],
    )
    got = {(i.kind, i.render, i.mode, i.tamper_id) for i in items}
    assert ("clean", "scan_pdf", "oracle", None) in got and (
        "clean",
        "scan_pdf",
        "ocr",
        None,
    ) in got
    assert ("tamper", "scan_pdf", "ocr", "T01") in got
    assert not any(i.tamper_id == "T03" for i in items)  # that case has no scan_pdf render
    tamper = next(i for i in items if i.kind == "tamper")
    assert Path(tamper.base_dir) == clean.resolve()
    assert {
        "render": "scan_sim_med",
        "mode": "oracle",
        "why": "no oracle for simulated renders",
    } in skipped
    assert any("T03-loan-clean-i0 lacks it" in s["why"] for s in skipped)


def test_report_subcommand_rerenders_from_results_json(tmp_path: Path) -> None:
    payload = {
        "extraction": {},
        "false_alarms": {
            "scan_sim_heavy|ocr": {
                "n_clean": 3,
                "bundles_with_false_alarm": [0, 3],
                "flags_ge_medium": 0,
                "per_10": 0.0,
                "fast_lane": [0, 3],
                "mean_score": 0.0,
                "mean_coverage": 0.26,
                "pages_not_reviewed": [60, 80],
                "rules": {"a": 1, "b": 5},
                "rows": [],
            }
        },
        "recall": {},
        "errors": [],
        "skipped": [],
        "limits": ev.LIMITS,
    }
    res = tmp_path / "results.json"
    res.write_text(json.dumps(payload), encoding="utf-8")
    assert ev.main(["report", "--results", str(res)]) == 0
    md = (tmp_path / "real-ocr.md").read_text(encoding="utf-8")
    assert "scan_sim_heavy | ocr" in md and "60/80" not in md and "0.750" in md
    assert "b x5, a x1" in md  # most frequent rule first
    assert "No flags with low coverage is not a clean result" in md
    assert (tmp_path / "false_alarms.csv").exists()
