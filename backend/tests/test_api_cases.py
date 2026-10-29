"""Upload, jobs, case view, page images, downloads (api.md sections 3, 5, 7, 11; tasks T-api-backend-01/03).

The first test is the walking skeleton: real upload, real job runner, real pipeline stages with the
Fake LLM, no network.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

import app.pipeline as pipeline
from app.checks.registry import all_checks
from app.core.config import Settings
from app.core.db import Database
from app.core.models import Classification, Document, FileRef, PageInfo
from tests.test_api_support import (
    Env,
    make_env,
    mk_flag,
    review_body,
    tiny_pdf,
    tiny_png,
    write_fixture,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def upload(env: Env, files: list[tuple[str, bytes, str]], **form: str):  # noqa: ANN201
    data = {"use_case": "loan", **form}
    return env.client.post(
        "/cases",
        data=data,
        files=[("files", (name, content, mime)) for name, content, mime in files],
    )


def finish(env: Env, created: dict[str, Any]) -> dict[str, Any]:
    job = env.app.state.runner.wait(created["job_id"], timeout=120)
    assert job.state == "succeeded", job.error
    return env.client.get(f"/cases/{created['case_id']}").json()


def err(resp) -> dict:  # noqa: ANN001
    body = resp.json()
    assert set(body) == {"error"} and resp.headers["x-request-id"], resp.text
    return body["error"]


# -- walking skeleton -----------------------------------------------------------------------------


def test_walking_skeleton_upload_runs_the_job_and_the_case_reaches_the_queue(env: Env) -> None:
    r = upload(
        env,
        [("statement.pdf", tiny_pdf(), "application/pdf"), ("scan.png", tiny_png(), "image/png")],
        reference="ACME-2026-014",
        applicant_name="Example Trading Pte. Ltd.",
        applicant_uen="201912345r",
    )
    assert r.status_code == 202, r.text
    created = r.json()
    assert set(created) == {"case_id", "job_id", "status_url", "case_url"}
    assert r.headers["location"] == created["status_url"] == f"/jobs/{created['job_id']}"
    assert created["case_url"] == f"/cases/{created['case_id']}"

    case = finish(env, created)
    assert case["status"] == "open" and case["version"] == 1
    assert case["reference"] == "ACME-2026-014"
    assert case["applicant"] == {
        "name": "Example Trading Pte. Ltd.",
        "uen": "201912345R",
        "source": "upload",
    }
    assert [f["name"] for f in case["files"]] == ["statement.pdf", "scan.png"]
    assert case["score"] is not None and case["coverage"]["applicable"] > 0
    assert case["provenance"]["flags_digest"]
    assert [s["stage"] for s in case["stages"]] == [
        "ingest",
        "classify",
        "extract",
        "forensics",
        "rules",
        "crossdoc",
        "score",
    ]
    assert all("words" not in d for d in case["documents"])

    job = env.client.get(created["status_url"]).json()
    assert job["state"] == "succeeded" and job["kind"] == "analyse"
    assert job["progress"] == {"stage_index": 7, "stage_count": 7, "current_stage": None}
    assert len(job["stages"]) == 7 and job["error"] is None

    queue = env.client.get("/cases").json()
    assert queue["total"] == 1 and queue["items"][0]["case_id"] == created["case_id"]
    assert queue["items"][0]["status"] == "open" and queue["items"][0]["file_count"] == 2

    actions = [e["action"] for e in env.audit(created["case_id"])]
    assert actions == ["case.created", "case.analysis_finished"]  # created always comes first
    finished = env.audit(created["case_id"])[1]
    assert finished["actor"] == "system"
    assert finished["detail"]["flags_digest"] == case["provenance"]["flags_digest"]
    assert "Example Trading" not in json.dumps(env.audit_rows())
    write_fixture("case_detail", case)
    write_fixture("job", job)


def test_honest_quirk_unreadable_scan_gives_a_partial_but_reviewable_case(env: Env) -> None:
    created = upload(env, [("scan.png", tiny_png(), "image/png")]).json()
    case = finish(env, created)
    assert case["partial"] is True and case["partial_reasons"]
    assert case["score"]["partial"] is True
    assert case["allowed_verdicts"], "a partial case is still reviewable"
    # reviewing it is allowed; the audit says it was partial
    r = env.review(
        created["case_id"],
        **review_body("verdict", "request_documents", reason="Need a readable statement"),
    )
    assert r.status_code == 201, r.text
    row = next(e for e in env.audit(created["case_id"]) if e["action"] == "review.verdict")
    assert row["detail"]["partial"] is True


def test_the_same_file_twice_is_accepted_duplicates_are_a_check_not_a_gate(env: Env) -> None:
    pdf = tiny_pdf()
    r = upload(env, [("a.pdf", pdf, "application/pdf"), ("b.pdf", pdf, "application/pdf")])
    assert r.status_code == 202
    case = finish(env, r.json())
    assert len(case["files"]) == 2 and case["files"][0]["sha256"] == case["files"][1]["sha256"]


# -- upload validation ----------------------------------------------------------------------------


def stored_blobs(env: Env) -> list[str]:
    root = env.db.files.files_dir
    return [p.name for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_text_named_pdf_is_rejected_by_magic_bytes(env: Env) -> None:
    r = upload(env, [("statement.pdf", b"just some text", "application/pdf")])
    assert r.status_code == 415 and err(r)["code"] == "unsupported_media_type"
    assert (
        env.client.get("/cases", params={"status": "processing,open,closed,error"}).json()["total"]
        == 0
    )


def test_declared_content_type_does_not_matter_only_the_bytes(env: Env) -> None:
    r = upload(env, [("x.txt", tiny_pdf(), "text/plain")])
    assert r.status_code == 202


def test_oversize_file_is_413(settings: Settings, db: Database) -> None:
    env = make_env(settings.model_copy(update={"max_upload_mb": 1}), db)
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1)
    r = upload(env, [("big.pdf", big, "application/pdf")])
    assert r.status_code == 413 and err(r)["code"] == "file_too_large"
    assert err(r)["details"]["max_mb"] == 1


def test_zero_files_and_too_many_files_are_422(settings: Settings, db: Database) -> None:
    env = make_env(settings.model_copy(update={"max_files_per_case": 2}), db)
    none = env.client.post("/cases", data={"use_case": "loan"})
    assert none.status_code == 422 and err(none)["code"] == "no_files"
    many = upload(env, [(f"{i}.pdf", tiny_pdf(), "application/pdf") for i in range(3)])
    assert many.status_code == 422 and err(many)["code"] == "too_many_files"
    assert err(many)["details"] == {"max": 2, "received": 3}


def test_a_bad_third_file_writes_nothing(env: Env) -> None:
    r = upload(
        env,
        [
            ("a.pdf", tiny_pdf("one"), "application/pdf"),
            ("b.png", tiny_png(), "image/png"),
            ("c.pdf", b"not a pdf", "application/pdf"),
        ],
    )
    assert r.status_code == 415
    assert stored_blobs(env) == []
    assert (
        env.client.get("/cases", params={"status": "processing,open,closed,error"}).json()["total"]
        == 0
    )
    assert env.audit_rows() == []


@pytest.mark.parametrize(
    "form",
    [
        {"use_case": "mortgage"},
        {"use_case": "loan", "reference": "r" * 81},
        {"use_case": "loan", "applicant_name": "n" * 121},
        {"use_case": "loan", "applicant_uen": "u" * 21},
    ],
)
def test_form_field_violations_are_validation_errors(env: Env, form: dict[str, str]) -> None:
    r = env.client.post(
        "/cases", data=form, files=[("files", ("a.pdf", tiny_pdf(), "application/pdf"))]
    )
    assert r.status_code == 422 and err(r)["code"] == "validation_error"
    assert err(r)["details"]["errors"]
    assert stored_blobs(env) == []


def test_unusual_filename_is_sanitised_for_display(env: Env) -> None:
    r = upload(env, [("../../etc/passwd.pdf", tiny_pdf(), "application/pdf")])
    case = finish(env, r.json())
    assert case["files"][0]["name"] == "passwd.pdf"
    r2 = upload(env, [("..\\..\\win\\ini.pdf", tiny_pdf("two"), "application/pdf")])
    assert finish(env, r2.json())["files"][0]["name"] == "ini.pdf"
    assert case["files"][0]["sha256"] in stored_blobs(env)  # the stored name is the hash


# -- jobs -----------------------------------------------------------------------------------------


def test_unknown_job_is_404(env: Env) -> None:
    r = env.client.get("/jobs/j_nope")
    assert (
        r.status_code == 404
        and err(r)["code"] == "not_found"
        and err(r)["details"]["kind"] == "job"
    )


def test_a_crashed_job_marks_the_case_error_and_a_rerun_recovers(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx):  # noqa: ANN001, ANN202
        raise RuntimeError("provenance exploded")

    monkeypatch.setattr(pipeline, "build_provenance", boom)
    created = upload(env, [("a.pdf", tiny_pdf(), "application/pdf")]).json()
    job = env.app.state.runner.wait(created["job_id"], timeout=120)
    assert job.state == "failed" and "provenance exploded" in (job.error or "")
    body = env.client.get(created["status_url"]).json()
    assert body["state"] == "failed" and body["progress"]["current_stage"] is None
    assert env.client.get(created["case_url"]).json()["status"] == "error"
    actions = [e["action"] for e in env.audit(created["case_id"])]
    assert actions == ["case.created", "case.analysis_failed"]
    # a case in error cannot be reviewed but can be rerun
    assert env.review(created["case_id"], **review_body("verdict", "approve")).status_code == 409
    monkeypatch.undo()
    rr = env.client.post(f"/cases/{created['case_id']}/rerun")
    assert rr.status_code == 202 and rr.json()["version"] == 2
    case = finish(env, rr.json())
    assert case["status"] == "open" and case["version"] == 2


# -- case view ------------------------------------------------------------------------------------


def test_unknown_case_version_and_include_values(env: Env) -> None:
    case = env.seed(flags=[])
    assert env.client.get("/cases/c_missing").status_code == 404
    r = env.client.get(f"/cases/{case.id}", params={"version": 7})
    assert r.status_code == 404 and err(r)["details"]["kind"] == "version"
    r = env.client.get(f"/cases/{case.id}", params={"include": "words,secrets"})
    assert r.status_code == 422 and err(r)["code"] == "validation_error"
    assert env.client.get(f"/cases/{case.id}", params={"version": 0}).status_code == 422


def _doc_with_words(env: Env, case_id: str) -> None:
    from app.core.models import Word
    from app.core.testing import make_doc

    case = env.db.load_case(case_id)
    doc = make_doc(
        "invoice", id="d1", words=[Word(page=1, text="Hello", x=0.1, y=0.1, w=0.1, h=0.02)]
    )
    case.documents = [doc]
    env.db.save_case(case)


def test_words_are_omitted_by_default_and_fetched_on_demand(env: Env) -> None:
    case = env.seed(flags=[])
    _doc_with_words(env, case.id)
    plain = env.client.get(f"/cases/{case.id}").json()
    assert "words" not in plain["documents"][0]
    with_words = env.client.get(f"/cases/{case.id}", params={"include": "words"}).json()
    assert with_words["documents"][0]["words"][0]["text"] == "Hello"
    r = env.client.get(f"/cases/{case.id}/documents/d1/words")
    assert r.status_code == 200 and r.json()["document_id"] == "d1"
    assert r.json()["words"][0]["text"] == "Hello"
    missing = env.client.get(f"/cases/{case.id}/documents/d9/words")
    assert missing.status_code == 404 and err(missing)["details"]["kind"] == "document"
    assert env.client.get("/cases/c_missing/documents/d1/words").status_code == 404


def test_not_checked_documents_list_received_documents_and_boilerplate_pages(env: Env) -> None:
    from app.core.testing import make_doc

    case = env.seed(flags=[])
    loaded = env.db.load_case(case.id)
    loaded.files = [
        FileRef(
            id="f1",
            name="bundle.pdf",
            sha256="a" * 64,
            size_bytes=10,
            mime="application/pdf",
            pages=[
                PageInfo(
                    page=n,
                    width_pt=595,
                    height_pt=842,
                    has_text_layer=True,
                    is_scan=False,
                    classification=Classification(
                        label=label, probability=0.9, classifier="rules", version="1"
                    ),
                )
                for n, label in enumerate(["invoice", "other", "boilerplate"], start=1)
            ],
        )
    ]
    other = make_doc(
        "other", id="d2", file_id="f1", page_start=2, page_end=2, status="received_not_checked"
    )
    loaded.documents = [make_doc("invoice", id="d1", file_id="f1"), other]
    env.db.save_case(loaded)
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["not_checked_documents"] == [
        {
            "document_id": "d2",
            "doc_type": "other",
            "pages": "f1:2",
            "reason": "classified as other: no checks apply",
        },
        {
            "document_id": "-",
            "doc_type": "boilerplate",
            "pages": "f1:3",
            "reason": "page classified as boilerplate",
        },
    ]
    assert isinstance(Document, type)


def test_case_detail_has_the_documented_extra_keys(env: Env) -> None:
    case = env.seed(flags=[mk_flag()])
    detail = env.client.get(f"/cases/{case.id}").json()
    for key in (
        "pending_second_approvals",
        "allowed_verdicts",
        "not_checked_documents",
        "cost_summary",
    ):
        assert key in detail
    assert set(detail["cost_summary"]) == {
        "total_usd",
        "calls",
        "cache_hits",
        "seconds_by_stage",
        "usd_by_stage",
    }
    assert detail["flags"][0]["disposition"] == "open"
    assert "Traceback" not in json.dumps(detail)


# -- page images ------------------------------------------------------------------------------------


def seed_with_files(env: Env, files: list[tuple[str, bytes]]):  # noqa: ANN202
    return env.seed(flags=[], files=files)


def test_page_image_is_a_png_with_a_stable_etag_and_is_cached(env: Env) -> None:
    case = seed_with_files(env, [("a.pdf", tiny_pdf(pages=2))])
    url = f"/cases/{case.id}/pages/2"
    r = env.client.get(url, params={"file_id": "f1", "w": 600})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(PNG_MAGIC)
    sha = case.files[0].sha256
    assert r.headers["etag"] == f'"{sha}-2-600"'
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    from PIL import Image

    assert Image.open(io.BytesIO(r.content)).width == 600
    cached = env.db.files.renders_dir / sha / "2_600.png"
    assert cached.is_file()
    again = env.client.get(url, params={"file_id": "f1", "w": 600})
    assert again.content == r.content and again.headers["etag"] == r.headers["etag"]
    not_modified = env.client.get(
        url, params={"file_id": "f1", "w": 600}, headers={"If-None-Match": r.headers["etag"]}
    )
    assert not_modified.status_code == 304 and not_modified.content == b""
    default_w = env.client.get(url, params={"file_id": "f1"})
    assert Image.open(io.BytesIO(default_w.content)).width == 1000


def test_page_image_errors(env: Env) -> None:
    case = seed_with_files(env, [("a.pdf", tiny_pdf()), ("b.png", tiny_png())])
    base = f"/cases/{case.id}/pages/1"
    missing_file_id = env.client.get(base)
    assert missing_file_id.status_code == 422 and err(missing_file_id)["code"] == "validation_error"
    assert env.client.get(base, params={"file_id": "f9"}).status_code == 404
    assert env.client.get(f"/cases/{case.id}/pages/3", params={"file_id": "f1"}).status_code == 404
    assert env.client.get(f"/cases/{case.id}/pages/0", params={"file_id": "f1"}).status_code == 404
    assert env.client.get(f"/cases/{case.id}/pages/2", params={"file_id": "f2"}).status_code == 404
    for w in (199, 2001, "abc"):
        r = env.client.get(base, params={"file_id": "f1", "w": w})
        assert r.status_code == 422 and err(r)["code"] == "validation_error", w
    assert env.client.get("/cases/c_nope/pages/1", params={"file_id": "f1"}).status_code == 404


def test_a_case_with_one_file_needs_no_file_id(env: Env) -> None:
    case = seed_with_files(env, [("b.png", tiny_png())])
    r = env.client.get(f"/cases/{case.id}/pages/1")
    assert r.status_code == 200 and r.content.startswith(PNG_MAGIC)


def test_image_pages_are_rotated_upright_before_rendering(env: Env) -> None:
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (300, 100), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    img.save(buf, "JPEG", exif=exif)
    case = seed_with_files(env, [("photo.jpg", buf.getvalue())])
    r = env.client.get(f"/cases/{case.id}/pages/1", params={"w": 400})
    out = Image.open(io.BytesIO(r.content))
    assert out.width == 400 and out.height == 1200  # portrait after exif_transpose, not 400x133


def test_corrupt_stored_pdf_gives_a_clean_404_not_a_crash(env: Env) -> None:
    case = seed_with_files(env, [("a.pdf", b"%PDF-1.4 definitely not a pdf")])
    r = env.client.get(f"/cases/{case.id}/pages/1")
    assert r.status_code == 404 and err(r)["details"]["reason"] == "unreadable"


def test_original_download_keeps_bytes_sets_a_safe_header_and_is_audited(env: Env) -> None:
    pdf = tiny_pdf("original")
    case = seed_with_files(env, [('na"me é.pdf', pdf)])
    r = env.client.get(f"/cases/{case.id}/files/f1")
    assert r.status_code == 200 and r.content == pdf
    assert r.headers["content-type"] == "application/pdf"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=") and '"na"me' not in disposition
    row = [e for e in env.audit(case.id) if e["action"] == "file.downloaded"]
    assert row and row[0]["detail"] == {"file_sha256": case.files[0].sha256}
    assert env.client.get(f"/cases/{case.id}/files/f9").status_code == 404


# -- health and rules --------------------------------------------------------------------------------


def test_health_echoes_ui_settings_without_secrets(env: Env) -> None:
    h = env.client.get("/health").json()
    assert h["status"] == "ok" and h["llm_backend"] == "fake"
    assert h["settings"] == {
        "classifier_min_prob": 0.7,
        "max_upload_mb": 25,
        "max_files_per_case": 40,
        "require_second_approver": True,
    }
    assert "key" not in json.dumps(h).lower()


def test_rules_endpoint_equals_the_registry_unfiltered(env: Env) -> None:
    rules = env.client.get("/rules").json()["rules"]
    checks = all_checks()
    assert [r["rule"] for r in rules] == [c.rule for c in checks]
    first = rules[0]
    assert set(first) == {
        "rule",
        "stage",
        "severity",
        "applies_to",
        "requires",
        "use_cases",
        "description",
        "tamper",
        "version",
        # check catalogue overrides (api.md section 9a): intentionally added to every item
        "default_severity",
        "effective_severity",
        "enabled",
        "override",
    }
    by_rule = {c.rule: c for c in checks}
    for r in rules:
        meta = by_rule[r["rule"]]
        assert r["applies_to"] == sorted(meta.applies_to) and r["use_cases"] == sorted(
            meta.use_cases
        )
        assert r["severity"] == meta.severity and r["version"] == meta.version
    assert "bs_running_balance" in by_rule
