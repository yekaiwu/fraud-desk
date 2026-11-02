"""GET /monitoring reports the application-level encryption status (docs/encryption-at-rest.md section 6)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.crypto import Vault, keyring_from_sources, vault_from_settings
from app.core.db import Database
from app.monitoring import encryption_status
from tests.test_api_support import Env, make_env, seed_case, tiny_pdf


def k(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


def settings_with(tmp_path: Path, keys: str, mode: str = "on") -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        encrypt_at_rest=mode,  # type: ignore[arg-type]
        encryption_keys=keys,
    )


def build(tmp_path: Path, keys: str = f"k1:{k(1)}", mode: str = "on") -> tuple[Settings, Database]:
    s = settings_with(tmp_path, keys, mode)
    db = Database(s.database_path, data_dir=s.data_dir, vault=vault_from_settings(s))
    db.init_schema()
    return s, db


def test_off_says_so_plainly(settings: Settings, db: Database) -> None:
    seed_case(db, settings, files=[("a.pdf", tiny_pdf("x"))])
    st = encryption_status(db, settings)
    assert st["mode"] == "off" and st["status"] == "off" and st["active_key_id"] is None
    assert "OFF" in st["message"] and "volume" in st["message"]
    assert st["categories"]["cases"]["plaintext"] == 1
    assert st["all_sampled_encrypted"] is False
    assert any("applicant_name" in x for x in st["plaintext_by_design"])


def test_on_and_everything_encrypted(tmp_path: Path) -> None:
    s, db = build(tmp_path)
    seed_case(db, s, files=[("a.pdf", tiny_pdf("x"))])
    db.files.put_render(
        db.load_case(db.list_cases()[0][0]["id"]).files[0].sha256, "1_1000.png", b"p"
    )
    st = encryption_status(db, s)
    assert st["mode"] == "on" and st["status"] == "on" and st["active_key_id"] == "k1"
    assert st["all_sampled_encrypted"] is True and st["plaintext_items"] == 0
    assert st["categories"]["files"]["encrypted"] == 1
    assert st["categories"]["renders"]["encrypted"] == 1
    assert st["categories"]["cases"]["encrypted"] == 1
    assert "ON" in st["message"] and "k1" in st["message"]
    assert "k1:" not in str(st) and k(1) not in str(st)  # ids only, never key material


def test_on_with_legacy_plaintext_is_called_out(tmp_path: Path) -> None:
    s, db = build(tmp_path)
    seed_case(db, s, files=[("a.pdf", tiny_pdf("x"))])
    plain = Database(s.database_path, data_dir=s.data_dir)  # an older plaintext write
    seed_case(
        plain, s.model_copy(update={"encrypt_at_rest": "off"}), files=[("b.pdf", tiny_pdf("y"))]
    )
    st = encryption_status(db, s)
    assert st["status"] == "on_with_plaintext" and st["all_sampled_encrypted"] is False
    assert st["plaintext_items"] >= 2 and "encrypt_data.py" in st["message"]


def test_old_key_and_missing_key_are_reported(tmp_path: Path) -> None:
    s, db = build(tmp_path)
    seed_case(db, s, files=[("a.pdf", tiny_pdf("x"))])
    rotated = Vault(keyring_from_sources(f"k2:{k(2)},k1:{k(1)}"), enabled=True)
    db.use_vault(rotated)
    st = encryption_status(db, s)
    assert st["status"] == "on" and st["active_key_id"] == "k2"
    assert st["categories"]["cases"]["under_old_key"] == 1 and "--rotate" in st["message"]
    db.use_vault(Vault(keyring_from_sources(f"k3:{k(3)}"), enabled=True))
    st = encryption_status(db, s)
    assert st["status"] == "key_missing" and "not loaded" in st["message"]


def test_the_sample_is_bounded(tmp_path: Path) -> None:
    s, db = build(tmp_path)
    for _ in range(12):
        seed_case(db, s)
    st = encryption_status(db, s, sample=6)
    assert st["categories"]["cases"]["sampled"] <= 6 and st["categories"]["cases"]["total"] == 12


def test_a_failure_while_sampling_never_breaks_monitoring(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.monitoring._walk_headers", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    st = encryption_status(db, settings)
    assert st["status"] == "error" and st["all_sampled_encrypted"] is None


def test_the_endpoint_carries_it_next_to_disk_encryption(tmp_path: Path) -> None:
    s, db = build(tmp_path)
    seed_case(db, s)
    env: Env = make_env(s, db)
    body = env.client.get("/monitoring").json()
    assert "disk_encryption" in body
    enc = body["encryption_at_rest"]
    assert (
        enc["mode"] == "on"
        and enc["active_key_id"] == "k1"
        and enc["all_sampled_encrypted"] is True
    )
