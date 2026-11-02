"""Encrypt, check or re-key the stored data (docs/encryption-at-rest.md section 5).

    uv run python backend/scripts/encrypt_data.py                 # --dry-run (the default): counts only
    uv run python backend/scripts/encrypt_data.py --apply         # encrypt every plaintext item
    uv run python backend/scripts/encrypt_data.py --rotate        # re-encrypt items under the active key

BACK UP THE DATA DIRECTORY FIRST, and stop the backend while this runs. The tool reads the same
settings as the application (``GLDB_DATA_DIR``, ``GLDB_ENCRYPTION_KEYS``, ``GLDB_ENCRYPTION_KEY_FILE``;
``--data-dir`` / ``--db-path`` override the first two). The first key in the keyring is the active one.

Categories: files, renders, registry_cache, cases, case_versions, review_entries, llm_cache, jobs.

* Every item is converted on its own and atomically: the new envelope is decrypted again and compared
  with the original before it replaces the old one (a file is written beside the old one and renamed;
  a database row is updated only if it still holds the text that was read). A failure leaves that item
  untouched and the run goes on; the exit code is 1 if anything failed.
* Idempotent and resumable: an item already under the active key is skipped, so a second run (or a run
  after an interruption) converts only what is left.
* Content is never printed. Failures name the category and the item id (a case id, a file hash).
* After a database change the plaintext helper columns are rebuilt (``Database.reindex``), the WAL is
  checkpointed and the database file is rebuilt (VACUUM) so replaced plaintext does not stay in free
  pages (``--no-vacuum`` skips the rebuild).

Exit codes: 0 done, 1 an item failed, 2 bad usage or configuration (for example no key).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import Settings  # noqa: E402
from app.core.crypto import (  # noqa: E402
    CryptoConfigError,
    CryptoError,
    Vault,
    ctx_case,
    ctx_case_version,
    ctx_file,
    ctx_llm_cache,
    ctx_registry,
    ctx_render,
    ctx_review,
    ctx_top_finding,
    keyring_from_sources,
)
from app.core.db import Database  # noqa: E402
from app.core.fingerprints import ctx_fingerprint  # noqa: E402

CATEGORIES = (
    "files",
    "renders",
    "registry_cache",
    "cases",
    "top_finding",
    "case_versions",
    "review_entries",
    "llm_cache",
    "fingerprints",
    "jobs",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_UEN_CACHE = re.compile(r"^acra-([0-9A-Z]{9,10})\.json$")
MAX_FAILURE_IDS = 20


@dataclass
class Counts:
    plaintext: int = 0  # found as plaintext
    active_key: int = 0  # encrypted under the active key
    old_key: int = 0  # encrypted under another loaded key
    unknown_key: int = 0  # encrypted under a key that is not loaded
    converted: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def fail(self, ident: str) -> None:
        self.failed += 1
        if len(self.failures) < MAX_FAILURE_IDS:
            self.failures.append(ident)

    def as_dict(self) -> dict[str, object]:
        return {
            "plaintext": self.plaintext,
            "encrypted_active_key": self.active_key,
            "encrypted_old_key": self.old_key,
            "encrypted_unknown_key": self.unknown_key,
            "converted": self.converted,
            "failed": self.failed,
            "failed_ids": self.failures,
        }


def _atomic_replace(path: Path, data: bytes, expect: os.stat_result) -> None:
    """Write ``data`` beside ``path`` and rename it over, unless ``path`` changed since ``expect``."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        now = path.stat()
        if (now.st_mtime_ns, now.st_size) != (expect.st_mtime_ns, expect.st_size):
            raise RuntimeError("changed while the tool was running")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Migrator:
    def __init__(
        self,
        vault: Vault | None,
        data_dir: Path,
        db_path: Path,
        *,
        apply: bool,
        rotate: bool,
        vacuum: bool = True,
    ):
        self.vault = vault
        self.data_dir = data_dir
        self.db_path = db_path
        self.apply = apply
        self.rotate = rotate
        self.vacuum = vacuum
        self.counts = {name: Counts() for name in CATEGORIES}

    # -- classification ------------------------------------------------------------------

    def _state(self, key_id: str | None, encrypted: bool) -> str:
        if not encrypted:
            return "plaintext"
        v = self.vault
        if v is None or key_id not in v.key_ids():
            return "unknown"
        return "active" if key_id == v.active_key_id else "old"

    def _tally(self, cat: str, state: str) -> None:
        c = self.counts[cat]
        if state == "plaintext":
            c.plaintext += 1
        elif state == "active":
            c.active_key += 1
        elif state == "old":
            c.old_key += 1
        else:
            c.unknown_key += 1

    def _wants_conversion(self, state: str) -> bool:
        return (self.apply and state == "plaintext") or (self.rotate and state == "old")

    # -- the generic "convert one value" step --------------------------------------------

    def _convert_text(self, text: str, context: str) -> str:
        assert self.vault is not None
        plain = self.vault.open_text(text, context)
        new = self.vault.encrypt_text(plain, context)
        if self.vault.open_text(new, context) != plain:  # verify before anything is replaced
            raise RuntimeError("verification failed")
        return new

    def _convert_blob(self, raw: bytes, context: str, check: Callable[[bytes], None]) -> bytes:
        assert self.vault is not None
        plain = self.vault.open(raw, context)
        check(plain)
        new = self.vault.encrypt(plain, context)
        if self.vault.decrypt(new, context) != plain:
            raise RuntimeError("verification failed")
        return new

    # -- files on disk ------------------------------------------------------------------

    def _file_item(
        self, cat: str, path: Path, ident: str, context: str, check: Callable[[bytes], None]
    ) -> None:
        c = self.counts[cat]
        try:
            st = path.stat()
            raw = path.read_bytes()
        except OSError:
            c.fail(ident)
            return
        enc = Vault.is_encrypted(raw)
        state = self._state(Vault.key_id_of(raw) if enc else None, enc)
        self._tally(cat, state)
        if not self._wants_conversion(state):
            return
        try:
            _atomic_replace(path, self._convert_blob(raw, context, check), st)
            c.converted += 1
        except (CryptoError, RuntimeError, OSError, ValueError):
            c.fail(ident)

    def _walk(self, root: Path):  # noqa: ANN202
        if not root.is_dir():
            return
        for shard in sorted(root.iterdir()):
            if shard.is_dir():
                for item in sorted(shard.iterdir()):
                    if item.is_file() and not item.name.startswith("."):
                        yield shard, item

    def run_files(self) -> None:
        for _shard, item in self._walk(self.data_dir / "files"):
            if not _SHA.match(item.name):
                continue

            def check(plain: bytes, name: str = item.name) -> None:
                if hashlib.sha256(plain).hexdigest() != name:
                    raise RuntimeError("stored file does not match its hash")

            self._file_item("files", item, item.name[:12], ctx_file(item.name), check)

    def run_renders(self) -> None:
        for shard, item in self._walk(self.data_dir / "renders"):
            if _SHA.match(shard.name):
                self._file_item(
                    "renders",
                    item,
                    f"{shard.name[:12]}/{item.name}",
                    ctx_render(shard.name, item.name),
                    lambda _p: None,
                )

    def run_registry_cache(self) -> None:
        root = self.data_dir / "registry-cache"
        if not root.is_dir():
            return
        for item in sorted(root.iterdir()):
            m = _UEN_CACHE.match(item.name)
            if m and item.is_file():
                self._file_item(
                    "registry_cache", item, item.name, ctx_registry(m.group(1)), lambda _p: None
                )

    # -- database rows ------------------------------------------------------------------

    def _text_rows(
        self,
        conn: sqlite3.Connection,
        cat: str,
        table: str,
        column: str,
        pk: tuple[str, ...],
        context_of: Callable[[dict], str],
        ident_of: Callable[[dict], str],
        *,
        where: str = "",
        skip: Callable[[str], bool] = lambda _t: False,
    ) -> None:
        """Walk ``table.column``: the primary keys are listed first and each value is fetched, converted
        and written on its own, so memory stays small and one bad row stops nothing."""
        c = self.counts[cat]
        keys = conn.execute(
            f"SELECT {', '.join(pk)} FROM {table}" + (f" WHERE {where}" if where else "")
        ).fetchall()
        match = " AND ".join(f"{k} IS ?" for k in pk)  # IS: a NULL document_id matches too
        for krow in keys:
            ids = {k: krow[k] for k in pk}
            got = conn.execute(
                f"SELECT {column} AS t FROM {table} WHERE {match}", tuple(ids.values())
            ).fetchone()
            text = None if got is None else got["t"]
            if text is None or skip(text):
                continue
            enc = Vault.is_encrypted_text(text)
            key_id = self.vault.key_id_of_text(text) if (enc and self.vault) else None
            state = self._state(key_id, enc)
            self._tally(cat, state)
            if not self._wants_conversion(state):
                continue
            try:
                new = self._convert_text(text, context_of(ids))
                done = conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {match} AND {column} = ?",
                    (new, *ids.values(), text),
                ).rowcount
                conn.commit()
                if done != 1:
                    raise RuntimeError("row changed while the tool was running")
                c.converted += 1
            except (CryptoError, RuntimeError, ValueError, sqlite3.Error):
                conn.rollback()
                c.fail(ident_of(ids))

    def run_database(self) -> None:
        if not self.db_path.is_file():
            return
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA secure_delete=ON")  # overwritten text is zeroed, not left behind
            conn.execute("PRAGMA busy_timeout=30000")
            self._text_rows(
                conn, "cases", "cases", "body", ("id",),
                lambda i: ctx_case(i["id"]), lambda i: i["id"],
            )  # fmt: skip
            self._text_rows(
                conn, "top_finding", "cases", "top_finding", ("id",),
                lambda i: ctx_top_finding(i["id"]), lambda i: i["id"],
            )  # fmt: skip
            self._text_rows(
                conn, "case_versions", "case_versions", "body", ("case_id", "version"),
                lambda i: ctx_case_version(i["case_id"], i["version"]),
                lambda i: f"{i['case_id']} v{i['version']}",
            )  # fmt: skip
            self._text_rows(
                conn, "review_entries", "review_entries", "body", ("id",),
                lambda i: ctx_review(i["id"]), lambda i: i["id"],
            )  # fmt: skip
            self._text_rows(
                conn, "llm_cache", "llm_cache", "value", ("key",),
                lambda i: ctx_llm_cache(i["key"]), lambda i: i["key"][:12],
            )  # fmt: skip
            self._text_rows(
                conn, "fingerprints", "fingerprints", "meta",
                ("kind", "fingerprint", "case_id", "document_id"),
                lambda i: ctx_fingerprint(i["kind"], i["fingerprint"], i["case_id"], i["document_id"]),
                lambda i: f"{i['kind']} {i['case_id']}",
                skip=lambda t: t == "{}",
            )  # fmt: skip
            self._text_rows(
                conn, "jobs", "jobs", "stages", ("id",),
                lambda i: f"job:{i['id']}:stages", lambda i: i["id"],
                skip=lambda t: t == "[]",
            )  # fmt: skip
            self._text_rows(
                conn, "jobs", "jobs", "error", ("id",),
                lambda i: f"job:{i['id']}:error", lambda i: i["id"],
                where="error IS NOT NULL",
            )  # fmt: skip
        finally:
            conn.close()

    def finish_database(self) -> None:
        """Rebuild helper columns, fold the WAL in and rewrite the file without free pages."""
        if not self.db_path.is_file() or not self.vault:
            return
        db = Database(self.db_path, data_dir=self.data_dir, vault=self.vault)
        db.init_schema()
        db.reindex()
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if self.vacuum:
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    def run(self) -> dict[str, Counts]:
        self.run_files()
        self.run_renders()
        self.run_registry_cache()
        self.run_database()
        changed_db = any(
            self.counts[k].converted
            for k in CATEGORIES
            if k not in ("files", "renders", "registry_cache")
        )
        if (self.apply or self.rotate) and changed_db:
            self.finish_database()
        return self.counts


def build_vault(settings: Settings, *, need_keys: bool) -> Vault | None:
    ring = keyring_from_sources(
        settings.encryption_keys_value, settings.encryption_key_file.strip()
    )
    if ring is None:
        if need_keys:
            raise CryptoConfigError(
                "no key: set GLDB_ENCRYPTION_KEYS or GLDB_ENCRYPTION_KEY_FILE (first key = active)"
            )
        return None
    return Vault(ring, enabled=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="count only, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="encrypt every plaintext item")
    mode.add_argument("--rotate", action="store_true", help="re-encrypt items under the active key")
    ap.add_argument("--data-dir", help="override GLDB_DATA_DIR")
    ap.add_argument(
        "--db-path", help="override the database path (default <data-dir>/gldb.sqlite3)"
    )
    ap.add_argument("--no-vacuum", action="store_true", help="skip rebuilding the database file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    settings = Settings()
    data_dir = Path(a.data_dir) if a.data_dir else settings.data_dir
    db_path = (
        Path(a.db_path)
        if a.db_path
        else (Path(settings.database_path) if not a.data_dir else data_dir / "gldb.sqlite3")
    )
    writing = a.apply or a.rotate
    try:
        vault = build_vault(settings, need_keys=writing)
    except CryptoConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not data_dir.is_dir():
        print("error: the data directory does not exist", file=sys.stderr)
        return 2

    migrator = Migrator(
        vault, data_dir, db_path, apply=a.apply, rotate=a.rotate, vacuum=not a.no_vacuum
    )
    counts = migrator.run()
    failed = sum(c.failed for c in counts.values())
    label = "apply" if a.apply else "rotate" if a.rotate else "dry-run"
    report = {
        "mode": label,
        "active_key_id": vault.active_key_id if vault else None,
        "categories": {k: v.as_dict() for k, v in counts.items()},
        "failed": failed,
    }
    if a.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"mode: {label}   active key: {report['active_key_id'] or '(none configured)'}")
        print(
            f"{'category':<16}{'plaintext':>10}{'active':>8}{'old key':>9}{'unknown':>9}{'converted':>11}{'failed':>8}"
        )
        for name, c in counts.items():
            print(
                f"{name:<16}{c.plaintext:>10}{c.active_key:>8}{c.old_key:>9}{c.unknown_key:>9}"
                f"{c.converted:>11}{c.failed:>8}"
            )
            if c.failures:
                print(f"    failed: {', '.join(c.failures)}")
        if not writing:
            print("dry run: nothing was written. Use --apply to encrypt, --rotate to re-key.")
        if any(c.unknown_key for c in counts.values()):
            print("warning: some items use a key that is not loaded; add it to the keyring.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
