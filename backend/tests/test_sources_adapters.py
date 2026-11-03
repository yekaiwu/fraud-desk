"""GLEIF, OneMap and IRAS adapters (app/sources) against fake transports. No network.

Each adapter: a result, ``None``/empty for "the source has no such thing", or SourceUnavailable; answers
cached on disk; rate limits and refusals are unavailable (never "not found"); no secret in any log line.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.sources import common, gleif, iras, onemap
from app.sources.common import Deadline, SourceUnavailable

SECRET = "tok-SECRET-9f8e7d6c"
UEN = "197200078R"

GLEIF_RECORD = {
    "type": "lei-records",
    "id": "549300ZG62N2599PWR15",
    "attributes": {
        "lei": "549300ZG62N2599PWR15",
        "entity": {
            "legalName": {"name": "SINGAPORE AIRLINES LIMITED"},
            "legalAddress": {
                "addressLines": ["25 AIRLINE ROAD AIRLINE HOUSE"],
                "city": "Singapore",
                "country": "SG",
                "postalCode": "819829",
            },
            "registeredAs": UEN,
            "jurisdiction": "SG",
            "status": "ACTIVE",
        },
        "registration": {"status": "ISSUED"},
    },
    "relationships": {
        "direct-parent": {
            "links": {"lei-record": "https://api.gleif.org/api/v1/lei-records/AAAA/direct-parent"}
        },
        "ultimate-parent": {"links": {"reporting-exception": "https://x/y"}},
    },
}
PARENT = {
    "type": "lei-records",
    "id": "AAAA",
    "attributes": {
        "lei": "AAAA",
        "entity": {"legalName": {"name": "TEMASEK HOLDINGS"}, "status": "ACTIVE"},
        "registration": {"status": "ISSUED"},
    },
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common.time, "sleep", lambda s: None)


def mock(handler, seen: list | None = None) -> httpx.Client:  # noqa: ANN001
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped))


def gleif_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/direct-parent"):
        return httpx.Response(200, json={"data": PARENT})
    params = request.url.params
    if params.get("filter[entity.registeredAs]") == UEN or "SINGAPORE AIRLINES" in params.get(
        "filter[entity.legalName]", ""
    ):
        return httpx.Response(200, json={"data": [GLEIF_RECORD]})
    return httpx.Response(200, json={"data": []})


# ------------------------------------------------------------------------------------ GLEIF


def test_gleif_finds_a_record_by_registered_number_with_parent_context() -> None:
    rec = gleif.GleifLookup(client=mock(gleif_handler)).lookup(UEN, "whatever")
    assert rec is not None and rec.lei == "549300ZG62N2599PWR15"
    assert rec.legal_name == "SINGAPORE AIRLINES LIMITED" and rec.entity_status == "ACTIVE"
    assert rec.registered_as == UEN and rec.postal_code == "819829"
    assert rec.direct_parent and rec.direct_parent.name == "TEMASEK HOLDINGS"
    assert rec.ultimate_parent is None  # only a reporting exception: no parent to show


def test_gleif_falls_back_to_the_legal_name_and_rejects_another_registration_number() -> None:
    assert gleif.GleifLookup(client=mock(gleif_handler)).lookup(None, "SINGAPORE AIRLINES LIMITED")
    other = {**GLEIF_RECORD, "attributes": {**GLEIF_RECORD["attributes"]}}
    other["attributes"]["entity"] = {
        **GLEIF_RECORD["attributes"]["entity"],
        "registeredAs": "201912345A",
    }

    def by_name(request: httpx.Request) -> httpx.Response:
        if "filter[entity.legalName]" in request.url.params:
            return httpx.Response(200, json={"data": [other]})
        return httpx.Response(200, json={"data": []})

    assert gleif.GleifLookup(client=mock(by_name)).lookup(UEN, "SINGAPORE AIRLINES LIMITED") is None


def test_gleif_no_record_is_none_not_an_error() -> None:
    assert (
        gleif.GleifLookup(client=mock(gleif_handler)).lookup("201912345A", "Nobody Pte Ltd") is None
    )


@pytest.mark.parametrize("status", [429, 500, 503, 401, 403, 400])
def test_gleif_failures_are_unavailable_never_not_found(status: int) -> None:
    with pytest.raises(SourceUnavailable):
        gleif.GleifLookup(client=mock(lambda r: httpx.Response(status))).lookup(UEN, "x")


def test_gleif_garbage_is_unavailable() -> None:
    with pytest.raises(SourceUnavailable):
        gleif.GleifLookup(client=mock(lambda r: httpx.Response(200, json={"oops": 1}))).lookup(UEN)
    with pytest.raises(SourceUnavailable):
        gleif.GleifLookup(client=mock(lambda r: httpx.Response(200, text="<html>"))).lookup(UEN)


def test_gleif_answers_are_cached_and_a_miss_is_cached_too(tmp_path: Path) -> None:
    seen: list = []
    lookup = gleif.GleifLookup(tmp_path, mock(gleif_handler, seen))
    first = lookup.lookup(UEN, "x")
    n = len(seen)
    assert lookup.lookup(UEN, "x") == first and len(seen) == n  # second call: no request
    lookup.lookup("201912345A", "Nobody")
    n = len(seen)
    assert lookup.lookup("201912345A", "Nobody") is None and len(seen) == n
    assert list(tmp_path.glob("gleif-*.json"))


def test_gleif_a_failed_lookup_is_not_cached(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailable):
        gleif.GleifLookup(tmp_path, mock(lambda r: httpx.Response(503))).lookup(UEN)
    assert not list(tmp_path.glob("*.json"))


def test_gleif_a_parent_that_cannot_be_fetched_is_just_not_shown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/direct-parent"):
            return httpx.Response(500)
        return gleif_handler(request)

    rec = gleif.GleifLookup(client=mock(handler)).lookup(UEN)
    assert rec is not None and rec.direct_parent is None


# ----------------------------------------------------------------------------------- OneMap

ONEMAP_ROW = {
    "SEARCHVAL": "AIRLINE HOUSE",
    "BLK_NO": "25",
    "ROAD_NAME": "AIRLINE ROAD",
    "BUILDING": "AIRLINE HOUSE",
    "ADDRESS": "25 AIRLINE ROAD AIRLINE HOUSE SINGAPORE 819829",
    "POSTAL": "819829",
}


def onemap_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("getToken"):
        body = json.loads(request.content)
        assert body["email"] == "me@example.com" and body["password"]
        return httpx.Response(200, json={"access_token": SECRET, "expiry_timestamp": "9999999999"})
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    postal = request.url.params["searchVal"]
    rows = [ONEMAP_ROW] if postal == "819829" else []
    return httpx.Response(200, json={"found": len(rows), "results": rows})


def test_onemap_finds_block_and_road_for_a_postal_code() -> None:
    recs = onemap.OneMapLookup(client=mock(onemap_handler), token=SECRET).lookup("819829")
    assert [(r.block, r.road) for r in recs] == [("25", "AIRLINE ROAD")]


def test_onemap_unknown_postal_code_is_empty_not_an_error() -> None:
    assert onemap.OneMapLookup(client=mock(onemap_handler), token=SECRET).lookup("000001") == []


def test_onemap_ignores_rows_of_another_postal_code_and_non_codes() -> None:
    other = {**ONEMAP_ROW, "POSTAL": "819830"}
    c = mock(lambda r: httpx.Response(200, json={"found": 1, "results": [other]}))
    assert onemap.OneMapLookup(client=c, token=SECRET).lookup("819829") == []
    assert onemap.OneMapLookup(client=c, token=SECRET).lookup("AIRLINE") == []


def test_onemap_token_is_fetched_from_email_and_password_and_reused() -> None:
    seen: list = []
    onemap._TOKENS.clear()
    lookup = onemap.OneMapLookup(
        client=mock(onemap_handler, seen), email="me@example.com", password="pw-SECRET"
    )
    assert lookup.configured
    lookup.lookup("819829")
    lookup.lookup("000001")
    assert sum(r.url.path.endswith("getToken") for r in seen) == 1


def test_onemap_an_expired_token_is_renewed_once() -> None:
    onemap._TOKENS.clear()
    calls = {"search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getToken"):
            return httpx.Response(
                200, json={"access_token": SECRET, "expiry_timestamp": "9999999999"}
            )
        calls["search"] += 1
        if calls["search"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"found": 1, "results": [ONEMAP_ROW]})

    lookup = onemap.OneMapLookup(client=mock(handler), email="me@example.com", password="pw")
    assert lookup.lookup("819829")


def test_onemap_a_static_token_that_is_refused_is_unavailable() -> None:
    with pytest.raises(SourceUnavailable):
        onemap.OneMapLookup(client=mock(lambda r: httpx.Response(401)), token="old").lookup(
            "819829"
        )


def test_onemap_no_token_is_unavailable_and_not_configured() -> None:
    lookup = onemap.OneMapLookup(client=mock(onemap_handler))
    assert not lookup.configured
    with pytest.raises(SourceUnavailable):
        lookup.lookup("819829")


def test_onemap_answers_are_cached(tmp_path: Path) -> None:
    seen: list = []
    lookup = onemap.OneMapLookup(tmp_path, mock(onemap_handler, seen), token=SECRET)
    lookup.lookup("819829")
    lookup.lookup("819829")
    lookup.lookup("000001")
    lookup.lookup("000001")
    assert len(seen) == 2
    assert SECRET not in "".join(
        p.read_text() for p in tmp_path.glob("*.json")
    )  # the token is never stored


# ------------------------------------------------------------------------------------- IRAS

IRAS_OK = {
    "returnCode": 10,
    "data": {
        "name": "ACME TRADING PTE. LTD.",
        "gstRegistrationNumber": "M2-1234567-8",
        "registrationDate": "2015-01-01",
        "RemovalDate": "",
        "status": "Registered",
    },
    "info": {},
}


def iras_client(body: dict, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith(
            "/GSTListing/SearchGSTRegistered"
        )
        assert request.headers["x-ibm-client-id"] == SECRET
        assert json.loads(request.content)["regID"] == "M2-1234567-8"
        return httpx.Response(200, json=body)

    return mock(handler, seen)


def test_iras_registered_supplier() -> None:
    status = iras.IrasLookup(SECRET, client=iras_client(IRAS_OK)).lookup("m2-1234567-8")
    assert (
        status.registered
        and status.name == "ACME TRADING PTE. LTD."
        and status.registered_from == "2015-01-01"
    )


def test_iras_not_registered_and_removed() -> None:
    body = {**IRAS_OK, "data": {**IRAS_OK["data"], "status": "Not Registered"}}
    assert not iras.IrasLookup(SECRET, client=iras_client(body)).lookup("M2-1234567-8").registered
    gone = {
        **IRAS_OK,
        "data": {**IRAS_OK["data"], "status": "Deregistered", "RemovalDate": "31/12/2020"},
    }
    s = iras.IrasLookup(SECRET, client=iras_client(gone)).lookup("M2-1234567-8")
    assert not s.registered and s.removed_on == "2020-12-31"


@pytest.mark.parametrize(
    "body",
    [
        {"returnCode": 20, "data": {}},
        {"returnCode": 10, "data": {"status": "Something New"}},
        {"returnCode": 10},
        ["not", "a", "dict"],
    ],
)
def test_iras_anything_it_cannot_read_is_unavailable_never_not_registered(body) -> None:  # noqa: ANN001
    with pytest.raises(SourceUnavailable):
        iras.IrasLookup(SECRET, client=iras_client(body)).lookup("M2-1234567-8")


def test_iras_rate_limit_and_refusal_are_unavailable() -> None:
    for status in (429, 403, 500):
        with pytest.raises(SourceUnavailable):
            iras.IrasLookup(SECRET, client=mock(lambda r, s=status: httpx.Response(s))).lookup(
                "M2-1234567-8"
            )


def test_iras_is_off_without_a_key_and_rejects_a_malformed_id() -> None:
    assert not iras.IrasLookup("", client=iras_client(IRAS_OK)).configured
    with pytest.raises(SourceUnavailable):
        iras.IrasLookup(SECRET, client=iras_client(IRAS_OK)).lookup("x")


def test_iras_answers_are_cached_and_the_key_is_not(tmp_path: Path) -> None:
    seen: list = []
    lookup = iras.IrasLookup(SECRET, cache_dir=tmp_path, client=iras_client(IRAS_OK, seen))
    lookup.lookup("M2-1234567-8")
    lookup.lookup("M2-1234567-8")
    assert len(seen) == 1
    assert SECRET not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


# ------------------------------------------------------------------------- shared behaviour


def test_the_time_budget_stops_further_calls() -> None:
    now = [0.0]
    deadline = Deadline(10, clock=lambda: now[0])
    now[0] = 11.0
    with pytest.raises(SourceUnavailable, match="time budget"):
        gleif.GleifLookup(client=mock(gleif_handler), deadline=deadline).lookup(UEN)


def test_every_request_carries_the_honest_user_agent() -> None:
    assert common.USER_AGENT.startswith("fraud-desk/")
    assert common.make_client().headers["user-agent"] == common.USER_AGENT


def test_no_secret_reaches_a_log_line_or_an_error_message(caplog: pytest.LogCaptureFixture) -> None:
    """Every failure path with a key, token and password set; nothing secret may be in what is logged
    or raised (the messages carry a status or an exception class, never a URL, header or body)."""
    caplog.set_level(logging.DEBUG)
    errors: list[str] = []
    for status in (401, 403, 429, 500):
        for lookup in (
            lambda s=status: iras.IrasLookup(
                SECRET, "SECRET-2", client=mock(lambda r: httpx.Response(s, text=SECRET))
            ).lookup("M2-1234567-8"),
            lambda s=status: onemap.OneMapLookup(
                client=mock(lambda r: httpx.Response(s, text=SECRET)), token=SECRET
            ).lookup("819829"),
            lambda s=status: onemap.OneMapLookup(
                client=mock(lambda r: httpx.Response(s, text=SECRET)),
                email="me@example.com",
                password="pw-SECRET",
            ).lookup("819829"),
        ):
            onemap._TOKENS.clear()
            with pytest.raises(SourceUnavailable) as exc:
                lookup()
            errors.append(str(exc.value))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {SECRET}")

    with pytest.raises(SourceUnavailable) as exc:
        iras.IrasLookup(SECRET, client=mock(boom)).lookup("M2-1234567-8")
    errors.append(str(exc.value))
    blob = "\n".join([*errors, *(r.getMessage() for r in caplog.records)])
    for secret in (SECRET, "SECRET-2", "pw-SECRET"):
        assert secret not in blob
