import re

import pytest

from app.core.textnorm import (
    COMPANY_MATCH_MIN,
    company_disc,
    company_key,
    company_keys,
    company_match,
    normalise_uen,
    uen_disc,
)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Example Logistics Pte. Ltd.", "EXAMPLE LOGISTICS PTE LTD", True),
        ("ACME LOGISTICS PTE LTD", "ACME LOGISTIC PTE. LTD.", True),
        ("ACME TRADING PTE LTD", "ACME TRADERS PTE LTD", False),
        ("Tom & Jerry Pte Ltd", "TOM AND JERRY PRIVATE LIMITED", True),
        ("ACME 1 PTE LTD", "ACME 2 PTE LTD", False),
        ("", "X", False),
        ("X", "", False),
        ("", "", False),
    ],
)
def test_reference_pairs(a: str, b: str, expected: bool) -> None:
    assert company_match(a, b) is expected


def test_trading_as_matches_either_name() -> None:
    assert company_keys("Alpha Pte Ltd T/A Beta Mart") == ["ALPHA", "BETA MART"]
    assert company_match("Alpha Pte Ltd T/A Beta Mart", "BETA MART")
    assert company_match("ALPHA PTE LTD", "Alpha Pte Ltd t/a Beta Mart")
    assert not company_match("Gamma Pte Ltd", "Alpha Pte Ltd T/A Beta Mart")


def test_key_strips_legal_forms_repeatedly_but_keeps_one_token() -> None:
    assert company_key("Acme Co. Pte. Ltd.") == "ACME"
    assert company_key("LTD") == "LTD"
    assert company_key("  ") == ""


def test_disc_forms() -> None:
    d = company_disc("Example Logistics Pte. Ltd.")
    assert d == company_disc("EXAMPLE LOGISTICS PTE LTD") == "example_logistics"
    assert re.fullmatch(r"[a-z0-9_]+", d)
    assert company_disc("") == ""
    assert company_disc("Zeta Alpha Pte Ltd") == "alpha_zeta"  # tokens sorted
    assert uen_disc("201912345R") == "uen_201912345r"
    assert uen_disc(" 2019-12345 R ") == "uen_201912345r"


def test_misc() -> None:
    assert COMPANY_MATCH_MIN == 0.92
    assert normalise_uen(" 2019 12345r ") == "201912345R"
    assert normalise_uen(None) == ""
