"""Content-addressed file store under ``GLDB_DATA_DIR`` (architecture.md section 6).

``files/<sha[:2]>/<sha256>`` holds the original bytes (stored name is the hash, never a user
supplied name). ``renders/<sha256>/`` holds the page PNG cache.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    def path_for(self, sha256: str) -> Path:
        if not _SHA.match(sha256):
            raise ValueError("not a sha256 hex digest")
        return self.files_dir / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put(self, data: bytes) -> str:
        """Store bytes (idempotent, atomic) and return the sha256 hex digest."""
        sha = sha256_bytes(data)
        target = self.path_for(sha)
        if target.is_file():
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha

    def read(self, sha256: str) -> bytes:
        return self.path_for(sha256).read_bytes()

    def delete(self, sha256: str) -> None:
        """Remove the original and its page renders."""
        self.path_for(sha256).unlink(missing_ok=True)
        shutil.rmtree(self.renders_dir / sha256, ignore_errors=True)
