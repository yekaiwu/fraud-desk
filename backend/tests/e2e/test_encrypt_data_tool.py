"""backend/scripts/encrypt_data.py on a plaintext data directory (docs/encryption-at-rest.md section 5)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.crypto import DecryptionError, Vault, keyring_from_sources
from app.core.db import Database
from app.registry_live import AcraLookup
from scripts import encrypt_data
from tests.e2e._support import build_bundles, run_bundle, temp_root

NRIC = re.compile(r"[STFGM]\d{7}[A-Z]")


def key(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


@pytest.fixture(scope="module")
def plain_source():  # noqa: ANN201
    """A finished analysis stored as plaintext (encryption off), with renders and a register cache."""
    root = temp_root("gldb-enc-src-")
    bundle = build_bundles(root, scenario="kyc", variants=("clean",))["clean"]
    run = run_bundle(bundle, tmp=root / "run")
    for n, ref in enumerate(run.case.files[:3]):
        run.db.files.put_render(ref.sha256, "1_1000.png", b"\x89PNG-fake-render-%d" % n)
    AcraLookup(run.settings.data_dir / "registry-cache")._store("201681302M", {"name": "X"}, 1.0)
    # a job error and a second case version, so every category has something
    run.db.save_case(run.db.load_case(run.case.id), snapshot_version=True)
    markers = sorted(set(NRIC.findall(run.case.model_dump_json())))
    assert markers
    yield run, markers
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def data(plain_source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    run, markers = plain_source
    target = tmp_path / "data"
    shutil.copytree(run.settings.data_dir, target)
    monkeypatch.setenv("GLDB_DATA_DIR", str(target))
    monkeypatch.setenv("GLDB_ENCRYPTION_KEYS", f"k1:{key(1)}")
    return target, run.case.id, markers


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.endswith(("-shm",)):
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


def run_tool(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = encrypt_data.main([*args, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out)


def vault_of(*pairs: tuple[str, int]) -> Vault:
    ring = keyring_from_sources(",".join(f"{i}:{key(n)}" for i, n in pairs))
    return Vault(ring, enabled=True)


def open_db(data_dir: Path, vault: Vault) -> Database:
    return Database(data_dir / "gldb.sqlite3", data_dir=data_dir, vault=vault)


def test_dry_run_is_the_default_counts_and_writes_nothing(data, capsys) -> None:  # noqa: ANN001
    root, case_id, markers = data
    before = tree_digest(root)
    code = encrypt_data.main([])
    out = capsys.readouterr().out
    assert code == 0 and tree_digest(root) == before
    assert "dry run" in out and "nothing was written" in out
    for m in markers:
        assert m not in out  # content is never printed
    code, rep = run_tool(capsys, "--dry-run")
    assert code == 0 and rep["mode"] == "dry-run" and rep["failed"] == 0
    cats = rep["categories"]
    assert cats["files"]["plaintext"] == 10 and cats["files"]["encrypted_active_key"] == 0
    assert cats["renders"]["plaintext"] == 3 and cats["registry_cache"]["plaintext"] == 1
    assert cats["cases"]["plaintext"] == 1 and cats["case_versions"]["plaintext"] == 1
    assert all(c["converted"] == 0 for c in cats.values())
    assert tree_digest(root) == before


def test_dry_run_needs_no_key(data, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    root, _, _ = data
    monkeypatch.delenv("GLDB_ENCRYPTION_KEYS")
    code, rep = run_tool(capsys)
    assert (
        code == 0 and rep["active_key_id"] is None and rep["categories"]["files"]["plaintext"] == 10
    )


def test_apply_encrypts_everything_and_the_app_still_reads_it(data, capsys) -> None:  # noqa: ANN001
    root, case_id, markers = data
    code, rep = run_tool(capsys, "--apply")
    assert code == 0 and rep["failed"] == 0 and rep["active_key_id"] == "k1"
    cats = rep["categories"]
    assert cats["files"]["converted"] == 10 and cats["renders"]["converted"] == 3
    assert cats["registry_cache"]["converted"] == 1
    assert cats["cases"]["converted"] == 1 and cats["case_versions"]["converted"] == 1

    # nothing is plaintext any more
    code, rep = run_tool(capsys, "--dry-run")
    assert code == 0
    for name in ("files", "renders", "registry_cache", "cases", "case_versions"):
        assert rep["categories"][name]["plaintext"] == 0, name
        assert rep["categories"][name]["encrypted_active_key"] >= 1, name
    # no plaintext left on disk: scan every file and the raw SQLite bytes
    for p in root.rglob("*"):
        if p.is_file() and not p.name.startswith("gldb.sqlite3"):
            assert Vault.is_encrypted(p.read_bytes()), p.relative_to(root)
    raw = (root / "gldb.sqlite3").read_bytes()
    wal = root / "gldb.sqlite3-wal"
    raw += wal.read_bytes() if wal.exists() else b""
    for m in markers:
        assert m.encode() not in raw
    assert not list(root.rglob(".tmp-*"))
    con = sqlite3.connect(root / "gldb.sqlite3")
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()

    # the application reads it all, through the same code path as production
    db = open_db(root, vault_of(("k1", 1)))
    db.init_schema()
    case = db.load_case(case_id)
    assert case.status == "open" and case.applicant.uen
    assert db.load_case(case_id, version=1).id == case_id
    for ref in case.files:
        assert hashlib.sha256(db.files.read(ref.sha256)).hexdigest() == ref.sha256
    assert db.files.get_render(case.files[0].sha256, "1_1000.png") == b"\x89PNG-fake-render-0"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM case_files").fetchone()[0] == len(case.files)
    assert AcraLookup(root / "registry-cache", vault=db.vault)._cached("201681302M", 2.0)[0]
    # and delete still removes the encrypted files
    result = db.delete_case(case_id)
    assert len(result.files_removed) == len(case.files)


def test_apply_is_idempotent(data, capsys) -> None:  # noqa: ANN001
    root, _, _ = data
    run_tool(capsys, "--apply")
    files_once = tree_digest(root / "files")
    code, rep = run_tool(capsys, "--apply")
    assert code == 0 and all(c["converted"] == 0 for c in rep["categories"].values())
    assert tree_digest(root / "files") == files_once  # not re-encrypted: same ciphertext


def test_review_entries_jobs_and_fingerprint_meta_are_converted_too(data, capsys) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from app.core.fingerprints import SqliteFingerprintStore
    from app.core.models import ReviewEntry, StageRecord

    root, case_id, _ = data
    plain = Database(root / "gldb.sqlite3", data_dir=root)
    plain.init_schema()
    job = plain.create_job(case_id)
    plain.update_job(
        job.id,
        state="failed",
        error="could not read page for S0000001A",
        stages=[StageRecord(stage="ingest", status="failed", error="bad page S0000001A")],
    )
    entry = ReviewEntry(
        id="r9", kind="verdict", reviewer="a.tan", decision="approve",
        reason="reviewed S0000001A", status="effective", case_version=1,
        created_at=datetime(2026, 10, 27, tzinfo=UTC),
    )  # fmt: skip
    with plain.conn() as c:
        c.execute(
            "INSERT INTO review_entries (id, case_id, case_version, created_at, body, status) "
            "VALUES (?,?,?,?,?,?)",
            (
                f"{case_id}/r9",
                case_id,
                1,
                "2026-10-27T00:00:00.000000Z",
                entry.model_dump_json(),
                "effective",
            ),
        )
    SqliteFingerprintStore(plain).record(
        "crop_phash",
        "ab" * 8,
        case_id=case_id,
        document_id=None,
        meta={"applicant_uen": "S0000001A"},
    )
    code, rep = run_tool(capsys, "--apply")
    cats = rep["categories"]
    assert code == 0 and cats["review_entries"]["converted"] == 1
    assert cats["jobs"]["converted"] == 2 and cats["fingerprints"]["converted"] >= 1
    raw = (root / "gldb.sqlite3").read_bytes()
    assert b"S0000001A" not in raw
    db = open_db(root, vault_of(("k1", 1)))
    got = db.get_job(job.id)
    assert got is not None and got.error == "could not read page for S0000001A"
    assert db.load_case(case_id).review[0].reason == "reviewed S0000001A"
    hits = SqliteFingerprintStore(db).lookup("crop_phash", "ab" * 8)
    assert hits[0].meta == {"applicant_uen": "S0000001A"}


def test_apply_without_a_key_is_a_usage_error_and_changes_nothing(
    data,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    root, _, _ = data
    monkeypatch.delenv("GLDB_ENCRYPTION_KEYS")
    before = tree_digest(root)
    assert encrypt_data.main(["--apply"]) == 2
    assert "no key" in capsys.readouterr().err
    assert tree_digest(root) == before


def test_a_bad_item_fails_the_run_but_not_the_others_and_a_rerun_finishes(
    data,
    capsys,  # noqa: ANN001
) -> None:
    root, case_id, markers = data
    # corrupt one stored original: its bytes no longer match its hash
    victim = sorted((root / "files").rglob("*"))
    victim = [p for p in victim if p.is_file()][0]
    good = victim.read_bytes()
    victim.write_bytes(good + b"garbage")
    code = encrypt_data.main(["--apply"])
    out = capsys.readouterr().out
    assert code == 1
    assert victim.name[:12] in out  # the id is named ...
    for m in markers:
        assert m not in out  # ... the content never is
    assert victim.read_bytes() == good + b"garbage"  # left untouched
    others = [p for p in (root / "files").rglob("*") if p.is_file() and p != victim]
    assert others and all(Vault.is_encrypted(p.read_bytes()) for p in others)
    # repair it; a second run finishes the job (resumable)
    victim.write_bytes(good)
    code, rep = run_tool(capsys, "--apply")
    assert code == 0 and rep["categories"]["files"]["converted"] == 1
    assert Vault.is_encrypted(victim.read_bytes())


def test_rotate_re_encrypts_under_the_new_key_and_old_key_can_then_be_retired(
    data,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    root, case_id, _ = data
    run_tool(capsys, "--apply")
    monkeypatch.setenv("GLDB_ENCRYPTION_KEYS", f"k2:{key(2)},k1:{key(1)}")
    code, rep = run_tool(capsys, "--dry-run")
    assert code == 0 and rep["active_key_id"] == "k2"
    assert rep["categories"]["files"]["encrypted_old_key"] == 10
    assert rep["categories"]["cases"]["encrypted_old_key"] == 1
    # apply does not touch items that are already encrypted
    code, rep = run_tool(capsys, "--apply")
    assert code == 0 and all(c["converted"] == 0 for c in rep["categories"].values())
    code, rep = run_tool(capsys, "--rotate")
    assert code == 0 and rep["failed"] == 0
    assert (
        rep["categories"]["files"]["converted"] == 10
        and rep["categories"]["cases"]["converted"] == 1
    )
    code, rep = run_tool(capsys, "--rotate")
    assert all(c["converted"] == 0 for c in rep["categories"].values())  # idempotent
    # the old key is no longer needed for anything
    monkeypatch.setenv("GLDB_ENCRYPTION_KEYS", f"k2:{key(2)}")
    code, rep = run_tool(capsys, "--dry-run")
    assert rep["categories"]["files"]["encrypted_active_key"] == 10
    assert rep["categories"]["files"]["encrypted_unknown_key"] == 0
    db = open_db(root, vault_of(("k2", 2)))
    case = db.load_case(case_id)
    assert db.files.read(case.files[0].sha256)
    # while the previous key alone no longer opens it
    old_only = open_db(root, vault_of(("k1", 1)))
    with pytest.raises(DecryptionError):
        old_only.load_case(case_id)


def test_items_under_a_key_that_is_not_loaded_are_reported_not_touched(
    data,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    root, _, _ = data
    run_tool(capsys, "--apply")
    monkeypatch.setenv("GLDB_ENCRYPTION_KEYS", f"k9:{key(9)}")
    before = tree_digest(root)
    code, rep = run_tool(capsys, "--rotate")
    assert code == 0 and rep["categories"]["files"]["encrypted_unknown_key"] == 10
    assert all(c["converted"] == 0 for c in rep["categories"].values())
    assert tree_digest(root) == before


def test_a_tampered_encrypted_item_makes_rotate_fail_loudly(
    data,
    monkeypatch: pytest.MonkeyPatch,
    capsys,  # noqa: ANN001
) -> None:
    root, _, _ = data
    run_tool(capsys, "--apply")
    victim = [p for p in (root / "files").rglob("*") if p.is_file()][0]
    raw = bytearray(victim.read_bytes())
    raw[-5] ^= 0x01
    victim.write_bytes(bytes(raw))
    monkeypatch.setenv("GLDB_ENCRYPTION_KEYS", f"k2:{key(2)},k1:{key(1)}")
    code, rep = run_tool(capsys, "--rotate")
    assert code == 1 and rep["categories"]["files"]["failed"] == 1
    assert rep["categories"]["files"]["failed_ids"] == [victim.name[:12]]
    assert victim.read_bytes() == bytes(raw)  # untouched


def test_the_tool_reads_a_key_file_and_refuses_a_loose_one(
    data,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,  # noqa: ANN001
) -> None:
    root, _, _ = data
    monkeypatch.delenv("GLDB_ENCRYPTION_KEYS")
    kf = tmp_path / "keys.txt"
    kf.write_text(f"filekey:{key(5)}\n")
    kf.chmod(0o644)
    monkeypatch.setenv("GLDB_ENCRYPTION_KEY_FILE", str(kf))
    assert encrypt_data.main(["--apply"]) == 2
    assert "group or others" in capsys.readouterr().err
    kf.chmod(0o600)
    code, rep = run_tool(capsys, "--apply")
    assert code == 0 and rep["active_key_id"] == "filekey"


def test_a_missing_data_dir_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    monkeypatch.setenv("GLDB_DATA_DIR", str(tmp_path / "nope"))
    assert encrypt_data.main([]) == 2


def test_the_text_report_lists_every_category(data, capsys) -> None:  # noqa: ANN001
    assert encrypt_data.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    for name in encrypt_data.CATEGORIES:
        assert name in out
    assert Settings.model_fields["encrypt_at_rest"]
