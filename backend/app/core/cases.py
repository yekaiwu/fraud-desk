"""Helpers to build a new case and register uploaded files (used by the API and by scripts)."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date

from app.core.config import Settings
from app.core.models import Applicant, Case, FileRef, UseCase, utcnow
from app.core.storage import FileStore, sha256_bytes
from app.core.textnorm import normalise_uen

MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG", "image/png"),
)
MAX_NAME = 120


def sniff_mime(data: bytes) -> str | None:
    """Mime by magic bytes (``%PDF-``, JPEG ``FFD8FF``, PNG ``89504E47``), else ``None``."""
    for prefix, mime in MAGIC:
        if data.startswith(prefix):
            return mime
    return None


def sanitise_filename(name: str | None) -> str:
    """Display-only: basename, control characters stripped, max 120 chars. Never used as a path."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in unicodedata.normalize("NFKC", base) if ch.isprintable())
    base = base.strip().lstrip(".") or "upload"
    return base[:MAX_NAME]


def new_case_id() -> str:
    return "c_" + uuid.uuid4().hex[:12]


def new_case(
    settings: Settings,
    use_case: UseCase,
    *,
    reference: str | None = None,
    applicant_name: str | None = None,
    applicant_uen: str | None = None,
    today: date | None = None,
) -> Case:
    """A fresh ``processing`` case. ``today`` = argument, else ``GLDB_TODAY``, else the server date
    (the only wall-clock read for case dates; reruns reuse the stored value)."""
    now = utcnow()
    name = (applicant_name or "").strip()[:120] or None
    uen = normalise_uen(applicant_uen)[:20] or None
    return Case(
        id=new_case_id(),
        reference=(reference or "").strip()[:80] or None,
        use_case=use_case,
        status="processing",
        today=today or settings.today or now.date(),
        created_at=now,
        updated_at=now,
        applicant=Applicant(name=name, uen=uen, source="upload" if (name or uen) else "derived"),
    )


def next_file_id(case: Case) -> str:
    used = [int(f.id[1:]) for f in case.files if f.id[1:].isdigit()]
    return f"f{max(used, default=0) + 1}"


def add_file(case: Case, store: FileStore, *, name: str | None, data: bytes) -> FileRef:
    """Store bytes content-addressed and append a ``FileRef`` (mime from magic bytes).

    Raises ``ValueError`` for an unsupported type; the API turns that into 415."""
    mime = sniff_mime(data)
    if mime is None:
        raise ValueError("unsupported media type (not PDF, JPEG or PNG by magic bytes)")
    sha = store.put(data)
    assert sha == sha256_bytes(data)
    ref = FileRef(
        id=next_file_id(case),
        name=sanitise_filename(name),
        sha256=sha,
        size_bytes=len(data),
        mime=mime,  # type: ignore[arg-type]
        added_in_version=case.version,
    )
    case.files.append(ref)
    return ref
