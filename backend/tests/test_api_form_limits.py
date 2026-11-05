"""The multipart pre-read: files and text fields have separate error codes, and only the upload
routes read a form body (a multipart body sent to a JSON route gets the ordinary validation answer)."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main
from tests.test_access import REMOTE, SECRET, H, assert_envelope
from tests.test_api_support import make_env, seed_case, tiny_pdf


def client(settings, monkeypatch, **update) -> TestClient:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    s = settings.model_copy(update=update)
    return TestClient(main.create_app(s), client=REMOTE)


def test_one_pdf_and_many_text_fields_is_too_many_fields_not_too_many_files(
    settings, monkeypatch
) -> None:
    c = client(settings, monkeypatch, max_files_per_case=2)
    data = {"use_case": "loan", **{f"extra{i}": "x" for i in range(60)}}
    files = [("files", ("a.pdf", tiny_pdf(), "application/pdf"))]
    for path in ("/cases", "/cases/c_x/files"):
        r = c.post(path, headers=H, data=data, files=files)
        assert r.status_code == 422, path
        err = assert_envelope(r.json(), "too_many_fields")
        assert "files" not in err["message"].lower()
        assert err["details"]["max"] == 2 + 1 + 8


def test_too_many_files_keeps_its_own_code(settings, monkeypatch) -> None:
    c = client(settings, monkeypatch, max_files_per_case=2)
    files = [("files", (f"{i}.pdf", b"%PDF-1.4", "application/pdf")) for i in range(50)]
    r = c.post("/cases", headers=H, data={"use_case": "loan"}, files=files)
    assert r.status_code == 422
    assert assert_envelope(r.json(), "too_many_files")["details"]["max"] == 2


def test_a_multipart_body_on_a_json_route_is_a_validation_error_like_before(settings, db) -> None:
    env = make_env(settings, db)
    case = seed_case(db, settings, flags=[], status="open")
    form = {"kind": (None, "verdict"), "verdict": (None, "approve")}
    for path in (f"/cases/{case.id}/review", "/admin/retention/purge"):
        r = env.client.post(path, files=form)
        assert r.status_code == 422, (path, r.text)
        assert r.json()["error"]["code"] == "validation_error", path
