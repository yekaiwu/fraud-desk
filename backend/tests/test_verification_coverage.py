"""Coverage and the fast lane with the verification sources (docs/verification-sources.md).

Wave 1 lesson: coverage is ran / listed, the fast lane needs >= 0.80, and six new receipt rules that were
listed for every bundle silently closed it. Here every new check is registered and a clean generated
bundle must keep its fast lane:

* not configured (the default) and blocked by private mode: the four source-backed checks are outside
  coverage, so the numbers are exactly those of a build without them, plus the one offline check that has
  something to read (``inv_bank_details_valid`` on an invoice);
* switched on and answered (faked): they run and add to both sides of the ratio;
* switched on and unreachable: they count as NOT run and the case is partial, never clear and never a flag.

Full pipeline on the generated clean loan bundle, offline (fake transports), semantic checks off.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.checks import registry as check_registry
from app.core.config import Settings
from app.sources import gleif as gleif_mod
from app.sources import onemap as onemap_mod
from scripts import update_sanctions as U
from scripts.red_team import attacks  # noqa: F401  (registers the attacks the runner imports)
from scripts.red_team.core import Runner, analyse

SOURCE_RULES = (
    "x_sanctions_screen",
    "x_entity_vs_gleif",
    "x_address_vs_registry",
    "inv_gst_registered",
)
NEW_RULES = (*SOURCE_RULES, "inv_bank_details_valid")


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    r = Runner(tmp_path_factory.mktemp("cov"))
    yield r
    r.close()


@pytest.fixture(scope="module")
def baseline(runner):  # noqa: ANN001, ANN201
    """The clean loan bundle as a build WITHOUT the five new checks sees it."""
    check_registry.discover()
    saved = {r: check_registry._REGISTRY.pop(r) for r in NEW_RULES}
    try:
        return analyse(runner.base("loan"), runner.work, "base-without")
    finally:
        check_registry._REGISTRY.update(saved)


def applicant(runner) -> dict:  # noqa: ANN001
    return runner.base("loan").manifest["applicant"]


def registry_entity(runner) -> dict:  # noqa: ANN001
    path = runner.base("loan").registry_path
    uen = applicant(runner)["uen"]
    return next(e for e in json.loads(path.read_text())["entities"] if e["uen"] == uen)


def fake_gleif(
    runner, monkeypatch: pytest.MonkeyPatch, *, record: bool = True, status: int = 200
) -> list:  # noqa: ANN001
    """GLEIF answers with a record that agrees with the documents (or none, or an error)."""
    seen: list = []
    ent = registry_entity(runner)
    postal = ent["registered_address"].rsplit(" ", 1)[-1]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if status != 200:
            return httpx.Response(status)
        if not record:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [{
            "id": "LEI0000000000000000X", "attributes": {"lei": "LEI0000000000000000X",
            "entity": {"legalName": {"name": ent["entity_name"]}, "registeredAs": ent["uen"], "jurisdiction": "SG",
                       "status": "ACTIVE", "legalAddress": {"postalCode": postal, "city": "Singapore", "country": "SG"}},
            "registration": {"status": "ISSUED"}}}]})  # fmt: skip

    monkeypatch.setattr(
        gleif_mod,
        "make_client",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return seen


def fake_onemap(runner, monkeypatch: pytest.MonkeyPatch) -> list:  # noqa: ANN001
    seen: list = []
    addr = registry_entity(runner)["registered_address"]
    street, postal = addr.split(", Singapore ")
    block, road = street.split(" #")[0].split(" ", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["searchVal"])
        row = {
            "BLK_NO": block,
            "ROAD_NAME": road.upper(),
            "POSTAL": postal,
            "ADDRESS": addr,
            "BUILDING": "",
        }
        found = [row] if request.url.params["searchVal"] == postal else []
        return httpx.Response(200, json={"found": len(found), "results": found})

    monkeypatch.setattr(
        onemap_mod,
        "make_client",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return seen


def fresh_snapshot(tmp: Path) -> Path:
    now = datetime.now(UTC).replace(microsecond=0)
    results = {s: (b"raw", [{"id": f"{s}:1", "list": s, "ref": "1", "kind": "person", "names": ["Nobody Listed Atall"],
                             "dob": [], "nationality": [], "ids": [], "programs": [], "listed_on": None, "weak_aliases": 0}])
               for s in ("un", "ofac", "eu", "uk")}  # fmt: skip
    U.write_snapshot(U.build_snapshot(results, None, now), tmp, now)
    return tmp / "current.json"


# ----------------------------------------------------------------------------------------


def test_unconfigured_new_checks_leave_the_clean_bundle_in_the_fast_lane(runner, baseline) -> None:  # noqa: ANN001
    rec = runner.control("loan")
    assert rec.lane == "fast" and rec.medhigh() == [] and not rec.partial
    assert rec.coverage_ran / rec.coverage_applicable >= 0.80
    # the four source-backed checks are outside the ratio; only the offline form check adds a run
    assert (
        rec.coverage_ran - baseline.coverage_ran,
        rec.coverage_applicable - baseline.coverage_applicable,
    ) == (1, 1)
    assert baseline.lane == "fast"


def test_every_source_check_is_registered_and_unconfigured_is_a_coded_skip(runner) -> None:  # noqa: ANN001
    check_registry.discover()
    for rule in NEW_RULES:
        assert check_registry.get_check(rule) is not None
    # listed only when their document is present: none of them is scoped to a use case
    for rule in NEW_RULES:
        assert (
            check_registry.get_check(rule).use_cases == frozenset() or rule == "x_sanctions_screen"
        )
    assert check_registry.get_check("x_sanctions_screen").use_cases == frozenset(
        {"kyc", "aml", "loan"}
    )


def test_gleif_and_onemap_switched_on_and_answered_keep_the_fast_lane(
    runner, baseline, monkeypatch
) -> None:  # noqa: ANN001
    g = fake_gleif(runner, monkeypatch)
    o = fake_onemap(runner, monkeypatch)
    rec = analyse(runner.base("loan"), runner.work, "gl-on", gleif="on", onemap_token="tok-test")
    assert g and o  # both were asked
    assert rec.lane == "fast" and rec.medhigh() == [] and not rec.partial
    assert rec.coverage_ran / rec.coverage_applicable >= 0.80
    # they ran, so they are on both sides of the ratio: +2 over the unconfigured run
    ctl = runner.control("loan")
    assert (
        rec.coverage_ran - ctl.coverage_ran,
        rec.coverage_applicable - ctl.coverage_applicable,
    ) == (2, 2)


def test_gleif_with_no_lei_is_normal_and_changes_nothing_else(runner, monkeypatch) -> None:  # noqa: ANN001
    fake_gleif(runner, monkeypatch, record=False)
    rec = analyse(runner.base("loan"), runner.work, "gl-none", gleif="on")
    assert rec.lane == "fast" and rec.medhigh() == [] and not rec.partial
    assert [f for f in rec.flags if f.rule == "x_entity_vs_gleif"] == []


def test_a_configured_source_that_cannot_be_reached_counts_as_not_run_and_marks_the_case_partial(
    runner, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr("app.sources.common.time.sleep", lambda s: None)
    fake_gleif(runner, monkeypatch, status=503)
    rec = analyse(runner.base("loan"), runner.work, "gl-down", gleif="on")
    ctl = runner.control("loan")
    assert rec.partial and rec.lane != "fast"  # never clear
    assert [f for f in rec.flags if f.rule == "x_entity_vs_gleif"] == []  # and never a flag
    assert (
        rec.coverage_applicable - ctl.coverage_applicable == 1
        and rec.coverage_ran == ctl.coverage_ran
    )


def test_private_mode_blocks_every_outbound_source_and_leaves_coverage_alone(
    runner, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(Settings, "private_mode", "on", raising=False)

    def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("a connection was opened in private mode")

    for mod in (gleif_mod, onemap_mod):
        monkeypatch.setattr(mod, "make_client", boom)
    monkeypatch.setattr("app.sources.iras.make_client", boom)
    rec = analyse(
        runner.base("loan"), runner.work, "private",
        gleif="on", onemap_token="tok-test", iras_api_key="key-test", registry_live=True,
    )  # fmt: skip
    ctl = runner.control("loan")
    assert rec.lane == "fast" and not rec.partial and rec.medhigh() == []
    # nothing lowered: the only change is that the existing live-register check (switched on here with
    # registry_live) is now a coded private-mode skip, so it leaves the denominator
    assert (rec.coverage_ran, rec.coverage_applicable) == (
        ctl.coverage_ran,
        ctl.coverage_applicable - 1,
    )


def test_sanctions_on_with_a_fresh_snapshot_keeps_the_fast_lane_and_a_stale_one_does_not(
    runner, tmp_path
) -> None:  # noqa: ANN001
    snap = fresh_snapshot(tmp_path)
    rec = analyse(
        runner.base("loan"), runner.work, "sanc-ok", sanctions_screen="on", sanctions_snapshot=snap
    )
    ctl = runner.control("loan")
    assert rec.lane == "fast" and rec.medhigh() == [] and not rec.partial
    assert (
        rec.coverage_ran - ctl.coverage_ran,
        rec.coverage_applicable - ctl.coverage_applicable,
    ) == (1, 1)
    # the same snapshot, a month later: not a clean result
    blob = json.loads(snap.read_text())
    for s in blob["sources"]:
        s["downloaded_at"] = "2026-09-01T00:00:00+00:00"
    old = tmp_path / "old.json"
    old.write_text(json.dumps(blob))
    stale = analyse(
        runner.base("loan"), runner.work, "sanc-old", sanctions_screen="on", sanctions_snapshot=old
    )
    assert stale.partial and stale.lane != "fast"
    missing = analyse(
        runner.base("loan"),
        runner.work,
        "sanc-none",
        sanctions_screen="on",
        sanctions_snapshot=tmp_path / "nope.json",
    )
    assert missing.partial and missing.lane != "fast"
