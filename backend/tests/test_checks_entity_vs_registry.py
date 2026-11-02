"""x_entity_vs_registry: a document against the live company register."""

from __future__ import annotations

from datetime import date

import pytest

from app.checks.kyc import x_entity_vs_registry
from app.checks.registry import CheckSkipped
from app.core.registry import MockRegistry, RegistryEntity
from app.core.testing import make_doc
from tests.test_checks_kyc_fixtures import APPLICANT_NAME, UEN, ctx_for


def registry(source: str = "acra", status: str = "Live Company", name: str | None = None):  # noqa: ANN201
    entity = RegistryEntity(
        uen=UEN,
        entity_name=name or APPLICANT_NAME,
        entity_type="Local Company",
        status=status,
        incorporation_date=date(2019, 5, 6),
        principal_activity="x",
        source=source,  # type: ignore[arg-type]
    )
    return MockRegistry(
        schema_version="1", generated_by="t", as_of=date(2026, 11, 2), entities=[entity]
    )


def profile(name: str = APPLICANT_NAME, incorporated: str | None = "2019-05-06"):  # noqa: ANN201
    return make_doc(
        "company_profile",
        id="d1",
        fields={"company_name": name, "uen": UEN, "incorporation_date": incorporated},
    )


def run(doc, reg):  # noqa: ANN001, ANN201
    return x_entity_vs_registry(ctx_for([doc], registry=reg))


def test_a_matching_live_company_raises_nothing() -> None:
    assert run(profile(), registry()) == []


def test_a_different_name_is_flagged() -> None:
    flags = run(profile("Orchid Logistics Pte Ltd"), registry())
    assert [f.id.rsplit("~", 1)[-1] for f in flags] == ["name"] and flags[0].severity == "high"
    assert "Orchid" in flags[0].evidence


def test_a_different_incorporation_date_is_flagged() -> None:
    flags = run(profile(incorporated="2015-01-01"), registry())
    assert [f.id.rsplit("~", 1)[-1] for f in flags] == ["date"]
    assert "2019-05-06" in flags[0].evidence


@pytest.mark.parametrize("status", ["Struck Off", "Dissolved", "Cancelled", "In Liquidation"])
def test_a_company_that_is_not_live_is_flagged(status: str) -> None:
    flags = run(profile(), registry(status=status))
    assert [f.id.rsplit("~", 1)[-1] for f in flags] == ["status"]


@pytest.mark.parametrize("status", ["Live Company", "Live", "Registered"])
def test_live_statuses_pass(status: str) -> None:
    assert run(profile(), registry(status=status)) == []


def test_a_missing_date_or_a_slightly_different_legal_form_is_not_a_flag() -> None:
    assert run(profile(incorporated=None), registry()) == []
    assert run(profile(APPLICANT_NAME.replace("Pte Ltd", "Pte. Ltd.")), registry()) == []


def test_it_stays_out_of_the_mock_registry_and_a_missing_registry() -> None:
    with pytest.raises(CheckSkipped):
        run(profile("Orchid Logistics Pte Ltd"), registry(source="mock"))
    with pytest.raises(CheckSkipped):
        x_entity_vs_registry(ctx_for([profile()], registry=None))


def test_a_certificate_may_carry_the_name_the_company_had_when_it_was_incorporated() -> None:
    cert = make_doc(
        "certificate_of_incorporation",
        id="d1",
        fields={"company_name": "Old Name Pte Ltd", "uen": UEN, "incorporation_date": "2019-05-06"},
    )
    assert run(cert, registry()) == []  # renamed since; only the profile states the current name


def test_the_owner_and_director_checks_do_not_judge_a_live_entity() -> None:
    from app.checks.kyc import x_directors, x_ownership_vs_registry
    from tests.test_checks_kyc_fixtures import company_profile_doc

    ctx = ctx_for([company_profile_doc()], registry=registry())  # shareholders and officers unknown
    with pytest.raises(CheckSkipped):
        x_ownership_vs_registry(ctx)
    with pytest.raises(CheckSkipped):
        x_directors(ctx)
