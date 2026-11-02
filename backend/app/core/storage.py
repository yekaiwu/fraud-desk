"""Content-addressed file store under ``GLDB_DATA_DIR`` (architecture.md section 6).

``files/<sha[:2]>/<sha256>`` holds the original bytes (stored name is the hash, never a user
supplied name). ``renders/<sha256>/`` holds the page PNG cache.

With encryption at rest on (docs/encryption-at-rest.md) the *id* stays the sha256 of the plaintext
but the bytes on disk are an AES-256-GCM envelope bound to that id. Always go through ``read`` (or
``source``/``materialised``); ``path_for`` only says where the (possibly encrypted) file lives and
must not be opened by anything that expects the original bytes.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.crypto import Vault, ctx_file, ctx_render, plaintext_vault

_SHA = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileSource:
    """Hashable handle on one stored file: ``read()`` gives the plaintext bytes.

    Used as a cache key by the forensics checks (equal when the same store location and sha256),
    so an ``lru_cache`` never holds plaintext and a path never has to exist on disk."""

    __slots__ = ("_store", "sha256")

    def __init__(self, store: FileStore, sha256: str):
        self._store = store
        self.sha256 = sha256

    def read(self) -> bytes:
        return self._store.read(self.sha256)

    def __hash__(self) -> int:
        return hash((str(self._store.data_dir), self.sha256))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, FileSource)
            and other.sha256 == self.sha256
            and other._store.data_dir == self._store.data_dir
        )

    def __repr__(self) -> str:
        return f"FileSource({self.sha256[:12]})"


class FileStore:
    def __init__(self, data_dir: Path | str, vault: Vault | None = None):
        self.data_dir = Path(data_dir)
        self.vault = vault if vault is not None else plaintext_vault()

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    def path_for(self, sha256: str) -> Path:
        """Where the stored file lives. The bytes there are encrypted when encryption is on."""
        if not _SHA.match(sha256):
            raise ValueError("not a sha256 hex digest")
        return self.files_dir / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    @staticmethod
    def _write_atomic(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put(self, data: bytes) -> str:
        """Store bytes (idempotent, atomic) and return the sha256 hex digest of the plaintext.

        With the mode on the file is encrypted; a legacy plaintext copy of the same content is
        replaced by an encrypted one."""
        sha = sha256_bytes(data)
        target = self.path_for(sha)
        if target.is_file():
            if self.vault.enabled and not self.is_encrypted_on_disk(sha):
                self._write_atomic(target, self.vault.seal(data, ctx_file(sha)))
            return sha
        self._write_atomic(target, self.vault.seal(data, ctx_file(sha)))
        return sha

    def read(self, sha256: str) -> bytes:
        """Plaintext bytes (decrypts; legacy plaintext files are returned as they are)."""
        return self.vault.open(self.path_for(sha256).read_bytes(), ctx_file(sha256))

    def is_encrypted_on_disk(self, sha256: str) -> bool:
        with open(self.path_for(sha256), "rb") as fh:
            return Vault.is_encrypted(fh.read(8))

    def source(self, sha256: str) -> FileSource:
        if not _SHA.match(sha256):
            raise ValueError("not a sha256 hex digest")
        return FileSource(self, sha256)

    @contextmanager
    def materialised(self, sha256: str, *, suffix: str = "") -> Iterator[Path]:
        """A real path to the plaintext, for a library that cannot take bytes.

        A legacy plaintext file is yielded in place. An encrypted one is decrypted into a private
        (mode 0700) directory under the system temp dir, never under the data dir, and removed in a
        ``finally`` block. Nothing in the application needs this today (PyMuPDF, pikepdf, Pillow and
        Tesseract all take bytes or a pipe); it exists so a future library does not force plaintext
        into the data dir."""
        if not self.is_encrypted_on_disk(sha256):
            yield self.path_for(sha256)
            return
        tmpdir = Path(tempfile.mkdtemp(prefix="gldb-plain-"))
        try:
            os.chmod(tmpdir, 0o700)
            target = tmpdir / (sha256 + suffix)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(self.read(sha256))
            yield target
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # -- page render cache -----------------------------------------------------------------

    def render_path(self, sha256: str, name: str) -> Path:
        if not _SHA.match(sha256) or not re.fullmatch(r"[0-9A-Za-z_.-]{1,40}", name):
            raise ValueError("bad render cache name")
        return self.renders_dir / sha256 / name

    def get_render(self, sha256: str, name: str) -> bytes | None:
        """Cached PNG bytes (decrypted), or ``None`` on a miss."""
        path = self.render_path(sha256, name)
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        return self.vault.open(raw, ctx_render(sha256, name))

    def put_render(self, sha256: str, name: str, data: bytes) -> None:
        self._write_atomic(
            self.render_path(sha256, name), self.vault.seal(data, ctx_render(sha256, name))
        )

    def delete(self, sha256: str) -> None:
        """Remove the original and its page renders."""
        self.path_for(sha256).unlink(missing_ok=True)
        shutil.rmtree(self.renders_dir / sha256, ignore_errors=True)
