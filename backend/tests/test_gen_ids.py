"""Identifiers, names and the counterparty lexicon (T-gen-loan-02)."""

from __future__ import annotations

import random
from datetime import date

import pytest
from stdnum.sg import uen as sg_uen

from scripts._gen import ids, lexicon, names_data

NRIC_VECTORS = ["S1234567D", "T0812345J", "F1234567N", "G1234567X", "S8222222G", "S0000001I"]


@pytest.mark.parametrize("nric", NRIC_VECTORS)
def test_pinned_nric_vectors_are_reproduced(nric: str) -> None:
    assert ids.make_nric(nric[0], nric[1:8]) == nric
    assert ids.is_valid_nric(nric)
    assert not ids.is_valid_nric(nric[:-1] + ("A" if nric[-1] != "A" else "B"))


def test_nric_prefix_follows_birth_year() -> None:
    assert ids.nric_prefix_for_birth(1999) == "S"
    assert ids.nric_prefix_for_birth(2000) == "T"
    assert ids.nric_prefix_for_birth(1980, foreign=True) == "F"
    assert ids.nric_prefix_for_birth(2005, foreign=True) == "G"
    rng = random.Random("nric")
    for by in (1960, 1999, 2000, 2004):
        v = ids.random_nric(rng, by)
        assert ids.is_valid_nric(v) and v[0] == ("S" if by < 2000 else "T")


def test_generated_uens_validate_with_python_stdnum() -> None:
    rng = random.Random("uen")
    for _ in range(200):
        uen = ids.random_uen(rng, rng.randint(1995, 2024))
        assert sg_uen.is_valid(uen), uen
        assert len(uen) == 10 and uen[:9].isdigit()
    assert ids.make_uen(2019, 12345) == "201912345R"


def test_gst_number_format() -> None:
    rng = random.Random("gst")
    for _ in range(50):
        g = ids.random_gst_no(rng)
        assert ids.is_gst_format(g) and g[0] == "M" and len(g) == 10
    assert ids.is_gst_format("M21234567K")
    assert not ids.is_gst_format("X21234567K")


def test_mrz_vectors_icao_specimen() -> None:
    assert ids.mrz_check_digit("L898902C3") == "6"
    assert ids.mrz_check_digit("740812") == "2"
    assert ids.mrz_check_digit("120415") == "9"
    specimen = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
    assert ids.mrz_composite(specimen) == "0"
    assert ids.mrz_line2_valid(specimen)
    assert not ids.mrz_line2_valid(specimen[:9] + "7" + specimen[10:])


def test_build_td3_round_trip() -> None:
    l1, l2 = ids.build_td3(
        issuing="UTO", surname="Eriksson", given_names="Anna Maria", passport_no="L898902C3",
        nationality="UTO", dob=date(1974, 8, 12), sex="F", expiry=date(2012, 4, 15), personal_no="ZE184226B",
    )  # fmt: skip
    assert len(l1) == len(l2) == 44
    assert l1.startswith("P<UTOERIKSSON<<ANNA<MARIA")
    assert l2 == "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


def test_names_cover_all_groups_and_patronymics() -> None:
    rng = random.Random("names")
    seen = {}
    for g in names_data.NAME_GROUPS:
        for sex in "MF":
            seen[(g, sex)] = names_data.make_name(rng, g, sex)
    assert " bin " in " " + seen[("malay_patronymic", "M")] + " "
    assert any(w in seen[("malay_patronymic", "F")].split() for w in ("binte", "binti"))
    assert "s/o" in seen[("indian_patronymic", "M")] and "d/o" in seen[("indian_patronymic", "F")]
    assert len(seen[("mononym", "M")].split()) == 1
    assert names_data.split_title(seen[("titles", "M")])[0] != ""
    assert seen[("chinese_order", "F")].split()[0] in names_data.CHINESE_SURNAMES


def test_registry_form_drops_title_and_uppercases() -> None:
    assert names_data.registry_form("Dr Lim Hui Min", "titles", 0) == "LIM HUI MIN"
    assert (
        names_data.registry_form("Mohd Faizal bin Rahmat", "malay_patronymic", 0)
        == "MOHD FAIZAL BIN RAHMAT"
    )
    assert (
        names_data.registry_form("Mohd Faizal bin Rahmat", "malay_patronymic", 1)
        == "MOHD FAIZAL B. RAHMAT"
    )


def test_company_names_are_unique() -> None:
    names = [names_data.company_name(i) for i in range(1, 300)]
    assert len(set(names)) == len(names)
    assert all(n.endswith("Pte. Ltd.") for n in names)


def test_lexicon_tokens_and_hard_names() -> None:
    rng = random.Random("lex")
    used: set[str] = set()
    for label, tokens in lexicon.C5_TOKENS.items():
        name = lexicon.make_counterparty(rng, label, used)
        assert any(t.upper() in name for t in tokens)
        assert lexicon.label_from_tokens(name) == label
    for _ in range(40):
        hard = lexicon.make_hard_counterparty(rng, used)
        assert lexicon.label_from_tokens(hard) is None
    assert lexicon.proper("ACME TRADING PTE LTD") == "Acme Trading Pte. Ltd."
