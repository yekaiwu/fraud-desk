"""OneMap (Singapore Land Authority) postal-code lookup: does this postal code exist, and which block
and road does it belong to? Used to check that the address printed on a company document is a real one
and fits its postal code.

OneMap needs a token (free, from developers.onemap.sg, valid for a few days). Either ``GLDB_ONEMAP_TOKEN``
is set, or ``GLDB_ONEMAP_EMAIL`` and ``GLDB_ONEMAP_PASSWORD`` are, and the token is then fetched and
renewed here (kept in memory only, never written to disk or to a log). No token = the source is off.

An answer is a :class:`PostalRecord` list (all the places that share the code), ``[]`` (OneMap knows no such
code) or :class:`~app.sources.common.SourceUnavailable`. Answers are cached on disk.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass
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

SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"
TOKEN_URL = "https://www.onemap.gov.sg/api/auth/post/getToken"
POSTAL_RE = re.compile(r"^\d{6}$")
TOKEN_REFRESH_MARGIN_S = 300
_TOKENS: dict[
    str, tuple[str, float]
] = {}  # sha of the account -> (token, expiry epoch); memory only
_TOKENS_LOCK = threading.Lock()


@dataclass(frozen=True)
class PostalRecord:
    postal: str
    block: str
    road: str
    building: str
    address: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


def _records(body: Any, postal: str) -> list[PostalRecord]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise SourceUnavailable("answered something that is not a result list")
    out: list[PostalRecord] = []
    for row in body["results"]:
        if not isinstance(row, dict) or str(row.get("POSTAL") or "").strip() != postal:
            continue  # a search is free text; only a row with this very postal code counts
        out.append(
            PostalRecord(
                postal=postal,
                block=str(row.get("BLK_NO") or "").strip(),
                road=str(row.get("ROAD_NAME") or "").strip(),
                building=str(row.get("BUILDING") or "").strip(),
                address=str(row.get("ADDRESS") or "").strip(),
            )
        )
    return out


class OneMapLookup:
    def __init__(
        self,
        cache_dir=None,  # noqa: ANN001 - Path | None
        client: httpx.Client | None = None,
        vault: Vault | None = None,
        deadline: Deadline | None = None,
        token: str = "",
        email: str = "",
        password: str = "",
    ):
        self._cache = AnswerCache(cache_dir, "onemap", vault)
        self._client = client
        self._deadline = deadline
        self._token = token.strip()
        self._email, self._password = email.strip(), password
        self._account = self._email

    @property
    def configured(self) -> bool:
        return bool(self._token or (self._email and self._password))

    def _fetch_token(self, client: httpx.Client) -> str:
        _, body = request_json(
            client,
            "POST",
            TOKEN_URL,
            deadline=self._deadline,
            json={"email": self._email, "password": self._password},
        )
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise SourceUnavailable("the token request returned no token")
        try:
            expiry = float(body.get("expiry_timestamp"))
        except (TypeError, ValueError):
            expiry = time.time() + 3600
        with _TOKENS_LOCK:
            _TOKENS[self._account] = (token, expiry)
        return token

    def _bearer(self, client: httpx.Client, *, renew: bool = False) -> str:
        if self._email and self._password:
            with _TOKENS_LOCK:
                cached = _TOKENS.get(self._account)
            if cached and not renew and cached[1] - time.time() > TOKEN_REFRESH_MARGIN_S:
                token = cached[0]
            else:
                token = self._fetch_token(client)
        elif self._token:
            token = self._token
        else:
            raise SourceUnavailable("no OneMap token is configured")
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    def lookup(self, postal: str) -> list[PostalRecord]:
        postal = postal.strip()
        if not POSTAL_RE.fullmatch(postal):
            return []
        now = time.time()
        hit, blob = self._cache.get(postal, now)
        if hit:
            return [PostalRecord(**r) for r in blob or []]
        own = self._client is None
        client = self._client or make_client()
        params = {"searchVal": postal, "returnGeom": "N", "getAddrDetails": "Y", "pageNum": "1"}
        try:
            try:
                _, body = request_json(
                    client,
                    "GET",
                    SEARCH_URL,
                    deadline=self._deadline,
                    params=params,
                    headers={"Authorization": self._bearer(client)},
                )
            except SourceUnavailable as exc:
                if "refused" not in str(exc) or not (self._email and self._password):
                    raise
                # a token that expired early: fetch a new one once, then ask again
                _, body = request_json(
                    client,
                    "GET",
                    SEARCH_URL,
                    deadline=self._deadline,
                    params=params,
                    headers={"Authorization": self._bearer(client, renew=True)},
                )
        finally:
            if own:
                client.close()
        records = _records(body, postal)
        self._cache.put(postal, [r.to_json() for r in records] or None, now)
        return records
