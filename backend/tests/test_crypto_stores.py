"""Encryption at rest in the file store, render cache, database and response cache, and delete/purge/review
on top of them (docs/encryption-at-rest.md sections 2 and 4)."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import stat
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.audit import AuditLog
from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.crypto import DecryptionError, Vault, keyring_from_sources, vault_from_settings
from app.core.db import CaseNotFound, Database
from app.core.storage import FileStore, sha256_bytes
from app.retention import expired_case_ids, purge
from tests.test_api_support import (
    FIXED_NOW,
    Env,
    FakeClock,
    make_env,
    mk_flag,
    review_body,
    seed_case,
    tiny_pdf,
)

NAME = "Zebulon Quasimodo Holdings"
NRIC = "S7654321Z"


def k(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


def vault_of(*ids: str, enabled: bool = True) -> Vault:
    ring = keyring_from_sources(",".join(f"{i}:{k(n)}" for n, i in enumerate(ids, start=1)))
    return Vault(ring, enabled=enabled)


@pytest.fixture
def enc_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        encrypt_at_rest="on",
        encryption_keys=f"k1:{k(1)}",
    )


@pytest.fixture
def enc_db(enc_settings: Settings) -> Database:
    db = Database(
        enc_settings.database_path,
        data_dir=enc_settings.data_dir,
        vault=vault_from_settings(enc_settings),
    )
    db.init_schema()
    return db


def raw_db_bytes(db: Database) -> bytes:
    """Every byte SQLite holds for this database: the file and its WAL."""
    out = b""
    for suffix in ("", "-wal"):
        p = Path(db.path + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


# ---- FileStore --------------------------------------------------------------------------


def test_file_store_encrypts_on_disk_and_keeps_the_plaintext_hash_as_the_id(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    data = b"%PDF-1.7 " + NRIC.encode() + os.urandom(200)
    sha = store.put(data)
    assert sha == sha256_bytes(data)  # the id is the hash of the PLAINTEXT
    on_disk = store.path_for(sha).read_bytes()
    assert on_disk != data and NRIC.encode() not in on_disk and Vault.is_encrypted(on_disk)
    assert store.read(sha) == data
    assert store.is_encrypted_on_disk(sha) and store.exists(sha)
    assert stat.S_IMODE(store.path_for(sha).stat().st_mode) == 0o600


def test_file_store_reads_legacy_plaintext_and_put_upgrades_it(tmp_path: Path) -> None:
    plain = FileStore(tmp_path)  # written before encryption was switched on
    data = b"%PDF-1.4 legacy"
    sha = plain.put(data)
    assert plain.path_for(sha).read_bytes() == data

    store = FileStore(tmp_path, vault_of("k1"))
    assert store.read(sha) == data  # gradual migration: legacy still reads
    assert not store.is_encrypted_on_disk(sha)
    assert store.put(data) == sha  # the same content uploaded again is re-sealed
    assert store.is_encrypted_on_disk(sha) and store.read(sha) == data
    assert data not in store.path_for(sha).read_bytes()


def test_file_store_mode_off_writes_plaintext(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1", enabled=False))
    sha = store.put(b"%PDF-1.4 x")
    assert store.path_for(sha).read_bytes() == b"%PDF-1.4 x"


def test_file_swap_between_hashes_is_detected(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    a, b = store.put(b"%PDF-1.4 aaaa"), store.put(b"%PDF-1.4 bbbb")
    store.path_for(b).write_bytes(store.path_for(a).read_bytes())  # attacker copies A over B
    with pytest.raises(DecryptionError):
        store.read(b)


def test_file_tamper_is_detected(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    sha = store.put(b"%PDF-1.4 hello world")
    raw = bytearray(store.path_for(sha).read_bytes())
    raw[-3] ^= 0x10
    store.path_for(sha).write_bytes(bytes(raw))
    with pytest.raises(DecryptionError):
        store.read(sha)


def test_render_cache_round_trip_and_swap(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    sha_a, sha_b = "a" * 64, "b" * 64
    png = b"\x89PNG\r\n\x1a\n" + b"pixels-of-a-page" * 20
    assert store.get_render(sha_a, "1_1000.png") is None
    store.put_render(sha_a, "1_1000.png", png)
    on_disk = store.render_path(sha_a, "1_1000.png").read_bytes()
    assert not on_disk.startswith(b"\x89PNG") and b"pixels-of-a-page" not in on_disk
    assert store.get_render(sha_a, "1_1000.png") == png
    # the same ciphertext under another page name or another file does not open
    store.render_path(sha_a, "2_1000.png").write_bytes(on_disk)
    with pytest.raises(DecryptionError):
        store.get_render(sha_a, "2_1000.png")
    store.render_path(sha_b, "1_1000.png").parent.mkdir(parents=True)
    store.render_path(sha_b, "1_1000.png").write_bytes(on_disk)
    with pytest.raises(DecryptionError):
        store.get_render(sha_b, "1_1000.png")


def test_render_names_cannot_escape_the_cache(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    for bad in ("../x.png", "a/b.png", ""):
        with pytest.raises(ValueError):
            store.render_path("a" * 64, bad)


def test_delete_removes_file_and_renders(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    sha = store.put(b"%PDF-1.4 bye")
    store.put_render(sha, "1_1000.png", b"png")
    store.delete(sha)
    assert not store.exists(sha) and not (store.renders_dir / sha).exists()


def test_materialised_uses_a_private_temp_dir_outside_the_data_dir_and_cleans_up(
    tmp_path: Path,
) -> None:
    store = FileStore(tmp_path / "data", vault_of("k1"))
    data = b"%PDF-1.4 needs a path"
    sha = store.put(data)
    with store.materialised(sha, suffix=".pdf") as path:
        assert path.read_bytes() == data
        assert tmp_path / "data" not in path.parents
        assert Path(tempfile.gettempdir()).resolve() in path.resolve().parents
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        kept = path.parent
    assert not kept.exists()


def test_materialised_cleans_up_when_the_caller_fails(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "data", vault_of("k1"))
    sha = store.put(b"%PDF-1.4 boom")
    kept: Path | None = None
    with pytest.raises(RuntimeError), store.materialised(sha) as path:
        kept = path.parent
        raise RuntimeError("library blew up")
    assert kept is not None and not kept.exists()


def test_materialised_gives_legacy_plaintext_in_place(tmp_path: Path) -> None:
    sha = FileStore(tmp_path).put(b"%PDF-1.4 legacy")
    store = FileStore(tmp_path, vault_of("k1"))
    with store.materialised(sha) as path:
        assert path == store.path_for(sha)
    assert store.exists(sha)  # not removed


def test_file_source_is_hashable_by_location_and_hash(tmp_path: Path) -> None:
    store = FileStore(tmp_path, vault_of("k1"))
    sha = store.put(b"%PDF-1.4 src")
    a, b = store.source(sha), FileStore(tmp_path, vault_of("k1")).source(sha)
    assert a == b and hash(a) == hash(b) and a.read() == b"%PDF-1.4 src"
    assert a != FileStore(tmp_path / "other").source(sha)
    with pytest.raises(ValueError):
        store.source("not a hash")


# ---- database bodies ---------------------------------------------------------------------


def test_case_bodies_are_encrypted_and_load_back(enc_db: Database, enc_settings: Settings) -> None:
    case = seed_case(
        enc_db,
        enc_settings,
        applicant_name=NAME,
        reference="REF-1",
        flags=[mk_flag(evidence=f"nric {NRIC} differs")],
        files=[("a.pdf", tiny_pdf(f"for {NRIC}"))],
    )
    enc_db.save_case(case, snapshot_version=True)
    with enc_db.conn() as c:
        body = c.execute("SELECT body FROM cases WHERE id=?", (case.id,)).fetchone()[0]
        vbody = c.execute("SELECT body FROM case_versions WHERE case_id=?", (case.id,)).fetchone()[
            0
        ]
        name_col = c.execute("SELECT applicant_name FROM cases").fetchone()[0]
    assert Vault.is_encrypted_text(body) and Vault.is_encrypted_text(vbody)
    assert NRIC not in body and NRIC not in vbody and body != vbody
    assert name_col == NAME  # the documented plaintext index column
    raw = raw_db_bytes(enc_db)
    assert NRIC.encode() not in raw  # nowhere in the database file or its WAL
    loaded = enc_db.load_case(case.id)
    assert loaded.flags[0].evidence == f"nric {NRIC} differs"
    assert enc_db.load_case(case.id, version=1).id == case.id


def test_a_body_moved_to_another_case_is_detected(enc_db: Database, enc_settings: Settings) -> None:
    a = seed_case(enc_db, enc_settings, applicant_name="Alpha")
    b = seed_case(enc_db, enc_settings, applicant_name="Beta")
    with enc_db.conn() as c:
        body_a = c.execute("SELECT body FROM cases WHERE id=?", (a.id,)).fetchone()[0]
        c.execute("UPDATE cases SET body=? WHERE id=?", (body_a, b.id))
    with pytest.raises(DecryptionError):
        enc_db.load_case(b.id)
    assert enc_db.load_case(a.id).id == a.id


def test_a_snapshot_moved_to_another_version_is_detected(
    enc_db: Database, enc_settings: Settings
) -> None:
    case = seed_case(enc_db, enc_settings)
    enc_db.save_case(case, snapshot_version=True)
    with enc_db.conn() as c:
        body = c.execute("SELECT body FROM case_versions").fetchone()[0]
        c.execute("INSERT INTO case_versions (case_id, version, created_at, body) VALUES (?,?,?,?)",
                  (case.id, 7, "2026-01-01T00:00:00.000000Z", body))  # fmt: skip
    with pytest.raises(DecryptionError):
        enc_db.load_case(case.id, version=7)


def test_plaintext_rows_still_load_while_the_mode_is_on(
    tmp_path: Path, settings: Settings, enc_settings: Settings
) -> None:
    plain_db = Database(enc_settings.database_path, data_dir=enc_settings.data_dir)
    plain_db.init_schema()
    old = seed_case(plain_db, settings, applicant_name="Legacy Ltd")  # stored as plaintext
    with plain_db.conn() as c:
        assert c.execute("SELECT body FROM cases").fetchone()[0].startswith("{")
    db = Database(
        enc_settings.database_path,
        data_dir=enc_settings.data_dir,
        vault=vault_from_settings(enc_settings),
    )
    assert db.load_case(old.id).applicant.name == "Legacy Ltd"
    new = seed_case(db, enc_settings, applicant_name="New Ltd")
    with db.conn() as c:
        rows = {r["id"]: r["body"] for r in c.execute("SELECT id, body FROM cases")}
    assert rows[old.id].startswith("{") and Vault.is_encrypted_text(rows[new.id])
    db.save_case(db.load_case(old.id))  # a save of the legacy case encrypts it
    with db.conn() as c:
        assert Vault.is_encrypted_text(
            c.execute("SELECT body FROM cases WHERE id=?", (old.id,)).fetchone()[0]
        )


def test_listing_and_search_still_work_on_the_index_columns(
    enc_db: Database, enc_settings: Settings
) -> None:
    from app.core.db import CaseFilters

    seed_case(enc_db, enc_settings, applicant_name="Findable Trading", reference="ZX-1")
    seed_case(enc_db, enc_settings, applicant_name="Other Co", reference="QQ-2")
    rows, total = enc_db.list_cases(CaseFilters(q="findable"))
    assert total == 1 and rows[0]["applicant_name"] == "Findable Trading"
    rows, total = enc_db.list_cases(CaseFilters(q="qq-2"))
    assert total == 1
    assert enc_db.list_cases(None, "priority")[1] == 2


def test_jobs_stages_and_errors_are_encrypted(enc_db: Database, enc_settings: Settings) -> None:
    from app.core.models import StageRecord

    case = seed_case(enc_db, enc_settings)
    job = enc_db.create_job(case.id)
    stage = StageRecord(stage="ingest", status="failed", error=f"bad page for {NRIC}")
    enc_db.update_job(job.id, state="failed", error=f"boom {NRIC}", stages=[stage])
    assert NRIC.encode() not in raw_db_bytes(enc_db)
    got = enc_db.get_job(job.id)
    assert got is not None and got.error == f"boom {NRIC}" and got.stages[0].error == stage.error


# ---- response cache ----------------------------------------------------------------------


def test_response_cache_is_encrypted_and_round_trips(enc_db: Database) -> None:
    cache = ResponseCache(enc_db)
    sha = "c" * 64
    cache.put(
        "key1",
        {"holder": NAME, "id": NRIC},
        model="fake",
        purpose="extract",
        prompt_tokens=3,
        completion_tokens=4,
        usd=Decimal("0.01"),
        file_sha256=sha,
    )
    with enc_db.conn() as c:
        row = c.execute("SELECT value, file_sha256 FROM llm_cache").fetchone()
    assert Vault.is_encrypted_text(row["value"]) and NRIC not in row["value"]
    assert row["file_sha256"] == sha
    assert NRIC.encode() not in raw_db_bytes(enc_db)
    hit = cache.get("key1")
    assert (
        hit is not None and hit.data == {"holder": NAME, "id": NRIC} and hit.usd == Decimal("0.01")
    )


def test_response_cache_row_moved_to_another_key_is_detected(enc_db: Database) -> None:
    cache = ResponseCache(enc_db)
    for key in ("k-a", "k-b"):
        cache.put(key, {"v": key}, model="m", purpose="p", prompt_tokens=0, completion_tokens=0,
                  usd=Decimal(0), file_sha256=None)  # fmt: skip
    with enc_db.conn() as c:
        va = c.execute("SELECT value FROM llm_cache WHERE key='k-a'").fetchone()[0]
        c.execute("UPDATE llm_cache SET value=? WHERE key='k-b'", (va,))
    with pytest.raises(DecryptionError):
        cache.get("k-b")


def test_response_cache_reads_legacy_plaintext_rows(enc_db: Database) -> None:
    with enc_db.conn() as c:
        c.execute(
            "INSERT INTO llm_cache (key, value, created_at, model, purpose) VALUES (?,?,?,?,?)",
            (
                "old",
                json.dumps({"v": 1, "data": {"a": 1}}),
                "2026-10-27T00:00:00.000000Z",
                "m",
                "p",
            ),
        )
    hit = ResponseCache(enc_db).get("old")
    assert hit is not None and hit.data == {"a": 1}


# ---- schema migration of an existing (pre-encryption) database --------------------------


OLD_SCHEMA = """
CREATE TABLE cases (id TEXT PRIMARY KEY, use_case TEXT NOT NULL, status TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, reference TEXT, applicant_name TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, score INTEGER, band TEXT, lane TEXT, coverage_ratio REAL,
  partial INTEGER NOT NULL DEFAULT 0, findings_count INTEGER NOT NULL DEFAULT 0,
  high_count INTEGER NOT NULL DEFAULT 0, decision TEXT, top_finding TEXT, body TEXT NOT NULL);
CREATE TABLE case_versions (case_id TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL,
  body TEXT NOT NULL, PRIMARY KEY (case_id, version));
CREATE TABLE review_entries (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, case_version INTEGER NOT NULL,
  created_at TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE llm_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL,
  model TEXT NOT NULL, purpose TEXT NOT NULL);
"""


def test_an_old_database_gains_the_helper_columns_and_they_are_backfilled(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    sha = "d" * 64
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    body = json.dumps({"files": [{"sha256": sha}], "escalated_at": "2026-01-01T00:00:00Z"})
    con.execute(
        "INSERT INTO cases (id, use_case, status, created_at, updated_at, body) VALUES "
        "('c_1','loan','closed','2026-01-01','2026-01-01',?)", (body,))  # fmt: skip
    con.execute(
        "INSERT INTO review_entries (id, case_id, case_version, created_at, body) VALUES "
        "('c_1/r1','c_1',1,'2026-01-01',?)", (json.dumps({"status": "pending_second_approval"}),))  # fmt: skip
    con.execute(
        "INSERT INTO llm_cache VALUES ('k','"
        + json.dumps({"file_sha256": sha}).replace("'", "''")
        + "','2026-01-01','m','p')"
    )
    con.commit()
    con.close()
    db = Database(path, data_dir=tmp_path)
    db.init_schema()
    db.init_schema()  # idempotent
    with db.conn() as c:
        assert c.execute("SELECT escalated FROM cases").fetchone()[0] == 1
        assert (
            c.execute("SELECT status FROM review_entries").fetchone()[0]
            == "pending_second_approval"
        )
        assert c.execute("SELECT file_sha256 FROM llm_cache").fetchone()[0] == sha
        assert c.execute("SELECT sha256 FROM case_files WHERE case_id='c_1'").fetchone()[0] == sha


def test_reindex_rebuilds_helper_columns_from_encrypted_rows(
    enc_db: Database, enc_settings: Settings
) -> None:
    case = seed_case(enc_db, enc_settings, files=[("a.pdf", tiny_pdf("x"))], escalated_at=FIXED_NOW)
    with enc_db.conn() as c:
        c.execute("UPDATE cases SET escalated = NULL")
        c.execute("DELETE FROM case_files")
    counts = enc_db.reindex()
    assert counts["cases"] == 1 and counts["case_files"] == 1
    with enc_db.conn() as c:
        assert c.execute("SELECT escalated FROM cases").fetchone()[0] == 1
        assert c.execute("SELECT sha256 FROM case_files").fetchone()[0] == case.files[0].sha256


# ---- delete, purge, review on an encrypted store -----------------------------------------


@pytest.fixture
def env(enc_settings: Settings, enc_db: Database) -> Env:
    return make_env(enc_settings, enc_db, FakeClock())


def test_delete_case_removes_encrypted_files_renders_and_cache(env: Env) -> None:
    pdf = tiny_pdf(f"Statement for {NAME}")
    case = seed_case(env.db, env.settings, applicant_name=NAME, files=[("s.pdf", pdf)])
    sha = case.files[0].sha256
    env.db.files.put_render(sha, "1_1000.png", b"png")
    ResponseCache(env.db).put("kk", {"x": 1}, model="m", purpose="p", prompt_tokens=0,
                              completion_tokens=0, usd=Decimal(0), file_sha256=sha)  # fmt: skip
    ResponseCache(env.db).put("other", {"x": 1}, model="m", purpose="p", prompt_tokens=0,
                              completion_tokens=0, usd=Decimal(0), file_sha256="f" * 64)  # fmt: skip
    assert env.db.files.exists(sha)
    assert (
        env.client.delete(
            f"/cases/{case.id}",
            params={"reviewer": "a.tan", "reason": "duplicate upload, not needed"},
        ).status_code
        == 204
    )
    assert not env.db.files.exists(sha) and not (env.db.files.renders_dir / sha).exists()
    with env.db.conn() as c:
        assert [r["key"] for r in c.execute("SELECT key FROM llm_cache")] == ["other"]
        assert c.execute("SELECT COUNT(*) FROM case_files").fetchone()[0] == 0
    with pytest.raises(CaseNotFound):
        env.db.load_case(case.id)


def test_a_file_shared_with_another_case_survives_delete(env: Env) -> None:
    pdf = tiny_pdf("shared")
    a = seed_case(env.db, env.settings, files=[("s.pdf", pdf)])
    b = seed_case(env.db, env.settings, files=[("s.pdf", pdf)])
    sha = a.files[0].sha256
    assert sha == b.files[0].sha256
    env.db.delete_case(a.id)
    assert env.db.files.exists(sha) and env.db.files.read(sha) == pdf
    result = env.db.delete_case(b.id)
    assert result.files_removed == [sha] and not env.db.files.exists(sha)


def test_a_file_in_an_older_version_only_is_still_found_on_delete(env: Env) -> None:
    case = seed_case(env.db, env.settings, files=[("s.pdf", tiny_pdf("v1"))])
    sha = case.files[0].sha256
    env.db.save_case(case, snapshot_version=True)
    case.files = []  # the live version no longer lists it
    env.db.save_case(case)
    assert env.db.delete_case(case.id).files_removed == [sha]


def test_purge_honours_escalation_and_age_with_encrypted_bodies(env: Env) -> None:
    old = FIXED_NOW - timedelta(days=2000)
    keep = seed_case(env.db, env.settings, status="closed", updated_at=old, created_at=old,
                     escalated_at=old, decision="escalate_aml")  # fmt: skip
    gone = seed_case(env.db, env.settings, status="closed", updated_at=old, created_at=old,
                     files=[("s.pdf", tiny_pdf("x"))])  # fmt: skip
    recent = seed_case(env.db, env.settings, status="closed")
    ids = expired_case_ids(env.db, older_than_days=1825, now=FIXED_NOW)
    assert ids == [gone.id]
    result = purge(
        env.db, AuditLog(env.db, env.settings), older_than_days=1825, dry_run=False, now=FIXED_NOW
    )
    assert result["case_ids"] == [gone.id]
    assert env.db.case_exists(keep.id) and env.db.case_exists(recent.id)
    assert not env.db.files.exists(gone.files[0].sha256)


def test_retention_period_remains_a_setting() -> None:
    assert Settings(_env_file=None, retention_days=2555).retention_days == 2555
    assert (
        Settings(_env_file=None).retention_days == 1825
    )  # the default is a bank decision, not confirmed


def test_review_flow_with_encrypted_entries(env: Env) -> None:
    flag = mk_flag("x_directors", "high", doc="d9", locator="-", disc="t")
    case = seed_case(env.db, env.settings, flags=[flag], files=[("s.pdf", tiny_pdf("r"))])
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[flag.id],
                                         reason=f"Seen on {NRIC}'s card"))  # fmt: skip
    assert r.status_code in (200, 201), r.text
    assert NRIC.encode() not in raw_db_bytes(env.db)
    with env.db.conn() as c:
        rows = c.execute("SELECT id, body, status FROM review_entries").fetchall()
    assert rows and all(Vault.is_encrypted_text(x["body"]) for x in rows)
    assert all(x["status"] for x in rows)
    loaded = env.db.load_case(case.id)
    assert loaded.review and NRIC in loaded.review[0].reason
    pending = env.client.get("/cases").json()
    assert pending["total"] == 1


def test_second_approval_pending_counts_use_the_plain_status_column(env: Env) -> None:
    flag = mk_flag("x_directors", "high", doc="d9", locator="-", disc="t")
    case = seed_case(env.db, env.settings, flags=[flag])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[flag.id]))
    row = env.client.get("/cases").json()["items"][0]
    assert row["pending_second_approvals"] == 1
    second = env.client.post(
        f"/cases/{case.id}/review",
        json={
            **review_body("second_approval", "approve", target_entry_id="r1"),
            "reviewer": "b.lim",
        },
    )
    assert second.status_code in (200, 201), second.text
    assert env.client.get("/cases").json()["items"][0]["pending_second_approvals"] == 0


def test_queue_page_image_and_download_work_with_encrypted_files(env: Env) -> None:
    pdf = tiny_pdf("page one")
    case = seed_case(env.db, env.settings, files=[("s.pdf", pdf)])
    r = env.client.get(f"/cases/{case.id}/files/f1")
    assert r.status_code == 200 and r.content == pdf
    on_disk = env.db.files.path_for(case.files[0].sha256).read_bytes()
    assert on_disk != pdf
    page = env.client.get(f"/cases/{case.id}/pages/1")
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
    cached = env.db.files.renders_dir / case.files[0].sha256 / "1_1000.png"
    assert cached.is_file() and not cached.read_bytes().startswith(
        b"\x89PNG"
    )  # the cache is encrypted
    again = env.client.get(f"/cases/{case.id}/pages/1")
    assert again.content == page.content  # served from the encrypted cache
    pack = env.client.get(f"/cases/{case.id}/pack")
    assert pack.status_code == 200


def test_database_without_a_vault_adopts_the_one_the_settings_ask_for(
    enc_settings: Settings, tmp_path: Path
) -> None:
    db = Database(enc_settings.database_path, data_dir=enc_settings.data_dir)  # built carelessly
    assert not db.vault.enabled
    import app.main as main

    main.create_app(enc_settings, db=db)
    assert db.vault.enabled and db.files.vault is db.vault
    case = seed_case(db, enc_settings, files=[("a.pdf", tiny_pdf("z"))])
    assert Vault.is_encrypted(db.files.path_for(case.files[0].sha256).read_bytes())


# ---- the other writers: register cache, UI render helper, and a guard on path use ---------


def test_register_cache_is_encrypted_and_a_wrong_key_is_a_miss(tmp_path: Path) -> None:
    from app.registry_live import AcraLookup

    v = vault_of("k1")
    cache = AcraLookup(tmp_path / "rc", vault=v)
    cache._store("201912345R", {"entity_name": NAME}, 100.0)
    raw = (tmp_path / "rc" / "acra-201912345R.json").read_bytes()
    assert Vault.is_encrypted(raw) and NAME.encode() not in raw
    hit, entity = cache._cached("201912345R", 101.0)
    assert hit is True
    other = AcraLookup(
        tmp_path / "rc", vault=Vault(keyring_from_sources(f"x:{k(8)}"), enabled=True)
    )
    assert other._cached("201912345R", 101.0) == (
        False,
        None,
    )  # undecryptable = ask the register again
    legacy = tmp_path / "rc" / "acra-200000001A.json"
    legacy.write_text(json.dumps({"at": 100.0, "record": None}))
    assert AcraLookup(tmp_path / "rc", vault=v)._cached("200000001A", 101.0) == (True, None)


def test_ui_render_helper_seals_its_cache_file_when_given_a_vault(tmp_path: Path) -> None:
    from app.ingest.render import render_page_png

    pdf = tiny_pdf("render me")
    sha = sha256_bytes(pdf)
    v = vault_of("k1")
    out = render_page_png(
        pdf,
        sha,
        "application/pdf",
        1,
        width_px=300,
        renders_dir=tmp_path / "r",
        render_dpi=72,
        vault=v,
    )
    on_disk = out.read_bytes()
    assert Vault.is_encrypted(on_disk) and not on_disk.startswith(b"\x89PNG")
    store = FileStore(tmp_path, v)
    assert store.vault.open(on_disk, f"render:{sha}:{out.name}").startswith(b"\x89PNG")


def test_no_production_code_opens_a_stored_file_by_path() -> None:
    """Guard for the audit in docs/encryption-at-rest.md section 2: stored files are read through
    FileStore/ctx.file_bytes (decrypting), never by opening ``path_for``/``ctx.file_path`` directly."""
    import re as _re

    app = Path(__file__).resolve().parents[1] / "app"
    allowed = {
        "core/storage.py",  # the store itself
        "core/context.py",  # defines file_path (a location only)
        "classify/stage.py",  # exists() check only
    }
    pattern = _re.compile(r"\b(path_for|file_path)\(")
    offenders = []
    for path in app.rglob("*.py"):
        rel = path.relative_to(app).as_posix()
        if rel in allowed:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{n}")
    assert offenders == []


def test_top_finding_can_quote_a_document_so_it_is_encrypted_but_still_shown(env: Env) -> None:
    flag = mk_flag(
        "x_directors",
        "high",
        doc="d1",
        locator="-",
        explanation=f"{NAME} director {NRIC} is not listed.",
    )
    case = seed_case(env.db, env.settings, flags=[flag], group=True)
    assert NRIC in (case.top_finding or "")
    with env.db.conn() as c:
        stored = c.execute("SELECT top_finding FROM cases").fetchone()[0]
    assert Vault.is_encrypted_text(stored) and NRIC not in stored
    assert NRIC.encode() not in raw_db_bytes(env.db)
    item = env.client.get("/cases").json()["items"][0]
    assert NRIC in item["top_finding"]


def test_deleted_rows_are_zeroed_not_left_in_free_pages(settings: Settings) -> None:
    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    marker = "Quixotic Marmalade Exports"
    case = seed_case(db, settings, applicant_name=marker, reference="UNIQUE-REF-9141")
    with db.conn() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert marker.encode() in Path(db.path).read_bytes()
    db.delete_case(case.id)
    with db.conn() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = Path(db.path).read_bytes()
    assert marker.encode() not in raw and b"UNIQUE-REF-9141" not in raw


def test_create_app_refuses_to_start_with_encryption_on_and_no_key(tmp_path: Path) -> None:
    import app.main as main
    from app.core.config import SettingsError

    s = Settings(_env_file=None, data_dir=tmp_path / "d", llm_backend="fake", encrypt_at_rest="on")
    with pytest.raises(SettingsError):
        main.create_app(s)
    assert not (
        tmp_path / "d" / "gldb.sqlite3"
    ).exists()  # nothing was written, in particular no plaintext
