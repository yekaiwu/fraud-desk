"""A full analysis with encryption at rest ON, then a scan of everything on disk for known plaintext.

The bundle is a generated, fictitious KYC pack (company name, UEN, three NRIC/FIN-looking numbers on the
director documents). The same pack is analysed once with encryption OFF as a control: there the markers
must be found by the scan, so a clean result in the encrypted run means something.
"""

from __future__ import annotations

import base64
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.crypto import Vault
from app.core.llm import FakeLLMClient
from app.core.testing import FIXED_TODAY
from scripts import run_case
from tests.e2e._support import build_bundles, make_settings, new_db, run_bundle, temp_root

KEY = base64.b64encode(bytes(range(32))).decode()  # a TEST key, not a secret
NRIC = re.compile(r"[STFGM]\d{7}[A-Z]")
# the documented plaintext index columns of `cases` (docs/encryption-at-rest.md section 3)
INDEX_COLUMNS = ("applicant_name", "reference")


@pytest.fixture(scope="module")
def kyc_bundle() -> Path:
    root = temp_root("gldb-enc-")
    out = build_bundles(root, scenario="kyc", variants=("clean",))["clean"]
    yield out
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def control(kyc_bundle: Path):  # noqa: ANN201
    """The same bundle with encryption OFF: the source of the markers and the proof the scan can see them."""
    run = run_bundle(kyc_bundle, tmp=temp_root("gldb-enc-off-"))
    body = run.case.model_dump_json()
    markers = {
        "nric": sorted(set(NRIC.findall(body))),
        "uen": run.case.applicant.uen,
        "name": run.case.applicant.name,
    }
    assert len(markers["nric"]) >= 2 and markers["uen"] and markers["name"]
    return run, markers


def every_file(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def raw_sqlite(db_path: str) -> bytes:
    out = b""
    for suffix in ("", "-wal", "-shm"):
        p = Path(db_path + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


def sanitised_copy(db_path: str, out: Path) -> bytes:
    """The database with the documented plaintext index columns blanked and rebuilt, so what is left
    can be scanned for a company name without the allowed column getting in the way."""
    src = sqlite3.connect(db_path)
    src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    src.execute(f"VACUUM INTO '{out}'")
    src.close()
    dst = sqlite3.connect(out)
    dst.execute("UPDATE cases SET " + ", ".join(f"{c} = NULL" for c in INDEX_COLUMNS))
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    return out.read_bytes()


def test_the_control_run_shows_the_scan_can_see_plaintext(control) -> None:  # noqa: ANN001
    run, markers = control
    raw = raw_sqlite(run.settings.database_path)
    assert markers["nric"][0].encode() in raw
    assert markers["uen"].encode() in raw
    originals = [
        p for p in every_file(run.settings.files_dir) if p.read_bytes().startswith(b"%PDF-")
    ]
    assert originals, "with encryption off the uploaded PDFs sit on disk as they are"


def test_full_api_run_with_encryption_on_leaves_no_plaintext(  # noqa: ANN201
    control, kyc_bundle: Path, tmp_path: Path
) -> None:
    ctrl_run, markers = control
    settings = make_settings(
        tmp_path, today=FIXED_TODAY, encrypt_at_rest="on", encryption_keys=f"k1:{KEY}"
    )
    db = new_db(settings)  # built without a vault: the app must adopt the one the settings ask for
    application = main.create_app(
        settings, db=db, llm=FakeLLMClient(), registry=run_case.bundle_registry(kyc_bundle)
    )
    client = TestClient(application, raise_server_exceptions=False)
    paths = run_case.bundle_files(kyc_bundle)
    originals = {p.name: p.read_bytes() for p in paths}
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    created = client.post(
        "/cases",
        data={"use_case": "kyc", "reference": "REF-ENC-1"},
        files=files,
    )
    assert created.status_code == 202, created.text
    case_id = created.json()["case_id"]
    job = application.state.runner.wait(created.json()["job_id"], timeout=180)
    assert job.state == "succeeded", job.error

    # the analysis is the same as without encryption
    case = db.load_case(case_id)
    assert case.status == "open"
    assert (case.score.value, case.score.band) == (
        ctrl_run.case.score.value,
        ctrl_run.case.score.band,
    )
    assert case.applicant.name == markers["name"] and case.applicant.uen == markers["uen"]
    assert [f.rule for f in case.flags] == [f.rule for f in ctrl_run.case.flags]

    # a second version (documents added) snapshots the first: case_versions gets a sealed body too
    originals["again.pdf"] = paths[0].read_bytes()
    more = client.post(
        f"/cases/{case_id}/files",
        files=[("files", ("again.pdf", originals["again.pdf"], "application/pdf"))],
    )
    assert more.status_code == 202, more.text
    rerun = application.state.runner.wait(more.json()["job_id"], timeout=180)
    assert rerun.state == "succeeded", rerun.error
    case = db.load_case(case_id)
    assert case.version == 2 and db.load_case(case_id, version=1).version == 1

    # the application still serves everything: queue, case, page images (renders), originals, pack
    assert client.get("/cases").json()["total"] == 1
    assert client.get(f"/cases/{case_id}").status_code == 200
    for ref in case.files:
        dl = client.get(f"/cases/{case_id}/files/{ref.id}")
        assert dl.status_code == 200 and dl.content == originals[ref.name]
    for ref in case.files[:4]:
        page = client.get(f"/cases/{case_id}/pages/1", params={"file_id": ref.id})
        assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
    assert client.get(f"/cases/{case_id}/pack").status_code == 200
    # a review entry carrying an identifier in its free text
    verdict = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "verdict",
            "decision": "approve",
            "reviewer": "a.tan",
            "reason": f"Checked {markers['nric'][0]} against the card.",
            "case_version": case.version,
        },
    )
    assert verdict.status_code in (200, 201), verdict.text
    assert client.get(f"/cases/{case_id}").json()["review"]

    # monitoring says it plainly
    enc = client.get("/monitoring").json()["encryption_at_rest"]
    assert enc["mode"] == "on" and enc["active_key_id"] == "k1"
    assert enc["status"] == "on" and enc["all_sampled_encrypted"] is True
    assert (
        enc["categories"]["files"]["encrypted"] >= 1
        and enc["categories"]["renders"]["encrypted"] >= 1
    )

    # ---- the scan: every file in the data dir, and the raw SQLite bytes -------------------------
    data_dir = settings.data_dir
    markers_b = [m.encode() for m in markers["nric"]] + [
        markers["uen"].encode(),
        markers["name"].encode(),
    ]
    stored = every_file(data_dir)
    assert stored
    kinds = {p.parent.parent.name for p in stored}
    assert {"files", "renders"} <= kinds
    for p in stored:
        if p.name.startswith("gldb.sqlite3"):
            continue  # the database is checked below
        blob = p.read_bytes()
        for m in markers_b:
            assert m not in blob, f"plaintext marker found in {p.relative_to(data_dir)}"
        assert not blob.startswith((b"%PDF-", b"\x89PNG", b"\xff\xd8\xff")), p.name
        assert Vault.is_encrypted(blob), f"{p.relative_to(data_dir)} is not an encrypted envelope"
    # no original file content anywhere: a distinctive slice of each upload must not occur in any file
    haystack = b"".join(p.read_bytes() for p in stored)
    for name, data in originals.items():
        assert data[:400] not in haystack and data[-400:] not in haystack, name

    raw = raw_sqlite(settings.database_path)
    for m in (*markers["nric"], markers["uen"]):
        assert m.encode() not in raw, "an identifier is readable in the SQLite bytes"
    assert (
        markers["name"].encode() in raw
    )  # ... except the documented index column (applicant_name)
    assert b"REF-ENC-1" in raw  # reference is the other documented index column
    clean = sanitised_copy(settings.database_path, tmp_path / "clean.sqlite3")
    for m in (*markers["nric"], markers["uen"], markers["name"], "REF-ENC-1"):
        assert m.encode() not in clean, "plaintext outside the documented index columns"
    # every sensitive column is an envelope
    con = sqlite3.connect(settings.database_path)
    for table, col in (
        ("cases", "body"),
        ("case_versions", "body"),
        ("review_entries", "body"),
        ("llm_cache", "value"),
        ("cases", "top_finding"),
        ("jobs", "stages"),
    ):
        values = [r[0] for r in con.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL")]
        assert values or table == "llm_cache", (table, col)  # the fake model makes no cached calls
        assert all(v.startswith("gldbenc1:") for v in values), (table, col)
    con.close()

    # the audit trail was pseudonymised before this change and still holds no identifiers
    for m in markers["nric"]:
        assert client.get(f"/cases/{case_id}/audit").text.count(m) == 0

    # delete: everything is removed, nothing is left behind
    deleted = client.delete(
        f"/cases/{case_id}", params={"reviewer": "a.tan", "reason": "end of test, delete it"}
    )
    assert deleted.status_code == 204
    assert not list((data_dir / "files").rglob("*")) or all(
        p.is_dir() for p in (data_dir / "files").rglob("*")
    )
    assert not any(p.is_file() for p in (data_dir / "renders").rglob("*"))


def test_analyse_files_with_encryption_on(kyc_bundle: Path, control, tmp_path: Path) -> None:  # noqa: ANN001
    """The helper the eval harness imports, with the settings choosing the encryption."""
    ctrl_run, markers = control
    run = run_bundle(kyc_bundle, tmp=tmp_path, encrypt_at_rest="on", encryption_keys=f"k1:{KEY}")
    assert run.case.provenance and ctrl_run.case.provenance
    assert run.case.provenance.flags_digest == ctrl_run.case.provenance.flags_digest
    raw = raw_sqlite(run.settings.database_path)
    assert markers["nric"][0].encode() not in raw and markers["uen"].encode() not in raw
    for p in every_file(run.settings.data_dir):
        if not p.name.startswith("gldb.sqlite3"):
            assert Vault.is_encrypted(p.read_bytes())
    # fingerprints (hashes) are kept and their meta, which holds the applicant UEN, is encrypted
    con = sqlite3.connect(run.settings.database_path)
    metas = [r[0] for r in con.execute("SELECT meta FROM fingerprints")]
    con.close()
    assert metas and all(m == "{}" or m.startswith("gldbenc1:") for m in metas)
    assert run.db.load_case(run.case.id).applicant.uen == markers["uen"]


def test_with_the_wrong_key_the_data_cannot_be_read(kyc_bundle: Path, tmp_path: Path) -> None:
    from app.core.crypto import DecryptionError

    run = run_bundle(kyc_bundle, tmp=tmp_path, encrypt_at_rest="on", encryption_keys=f"k1:{KEY}")
    other = base64.b64encode(bytes([9]) * 32).decode()
    wrong = run.settings.model_copy(update={"encryption_keys": f"k1:{other}"})
    from app.core.crypto import vault_from_settings
    from app.core.db import Database

    db2 = Database(wrong.database_path, data_dir=wrong.data_dir, vault=vault_from_settings(wrong))
    with pytest.raises(DecryptionError):
        db2.load_case(run.case.id)
    with pytest.raises(DecryptionError):
        db2.files.read(run.case.files[0].sha256)
