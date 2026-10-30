"""CaseContext: everything a stage or a check may read (docs/data-model.md section 4.1).

``today`` is THE only source of "today" for checks; never call the wall clock inside a check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.costs import CostMeter
from app.core.fingerprints import FingerprintHit, FingerprintStore
from app.core.llm import LLMClient
from app.core.models import Applicant, Case, Document, FileRef, RuleOverride, UseCase, Word
from app.core.registry import MockRegistry
from app.core.storage import FileStore
from app.core.textlayout import layout_text

__all__ = ["CaseContext", "FingerprintHit", "FingerprintStore", "natural_key"]

_DIGITS = re.compile(r"(\d+)")


def natural_key(identifier: str) -> list[object]:
    """Sort key for ids such as ``d2`` < ``d10`` (numeric parts compare as numbers)."""
    return [int(p) if p.isdigit() else p for p in _DIGITS.split(identifier)]


@dataclass(kw_only=True)
class CaseContext:
    case: Case  # mutable only by stages; checks treat it as read-only
    today: date  # == case.today
    settings: Settings
    llm: LLMClient
    registry: MockRegistry | None
    fingerprints: FingerprintStore
    costs: CostMeter
    cache: ResponseCache
    page_words: dict[tuple[str, int], list[Word]]  # (file_id, page) -> words; runtime only
    # check catalogue overrides read once when the analysis starts (rule id -> override); empty
    # means every check runs with its code defaults
    overrides: dict[str, RuleOverride] = field(default_factory=dict)
    # semantic checks (docs/semantic-signals.md): rule id -> claim counts of this run, written by
    # ``app.semantic.call.run_claims`` and read by the check runner; empty while the feature is off
    semantic_tally: dict[str, Any] = field(default_factory=dict)
    # semantic features that hand a result from one stage to a later one (feature name -> state);
    # per run, never stored with the case, empty while the feature is off
    semantic_state: dict[str, Any] = field(default_factory=dict)

    @property
    def use_case(self) -> UseCase:
        return self.case.use_case

    @property
    def applicant(self) -> Applicant:
        return self.case.applicant

    def docs(self, *types: str) -> list[Document]:
        """Documents of those doc types, in document-id order (all documents if no type given)."""
        wanted = set(types)
        found = [d for d in self.case.documents if not wanted or d.doc_type in wanted]
        return sorted(found, key=lambda d: natural_key(d.id))

    def doc(self, document_id: str) -> Document:
        for d in self.case.documents:
            if d.id == document_id:
                return d
        raise KeyError(document_id)

    def file(self, file_id: str) -> FileRef:
        for f in self.case.files:
            if f.id == file_id:
                return f
        raise KeyError(file_id)

    def file_path(self, file_id: str) -> Path:
        """Content-addressed original on disk (``<data_dir>/files/<sha[:2]>/<sha256>``)."""
        return FileStore(self.settings.data_dir).path_for(self.file(file_id).sha256)

    def page_text(self, file_id: str, page: int) -> str:
        """Words of the page in reading order ('' when there is no text layer)."""
        words = self.page_words.get((file_id, page), [])
        return layout_text(words) if words else ""
