"""Application-level encryption: the envelope, the keyring and the key file (docs/encryption-at-rest.md)."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import pytest

from app.core.config import Settings, SettingsError
from app.core.crypto import (
    HEADER_PREFIX,
    MAGIC,
    NONCE_BYTES,
    TEXT_PREFIX,
    CryptoConfigError,
    DecryptionError,
    KeyProvider,
    Keyring,
    Vault,
    ctx_case,
    ctx_file,
    keyring_from_sources,
    plaintext_vault,
    vault_from_settings,
)


def key(n: int) -> str:
    """A deterministic TEST key (32 bytes, base64). Not a secret."""
    return base64.b64encode(bytes([n]) * 32).decode()


def ring(*ids: str) -> Keyring:
    r = keyring_from_sources(",".join(f"{i}:{key(n)}" for n, i in enumerate(ids, start=1)))
    assert r is not None
    return r


def vault(*ids: str, enabled: bool = True) -> Vault:
    return Vault(ring(*ids or ("k1",)), enabled=enabled)


DATA = b"the quick brown fox \x00\xff" * 50
CTX = "file:" + "a" * 64


# ---- round trip -------------------------------------------------------------------------


def test_round_trip_and_layout() -> None:
    v = vault("k1")
    blob = v.encrypt(DATA, CTX)
    assert blob.startswith(HEADER_PREFIX) and blob.startswith(MAGIC)
    assert DATA[:20] not in blob
    # magic(7) version(1) idlen(1) id(2) nonce(12) ciphertext + 16-byte tag
    assert len(blob) == 8 + 1 + 2 + NONCE_BYTES + len(DATA) + 16
    assert Vault.key_id_of(blob) == "k1"
    assert v.decrypt(blob, CTX) == DATA
    assert v.open(blob, CTX) == DATA


def test_empty_and_large_payloads() -> None:
    v = vault()
    assert v.decrypt(v.encrypt(b"", CTX), CTX) == b""
    big = os.urandom(3_000_000)
    assert v.decrypt(v.encrypt(big, CTX), CTX) == big


def test_nonce_is_fresh_every_time() -> None:
    v = vault()
    blobs = {v.encrypt(DATA, CTX) for _ in range(50)}
    assert len(blobs) == 50
    nonces = {b[8 + 1 + 2 : 8 + 1 + 2 + NONCE_BYTES] for b in blobs}
    assert len(nonces) == 50


def test_text_form_round_trip() -> None:
    v = vault()
    text = v.seal_text('{"name": "Tan", "n": 1}', ctx_case("c_1"))
    assert text.startswith(TEXT_PREFIX) and "Tan" not in text
    assert v.is_encrypted_text(text) and not v.is_encrypted_text('{"a": 1}')
    assert v.key_id_of_text(text) == "k1"
    assert v.open_text(text, ctx_case("c_1")) == '{"name": "Tan", "n": 1}'


def test_mode_off_is_a_pass_through_for_new_writes() -> None:
    v = vault(enabled=False)
    assert v.seal(DATA, CTX) == DATA
    assert v.seal_text("x", CTX) == "x"
    assert plaintext_vault().seal(DATA, CTX) == DATA


def test_legacy_plaintext_reads_in_either_mode() -> None:
    for v in (vault(enabled=True), vault(enabled=False), plaintext_vault()):
        assert v.open(b"%PDF-1.7 legacy", CTX) == b"%PDF-1.7 legacy"
        assert v.open_text('{"legacy": true}', CTX) == '{"legacy": true}'


def test_on_without_a_provider_is_refused() -> None:
    with pytest.raises(CryptoConfigError):
        Vault(None, enabled=True)


def test_encrypted_data_without_any_key_fails_loudly_never_as_plaintext() -> None:
    blob = vault().encrypt(DATA, CTX)
    with pytest.raises(DecryptionError):
        plaintext_vault().open(blob, CTX)
    with pytest.raises(DecryptionError):
        plaintext_vault().open_text(vault().seal_text("x", CTX), CTX)


# ---- wrong key, tamper, context ---------------------------------------------------------


def test_wrong_key_is_detected() -> None:
    blob = vault("k1").encrypt(DATA, CTX)
    other = Vault(
        keyring_from_sources(f"k1:{key(99)}"),  # same id, different key bytes
        enabled=True,
    )
    with pytest.raises(DecryptionError):
        other.decrypt(blob, CTX)


def test_unknown_key_id_is_detected() -> None:
    blob = vault("k1").encrypt(DATA, CTX)
    with pytest.raises(DecryptionError, match="not loaded"):
        vault("zz").decrypt(blob, CTX)


@pytest.mark.parametrize("where", ["version", "idlen", "id", "nonce", "body", "tag"])
def test_every_byte_region_is_tamper_evident(where: str) -> None:
    v = vault("k1")
    blob = bytearray(v.encrypt(DATA, CTX))
    index = {
        "version": 7,
        "idlen": 8,
        "id": 9,
        "nonce": 11 + 3,
        "body": 11 + NONCE_BYTES + 10,
        "tag": len(blob) - 1,
    }[where]
    blob[index] ^= 0x01
    with pytest.raises(DecryptionError):
        v.open(bytes(blob), CTX)


def test_damaged_magic_is_read_as_legacy_plaintext_not_silently_decrypted() -> None:
    # Documented limit: only the magic decides "is this encrypted". A blob whose magic was damaged is
    # returned as plaintext bytes (garbage), never as a successful decryption.
    v = vault()
    blob = bytearray(v.encrypt(DATA, CTX))
    blob[0] ^= 0xFF
    assert v.open(bytes(blob), CTX) == bytes(blob)
    assert DATA not in bytes(blob)


def test_truncation_is_detected() -> None:
    v = vault()
    blob = v.encrypt(DATA, CTX)
    for cut in (len(blob) - 1, 40, 25, 10, 9, 8, 7):
        with pytest.raises(DecryptionError):
            v.decrypt(blob[:cut], CTX)


def test_appended_bytes_are_detected() -> None:
    v = vault()
    with pytest.raises(DecryptionError):
        v.decrypt(v.encrypt(DATA, CTX) + b"x", CTX)


def test_unsupported_version_is_refused() -> None:
    v = vault()
    blob = bytearray(v.encrypt(DATA, CTX))
    blob[7] = 2
    with pytest.raises(DecryptionError, match="version"):
        v.decrypt(bytes(blob), CTX)


def test_context_swap_is_detected() -> None:
    v = vault()
    blob = v.encrypt(DATA, ctx_file("a" * 64))
    with pytest.raises(DecryptionError):
        v.decrypt(blob, ctx_file("b" * 64))
    with pytest.raises(DecryptionError):
        v.decrypt(blob, ctx_case("a" * 64))
    text = v.seal_text("hello", ctx_case("c_1"))
    with pytest.raises(DecryptionError):
        v.open_text(text, ctx_case("c_2"))


def test_text_with_bad_base64_is_refused() -> None:
    with pytest.raises(DecryptionError):
        vault().open_text(TEXT_PREFIX + "!!!not base64!!!", CTX)


def test_error_messages_never_carry_key_or_data() -> None:
    v = vault()
    blob = v.encrypt(b"SECRET-PAYLOAD-MARKER", CTX)
    with pytest.raises(DecryptionError) as exc:
        v.decrypt(blob, "other")
    text = str(exc.value)
    assert "SECRET-PAYLOAD-MARKER" not in text and key(1) not in text
    assert key(1) not in repr(ring("k1")) and "ids=" in repr(ring("k1"))


# ---- rotation ---------------------------------------------------------------------------


def test_rotation_old_key_still_decrypts_and_new_writes_use_the_active_key() -> None:
    old = vault("k1")
    blob_old = old.encrypt(DATA, CTX)
    rotated = Vault(
        keyring_from_sources(f"k2:{key(2)},k1:{key(1)}"),
        enabled=True,
    )
    assert rotated.active_key_id == "k2" and rotated.key_ids() == ["k2", "k1"]
    assert rotated.decrypt(blob_old, CTX) == DATA  # the old key still reads
    blob_new = rotated.seal(DATA, CTX)
    assert Vault.key_id_of(blob_new) == "k2"
    assert Vault.key_id_of(blob_old) == "k1"
    # without the old key the old data is unreadable, with a clear error
    only_new = Vault(keyring_from_sources(f"k2:{key(2)}"), enabled=True)
    with pytest.raises(DecryptionError, match="not loaded"):
        only_new.decrypt(blob_old, CTX)
    assert only_new.decrypt(blob_new, CTX) == DATA


# ---- key parsing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "k1",  # no colon
        "k1:",  # empty key
        f"k1:{base64.b64encode(b'short').decode()}",  # wrong length
        f"k1:{base64.b64encode(bytes(33)).decode()}",  # wrong length
        "k1:not-base64-!!!",
        f"bad id:{key(1)}",
        f":{key(1)}",
        f"{'x' * 33}:{key(1)}",
    ],
)
def test_bad_key_entries_are_refused_without_echoing_the_key(text: str) -> None:
    with pytest.raises(CryptoConfigError) as exc:
        keyring_from_sources(text)
    assert key(1) not in str(exc.value)


def test_duplicate_ids_with_different_keys_are_refused_same_key_is_ignored() -> None:
    with pytest.raises(CryptoConfigError, match="twice"):
        keyring_from_sources(f"k1:{key(1)},k1:{key(2)}")
    r = keyring_from_sources(f"k1:{key(1)},k1:{key(1)}")
    assert r is not None and r.key_ids() == ["k1"]


def test_no_keys_means_none_and_a_blank_setting_is_not_a_key() -> None:
    assert keyring_from_sources("", None) is None
    assert keyring_from_sources("  ", "") is None
    with pytest.raises(CryptoConfigError):
        keyring_from_sources(" , ")


def test_urlsafe_base64_is_accepted() -> None:
    raw = bytes(range(32))
    url = base64.urlsafe_b64encode(raw).decode()
    r = keyring_from_sources(f"u:{url}")
    assert r is not None and r.key("u") == raw


# ---- key file ---------------------------------------------------------------------------


def write_key_file(path: Path, body: str, mode: int = 0o600) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(mode)
    return path


def test_key_file_with_comments_and_active_first(tmp_path: Path) -> None:
    f = write_key_file(tmp_path / "keys", f"# current\nnew:{key(2)}\n\nold:{key(1)}  # retired\n")
    r = keyring_from_sources("", f)
    assert r is not None and r.key_ids() == ["new", "old"] and r.active_key_id() == "new"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o700 | 0o070])
def test_key_file_readable_by_group_or_world_is_refused(tmp_path: Path, mode: int) -> None:
    f = write_key_file(tmp_path / "keys", f"k1:{key(1)}\n", mode)
    with pytest.raises(CryptoConfigError, match="group or others") as exc:
        keyring_from_sources("", f)
    assert key(1) not in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_key_file_mode_600_and_400_are_accepted(tmp_path: Path) -> None:
    for mode in (0o600, 0o400):
        f = write_key_file(tmp_path / f"k{mode}", f"k1:{key(1)}\n", mode)
        assert keyring_from_sources("", f) is not None
        assert stat.S_IMODE(f.stat().st_mode) == mode


def test_key_file_missing_or_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CryptoConfigError):
        keyring_from_sources("", tmp_path / "nope")
    with pytest.raises(CryptoConfigError):
        keyring_from_sources("", tmp_path)


def test_env_keys_come_before_file_keys_and_both_decrypt(tmp_path: Path) -> None:
    f = write_key_file(tmp_path / "keys", f"filekey:{key(2)}\n")
    r = keyring_from_sources(f"envkey:{key(1)}", f)
    assert r is not None and r.key_ids() == ["envkey", "filekey"]


def test_an_empty_key_file_is_refused(tmp_path: Path) -> None:
    f = write_key_file(tmp_path / "keys", "# nothing\n")
    with pytest.raises(CryptoConfigError):
        keyring_from_sources("", f)


# ---- settings, startup -------------------------------------------------------------------


def settings_for(tmp_path: Path, **kw: object) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", llm_backend="fake", **kw)  # type: ignore[arg-type]


def test_default_is_off_and_startup_passes_without_keys(tmp_path: Path) -> None:
    s = settings_for(tmp_path)
    assert s.encrypt_at_rest == "off"
    s.validate_startup()
    assert vault_from_settings(s).enabled is False


def test_on_without_a_key_fails_startup_loudly(tmp_path: Path) -> None:
    s = settings_for(tmp_path, encrypt_at_rest="on")
    with pytest.raises(SettingsError, match="GLDB_ENCRYPTION_KEYS"):
        s.validate_startup()


def test_on_with_a_bad_key_fails_startup(tmp_path: Path) -> None:
    s = settings_for(tmp_path, encrypt_at_rest="on", encryption_keys="k1:AAAA")
    with pytest.raises(SettingsError, match="encryption at rest"):
        s.validate_startup()


def test_on_with_a_good_key_starts_and_writes_encrypted(tmp_path: Path) -> None:
    s = settings_for(tmp_path, encrypt_at_rest="on", encryption_keys=f"k1:{key(1)}")
    s.validate_startup()
    v = vault_from_settings(s)
    assert v.enabled and v.active_key_id == "k1"
    assert Vault.is_encrypted(v.seal(b"x", CTX))


def test_the_key_never_shows_in_settings_repr(tmp_path: Path) -> None:
    s = settings_for(tmp_path, encrypt_at_rest="on", encryption_keys=f"k1:{key(1)}")
    assert key(1) not in repr(s) and key(1) not in str(s.model_dump())


def test_key_file_inside_the_data_dir_is_refused(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    f = write_key_file(data / "keys", f"k1:{key(1)}\n")
    s = settings_for(tmp_path, encrypt_at_rest="on", encryption_key_file=str(f))
    with pytest.raises(SettingsError, match="outside"):
        s.validate_startup()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_group_readable_key_file_fails_startup(tmp_path: Path) -> None:
    f = write_key_file(tmp_path / "keys", f"k1:{key(1)}\n", 0o640)
    s = settings_for(tmp_path, encrypt_at_rest="on", encryption_key_file=str(f))
    with pytest.raises(SettingsError, match="group or others"):
        s.validate_startup()


def test_key_file_start_up_and_edit_is_picked_up(tmp_path: Path) -> None:
    f = write_key_file(tmp_path / "keys", f"k1:{key(1)}\n")
    s = settings_for(tmp_path, encrypt_at_rest="on", encryption_key_file=str(f))
    assert vault_from_settings(s).active_key_id == "k1"
    write_key_file(f, f"k2:{key(2)}\nk1:{key(1)}\n")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 5_000_000_000))
    assert vault_from_settings(s).active_key_id == "k2"


def test_off_with_keys_still_reads_encrypted_data(tmp_path: Path) -> None:
    on = settings_for(tmp_path, encrypt_at_rest="on", encryption_keys=f"k1:{key(1)}")
    off = settings_for(tmp_path, encrypt_at_rest="off", encryption_keys=f"k1:{key(1)}")
    blob = vault_from_settings(on).seal(DATA, CTX)
    v = vault_from_settings(off)
    assert not v.enabled and v.open(blob, CTX) == DATA
    assert v.seal(DATA, CTX) == DATA  # but new writes are plaintext: that is what "off" means


# ---- pluggable provider ------------------------------------------------------------------


class FakeKms:
    """What a bank KMS adapter has to provide (see KeyProvider)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def active_key_id(self) -> str:
        return "kms-2026"

    def key_ids(self) -> list[str]:
        return ["kms-2026"]

    def key(self, key_id: str) -> bytes | None:
        self.calls.append(key_id)
        return bytes([7]) * 32 if key_id == "kms-2026" else None


def test_a_custom_key_provider_plugs_in() -> None:
    kms = FakeKms()
    assert isinstance(kms, KeyProvider)
    v = Vault(kms, enabled=True)
    blob = v.seal(DATA, CTX)
    assert Vault.key_id_of(blob) == "kms-2026" and v.open(blob, CTX) == DATA
    assert kms.calls  # the vault asked the provider for the key
