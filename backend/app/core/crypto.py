"""Application-level encryption at rest (docs/encryption-at-rest.md).

One envelope for everything the application stores: AES-256-GCM with a random 96-bit nonce and
authenticated associated data (AAD) that binds the blob to the record it belongs to, so a ciphertext
cannot be moved to another record (another file, case, cache row ...) without the tag failing.

Blob layout (bytes)::

    "GLDBENC" | version (1 byte) | key id length (1 byte) | key id (utf-8) | nonce (12) | ciphertext+tag

AAD = ``"gldb-aad-v1" 0x00 <key id> 0x00 <context>``. A text form (for SQLite TEXT columns) is
``"gldbenc1:" + base64(blob)``. Anything that does not start with the magic is *legacy plaintext*:
it is returned unchanged by :meth:`Vault.open`, so a deployment can migrate gradually.

Keys come from a :class:`KeyProvider`. Only the environment/file provider is implemented
(``GLDB_ENCRYPTION_KEYS`` and ``GLDB_ENCRYPTION_KEY_FILE``); a bank KMS or HSM implements the same
three methods (see :class:`KeyProvider`) and is handed to :class:`Vault`. The first key is the
active one for new writes; every listed key can decrypt (rotation).

Nothing here ever logs or formats key bytes, plaintext or ciphertext into a message.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from app.core.config import Settings

MAGIC = b"GLDBENC"
VERSION = 1
HEADER_PREFIX = MAGIC + bytes([VERSION])
TEXT_PREFIX = "gldbenc1:"
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_KEY_ID = 32
_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_AAD_TAG = b"gldb-aad-v1\x00"

__all__ = [
    "CryptoConfigError",
    "CryptoError",
    "DecryptionError",
    "KeyProvider",
    "Keyring",
    "Vault",
    "ctx_case",
    "ctx_case_version",
    "ctx_file",
    "ctx_llm_cache",
    "ctx_registry",
    "ctx_render",
    "ctx_review",
    "ctx_top_finding",
    "keyring_from_sources",
    "plaintext_vault",
    "vault_from_settings",
]


class CryptoError(Exception):
    """Base class. Messages never contain key material or data."""


class CryptoConfigError(CryptoError):
    """The key configuration is missing or unusable (start-up failure)."""


class DecryptionError(CryptoError):
    """Wrong key, unknown key id, tampered data or a ciphertext moved to another record."""


# ------------------------------------------------------------------------------------------
# contexts: the "which record is this" string that goes into the AAD
# ------------------------------------------------------------------------------------------


def ctx_file(sha256: str) -> str:
    return f"file:{sha256}"


def ctx_render(sha256: str, name: str) -> str:
    return f"render:{sha256}:{name}"


def ctx_case(case_id: str) -> str:
    return f"case:{case_id}"


def ctx_case_version(case_id: str, version: int) -> str:
    return f"case_version:{case_id}:{version}"


def ctx_top_finding(case_id: str) -> str:
    return f"case_top:{case_id}"


def ctx_review(entry_row_id: str) -> str:
    return f"review:{entry_row_id}"


def ctx_llm_cache(key: str) -> str:
    return f"cache:{key}"


def ctx_registry(uen: str) -> str:
    return f"registry:{uen}"


# ------------------------------------------------------------------------------------------
# key providers
# ------------------------------------------------------------------------------------------


@runtime_checkable
class KeyProvider(Protocol):
    """Where keys come from. Implement this to plug in a bank KMS / HSM.

    * ``active_key_id()``: id of the key used for every new write.
    * ``key_ids()``: every id that can decrypt (active first); used for status and rotation.
    * ``key(key_id)``: the 32 raw key bytes for ``key_id``, or ``None`` when unknown. An HSM-backed
      provider may instead unwrap a data key here; the vault never caches or logs what it gets.
    """

    def active_key_id(self) -> str: ...

    def key_ids(self) -> Sequence[str]: ...

    def key(self, key_id: str) -> bytes | None: ...


@dataclass(frozen=True)
class Keyring:
    """The built-in provider: an ordered list of ``(id, 32 bytes)``; the first is active."""

    entries: tuple[tuple[str, bytes], ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise CryptoConfigError("the keyring is empty")

    def active_key_id(self) -> str:
        return self.entries[0][0]

    def key_ids(self) -> Sequence[str]:
        return [k for k, _ in self.entries]

    def key(self, key_id: str) -> bytes | None:
        for kid, material in self.entries:
            if kid == key_id:
                return material
        return None

    def __repr__(self) -> str:  # never show key bytes
        return f"Keyring(ids={list(self.key_ids())})"


def _decode_key(raw: str) -> bytes:
    text = raw.strip()
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = text + "=" * (-len(text) % 4)
            out = decoder(padded)
        except (binascii.Error, ValueError):
            continue
        if len(out) == KEY_BYTES:
            return out
    raise CryptoConfigError(
        f"an encryption key is not base64 of exactly {KEY_BYTES} bytes (the key itself is not shown)"
    )


def _parse_entries(text: str, source: str) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for part in line.split(","):
            part = part.strip()
            if not part:
                continue
            kid, sep, material = part.partition(":")
            kid = kid.strip()
            if not sep or not _KEY_ID.match(kid):
                raise CryptoConfigError(
                    f"{source}: each key must look like id:base64 with an id of 1 to "
                    f"{MAX_KEY_ID} characters from A-Z a-z 0-9 _ . -"
                )
            try:
                out.append((kid, _decode_key(material)))
            except CryptoConfigError as exc:
                raise CryptoConfigError(f"{source}: key {kid!r}: {exc}") from None
    return out


def read_key_file(path: Path) -> str:
    """Read a key file, refusing one that group or others can read (or that is not a plain file)."""
    try:
        st = path.stat()
    except OSError as exc:
        raise CryptoConfigError(
            f"the encryption key file cannot be read ({exc.strerror})"
        ) from None
    if not stat.S_ISREG(st.st_mode):
        raise CryptoConfigError("the encryption key file is not a regular file")
    if os.name != "nt" and st.st_mode & 0o077:
        raise CryptoConfigError(
            "the encryption key file is readable by group or others (mode "
            f"{stat.S_IMODE(st.st_mode):04o}); run: chmod 600 on it"
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CryptoConfigError(
            f"the encryption key file cannot be read ({type(exc).__name__})"
        ) from None


def keyring_from_sources(keys: str = "", key_file: Path | str | None = None) -> Keyring | None:
    """Keyring from ``GLDB_ENCRYPTION_KEYS`` text and/or a key file; ``None`` when neither is set.

    Order: the env list first, then the file; the first key overall is active. The same id listed
    twice with the same key is ignored; with different keys it is an error."""
    entries: list[tuple[str, bytes]] = []
    if keys and keys.strip():
        entries += _parse_entries(keys, "GLDB_ENCRYPTION_KEYS")
    if key_file:
        entries += _parse_entries(
            read_key_file(Path(key_file).expanduser()), "GLDB_ENCRYPTION_KEY_FILE"
        )
    if not entries:
        if key_file or (keys and keys.strip()):
            raise CryptoConfigError("the encryption key setting holds no key")
        return None
    seen: dict[str, bytes] = {}
    unique: list[tuple[str, bytes]] = []
    for kid, material in entries:
        if kid in seen:
            if seen[kid] != material:
                raise CryptoConfigError(f"key id {kid!r} is listed twice with different keys")
            continue
        seen[kid] = material
        unique.append((kid, material))
    return Keyring(tuple(unique))


# ------------------------------------------------------------------------------------------
# the vault
# ------------------------------------------------------------------------------------------


def _aad(key_id: str, context: str) -> bytes:
    return _AAD_TAG + key_id.encode("utf-8") + b"\x00" + context.encode("utf-8")


class Vault:
    """Seal and open blobs.

    ``enabled`` (``GLDB_ENCRYPT_AT_REST=on``) decides whether *new writes* are encrypted. Reading
    works whenever a provider is configured, even with ``enabled`` False, so switching the mode
    off never strands encrypted data. Without a provider, reading an encrypted blob raises
    :class:`DecryptionError`; it is never returned as garbage and never silently skipped.
    """

    def __init__(self, provider: KeyProvider | None = None, *, enabled: bool = False):
        if enabled and provider is None:
            raise CryptoConfigError("encryption is on but no key is configured")
        self._provider = provider
        self.enabled = enabled
        self._aead: dict[str, AESGCM] = {}

    # -- introspection -------------------------------------------------------------------

    @property
    def active_key_id(self) -> str | None:
        return self._provider.active_key_id() if self._provider else None

    def key_ids(self) -> list[str]:
        return list(self._provider.key_ids()) if self._provider else []

    @property
    def has_keys(self) -> bool:
        return self._provider is not None

    # -- primitives ----------------------------------------------------------------------

    def _cipher(self, key_id: str) -> AESGCM:
        cipher = self._aead.get(key_id)
        if cipher is None:
            if self._provider is None:
                raise DecryptionError("data is encrypted but no encryption key is configured")
            material = self._provider.key(key_id)
            if material is None or len(material) != KEY_BYTES:
                raise DecryptionError(f"data is encrypted with key {key_id!r}, which is not loaded")
            cipher = AESGCM(material)
            self._aead[key_id] = cipher
        return cipher

    @staticmethod
    def is_encrypted(blob: bytes | memoryview) -> bool:
        return bytes(blob[: len(MAGIC)]) == MAGIC

    @staticmethod
    def is_encrypted_text(text: str) -> bool:
        return text.startswith(TEXT_PREFIX)

    @staticmethod
    def key_id_of(blob: bytes) -> str | None:
        """Key id in the header of an encrypted blob, ``None`` for plaintext or a bad header."""
        if not Vault.is_encrypted(blob) or len(blob) < len(HEADER_PREFIX) + 1:
            return None
        n = blob[len(HEADER_PREFIX)]
        raw = blob[len(HEADER_PREFIX) + 1 : len(HEADER_PREFIX) + 1 + n]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def key_id_of_text(self, text: str) -> str | None:
        if not text.startswith(TEXT_PREFIX):
            return None
        try:
            head = base64.b64decode(text[len(TEXT_PREFIX) : len(TEXT_PREFIX) + 64] + "====")
        except (binascii.Error, ValueError):
            return None
        return self.key_id_of(head)

    def encrypt(self, data: bytes, context: str) -> bytes:
        """Always encrypts under the active key (used by the migration tool and by :meth:`seal`)."""
        if self._provider is None:
            raise CryptoConfigError("no encryption key is configured")
        key_id = self._provider.active_key_id()
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._cipher(key_id).encrypt(nonce, data, _aad(key_id, context))
        kid = key_id.encode("utf-8")
        return HEADER_PREFIX + bytes([len(kid)]) + kid + nonce + sealed

    def decrypt(self, blob: bytes, context: str) -> bytes:
        """Decrypt an encrypted blob (raises :class:`DecryptionError` for anything wrong)."""
        pre = len(HEADER_PREFIX)
        if not blob.startswith(MAGIC) or len(blob) < pre + 1:
            raise DecryptionError("not an encrypted blob")
        if blob[len(MAGIC)] != VERSION:
            raise DecryptionError("unsupported encryption format version")
        n = blob[pre]
        end = pre + 1 + n
        if n == 0 or len(blob) < end + NONCE_BYTES + 16:
            raise DecryptionError("encrypted blob is truncated")
        try:
            key_id = blob[pre + 1 : end].decode("utf-8")
        except UnicodeDecodeError:
            raise DecryptionError("encrypted blob has a bad header") from None
        nonce = blob[end : end + NONCE_BYTES]
        body = blob[end + NONCE_BYTES :]
        cipher = self._cipher(key_id)
        try:
            return cipher.decrypt(nonce, body, _aad(key_id, context))
        except InvalidTag:
            raise DecryptionError(
                "decryption failed: wrong key, altered data or data belonging to another record"
            ) from None

    # -- the normal API ------------------------------------------------------------------

    def seal(self, data: bytes, context: str) -> bytes:
        """Encrypt when the mode is on, else return ``data`` unchanged."""
        return self.encrypt(data, context) if self.enabled else data

    def open(self, blob: bytes, context: str) -> bytes:
        """Plaintext of ``blob``: decrypted if it carries the magic, else returned as legacy plaintext."""
        if self.is_encrypted(blob):
            return self.decrypt(bytes(blob), context)
        return bytes(blob)

    def seal_text(self, text: str, context: str) -> str:
        if not self.enabled:
            return text
        return TEXT_PREFIX + base64.b64encode(self.encrypt(text.encode("utf-8"), context)).decode(
            "ascii"
        )

    def open_text(self, text: str, context: str) -> str:
        if not text.startswith(TEXT_PREFIX):
            return text
        try:
            blob = base64.b64decode(text[len(TEXT_PREFIX) :], validate=True)
        except (binascii.Error, ValueError):
            raise DecryptionError("encrypted text is not valid base64") from None
        return self.decrypt(blob, context).decode("utf-8")

    def encrypt_text(self, text: str, context: str) -> str:
        """Like :meth:`seal_text` but regardless of the mode (migration tool)."""
        return TEXT_PREFIX + base64.b64encode(self.encrypt(text.encode("utf-8"), context)).decode(
            "ascii"
        )

    def fingerprint(self) -> str:
        """Short non-secret label of the configured key ids (for status pages)."""
        return hashlib.sha256(",".join(self.key_ids()).encode()).hexdigest()[:8]


_PLAINTEXT = Vault(None, enabled=False)


def plaintext_vault() -> Vault:
    """The pass-through vault (mode off, no keys)."""
    return _PLAINTEXT


_CACHE: dict[tuple, Vault] = {}


def vault_from_settings(settings: Settings) -> Vault:
    """Vault for a settings object (cached per key configuration; the key file's mtime is part of
    the cache key, so editing the file takes effect). Raises :class:`CryptoConfigError` when the
    mode is ``on`` and no usable key exists. Keys configured with the mode ``off`` still allow
    reading encrypted data."""
    mode = settings.encrypt_at_rest
    keys = settings.encryption_keys_value
    key_file = settings.encryption_key_file.strip()
    stamp: tuple = ()
    if key_file:
        try:
            st = Path(key_file).expanduser().stat()
            stamp = (str(key_file), st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = (str(key_file), None)
    cache_key = (mode, keys, stamp)
    hit = _CACHE.get(cache_key)
    if hit is not None:
        return hit
    ring = keyring_from_sources(keys, key_file)
    if mode == "on" and ring is None:
        raise CryptoConfigError(
            "GLDB_ENCRYPT_AT_REST=on needs GLDB_ENCRYPTION_KEYS or GLDB_ENCRYPTION_KEY_FILE"
        )
    vault = Vault(ring, enabled=(mode == "on")) if (ring or mode == "on") else _PLAINTEXT
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[cache_key] = vault
    return vault
