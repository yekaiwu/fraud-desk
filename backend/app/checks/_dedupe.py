"""Helpers for checks that count documents or compare a case with earlier ones.

``distinct_files`` collapses documents of byte-identical files (the same upload twice, a double click)
so that totals are not doubled and an invoice is not reported as a duplicate of its own copy; the repeat
is still reported by ``file_reuse``. ``same_applicant`` says whether an earlier case belonged to the
applicant of this one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core.context import CaseContext
from app.core.models import Document
from app.core.textnorm import company_key


def _norm_uen(value: str | None) -> str:
    return "".join((value or "").split()).upper()


def distinct_files(ctx: CaseContext, docs: Sequence[Document]) -> list[Document]:
    """``docs`` without those that come from a file whose bytes (sha256) an earlier file of the case
    already had. The earlier file (the order they were uploaded in) is the one kept."""
    first_by_sha: dict[str, str] = {}
    keep_file: dict[str, bool] = {}
    for f in ctx.case.files:
        first = first_by_sha.setdefault(f.sha256, f.id)
        keep_file[f.id] = first == f.id
    return [d for d in docs if keep_file.get(d.file_id, True)]


def same_applicant(ctx: CaseContext, meta: Mapping[str, Any]) -> bool | None:
    """Whether a fingerprint recorded with ``meta`` (``applicant_uen``, ``applicant_key``) came from
    this case's applicant: the same UEN, or else the same normalised company name. ``None`` when
    the earlier case recorded neither, so nothing can be said."""
    hit_uen = _norm_uen(str(meta.get("applicant_uen") or ""))
    mine = _norm_uen(ctx.applicant.uen)
    if hit_uen and mine:
        return hit_uen == mine
    hit_key = str(meta.get("applicant_key") or "")
    my_key = company_key(ctx.applicant.name or "")
    if hit_key and my_key:
        return hit_key == my_key
    return None
