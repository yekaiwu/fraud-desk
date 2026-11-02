# Encryption at rest

The application can encrypt what it stores by itself, so the control does not depend on the disk, the volume or the backup system underneath. It is **off by default** (`GLDB_ENCRYPT_AT_REST=off`), so existing deployments and tests behave as before. With it on, every new write of the data below is an AES-256-GCM envelope, and the application refuses to start without a usable key. Nothing falls back to plaintext silently.

What this lets us say truthfully, once it is switched on and `backend/scripts/encrypt_data.py --apply` has been run on the existing data: *the application encrypts the uploaded files, the page-image cache, the case data, the review entries and the response cache itself, with keys held outside the data directory; the volume's encryption (FileVault, LUKS, a bank's storage encryption) is a second, independent layer, not the only one.* What it does not say is in section 9.

Contents: 1 settings, 2 what is encrypted, 3 what is deliberately not, 4 the format, 5 key management, 6 turning it on, 7 rotating keys, 8 backups, 9 threat model and honest limits, 10 where files are read (audit), 11 monitoring, 12 tests.

## 1 Settings

| Setting | Default | Meaning |
|---|---|---|
| `GLDB_ENCRYPT_AT_REST` | `off` | `off` or `on`. `on` encrypts every new write. `on` with no usable key makes `validate_startup()` fail (the backend does not start). |
| `GLDB_ENCRYPTION_KEYS` | empty | Keys as `id:base64(32 bytes)`, comma separated. The **first** key is the active one for new writes; **all** listed keys can decrypt. |
| `GLDB_ENCRYPTION_KEY_FILE` | empty | A file with the same `id:base64` entries (one per line or comma separated, `#` comments allowed). Refused if group or others can read it (use `chmod 600`), if it is not a regular file, or if it is inside `GLDB_DATA_DIR`. Entries from `GLDB_ENCRYPTION_KEYS` come first, then the file's. |

Reading works whenever keys are configured, whatever the mode: turning the mode `off` again never strands encrypted data (new writes are then plaintext, which is what `off` means). Without any key, encrypted data cannot be read and the error says so; it is never returned as garbage and never skipped.

A key id is 1 to 32 characters from `A-Z a-z 0-9 _ . -`, for example `2026-11` or `k1`. Ids are stored in the clear in every envelope (so the right key can be chosen); the key material is never stored, logged or shown (settings use `SecretStr`, error messages name ids only).

`GLDB_RETENTION_DAYS` is unchanged and still a setting. Its default of 1825 days is **not** confirmed: the retention period under MAS Notice 626 is the bank's decision and stays listed as one (see `docs/regulatory-mapping.md` section 6).

## 2 What is encrypted

| Data | Where | Context bound into the envelope |
|---|---|---|
| Uploaded originals | `files/<sha[:2]>/<sha256>` | `file:<sha256>` |
| Page-image cache | `renders/<sha256>/<page>_<width>.png` | `render:<sha256>:<name>` |
| Case body (documents, extracted fields incl. NRIC/FIN, flags, findings, applicant, stages) | `cases.body` | `case:<id>` |
| Version snapshots | `case_versions.body` | `case_version:<id>:<n>` |
| Review entries (verdicts, reasons) | `review_entries.body` | `review:<case>/<entry>` |
| Model response cache | `llm_cache.value` | `cache:<key>` |
| One-line queue summary | `cases.top_finding` | `case_top:<id>` |
| Job stage records and error text | `jobs.stages`, `jobs.error` | `job:<id>:stages`, `job:<id>:error` |
| Fingerprint meta (holds the applicant UEN) | `fingerprints.meta` | `fingerprint:<kind>:<fp>:<case>:<doc>` |
| Live register cache (only with `GLDB_REGISTRY_LIVE=on`) | `registry-cache/acra-<UEN>.json` | `registry:<UEN>` |

The **id of a file stays the sha256 of the plaintext** (the case body, fingerprints, audit rows and the API all use it); only the bytes on disk change. `FileStore.read()` returns plaintext and verifies the context; `ingest` still re-checks the hash and magic bytes of what it read.

## 3 What is deliberately not encrypted, and why

| Item | Why it stays readable | Residual risk |
|---|---|---|
| `cases.applicant_name`, `cases.reference` | The queue shows them and searches them by substring (`LIKE '%q%'` in SQL, with paging and a total count). Encrypted values cannot be searched that way; a blind index (HMAC) supports exact match only, and filtering in Python would decrypt every row on every search and change the paging contract. Neither sort order uses them (`priority`, `score`, `age`, `coverage`, `created`). | Anyone who reads the database file sees which applicants were checked and their internal references: a list of company names (and, if someone typed one, a person's name or an identifier into the reference). **Do not put personal identifiers in the reference field.** This is the one deliberate readable copy of identifying data. |
| Queue columns: id, use case, status, version, score, band, lane, coverage ratio, partial, finding counts, decision, dates, `escalated` | Needed to filter, sort and page in SQL. Numbers, enums and timestamps, no document content. | Reveals case counts, timing and outcomes. |
| `case_files(case_id, sha256)`, `llm_cache.file_sha256`, `review_entries.status` | Replace SQL queries into the (now sealed) JSON: "is this file still used by another case" (delete/purge) and "which entries wait for a second approval". | Links a case id to the hash of its files. |
| File names on disk | They are the sha256 of the plaintext (content-addressed, by design). | Someone who already holds a document can compute its hash and confirm the file is present. Sizes are visible (the envelope adds 37 bytes plus the key id). |
| `fingerprints` (kind, hash, case id, document id) | They are one-way hashes used for duplicate detection (file hash, invoice hash, signature-crop hash). The meta beside them is encrypted. | A low-entropy input (a known supplier UEN plus invoice number, date, total) could in principle be tested against an invoice hash by someone who can already read the database. |
| `audit_log` | Already pseudonymised by design: ids, hashes, enums and counts; free text goes through `scrub()` and case references are hashed after deletion (`backend/app/audit.py`). The end-to-end test finds no NRIC/FIN in `/cases/{id}/audit` and none in the database bytes. | Actor names (reviewer account names) and timestamps are in the clear. |
| `rule_overrides`, `rule_changes`, `daily_spend` | Configuration and counters, no applicant data. | None for personal data. |
| Process memory, swap, crash dumps, logs | Out of scope for storage encryption; logs go through the pseudonymiser when `GLDB_PSEUDONYMISE_LOGS=true`. | See section 9. |

## 4 The format

```
"GLDBENC" | version (1) | key-id length (1) | key id | nonce (12 random bytes) | ciphertext | tag (16)
```

* AES-256-GCM (`cryptography`), a fresh random 96-bit nonce for every encryption (nonces are never reused or derived from a counter), a 128-bit tag.
* The **authenticated associated data** is `"gldb-aad-v1" 0x00 <key id> 0x00 <context>`. The context names the record (table 2). A ciphertext copied to another file, case, version, review entry, cache key or page name fails authentication, as does any flipped bit in the header, key id, nonce, body or tag.
* Database columns are TEXT, so the stored form is `gldbenc1:` followed by the base64 of the blob. JSON columns that are encrypted start with `gldbenc1:`; legacy plaintext JSON starts with `{`, and a legacy file has no `GLDBENC` magic. **Only the magic decides "this is encrypted"**: a blob whose first bytes were destroyed reads as legacy plaintext (garbage) and is then caught by the hash check (files) or the JSON parser (columns), not by the tag. This is the price of reading legacy data while the mode is on.
* Random nonces are safe for about 2^32 encryptions under one key; rotate keys long before that (this tool writes a few million records at most).
* Not detected: **rollback** of a record to an older valid ciphertext of the *same* record (the context has no version counter), and **deletion** of records or files. Both need write access to the data directory.

## 5 Key management

* **Generate**: `openssl rand -base64 32`, or `python3 -c "import os,base64;print('2026-11:'+base64.b64encode(os.urandom(32)).decode())"`. One line per key: `2026-11:<base64>`.
* **Where it lives**: in a key file (mode `0600`, owned by the service account) or in the service's environment, **outside the data directory and outside every backup of it**. The backend refuses a key file inside `GLDB_DATA_DIR`. A key stored beside the data it protects protects nothing against theft of the data directory or of a backup.
* **Back the key up separately** from the data (a second copy in the bank's secret store, a sealed envelope, an HSM export), with a different access list. **Losing every copy of a key means losing everything encrypted under it, permanently.** There is no recovery path and no escrow in the application. Test a restore (section 8) before relying on it.
* **Who can read it**: anyone who can run as the service account, read the process environment (`ps eww`, `/proc/<pid>/environ`, launchd plist) or read the key file. Prefer the key file over `GLDB_ENCRYPTION_KEYS`: the environment holds a path, not the key.
* **Pluggable provider**: key lookup goes through the small `KeyProvider` interface in `backend/app/core/crypto.py`:

  ```python
  class KeyProvider(Protocol):
      def active_key_id(self) -> str: ...        # id used for every new write
      def key_ids(self) -> Sequence[str]: ...    # every id that can decrypt, active first
      def key(self, key_id: str) -> bytes | None: ...  # 32 raw bytes, or None if unknown
  ```

  Only the environment/file provider is implemented here. A bank KMS or HSM adapter implements those three methods (for example by unwrapping per-id data keys with the KMS) and is handed to `Vault(provider, enabled=True)`; `vault_from_settings()` is the one place that would choose it. The wire format needs no change: the envelope already carries the key id. Per-record data keys (true envelope encryption with a KMS-wrapped key per file) are not implemented; the master key here encrypts the data directly.

## 6 Turning it on

1. **Stop the backend** (on the macOS deployment: the launchd agents in `docs/deploy.md`). The migration tool should not run against a live database.
2. **Back up first**: copy the whole data directory (`files/`, `renders/`, `gldb.sqlite3` and its `-wal`/`-shm`, `registry-cache/`) to a location that is itself encrypted and access-controlled. This is the rollback point. Do not skip it.
3. **Create the key** (section 5), store it outside the data directory with mode 600, and back it up separately.
4. **Configure**: `GLDB_ENCRYPT_AT_REST=on` and `GLDB_ENCRYPTION_KEY_FILE=/path/to/keys` (or `GLDB_ENCRYPTION_KEYS`).
5. **Look first**: `uv run python backend/scripts/encrypt_data.py` (the default is `--dry-run`: counts per category of plaintext, encrypted under the active key, encrypted under an old key and encrypted under a key that is not loaded; it writes nothing and needs no key).
6. **Encrypt**: `uv run python backend/scripts/encrypt_data.py --apply`. Exit code 0 means every item was converted or already encrypted; 1 means at least one failed (named by category and id, never by content; fix it and run again, it resumes); 2 means a usage or configuration problem (no key, bad key file, no data directory). Each item is written beside the old one, **decrypted again and compared with the original**, then renamed over it (files) or updated only if the row still holds the text that was read (database). The run then rebuilds the helper columns, folds the write-ahead log in and rebuilds the database file (`VACUUM`, with `secure_delete` on while rows are rewritten), so replaced plaintext does not stay in free pages. `--no-vacuum` skips the rebuild.
7. **Start the backend** and open `GET /monitoring`: `encryption_at_rest.status` should be `on` and `all_sampled_encrypted` true.
8. **Retire the pre-migration copies**: the backup from step 2, older backups and snapshots still hold the plaintext. Keep the rollback copy only as long as needed, then destroy it according to the bank's media policy. The migration cannot scrub storage-level remnants (SSD wear levelling, copy-on-write snapshots, Time Machine); the volume encryption underneath is what covers those.

Gradual migration is supported: while the mode is on, plaintext items still read, new writes are encrypted, and saving an old case or uploading an already stored file again re-seals it. `GET /monitoring` says `on_with_plaintext` until `--apply` has covered the rest.

## 7 Rotating keys

1. Back up the data directory and the key file.
2. Generate a new key with a new id and put it **first**: `2026-12:<new>` then `2026-11:<old>` in the key file. Restart. New writes now use `2026-12`; everything old still reads.
3. `uv run python backend/scripts/encrypt_data.py` shows the old-key counts. Run `--rotate` (back up first; stop the backend). Each item encrypted under a key other than the active one is decrypted, re-encrypted under the active key, verified and replaced. Items under a key that is **not** loaded are reported, not touched; add that key back and run again.
4. When the dry run shows `encrypted_old_key: 0` and `encrypted_unknown_key: 0` everywhere, and `GET /monitoring` reports no item under an older key, remove the old key from the key file. **Keep the old key until every backup that was made under it has expired**, or those backups become unreadable.
5. Rotate on a schedule the bank sets, immediately if a key may have been exposed (after revoking access, and understanding that anyone who copied the old key and an old copy of the data can still read that copy).

## 8 Backups and restore

* A backup of the data directory is a backup of **ciphertext**. It is only as useful as the key that opens it, so the key must be backed up separately and to a place with its own access control (section 5). A backup that contains both the data and the key is not encrypted in any useful sense.
* After restoring, start with the key that was active when the backup was made (or a keyring that still contains it). If the old database predates this feature, `init_schema()` adds the helper columns and fills them from plaintext rows on start; for encrypted rows run `encrypt_data.py --apply` (it also runs `Database.reindex()`).
* Restore into a scratch directory and read a case and a file back before you trust a backup procedure.

## 9 Threat model and honest limits

Protects against:

* theft or loss of the disk, the machine, or a decommissioned volume that was not wiped;
* theft or leak of a **backup, snapshot or copy** of the data directory, without the key;
* another local account or a restore-to-the-wrong-place incident reading the files without the key;
* leftovers of deleted cases: SQLite runs with `PRAGMA secure_delete=ON`, so delete-case and retention purge zero the rows they remove (including the readable applicant name and reference) instead of leaving them in free pages; deleted files and renders are unlinked, not wiped, so the volume's encryption is what covers their blocks;
* swapping, copying or splicing stored records between each other undetected (the context binding), and silent modification of a stored record or file (the tag).

Does **not** protect against:

* an attacker who has the **running process** (a code-execution bug, a debugger, a memory dump): the process holds the key and the plaintext while it works;
* an attacker who has the **key on the same host** (same account, root, the environment, the key file) together with the data;
* a malicious or compromised administrator or reviewer account: the application decrypts for every signed-in user, and there are no per-user keys;
* plaintext in **memory, swap and crash dumps** (decrypted files are held in memory while a case is analysed; nothing is written decrypted to the data directory; a library that insisted on a path would get a 0700 temporary directory under the system temp dir, removed in a `finally`, but none of the current libraries needs one);
* **data leaving the application**: page text and page images go to the configured model provider (a hosted model gets plaintext over TLS, see `docs/anthropic-haiku.md`); the case pack, downloads and API responses are plaintext to the signed-in user; the browser caches what it shows;
* the readable items in section 3, **rollback** of a record to an older valid version, **deletion**, and traffic analysis from sizes and timestamps;
* plaintext that existed **before** migration: old backups, snapshots, free blocks on the disk (see section 6 step 8), and log files from before the change;
* a lost key (permanent data loss) or a leaked key plus a stolen backup (full disclosure of that backup);
* weak key handling by the operator, and the absence of a hardware boundary: without a KMS/HSM the key is just a file.

So this is **defence in depth, not a replacement for the volume's encryption**. Keep FileVault (or the platform equivalent) on; this layer means the control no longer depends on it, and the bank can evidence it from the application. The monitoring page reports both: `disk_encryption` for the volume (only checkable on macOS) and `encryption_at_rest` for this layer.

## 10 Where stored files are read (audit)

Every consumer goes through decrypted bytes; none opens the stored path. `backend/tests/test_crypto_stores.py::test_no_production_code_opens_a_stored_file_by_path` fails if production code calls `path_for(` or `ctx.file_path(` outside the store, the context and one `exists()` check.

| Consumer | How it reads |
|---|---|
| `ingest/stage.py` (hash and magic re-check, PyMuPDF, pikepdf, Pillow) | `ctx.file_bytes()` then `pymupdf.open(stream=...)`, `pikepdf.open(BytesIO)`, `Image.open(BytesIO)` |
| `ingest/ocr.py`, `extract/llm_extractor.py`, `ingest/render.py` (model render, OCR input) | `ctx.file_bytes()` passed to `render_for_model(bytes, ...)`; Tesseract gets the PNG on stdin, no temp file |
| `classify/stage.py` (blank-page check) | `exists()` on the path, then `ctx.file_bytes()` into PyMuPDF |
| `checks/forensics_pdf.py`, `checks/forensics_image.py` | `ctx.file_bytes()` or a hashable `ctx.file_source()` handle for the `lru_cache`d analyses (the cache keys hold a hash, never bytes or a path) |
| `api/files.py` (page images, download) | `FileStore.read()`, `get_render()`, `put_render()` (the render cache is sealed) |
| `pack.py` (case pack page images) | `FileStore.read()` |
| `core/db.py` delete-case, `retention.py` purge | delete by sha through `FileStore.delete()`; "still used" comes from `case_files`; the cache rows to drop from `llm_cache.file_sha256` |
| `registry_live.py` register cache | `Vault.open()` / `seal()` per entry; an unreadable entry is a cache miss |
| `ingest/render.py::render_page_png` (UI render helper) | optional `vault`; seals its cache file |
| `FileStore.materialised()` | for a future library that needs a path: 0700 temp dir outside the data dir, removed in `finally` |

## 11 Monitoring

`GET /monitoring` carries `encryption_at_rest` beside `disk_encryption`:

* `mode`, `algorithm`, `active_key_id`, `key_ids` (ids only);
* `categories`: for files, renders and each sealed table, how many of a bounded sample (database tables: the oldest and newest 20 rows; files and renders: the first 40 found) are encrypted, plaintext, under an older key, or under a key that is not loaded (header reads only, no decryption, no content);
* `all_sampled_encrypted` (true, false, or null if nothing is stored yet), `status` (`off`, `on`, `on_with_plaintext`, `key_missing`, `error`) and a plain-language `message`. With the mode off it says so: *Application-level encryption is OFF ... rely on the volume's encryption.*
* `plaintext_by_design`: the list in section 3, so the page never overstates.

A sample can miss a few plaintext items in a large store; `encrypt_data.py` (dry run) is the exact count.

## 12 Tests

`backend/tests/test_crypto.py` (envelope, tamper, wrong key, context swap, rotation, key parsing, key-file permissions, startup validation, pluggable provider), `test_crypto_stores.py` (file store, render cache, database bodies, response cache, schema migration, delete/purge/review on encrypted stores, the path guard), `test_monitoring_encryption.py`, `e2e/test_encryption_pipeline.py` (a full API analysis of a generated KYC bundle with encryption on, then a scan of every file in the data directory and the raw SQLite bytes (including the WAL) for the bundle's NRIC/FIN numbers, UEN and company name; the same bundle with encryption off is the control that proves the scan can see them) and `e2e/test_encrypt_data_tool.py` (dry run, apply, idempotence, resume after a bad item, rotation, missing keys, tampering).
