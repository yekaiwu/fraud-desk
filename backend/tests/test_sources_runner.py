"""app.core.outbound, the source runner (``attach_sources``) and the private-mode switch point."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import pipeline
from app.checks.kyc import x_entity_vs_registry
from app.checks.registry import CheckSkipped
from app.core.config import Settings
from app.core.outbound import outbound_blocked
from app.sources import runner
from tests.test_checks_kyc_fixtures import APPLICANT, company_profile_doc
from tests.test_checks_verification import ctx as make
from tests.test_checks_verification import gst_inv, profile, settings
from tests.test_sources_adapters import ONEMAP_ROW, gleif_handler, mock


def private(s: Settings) -> Settings:
    object.__setattr__(s, "private_mode", "on")
    return s


def no_client() -> httpx.Client:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"a request was sent: {request.url.host}")

    return httpx.Client(transport=httpx.MockTransport(refuse))


# ------------------------------------------------------------------------ the switch point


def test_outbound_blocked_reads_private_mode_from_any_settings_object() -> None:
    assert outbound_blocked(SimpleNamespace(private_mode="on")) is not None
    assert "GLDB_PRIVATE_MODE" in outbound_blocked(SimpleNamespace(private_mode="on"))  # type: ignore[operator]
    assert outbound_blocked(SimpleNamespace(private_mode="off")) is None
    assert outbound_blocked(SimpleNamespace()) is None  # a build without the setting blocks nothing
    assert outbound_blocked(Settings(_env_file=None)) is None


def test_source_status_routes_every_network_source_through_the_switch(tmp_path: Path) -> None:
    s = settings(tmp_path, sanctions_screen="on", gleif="on", onemap_token="t", iras_api_key="k")
    assert [runner.source_status(s, n) for n in ("sanctions", "gleif", "onemap", "iras")] == [
        ("", "")
    ] * 4
    private(s)
    codes = {n: runner.source_status(s, n)[0] for n in ("sanctions", "gleif", "onemap", "iras")}
    assert codes == {
        "sanctions": "",
        "gleif": "private_mode",
        "onemap": "private_mode",
        "iras": "private_mode",
    }
    off = settings(tmp_path)
    assert {runner.source_status(off, n)[0] for n in ("sanctions", "gleif", "onemap", "iras")} == {
        "not_configured"
    }


# ------------------------------------------------------------------------------ the runner


def docs() -> list:
    return [profile("1 Raffles Place, Singapore 048616"), gst_inv()]


def test_nothing_is_asked_when_nothing_is_configured(tmp_path: Path) -> None:
    c = make(docs(), settings(tmp_path))
    assert (
        runner.attach_sources(
            c, clients={"gleif": no_client(), "onemap": no_client(), "iras": no_client()}
        )
        == []
    )
    assert c.sources == {}


def test_nothing_is_asked_in_private_mode_and_it_is_not_a_gap(tmp_path: Path) -> None:
    s = private(settings(tmp_path, gleif="on", onemap_token="t", iras_api_key="k"))
    c = make(docs(), s)
    clients = {"gleif": no_client(), "onemap": no_client(), "iras": no_client()}
    assert runner.attach_sources(c, clients=clients) == [] and c.sources == {}


def test_configured_sources_are_asked_once_and_answers_are_read_by_key(tmp_path: Path) -> None:
    seen: list = []

    def onemap(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"found": 1, "results": [{**ONEMAP_ROW, "POSTAL": "048616"}]}
        )

    def iras(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"returnCode": 10, "data": {"status": "Registered", "name": "ACME"}}
        )

    s = settings(tmp_path, gleif="on", onemap_token="t", iras_api_key="k")
    c = make(docs(), s)
    reasons = runner.attach_sources(
        c, clients={"gleif": mock(gleif_handler, seen), "onemap": mock(onemap), "iras": mock(iras)}
    )
    assert reasons == []
    assert c.sources["onemap:048616"].data[0].road == "AIRLINE ROAD"
    assert c.sources["iras:M2-1234567-8"].data.registered
    assert c.sources["gleif"].status == "ok" and seen


def test_an_unreachable_source_is_unavailable_and_makes_the_case_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.sources.common.time.sleep", lambda s: None)
    down = mock(lambda r: httpx.Response(503))
    s = settings(tmp_path, gleif="on", onemap_token="t", iras_api_key="k")
    c = make(docs(), s)
    reasons = runner.attach_sources(c, clients={"gleif": down, "onemap": down, "iras": down})
    assert {r.split(":")[0] for r in reasons} == {
        "gleif_unavailable",
        "onemap_unavailable",
        "iras_unavailable",
    }
    assert {o.status for o in c.sources.values()} == {"unavailable"}


def test_no_secret_is_logged_when_every_source_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.sources.common.time.sleep", lambda s: None)
    caplog.set_level(logging.DEBUG)
    s = settings(
        tmp_path,
        gleif="on",
        onemap_token="tok-SECRET-1",
        iras_api_key="key-SECRET-2",
        iras_api_secret="sec-SECRET-3",
    )
    c = make(docs(), s)
    down = mock(lambda r: httpx.Response(500, text="tok-SECRET-1 key-SECRET-2"))
    reasons = runner.attach_sources(c, clients={"gleif": down, "onemap": down, "iras": down})
    blob = "\n".join(
        [
            *reasons,
            *(o.reason for o in c.sources.values()),
            *(r.getMessage() for r in caplog.records),
        ]
    )
    assert "SECRET" not in blob


def test_sanctions_snapshot_states(tmp_path: Path) -> None:
    from tests.test_verification_coverage import fresh_snapshot

    snap = fresh_snapshot(tmp_path)
    s = settings(tmp_path, sanctions_screen="on", sanctions_snapshot=snap)
    c = make(docs(), s)
    assert runner.attach_sources(c) == [] and c.sources["sanctions"].reason == ""
    later = datetime.now(UTC) + timedelta(days=30)
    c = make(docs(), s)
    reasons = runner.attach_sources(c, now=later)
    assert (
        reasons
        and reasons[0].startswith("sanctions_incomplete")
        and "30 days" in c.sources["sanctions"].reason
    )
    c = make(
        docs(), settings(tmp_path, sanctions_screen="on", sanctions_snapshot=tmp_path / "none.json")
    )
    assert runner.attach_sources(c) == ["sanctions_unavailable: no usable snapshot"]
    assert c.sources["sanctions"].status == "unavailable"


def test_helpers_find_postal_codes_and_supplier_numbers() -> None:
    assert runner.postal_codes("4 Defu Lane #04-25, Singapore 992155") == ["992155"]
    assert runner.postal_codes("PO Box 123456 and Singapore 048616") == ["048616", "123456"]
    assert runner.postal_codes(None) == [] and runner.postal_codes("no code 12345") == []


# ---------------------------------------------------------- the existing live register (ACRA)


def test_the_live_register_is_not_asked_in_private_mode_and_its_check_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.registry_live as rl

    monkeypatch.setattr(rl, "AcraLookup", lambda *a, **k: pytest.fail("the register was asked"))
    s = private(settings(tmp_path, registry_live=True))
    c = make([company_profile_doc()], s)
    c.case.applicant = APPLICANT
    pipeline.attach_live_entity(c)
    assert c.sources["acra"].status == "blocked"
    with pytest.raises(CheckSkipped) as exc:
        x_entity_vs_registry(c)
    assert exc.value.code == "private_mode"
    # with the switch off the same call reaches AcraLookup
    c2 = make([company_profile_doc()], settings(tmp_path, registry_live=True))
    c2.case.applicant = APPLICANT
    with pytest.raises(pytest.fail.Exception, match="the register was asked"):
        pipeline.attach_live_entity(c2)
