"""GLEIF legal-entity lookup (api.gleif.org, no key): the LEI record of a company, found by its
registration number (the UEN, which GLEIF returns as ``registeredAs``) or, failing that, its legal name.

What it gives the desk: the registered legal name, the entity and LEI status, the legal address and, when
the record has them, the direct and ultimate parent as context. Most companies have no LEI, so "no record"
is normal and never a reason to doubt a company.

An answer is a :class:`GleifRecord`, ``None`` (GLEIF has no such record) or raises
:class:`~app.sources.common.SourceUnavailable`. Answers are cached on disk (a week for a record, a day for
"none").
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from typing import Any

import httpx

from app.core.crypto import Vault
from app.core.textnorm import normalise_uen
from app.sources.common import (
    AnswerCache,
    Deadline,
    SourceUnavailable,
    make_client,
    request_json,
)

API = "https://api.gleif.org/api/v1"
HEADERS = {"Accept": "application/vnd.api+json"}
JURISDICTION = "SG"


@dataclass(frozen=True)
class GleifParent:
    lei: str
    name: str


@dataclass(frozen=True)
class GleifRecord:
    lei: str
    legal_name: str
    entity_status: str  # ACTIVE, INACTIVE, ...
    registration_status: str  # ISSUED, LAPSED, ...
    registered_as: str | None
    jurisdiction: str | None
    address: str | None
    postal_code: str | None
    direct_parent: GleifParent | None = None
    ultimate_parent: GleifParent | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, blob: dict[str, Any]) -> GleifRecord:
        def parent(p: Any) -> GleifParent | None:
            return GleifParent(**p) if p else None

        return cls(
            **{
                **blob,
                "direct_parent": parent(blob.get("direct_parent")),
                "ultimate_parent": parent(blob.get("ultimate_parent")),
            }
        )


def _join(*parts: Any) -> str | None:
    text = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
    return text or None


def record_from_resource(res: dict[str, Any]) -> GleifRecord | None:
    """One JSON:API ``lei-records`` resource as a record; None when it lacks a LEI or a legal name."""
    try:
        attrs = res["attributes"]
        entity = attrs["entity"]
        name = str(entity["legalName"]["name"]).strip()
        lei = str(attrs.get("lei") or res.get("id") or "").strip()
    except (KeyError, TypeError):
        return None
    if not name or not lei:
        return None
    addr = entity.get("legalAddress") or {}
    registration = attrs.get("registration") or {}
    return GleifRecord(
        lei=lei,
        legal_name=name,
        entity_status=str(entity.get("status") or "").upper() or "UNKNOWN",
        registration_status=str(registration.get("status") or "").upper() or "UNKNOWN",
        registered_as=(str(entity["registeredAs"]).strip() or None)
        if entity.get("registeredAs")
        else None,
        jurisdiction=entity.get("jurisdiction"),
        address=_join(*(addr.get("addressLines") or []), addr.get("city"), addr.get("country")),
        postal_code=addr.get("postalCode"),
    )


def _lei_link(res: dict[str, Any], relation: str) -> str | None:
    try:
        link = res["relationships"][relation]["links"].get("lei-record")
    except (KeyError, TypeError, AttributeError):
        return None
    return link if isinstance(link, str) and link.startswith(API + "/") else None


class GleifLookup:
    def __init__(
        self,
        cache_dir=None,  # noqa: ANN001 - Path | None
        client: httpx.Client | None = None,
        vault: Vault | None = None,
        deadline: Deadline | None = None,
        base_url: str = API,
    ):
        self._cache = AnswerCache(cache_dir, "gleif", vault)
        self._client = client
        self._deadline = deadline
        self._base = base_url

    def _get(self, client: httpx.Client, url: str, **kw: Any) -> tuple[int, Any]:
        return request_json(client, "GET", url, deadline=self._deadline, headers=HEADERS, **kw)

    def _search(self, client: httpx.Client, params: dict[str, str]) -> list[dict[str, Any]]:
        _, body = self._get(client, f"{self._base}/lei-records", params=params)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise SourceUnavailable("answered something that is not a list of records")
        return data

    def _parent(self, client: httpx.Client, url: str | None) -> GleifParent | None:
        """Parents are context. A parent that cannot be fetched is simply not shown."""
        if not url:
            return None
        try:
            status, body = self._get(client, url, allow_404=True)
        except SourceUnavailable:
            return None
        rec = record_from_resource(body["data"]) if status == 200 and body.get("data") else None
        return GleifParent(rec.lei, rec.legal_name) if rec else None

    def lookup(self, uen: str | None, name: str | None = None) -> GleifRecord | None:
        wanted = normalise_uen(uen)
        label = (name or "").strip()
        if not wanted and not label:
            return None
        key = f"uen={wanted}|name={label.casefold()}"
        now = time.time()
        hit, blob = self._cache.get(key, now)
        if hit:
            return GleifRecord.from_json(blob) if blob else None
        own = self._client is None
        client = self._client or make_client()
        try:
            resources: list[dict[str, Any]] = []
            if wanted:
                resources = self._search(
                    client,
                    {
                        "filter[entity.registeredAs]": wanted,
                        "filter[entity.jurisdiction]": JURISDICTION,
                        "page[size]": "5",
                    },
                )
            if not resources and label:
                resources = [
                    r
                    for r in self._search(
                        client,
                        {
                            "filter[entity.legalName]": label,
                            "filter[entity.jurisdiction]": JURISDICTION,
                            "page[size]": "5",
                        },
                    )
                    # a record that names another registration number is another company
                    if not wanted
                    or not (r.get("attributes", {}).get("entity", {}).get("registeredAs"))
                    or normalise_uen(r["attributes"]["entity"]["registeredAs"]) == wanted
                ]
            record, resource = None, None
            for res in resources:
                record = record_from_resource(res)
                if record is not None:
                    resource = res
                    break
            if record is not None and resource is not None:
                record = replace(
                    record,
                    direct_parent=self._parent(client, _lei_link(resource, "direct-parent")),
                    ultimate_parent=self._parent(client, _lei_link(resource, "ultimate-parent")),
                )
        finally:
            if own:
                client.close()
        self._cache.put(key, record.to_json() if record else None, now)
        return record
