"""IRAS "Check GST Register" (IRAS API Marketplace, apiservices.iras.gov.sg): is a supplier's GST
registration number (or UEN) on the GST register?

STATUS: BUILT FROM THE PUBLIC DOCUMENTATION AND A FAKE TRANSPORT. The project has never called the live
service (it needs an API Marketplace account and key, and the access terms for a bank's use are not
confirmed). The request and response shapes below are our reading of the public listing and may differ
from the live service; anything this module cannot read with certainty is ``SourceUnavailable``, never a
"not registered". Disabled unless ``GLDB_IRAS_API_KEY`` is set.

Request: ``POST {base}/GSTListing/SearchGSTRegistered`` with headers ``X-IBM-Client-Id`` (the key) and
``X-IBM-Client-Secret`` and a JSON body ``{"clientID": <key>, "regID": <GST number, UEN or NRIC>}``.
Response: ``{"returnCode": 10, "data": {"name", "gstRegistrationNumber", "registrationDate", "RemovalDate",
"status", "Remarks"}, "info": {...}}``; ``returnCode`` 10 is a successful answer.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import httpx

from app.core.crypto import Vault
from app.sources.common import (
    AnswerCache,
    Deadline,
    SourceUnavailable,
    make_client,
    request_json,
)

DEFAULT_BASE = "https://apiservices.iras.gov.sg/iras/prod"
OK_CODE = 10
_REG_ID = re.compile(r"[0-9A-Z-]{6,20}")
_REGISTERED = ("registered",)
_NOT_REGISTERED = ("not registered", "deregistered", "de-registered", "removed", "cancelled")


@dataclass(frozen=True)
class GstStatus:
    reg_id: str  # what was asked
    registered: bool
    name: str | None
    gst_number: str | None
    registered_from: str | None  # ISO date
    removed_on: str | None  # ISO date

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _iso(value: Any) -> str | None:
    """A date in ISO or day/month/year form as ISO; None when it is not a date."""
    text = str(value or "").strip()[:10]
    if not text:
        return None
    for pattern in (r"(\d{4})-(\d{2})-(\d{2})", r"(\d{2})/(\d{2})/(\d{4})"):
        m = re.fullmatch(pattern, text)
        if m:
            parts = [int(g) for g in m.groups()]
            y, mo, d = parts if pattern.startswith(r"(\d{4})") else (parts[2], parts[1], parts[0])
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                return None
    return None


def parse_answer(reg_id: str, body: Any) -> GstStatus:
    """The status in a response body; ``SourceUnavailable`` unless it is unambiguous."""
    if not isinstance(body, dict) or body.get("returnCode") != OK_CODE:
        raise SourceUnavailable("the answer is not a successful register answer")
    data = body.get("data")
    if not isinstance(data, dict):
        raise SourceUnavailable("the answer has no register data")
    status = str(data.get("status") or "").strip().lower()
    removed = _iso(data.get("RemovalDate") or data.get("removalDate"))
    if status in _REGISTERED:
        registered = True
    elif status in _NOT_REGISTERED:
        registered = False
    else:
        raise SourceUnavailable("the register status is not one this reader knows")
    return GstStatus(
        reg_id=reg_id,
        registered=registered,
        name=(str(data.get("name") or "").strip() or None),
        gst_number=(str(data.get("gstRegistrationNumber") or "").strip() or None),
        registered_from=_iso(data.get("registrationDate")),
        removed_on=removed,
    )


class IrasLookup:
    def __init__(
        self,
        api_key: str,
        api_secret: str = "",
        base_url: str = DEFAULT_BASE,
        cache_dir=None,  # noqa: ANN001 - Path | None
        client: httpx.Client | None = None,
        vault: Vault | None = None,
        deadline: Deadline | None = None,
    ):
        self._key, self._secret = api_key.strip(), api_secret.strip()
        self._base = base_url.rstrip("/")
        self._cache = AnswerCache(cache_dir, "iras", vault)
        self._client = client
        self._deadline = deadline

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def lookup(self, reg_id: str) -> GstStatus:
        """The register's answer for a GST number or UEN; raises SourceUnavailable when it cannot be had."""
        wanted = "".join((reg_id or "").split()).upper()
        if not _REG_ID.fullmatch(wanted):
            raise SourceUnavailable("not a GST number or UEN")
        now = time.time()
        hit, blob = self._cache.get(wanted, now)
        if hit and blob:
            return GstStatus(**blob)
        own = self._client is None
        client = self._client or make_client()
        headers = {"X-IBM-Client-Id": self._key, "Content-Type": "application/json"}
        if self._secret:
            headers["X-IBM-Client-Secret"] = self._secret
        try:
            _, body = request_json(
                client,
                "POST",
                f"{self._base}/GSTListing/SearchGSTRegistered",
                deadline=self._deadline,
                headers=headers,
                json={"clientID": self._key, "regID": wanted},
            )
        finally:
            if own:
                client.close()
        status = parse_answer(wanted, body)
        self._cache.put(wanted, status.to_json(), now)
        return status
