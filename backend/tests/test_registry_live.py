"""The live ACRA lookup (app/registry_live.py) and the checks built on it. No network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import registry_live as rl
from app.core.registry import MockRegistry, RegistryEntity

ROW = {
    "uen": "197200078R",
    "entity_name": "SINGAPORE AIRLINES LIMITED",
    "entity_type_description": "Local Company",
    "entity_status_description": "Live Company",
    "registration_incorporation_date": "1972-01-28",
    "primary_ssic_code": "51001",
    "primary_ssic_description": "SCHEDULED PASSENGER AIR TRANSPORT",
    "block": "25",
    "street_name": "AIRLINE ROAD",
    "postal_code": "819829",
}


def client(
    rows_by_dataset: dict[str, list[dict]], seen: list[str] | None = None, status: int = 200
):
    def handler(request: httpx.Request) -> httpx.Response:
        rid = request.url.params["resource_id"]
        if seen is not None:
            seen.append(rid)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(
            200, json={"success": True, "result": {"records": rows_by_dataset.get(rid, [])}}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)


def test_the_letter_of_the_name_is_asked_first() -> None:
    seen: list[str] = []
    c = client({rl.DATASETS["S"]: [ROW]}, seen)
    entity = rl.AcraLookup(client=c).lookup("197200078R", "Singapore Airlines Ltd")
    assert entity is not None and entity.entity_name == "SINGAPORE AIRLINES LIMITED"
    assert seen == [rl.DATASETS["S"]]  # one call, not twenty-seven
    assert entity.source == "acra" and entity.status == "Live Company"
    assert entity.registered_address == "25 AIRLINE ROAD, Singapore 819829"


def test_a_name_that_differs_from_the_register_still_finds_the_entity_by_scanning() -> None:
    c = client({rl.DATASETS["S"]: [ROW]})
    assert rl.AcraLookup(client=c).lookup("197200078R", "Zebra Pte Ltd") is not None


def test_no_such_uen_is_none_not_an_error() -> None:
    assert rl.AcraLookup(client=client({})).lookup("197200078R", "Singapore Airlines") is None


def test_rate_limits_and_bad_answers_mean_unavailable_never_not_found() -> None:
    with pytest.raises(rl.RegistryUnavailable):
        rl.AcraLookup(client=client({}, status=403)).lookup("197200078R", "S")


def test_answers_are_cached_on_disk(tmp_path: Path) -> None:
    seen: list[str] = []
    lookup = rl.AcraLookup(tmp_path, client({rl.DATASETS["S"]: [ROW]}, seen))
    lookup.lookup("197200078R", "Singapore Airlines")
    lookup.lookup("197200078R", "Singapore Airlines")
    assert len(seen) == 1
    assert json.loads((tmp_path / "acra-197200078R.json").read_text())["record"]["uen"]


def test_a_failed_lookup_is_not_cached_as_a_miss(tmp_path: Path) -> None:
    with pytest.raises(rl.RegistryUnavailable):
        rl.AcraLookup(tmp_path, client({}, status=429)).lookup("197200078R", "S")
    assert not list(tmp_path.glob("*.json"))


def test_rows_without_a_date_or_name_are_not_entities() -> None:
    assert rl.entity_from_record({**ROW, "registration_incorporation_date": ""}) is None
    assert rl.entity_from_record({**ROW, "entity_name": ""}) is None


def test_the_mock_file_wins_and_the_live_entity_is_added_otherwise() -> None:
    mock_entity = RegistryEntity(
        uen="201912345A",
        entity_name="X PTE. LTD.",
        entity_type="Local Company",
        status="Live",
        incorporation_date=date(2019, 1, 1),
        principal_activity="x",
    )
    mock = MockRegistry(
        schema_version="1", generated_by="t", as_of=date(2026, 1, 1), entities=[mock_entity]
    )
    seen: list[str] = []
    lookup = rl.AcraLookup(client=client({rl.DATASETS["S"]: [ROW]}, seen))
    same = rl.with_live_entity(mock, "201912345A", "X", date(2026, 11, 2), lookup)
    assert same is mock and not seen
    both = rl.with_live_entity(mock, "197200078R", "Singapore Airlines", date(2026, 11, 2), lookup)
    assert both is not None and both.lookup("197200078R").source == "acra"  # type: ignore[union-attr]
    assert both.lookup("201912345A") is not None and both.as_of == date(2026, 11, 2)


def test_a_client_error_is_not_retried() -> None:
    seen: list[str] = []
    with pytest.raises(rl.RegistryUnavailable):
        rl.AcraLookup(client=client({}, seen, status=404)).lookup("197200078R", "S")
    assert len(seen) == 1


def test_the_other_letters_are_asked_together_and_the_first_hit_wins() -> None:
    seen: list[str] = []
    c = client({rl.DATASETS["Q"]: [{**ROW, "uen": "197200078R"}]}, seen)
    entity = rl.AcraLookup(client=c).lookup(
        "197200078R", "Singapore Airlines"
    )  # S first, found in Q
    assert entity is not None and len(seen) == len(rl.DATASETS)


# ---- wrongly shaped answers, unreadable rows, a slow scan: the case goes partial, never to error


def raw_client(body: object, status: int = 200) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)))


@pytest.mark.parametrize(
    "body",
    [
        {"success": True, "result": None},
        {"success": True, "result": {"records": None}},
        {"success": True, "result": {"records": [None]}},
        {"success": True, "result": {"records": ["x"]}},
        {"success": True, "result": []},
        [],
        None,
    ],
)
def test_a_wrongly_shaped_answer_is_unavailable(body: object) -> None:
    with pytest.raises(rl.RegistryUnavailable):
        rl.AcraLookup(client=raw_client(body)).lookup("197200078R", "Singapore Airlines")


def test_a_matched_row_that_cannot_be_read_is_unavailable_and_not_cached(tmp_path: Path) -> None:
    bad = {**ROW, "registration_incorporation_date": "not a date"}
    c = client({rl.DATASETS["S"]: [bad]})
    with pytest.raises(rl.RegistryUnavailable):
        rl.AcraLookup(tmp_path, c).lookup("197200078R", "Singapore Airlines")
    assert not list(tmp_path.glob("*.json"))  # never "UEN not found", and never remembered


def test_an_unreadable_cached_row_is_asked_again(tmp_path: Path) -> None:
    import time

    bad = {**ROW, "registration_incorporation_date": ""}
    (tmp_path / "acra-197200078R.json").write_text(json.dumps({"at": time.time(), "record": bad}))
    entity = rl.AcraLookup(tmp_path, client({rl.DATASETS["S"]: [ROW]})).lookup(
        "197200078R", "Singapore Airlines"
    )
    assert entity is not None and entity.incorporation_date == date(1972, 1, 28)


def test_a_timeout_in_the_scan_of_the_other_letters_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.params["resource_id"] != rl.DATASETS["S"]:
            threading.Event().wait(0.3)  # (time.sleep is stubbed out by the autouse fixture)
        return httpx.Response(200, json={"success": True, "result": {"records": []}})

    monkeypatch.setattr(rl, "TOTAL_BUDGET_S", 0.05)
    c = httpx.Client(transport=httpx.MockTransport(slow))
    with pytest.raises(rl.RegistryUnavailable):
        rl.AcraLookup(client=c).lookup("197200078R", "Singapore Airlines")


def test_attach_live_entity_makes_the_case_partial_on_any_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.models import Applicant
    from app.core.testing import make_ctx
    from app.pipeline import attach_live_entity

    ctx = make_ctx([], applicant=Applicant(name="X", uen="197200078R", source="upload"))
    object.__setattr__(ctx.settings, "registry_live", True)

    def boom(*a: object, **k: object) -> None:
        raise TimeoutError("the scan ran out of time")

    monkeypatch.setattr(rl, "with_live_entity", boom)
    attach_live_entity(ctx)
    assert ctx.case.partial
    assert any(r.startswith("registry_unavailable") for r in ctx.case.partial_reasons)
