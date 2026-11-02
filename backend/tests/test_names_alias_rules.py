"""Alias rules added with the name-fairness work (docs/name-fairness.md): spacing splits of long
names and the table of surname variants. Each rule is tested for what it accepts and for what it must
keep rejecting."""

from __future__ import annotations

import pytest
from rapidfuzz.distance import Levenshtein

from app.names import (
    MERGE_MAX_TOKEN_LEN,
    SURNAME_VARIANTS,
    TOKEN_ALIASES,
    identity_status,
    match_person,
    normalise_person,
    same_identity,
)

NRIC = "S1234567D"


def accepted(a: str, b: str) -> bool:
    return same_identity(a, NRIC, b, NRIC) == "match"


# ------------------------------------------------------------------------ spacing of long names


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Brajakishore Tripathy", "Braja Kishore Tripathy"),
        ("Kommanapalli Ganapathi Rao", "Kommanapalli Ganapathirao"),
        ("Jogendra Nath Mandal", "Jogendranath Mandal"),
        ("Bojjala Gopala Krishna Reddy", "Bojjala Gopalakrishna Reddy"),
        ("Shafiqur Rahman Barq", "Shafiqurrahman Barq"),
        ("Hnialum Lal Ruat Feli", "Hnialum Lalruatfeli"),
        ("Ram Chandra Dome", "Ramchandra Dome"),
    ],
)
def test_a_name_written_joined_or_split_is_the_same_name(a: str, b: str) -> None:
    assert match_person(a, b).verdict == "same"
    assert accepted(a, b) and accepted(b, a)


def test_the_merge_limit_is_twelve_letters() -> None:
    assert MERGE_MAX_TOKEN_LEN == 12
    # a token longer than the limit is not joined to its neighbour
    long_a = "Thirunavukkarasu Pillai"  # 16 + 6
    long_b = "Thirunavukkarasupillai"
    assert match_person(long_a, long_b).verdict != "same"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Ram Chandra Dome", "Ramchander Dome"),  # a letter differs as well as the spacing
        ("Gopi Narayan Pradhan", "Gopinarain Pradhan"),
        ("Jogendra Nath Mandal", "Jogendranath Mandel"),
    ],
)
def test_joining_does_not_hide_a_letter_that_differs(a: str, b: str) -> None:
    assert not accepted(a, b)


def test_father_and_son_in_the_other_order_stay_different_people() -> None:
    assert match_person("Ahmad bin Ismail", "Ismail bin Ahmad").verdict == "different"
    assert not accepted("Mohd Faizal bin Rahmat", "Rahmat bin Mohd Faizal")


# ------------------------------------------------------------------------ surname variants table


def test_the_table_never_puts_two_names_one_letter_apart_in_one_group() -> None:
    members = [m for group in SURNAME_VARIANTS for m in group]
    assert len(members) == len(set(members)), "a spelling is in two groups"
    for group in SURNAME_VARIANTS:
        assert len(group) >= 2
        for i, x in enumerate(group):
            for y in group[i + 1 :]:
                assert Levenshtein.distance(x, y) >= 2, (x, y)


def test_the_table_does_not_touch_other_alias_rules_or_common_different_surnames() -> None:
    members = {m for group in SURNAME_VARIANTS for m in group}
    others = {k for k, v in TOKEN_ALIASES.items() if k not in members and v not in members}
    assert not members & others
    for common in (
        "lim",
        "lin",
        "tan",
        "chen",
        "lee",
        "li",
        "ng",
        "wong",
        "kumar",
        "singh",
        "khan",
    ):
        assert common not in members


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Mamata Banerjee", "Mamata Bandyopadhyay"),
        ("Hiranmoy Chatterjee", "Hiranmoy Chattopadhyay"),
        ("Hirendranath Mukherjee", "Hirendranath Mukhopadhyay"),
        ("Shahid Kapoor", "Shahid Kapur"),
        ("Sheila Dikshit", "Sheila Dixit"),
        ("Sukhamoy Chakraborty", "Sukhamoy Chakravarty"),
        ("Ayan Bhattacharya", "Ayan Bhattacharjee"),
        ("Bhabani Charan Pattanayak", "Bhabani Charan Pattnaik"),
        ("Laxman Singh", "Lakshman Singh"),
    ],
)
def test_a_documented_surname_variant_is_the_same_name(a: str, b: str) -> None:
    assert match_person(a, b).verdict == "same"
    assert accepted(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Mamata Banerjee", "Mamata Banerji"),  # not in the table: still flagged
        ("Shahid Kapoor", "Shahid Kapor"),
        ("Hiranmoy Chatterjee", "Hiranmoy Chatterji"),
        ("Sheila Dikshit", "Sheila Dikshet"),
        ("Mamata Banerjee", "Mamata Bannerjee"),
    ],
)
def test_a_small_change_next_to_a_variant_is_still_flagged(a: str, b: str) -> None:
    # a conflict with the same ID number: flagged, whether read as a slip or as a different name
    assert identity_status(a, NRIC, b, NRIC).match == "match_name_conflict"


def test_a_different_first_name_is_not_rescued_by_a_variant_surname() -> None:
    assert not accepted("Mamata Banerjee", "Mahua Bandyopadhyay")
    assert not accepted("Shahid Kapoor", "Shakti Kapur")


def test_entity_key_is_the_same_for_the_two_spellings_of_a_variant() -> None:
    from app.names import entity_key

    assert entity_key("Mamata Banerjee") == entity_key("Mamata Bandyopadhyay")
    assert normalise_person("Kapur").tokens == normalise_person("Kapoor").tokens
