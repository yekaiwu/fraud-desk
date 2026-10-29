"""Mock ACRA-shaped registry (docs/data-model.md section 8).

The registry is the "truth"; fraudulent documents disagree with it. A real lookup can replace it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.core.models import Strict
from app.core.textnorm import company_key, normalise_uen


class RegistryOfficer(Strict):
    name: str
    id_number: str | None = None
    nationality: str | None = None
    position: str
    appointment_date: date | None = None
    cessation_date: date | None = None


class RegistryShareholder(Strict):
    name: str
    id_or_uen: str | None = None
    holder_type: Literal["individual", "company"]
    shares: int
    percent: str
    share_class: str | None = None


class RegistryEntity(Strict):
    uen: str
    entity_name: str
    entity_type: str
    status: str
    incorporation_date: date
    registered_address: str | None = None
    principal_activity: str
    ssic_code: str | None = None
    paid_up_capital: str | None = None
    currency: str | None = None
    officers: list[RegistryOfficer] = []
    shareholders: list[RegistryShareholder] = []


class MockRegistry(Strict):
    schema_version: str
    generated_by: str
    as_of: date
    entities: list[RegistryEntity]

    def lookup(self, uen: str | None) -> RegistryEntity | None:
        """Normalised exact match on UEN."""
        wanted = normalise_uen(uen)
        if not wanted:
            return None
        for entity in self.entities:
            if normalise_uen(entity.uen) == wanted:
                return entity
        return None

    def find_by_name(self, name: str) -> list[RegistryEntity]:
        """Normalised exact name match only (fuzzy matching lives in names.py)."""
        wanted = company_key(name)
        if not wanted:
            return []
        return [e for e in self.entities if company_key(e.entity_name) == wanted]


def load_registry(path: Path | str) -> MockRegistry | None:
    """None if the file does not exist; ValueError if it is malformed."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return MockRegistry.model_validate(data)
    except (json.JSONDecodeError, ValidationError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed registry file {p.name}: {str(exc)[:300]}") from exc
