"""A live company lookup in Singapore's open ACRA dataset (data.gov.sg, "ACRA Information on Corporate
Entities"), so an applicant's UEN, name, status and incorporation date are checked against the real
register and not only against the generator's mock file.

What the open data holds: one row per entity (UEN, name, type, status, incorporation date, address,
SSIC). It does not hold shareholders or directors, so a live entity carries ``source="acra"`` and the
owner and director checks skip it (``x_entity_vs_registry`` uses it instead). The dataset is split by the
first letter of the entity name; the lookup asks the letter of the name on the documents first and only
scans the others when that misses.

A lookup answers one of three ways and never guesses: an entity, ``None`` (the register has no such
UEN), or :class:`RegistryUnavailable` (could not ask; the registry checks then skip and the case is
marked partial). Answers are cached on disk (a week for a hit, a day for a miss).
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.core.registry import MockRegistry, RegistryEntity
from app.core.textnorm import normalise_uen

log = logging.getLogger("gldb.registry")

API = "https://data.gov.sg/api/action/datastore_search"
USER_AGENT = "fraud-desk/0.1 (document-fraud research prototype)"
# dataset id per first letter of the entity name ("Others" holds names that start with a non-letter)
DATASETS: dict[str, str] = {
    "A": "d_8575e84912df3c28995b8e6e0e05205a",
    "B": "d_3a3807c023c61ddfba947dc069eb53f2",
    "C": "d_c0650f23e94c42e7a20921f4c5b75c24",
    "D": "d_acbc938ec77af18f94cecc4a7c9ec720",
    "E": "d_124a9bd407c7a25f8335b93b86e50fdd",
    "F": "d_4526d47d6714d3b052eed4a30b8b1ed6",
    "G": "d_b58303c68e9cf0d2ae93b73ffdbfbfa1",
    "H": "d_fa2ed456cf2b8597bb7e064b08fc3c7c",
    "I": "d_85518d970b8178975850457f60f1e738",
    "J": "d_478f45a9c541cbe679ca55d1cd2b970b",
    "K": "d_5573b0db0575db32190a2ad27919a7aa",
    "L": "d_a2141adf93ec2a3c2ec2837b78d6d46e",
    "M": "d_9af9317c646a1c881bb5591c91817cc6",
    "N": "d_67e99e6eabc4aad9b5d48663b579746a",
    "O": "d_5c4ef48b025fdfbc80056401f06e3df9",
    "Others": "d_300ddc8da4e8f7bdc1bfc62d0d99e2e7",
    "P": "d_181005ca270b45408b4cdfc954980ca2",
    "Q": "d_4130f1d9d365d9f1633536e959f62bb7",
    "R": "d_2b8c54b2a490d2fa36b925289e5d9572",
    "S": "d_df7d2d661c0c11a7c367c9ee4bf896c1",
    "T": "d_72f37e5c5d192951ddc5513c2b134482",
    "U": "d_0cc5f52a1f298b916f317800251057f3",
    "V": "d_e97e8e7fc55b85a38babf66b0fa46b73",
    "W": "d_af2042c77ffaf0db5d75561ce9ef5688",
    "X": "d_1cd970d8351b42be4a308d628a6dd9d3",
    "Y": "d_31af23fdb79119ed185c256f03cb5773",
    "Z": "d_4e3db8955fdcda6f9944097bef3d2724",
}
TIMEOUT_S = 12.0
RETRIES = 2
BACKOFF_S = 1.5
MAX_SCANNED = 27  # datasets asked per lookup, at most
HIT_TTL_S = 7 * 86400
MISS_TTL_S = 86400
TOTAL_BUDGET_S = 40.0  # for the scan of the other letters
PARALLEL = 4


class RegistryUnavailable(Exception):  # noqa: N818 - a state, not an error class
    """The register could not be asked (network, rate limit, bad answer)."""


class _Transient(Exception):  # noqa: N818 - retried, then reported as RegistryUnavailable
    pass


def dataset_order(name_hint: str | None) -> list[str]:
    """Dataset ids to ask, the letter of the name first, then the rest."""
    letter = next((c for c in (name_hint or "").strip().upper() if not c.isspace()), "")
    first = letter if letter in DATASETS else "Others"
    order = [first] + [k for k in DATASETS if k != first]
    return [DATASETS[k] for k in order][:MAX_SCANNED]


def _address(rec: dict[str, Any]) -> str | None:
    parts = [
        " ".join(p for p in (rec.get("block"), rec.get("street_name")) if p),
        " ".join(
            p
            for p in (
                f"#{rec['level_no']}-{rec['unit_no']}"
                if rec.get("level_no") and rec.get("unit_no")
                else "",
            )
            if p
        ),
        rec.get("building_name") or "",
        f"Singapore {rec['postal_code']}" if rec.get("postal_code") else "",
    ]
    text = ", ".join(p.strip() for p in parts if p and p.strip())
    return text or None


def entity_from_record(rec: dict[str, Any]) -> RegistryEntity | None:
    """One open-data row as a registry entity; None when the row lacks what an entity needs."""
    try:
        incorporated = date.fromisoformat(
            str(rec.get("registration_incorporation_date") or "")[:10]
        )
    except ValueError:
        return None
    uen, name = str(rec.get("uen") or "").strip(), str(rec.get("entity_name") or "").strip()
    if not uen or not name:
        return None
    activity = (
        rec.get("primary_user_described_activity")
        or rec.get("primary_ssic_description")
        or "not stated"
    )
    return RegistryEntity(
        uen=uen,
        entity_name=name,
        entity_type=str(rec.get("entity_type_description") or "").strip() or "not stated",
        status=str(rec.get("entity_status_description") or "").strip() or "not stated",
        incorporation_date=incorporated,
        registered_address=_address(rec),
        principal_activity=str(activity).strip(),
        ssic_code=str(rec.get("primary_ssic_code") or "").strip() or None,
        source="acra",
    )


def _ask(client: httpx.Client, dataset: str, uen: str) -> list[dict[str, Any]]:
    params = {"resource_id": dataset, "filters": json.dumps({"uen": uen}), "limit": "2"}
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = client.get(API, params=params)
            if resp.status_code in (400, 404):  # not transient: asking again changes nothing
                raise RegistryUnavailable(f"register answered {resp.status_code}") from None
            if resp.status_code in (403, 429, 500, 502, 503, 504):
                raise _Transient(f"register answered {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise RegistryUnavailable("register answered success=false")
            return list(data["result"]["records"])
        except RegistryUnavailable:
            raise
        except (httpx.HTTPError, ValueError, KeyError, _Transient) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(BACKOFF_S * (attempt + 1))
    raise RegistryUnavailable(f"{type(last).__name__}: {str(last)[:80]}")


class AcraLookup:
    def __init__(self, cache_dir: Path | None = None, client: httpx.Client | None = None):
        self.cache_dir = cache_dir
        self._client = client

    def _path(self, uen: str) -> Path | None:
        if self.cache_dir is None or not re.fullmatch(r"[0-9A-Z]{9,10}", uen):
            return None
        return self.cache_dir / f"acra-{uen}.json"

    def _cached(self, uen: str, now: float) -> tuple[bool, RegistryEntity | None]:
        path = self._path(uen)
        if path is None or not path.is_file():
            return False, None
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            record = blob["record"]
            ttl = HIT_TTL_S if record else MISS_TTL_S
            if now - float(blob["at"]) > ttl:
                return False, None
            return True, entity_from_record(record) if record else None
        except (OSError, ValueError, KeyError, TypeError):
            return False, None

    def _store(self, uen: str, record: dict[str, Any] | None, now: float) -> None:
        path = self._path(uen)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"at": now, "record": record}), encoding="utf-8")
        except OSError:
            log.warning("could not cache the register answer for a UEN")

    def lookup(self, uen: str, name_hint: str | None = None) -> RegistryEntity | None:
        """The entity with this UEN, None when the register has none; raises RegistryUnavailable."""
        wanted = normalise_uen(uen)
        if not wanted:
            return None
        now = time.time()
        hit, entity = self._cached(wanted, now)
        if hit:
            return entity
        own = self._client is None
        client = self._client or httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        try:
            order = dataset_order(name_hint)

            def find(dataset: str) -> dict[str, Any] | None:
                return next(
                    (
                        r
                        for r in _ask(client, dataset, wanted)
                        if normalise_uen(r.get("uen")) == wanted
                    ),
                    None,
                )

            # the letter of the name first (one call in the usual case), then the rest, a few at a time
            rec = find(order[0])
            if rec is None and len(order) > 1:
                with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
                    for found in pool.map(find, order[1:], timeout=TOTAL_BUDGET_S):
                        if found is not None:
                            rec = found
                            break
            if rec is not None:
                self._store(wanted, rec, now)
                return entity_from_record(rec)
        finally:
            if own:
                client.close()
        self._store(wanted, None, now)
        return None


def with_live_entity(
    registry: MockRegistry | None,
    uen: str | None,
    name_hint: str | None,
    today: date,
    lookup: AcraLookup,
) -> MockRegistry | None:
    """``registry`` plus the live entity for ``uen`` when the mock file does not hold it.

    Raises RegistryUnavailable when the register could not be asked, so the caller can say so."""
    if not uen or (registry is not None and registry.lookup(uen) is not None):
        return registry
    entity = lookup.lookup(uen, name_hint)
    entities = list(registry.entities) if registry is not None else []
    if entity is not None:
        entities.append(entity)
    return MockRegistry(
        schema_version=registry.schema_version if registry is not None else "1",
        generated_by="acra open data"
        if registry is None
        else f"{registry.generated_by} + acra open data",
        as_of=today,
        entities=entities,
    )
