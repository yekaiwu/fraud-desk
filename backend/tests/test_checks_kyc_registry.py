"""x_ownership_vs_registry and x_directors (checks-kyc-aml.md 4.4, 4.5; tests 10-13)."""

from __future__ import annotations

import pytest

from app.checks.kyc import kyc_shareholdings, x_directors, x_ownership_vs_registry
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Document, Region
from app.names import entity_key
from tests.test_checks_kyc_fixtures import (
    LIM_ID,
    RAVI_ID,
    TAN_ID,
    chart_doc,
    company_profile_doc,
    ctx_for,
    national_id_doc,
    passport_doc,
    register_doc,
    registry_with,
)

LIM_KEY = entity_key("Lim Siew Hoon")
ALPHA = "Alpha Holdings Pte Ltd"
ALPHA_UEN = "202012345H"


def own(*docs: Document, registry=True):  # noqa: ANN001, ANN201
    return x_ownership_vs_registry(ctx_for(docs, registry=registry))


def directors(*docs: Document, registry=True):  # noqa: ANN001, ANN201
    return x_directors(ctx_for(docs, registry=registry))


def naive_chart() -> Document:
    return chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ],
    )


def careful_chart() -> Document:
    return chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "85.71", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "14.29", None, "false"),
        ],
    )


def careful_register() -> Document:
    return register_doc(
        "d2",
        [("Tan Wei Ming", TAN_ID, "300000", "85.71"), ("Ravi Kumar", RAVI_ID, "50000", "14.29")],
        total_shares="350000",
    )


# ------------------------------------------------------------------ clean


def test_matching_documents_have_no_flags() -> None:
    docs = [chart_doc("d3"), register_doc("d2"), company_profile_doc("d1")]
    assert own(*docs) == []


# ------------------------------------------------------------------ tests 10 and 11


def test_naive_forger_holder_missing_from_chart() -> None:
    flags = own(naive_chart())
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "high" and f.table == "holders" and f.row is None and f.field is None
    assert f.id == f"x_ownership_vs_registry.d3.-~{LIM_KEY}"
    assert f.evidence == (
        "registry holds Lim Siew Hoon 30.00% (S****222G); not on ownership chart d3 "
        "(2 holders, 70.00% printed)"
    )
    assert f.explanation.startswith("Lim Siew Hoon holds 30.00% per the registry but is missing")
    assert "S8222222G" not in f.evidence


def test_missing_holder_region_is_the_union_of_holder_rows() -> None:
    doc = naive_chart()
    for i, y in enumerate((0.30, 0.40)):
        doc.tables["holders"].rows[i].box = Region(
            file_id="f4", page=1, x=0.08, y=y, w=0.84, h=0.05
        )
    region = own(doc)[0].region
    assert region is not None and region.label == "holder missing here" and region.file_id == "f4"
    assert region.y == 0.30 and round(region.y + region.h, 2) == 0.45


def test_careful_forger_still_triggers_percent_and_missing() -> None:
    flags = own(careful_chart())
    ids = sorted(f.id for f in flags)
    assert ids == sorted(
        [
            f"x_ownership_vs_registry.d3.-~{LIM_KEY}",
            f"x_ownership_vs_registry.d3.holders:0~{entity_key('Tan Wei Ming')}",
            f"x_ownership_vs_registry.d3.holders:1~{entity_key('Ravi Kumar')}",
        ]
    )
    pct = next(f for f in flags if f.row == 0)
    assert pct.evidence == "chart 85.71%, registry 60.00% for Tan Wei Ming"


def test_same_holder_missing_on_chart_and_register_shares_one_discriminator() -> None:
    flags = own(careful_chart(), careful_register())
    missing = [f for f in flags if f.row is None]
    assert [f.document_ids for f in missing] == [["d3"], ["d2"]]
    assert {f.id.split("~")[1] for f in missing} == {LIM_KEY}
    assert any("shareholder register d2" in f.evidence for f in missing)


def test_one_missing_shareholder_is_one_finding_not_two() -> None:
    """The printed holders sum to 70% only because Lim Siew Hoon (30%) is missing: the sum flag
    and the missing-holder flag are one cause, so they share a finding (plan 8.4)."""
    from app.findings import group_flags

    ctx = ctx_for([naive_chart()], registry=True)
    flags = [*kyc_shareholdings(ctx), *x_ownership_vs_registry(ctx)]
    assert {f.rule for f in flags} == {"kyc_shareholdings", "x_ownership_vs_registry"}
    assert {f.id.split("~")[1] for f in flags} == {LIM_KEY}
    findings = group_flags(ctx.case, flags)
    assert len(findings) == 1 and findings[0].group_key == f"holder:{LIM_KEY}"
    assert findings[0].rules == ["kyc_shareholdings", "x_ownership_vs_registry"]


def test_a_sum_shortfall_the_registry_does_not_explain_keeps_its_own_finding() -> None:
    doc = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "50.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ],
    )
    sums = [f for f in kyc_shareholdings(ctx_for([doc], registry=True)) if "sum to" in f.evidence]
    assert len(sums) == 1 and "~" not in sums[0].id


def test_profile_shareholders_are_compared_too() -> None:
    profile = company_profile_doc(
        "d1", shareholders=[("Tan Wei Ming", TAN_ID, "60.00"), ("Ravi Kumar", RAVI_ID, "40.00")]
    )
    flags = own(profile)
    assert {f.id for f in flags} == {
        f"x_ownership_vs_registry.d1.-~{LIM_KEY}",
        f"x_ownership_vs_registry.d1.shareholders:1~{entity_key('Ravi Kumar')}",
    }
    assert "company profile d1" in flags[0].evidence or "company profile d1" in flags[1].evidence


def test_rounding_in_percent_is_tolerated() -> None:
    chart = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.0", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "30.004", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10", None, "false"),
        ],
    )
    for row, raw in zip(chart.tables["holders"].rows, ["60.0", "30.004", "10"], strict=True):
        row.cells["percent"] = Cell(value=row.cells["percent"].value, raw=raw, kind="percent")
    assert own(chart) == []


# ------------------------------------------------------------------ other comparison flags


def test_holder_not_in_registry() -> None:
    chart = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Lim Siew Hoon", "individual", LIM_ID, "30.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "5.00", None, "false"),
            ("Zed Ng", "individual", "S7654321C", "5.00", None, "false"),
        ],
    )
    flags = own(chart)
    ids = {f.id for f in flags}
    assert f"x_ownership_vs_registry.d3.holders:3~{entity_key('Zed Ng')}" in ids
    zed = next(f for f in flags if f.row == 3)
    assert (
        "Zed Ng 5.00% (S****321C) is on ownership chart d3 but not among the registry"
        in zed.evidence
    )
    assert len(flags) == 2  # plus Ravi 5.00 vs 10.00


def test_same_id_different_name() -> None:
    chart = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Lee Ah Kow", "individual", LIM_ID, "30.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ],
    )
    flags = own(chart)
    assert len(flags) == 1
    assert flags[0].evidence == "S****222G is 'Lim Siew Hoon' in the registry, 'Lee Ah Kow' here"
    assert flags[0].id.endswith(
        f"~{LIM_KEY}"
    )  # groups with the missing-holder flags of that person


def test_unknown_identity_never_creates_a_flag() -> None:
    chart = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
            ("Lee Ah Kow", "individual", "S****222G", "30.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ],
    )
    assert own(chart) == []


def test_masked_id_with_matching_name_pairs() -> None:
    chart = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", "S****567D", "60.00", None, "true"),
            ("Wong Lim Siew", "individual", None, "30.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ],
    )
    flags = own(chart)
    assert any("Lim Siew Hoon" in f.evidence for f in flags)  # 'Wong Lim Siew' is not Lim Siew Hoon
    chart2 = chart_doc(
        "d3",
        [
            ("Tan Wei Ming", "individual", "S****567D", "60.00", None, "true"),
            ("Siew Hoon Lim", "individual", None, "30.00", None, "true"),
            ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
        ],
    )
    assert own(chart2) == []


def test_name_order_does_not_matter_for_pairing() -> None:
    chart = chart_doc(
        "d3",
        [
            ("Wei Ming Tan", "individual", None, "60.00", None, "true"),
            ("Hoon Lim Siew", "individual", None, "30.00", None, "true"),
            ("Kumar Ravi", "individual", None, "10.00", None, "false"),
        ],
    )
    assert own(chart) == []


# ------------------------------------------------------------------ look-through (e)


def alpha_registry(children=None):  # noqa: ANN001, ANN201
    return registry_with(
        shareholders=[
            {
                "name": "Tan Wei Ming",
                "id_or_uen": TAN_ID,
                "holder_type": "individual",
                "shares": 300000,
                "percent": "60.00",
            },
            {
                "name": ALPHA,
                "id_or_uen": ALPHA_UEN,
                "holder_type": "company",
                "shares": 200000,
                "percent": "40.00",
            },
        ],
        extra_entities=[
            {
                "uen": ALPHA_UEN,
                "entity_name": ALPHA,
                "entity_type": "Private Company Limited by Shares",
                "status": "Live",
                "incorporation_date": "2018-01-01",
                "principal_activity": "Holding",
                "officers": [],
                "shareholders": children
                or [
                    {
                        "name": "Lim Siew Hoon",
                        "id_or_uen": LIM_ID,
                        "holder_type": "individual",
                        "shares": 50,
                        "percent": "50.00",
                    },
                    {
                        "name": "Ravi Kumar",
                        "id_or_uen": RAVI_ID,
                        "holder_type": "individual",
                        "shares": 50,
                        "percent": "50.00",
                    },
                ],
            }
        ],
    )


def alpha_chart(lim: str = "50.00", ravi: str | None = "50.00") -> Document:
    rows = [
        ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
        (ALPHA, "company", ALPHA_UEN, "40.00", None, None),
        ("Lim Siew Hoon", "individual", LIM_ID, lim, ALPHA, "false"),
    ]
    if ravi is not None:
        rows.append(("Ravi Kumar", "individual", RAVI_ID, ravi, ALPHA, "false"))
    return chart_doc("d3", rows)


def test_look_through_clean_chart_has_no_flags() -> None:
    assert own(alpha_chart(), registry=alpha_registry()) == []


def test_look_through_percent_and_missing_child() -> None:
    flags = own(alpha_chart(lim="70.00", ravi="30.00"), registry=alpha_registry())
    assert len(flags) == 2 and all(
        "chart 70.00%" in f.evidence or "chart 30.00%" in f.evidence for f in flags
    )
    missing = own(alpha_chart(lim="100.00", ravi=None), registry=alpha_registry())
    gone = [f for f in missing if f.row is None]
    assert len(gone) == 1 and "Ravi Kumar" in gone[0].evidence
    assert f"under '{ALPHA}'" in gone[0].evidence
    assert gone[0].id.endswith(f"~{entity_key('Ravi Kumar')}")


def test_look_through_skipped_when_the_holder_is_not_in_the_registry() -> None:
    reg = registry_with(
        shareholders=[
            {
                "name": "Tan Wei Ming",
                "id_or_uen": TAN_ID,
                "holder_type": "individual",
                "shares": 300000,
                "percent": "60.00",
            },
            {
                "name": ALPHA,
                "id_or_uen": ALPHA_UEN,
                "holder_type": "company",
                "shares": 200000,
                "percent": "40.00",
            },
        ]
    )
    assert own(alpha_chart(lim="70.00", ravi="30.00"), registry=reg) == []  # a stated gap


# ------------------------------------------------------------------ test 12


def test_applicant_absent_from_a_loaded_registry_is_one_high_flag() -> None:
    reg = registry_with(uen=ALPHA_UEN)
    chart = chart_doc("d3")
    chart.fields["uen"] = Cell(
        value="201912345R", kind="id", box=Region(file_id="f3", page=1, x=0.1, y=0.1, w=0.2, h=0.03)
    )
    flags = own(chart, register_doc("d2"), registry=reg)
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "high" and f.field == "uen" and f.document_ids == ["d2", "d3"]
    assert f.evidence == "applicant UEN 201912345R not found in registry (as of 2026-09-30)"
    assert f.region is not None and f.region.file_id == "f3"
    with pytest.raises(CheckSkipped, match="entity absent from registry"):
        directors(company_profile_doc("d1"), registry=reg)


def test_registry_unavailable_skips_both() -> None:
    with pytest.raises(CheckSkipped, match="registry unavailable"):
        own(chart_doc("d3"), registry=None)
    with pytest.raises(CheckSkipped, match="registry unavailable"):
        directors(company_profile_doc("d1"), registry=None)


def test_unknown_applicant_uen_skips() -> None:
    from app.core.models import Applicant

    ctx = ctx_for([chart_doc("d3")], applicant=Applicant(name="X"))
    with pytest.raises(CheckSkipped, match="applicant uen unknown"):
        x_ownership_vs_registry(ctx)
    with pytest.raises(CheckSkipped, match="applicant uen unknown"):
        x_directors(ctx_for([company_profile_doc("d1")], applicant=Applicant(name="X")))


# ------------------------------------------------------------------ test 13: x_directors


def ids() -> list[Document]:
    return [
        national_id_doc("d4", "Tan Wei Ming", TAN_ID, "1985-03-12"),
        national_id_doc("d5", "Ravi Kumar", RAVI_ID, "2004-02-02"),
    ]


def test_clean_directors_have_no_flags() -> None:
    assert directors(company_profile_doc("d1"), *ids()) == []


def test_profile_director_not_in_registry() -> None:
    profile = company_profile_doc(
        "d1",
        directors=[
            ("Tan Wei Ming", TAN_ID),
            ("Ravi Kumar", RAVI_ID),
            ("Tan Mei Ling", "S1111678C"),
        ],
    )
    flags = [f for f in directors(profile, *ids()) if f.severity != "info"]
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "high" and f.table == "directors" and f.row == 2
    assert f.evidence == (
        "director Tan Mei Ling (S****678C) is on the profile but not among the 2 current registry directors"
    )
    assert f.id == f"x_directors.d1.directors:2~{entity_key('Tan Mei Ling')}"


def test_fourth_director_with_an_id_card_gets_no_info_note() -> None:
    profile = company_profile_doc(
        "d1",
        directors=[
            ("Tan Wei Ming", TAN_ID),
            ("Ravi Kumar", RAVI_ID),
            ("Tan Mei Ling", "S1111678C"),
        ],
    )
    card = national_id_doc("d6", "Tan Mei Ling", "S1111678C", "1990-01-01")
    flags = directors(profile, *ids(), card)
    assert [f.severity for f in flags] == ["high"]


def test_registry_director_missing_from_profile() -> None:
    flags = directors(company_profile_doc("d1", directors=[("Tan Wei Ming", TAN_ID)]), *ids())
    high = [f for f in flags if f.severity == "high"]
    assert (
        len(high) == 1
        and high[0].evidence
        == "registry director Ravi Kumar (T****345J) is missing from the profile"
    )
    assert high[0].row is None and high[0].table == "directors"


def test_ceased_registry_directors_are_ignored() -> None:
    reg = registry_with(
        officers=[
            {"name": "Tan Wei Ming", "id_number": TAN_ID, "position": "Director"},
            {
                "name": "Ravi Kumar",
                "id_number": RAVI_ID,
                "position": "Director",
                "cessation_date": "2025-01-01",
            },
            {
                "name": "Future Leaver",
                "id_number": None,
                "position": "Director",
                "cessation_date": "2027-01-01",
            },
        ]
    )
    profile = company_profile_doc("d1", directors=[("Tan Wei Ming", TAN_ID)])
    flags = x_directors(ctx_for([profile, ids()[0]], registry=reg))
    assert [f.evidence for f in flags] == [
        "registry director Future Leaver is missing from the profile"
    ]


def test_profile_id_differs_from_the_id_document() -> None:
    profile = company_profile_doc(
        "d1", directors=[("Tan Wei Ming", "S1234567E"), ("Ravi Kumar", RAVI_ID)]
    )
    flags = [f for f in directors(profile, *ids()) if f.severity == "high"]
    conflict = [f for f in flags if "ends" in f.evidence]
    assert len(conflict) == 1
    assert conflict[0].evidence == "id number on profile ends 567E, on ID document d4 ends 567D"
    assert conflict[0].document_ids == ["d1", "d4"]


def test_same_id_belonging_to_a_different_name() -> None:
    other = national_id_doc("d4", "Lee Ah Kow", TAN_ID, "1985-03-12")
    flags = [
        f for f in directors(company_profile_doc("d1"), other, ids()[1]) if f.severity == "high"
    ]
    names = [f for f in flags if "belongs to a different name" in f.evidence]
    assert len(names) == 1
    assert (
        "S****567D belongs to a different name: profile 'Tan Wei Ming', ID document d4 'Lee Ah Kow'"
        in names[0].evidence
    )


def test_director_without_an_id_document_is_info() -> None:
    flags = directors(company_profile_doc("d1"), ids()[0])
    assert [(f.severity, f.evidence) for f in flags] == [
        ("info", "no ID document supplied for director Ravi Kumar")
    ]


def test_id_document_not_linked_to_anyone_is_info() -> None:
    guarantor = passport_doc("d6", holder_name="Chen Guang Ming")
    flags = directors(company_profile_doc("d1"), *ids(), guarantor)
    assert [(f.severity, f.document_ids) for f in flags] == [("info", ["d6"])]
    assert (
        flags[0].evidence
        == "ID of Chen Guang Ming is not linked to a director or owner (guarantor?)"
    )


def test_id_document_of_a_shareholder_is_linked() -> None:
    owner = national_id_doc("d6", "Lim Siew Hoon", LIM_ID, "1980-05-05")
    assert directors(company_profile_doc("d1"), *ids(), owner) == []


def test_no_directors_extracted_skips() -> None:
    with pytest.raises(CheckSkipped, match="no directors"):
        directors(company_profile_doc("d1", directors=[]))


def test_a_passport_number_never_contradicts_the_profile_nric() -> None:
    passport = passport_doc(
        "d6"
    )  # passport number K1234567D for Tan Wei Ming, profile says S1234567D
    flags = directors(company_profile_doc("d1"), *ids(), passport)
    assert flags == []
    imposter = passport_doc("d6", holder_name="Lee Ah Kow", passport_number=TAN_ID)
    names = [
        f for f in directors(company_profile_doc("d1"), *ids(), imposter) if f.severity == "high"
    ]
    assert len(names) == 1 and "belongs to a different name" in names[0].evidence


def test_unverified_director_values_are_noted() -> None:
    profile = company_profile_doc(
        "d1",
        directors=[
            ("Tan Wei Ming", TAN_ID),
            ("Ravi Kumar", RAVI_ID),
            ("Tan Mei Ling", "S1111678C"),
        ],
    )
    profile.tables["directors"].rows[2].cells["director_name"] = Cell(
        value="Tan Mei Ling", verified=False
    )
    high = [f for f in directors(profile, *ids()) if f.severity == "high"]
    assert high[0].evidence.endswith("; value not confirmed in the text layer")


def test_a_one_letter_name_slip_on_the_same_id_is_flagged_on_the_director() -> None:
    """A near-spelled name does not pass on similarity alone, even when the id number is equal."""
    profile = company_profile_doc(
        "d1", directors=[("Tan Wei Ming", TAN_ID), ("Rajesh Kumar", RAVI_ID)]
    )
    id_doc = national_id_doc("d4", "Ramesh Kumar", RAVI_ID, "1980-01-01")
    flags = directors(profile, id_doc)
    assert any(
        "belongs to a different name" in f.evidence and f.document_ids == ["d1", "d4"]
        for f in flags
    ), [f.evidence for f in flags]
