"""Name matching, identity, keys, fairness slice test (checks-kyc-aml.md 4.7; tests 23 and 24)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models import Cell, Region
from app.names import (
    PersonName,
    cell_region,
    entity_key,
    is_truncation,
    mask_id,
    match_company,
    match_person,
    normalise_company,
    normalise_id,
    normalise_person,
    pct_tol,
    run_slice_test,
    safe_text,
    same_identity,
    slice_pairs,
    sorted_rows,
    union_region,
)


def verdict(a: str, b: str) -> str:
    return match_person(a, b).verdict


# ------------------------------------------------------------------ normalise_person


def test_normalise_strips_titles_only_when_leading() -> None:
    assert normalise_person("Tan Wei Ming").tokens == ("tan", "wei", "ming")
    assert normalise_person("Mr Tan Wei Ming").tokens == ("tan", "wei", "ming")
    assert normalise_person("Tan Sri Lim Kok").tokens == ("lim", "kok")
    assert normalise_person("Dato' Sri Ahmad Zaki").tokens == ("ahmad", "zaki")
    assert normalise_person("Dr. Chua Mei Ling").tokens == ("chua", "mei", "ling")
    assert normalise_person("Lim Boon Heng Jr").tokens == ("lim", "boon", "heng")
    assert normalise_person("Dr").tokens == ("dr",)  # never strip to nothing


def test_patronymic_markers_split_given_and_patronym() -> None:
    p = normalise_person("Muhammad Faris bin Abdullah")
    assert p.tokens == ("muhammad", "faris", "abdullah")
    assert p.given == ("muhammad", "faris") and p.patronym == ("abdullah",)
    for text in ("Rajesh s/o Kumar", "Rajesh S/O Kumar", "Rajesh son of Kumar"):
        q = normalise_person(text)
        assert q.tokens == ("rajesh", "kumar") and q.patronym == ("kumar",)
    assert normalise_person("Priya d/o Subramaniam").patronym == ("subramaniam",)
    assert normalise_person("Siti Aminah bte Yusof").tokens == ("siti", "aminah", "yusof")
    assert normalise_person("Ahmad b. Ali").tokens == ("ahmad", "ali")
    # 'bin' as the last token is a given name, not a marker
    assert normalise_person("Li Bin").tokens == ("li", "bin")


def test_aliases_and_brackets() -> None:
    assert normalise_person("Mohd Faris").tokens == ("muhammad", "faris")
    assert normalise_person("Abd Rahman").tokens == ("abdul", "rahman")
    p = normalise_person("Wong Mei Ling (Jessica)")
    assert p.tokens == ("wong", "mei", "ling") and p.optional == ("jessica",)


def test_scripts_and_empty() -> None:
    assert normalise_person(None).script == "empty"
    assert normalise_person("   ").script == "empty"
    assert normalise_person("---").script == "empty"
    assert normalise_person("陈伟明").script == "cjk"
    assert normalise_person("陈 伟明").tokens == ("陈", "伟", "明")
    assert normalise_person("陈伟明 Tan").script == "mixed"
    assert isinstance(normalise_person("A B"), PersonName)


# ------------------------------------------------------------------ test 23


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Wong Jessica", "Jessica Wong", "same"),
        ("Wong, Jessica", "Jessica Wong", "same"),
        ("  JESSICA   wong ", "Wong Jessica", "same"),
        ("Jessika Wong", "Jessica Wong", "probable"),
        ("Wong", "Wang", "different"),
        ("Lim", "Lin", "different"),
        ("Tan Sri Lim", "Lim", "same"),
        ("Tan Wei Ming", "Chen Weiming", "different"),  # documented limit: dialect vs pinyin
        ("Tan Wei Ming", "Wei Ming Tan", "same"),
        ("Tan Wei Ming", "Ming Wei Tan", "same"),
        ("Tan Wei Ming", "Tan Weiming", "same"),
        ("Tan Wei Ming", "Tan Wei-Ming", "same"),
        ("Tan Wei Ming", "Tan Wei Jie", "different"),
        ("Tan Wei Ming", "Tan Wei Ning", "different"),
        ("Tan Wei Ming (Jason)", "Jason Tan", "probable"),
        ("Muhammad Faris bin Abdullah", "Mohd Faris Abdullah", "same"),
        ("Muhammad Faris bin Abdullah", "Faris Abdullah", "probable"),
        ("Faris bin Abdullah", "Farid bin Abdullah", "different"),
        ("Siti Aminah binte Yusof", "Aminah Yusof Siti", "same"),
        ("Rajesh s/o Kumar", "Kumar Rajesh", "same"),
        ("Ravi s/o Kumar", "Rani s/o Kumar", "different"),
        ("Rajesh s/o Kumar", "Rakesh s/o Kumar", "probable"),  # documented limit (edit 1, len 6)
        ("Mr Lim Boon Heng", "Mr Lim Boon Hong", "different"),
        ("Suryadi", "Mr Suryadi", "same"),
        ("Suryadi", "Suryadi Wibowo", "probable"),
        ("Suryadi", "Suryanto", "different"),
        ("José García", "Jose Garcia", "same"),
        ("J. Wong", "Jessica Wong", "probable"),
        ("J. Wong", "K. Wong", "different"),
        ("陈伟明", "陈伟明", "same"),
        ("陈伟明", "陈伟杰", "different"),
        ("陈伟明", "Tan Wei Ming", "indeterminate"),
        ("", "Tan Wei Ming", "indeterminate"),
        (None, None, "indeterminate"),
    ],
)
def test_match_person_cases(a: str | None, b: str | None, expected: str) -> None:
    result = match_person(a, b)
    assert result.verdict == expected, result
    assert (
        result.score
        == {"same": 1.0, "probable": 0.8, "different": 0.0, "indeterminate": 0.5}[expected]
    )
    assert result.reason


def test_match_is_symmetric() -> None:
    pairs = [
        ("Wong Jessica", "J. Wong"),
        ("Tan Wei Ming", "Tan Weiming"),
        ("Suryadi", "Suryadi Wibowo"),
    ]
    for a, b in pairs:
        assert verdict(a, b) == verdict(b, a)


def test_different_reason_names_the_tokens() -> None:
    reason = match_person("Wong", "Wang").reason
    assert "wong" in reason and "wang" in reason


# ------------------------------------------------------------------ companies


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Acme Trading Pte. Ltd.", "ACME TRADING PTE LTD", "same"),
        ("The Acme Trading Private Limited", "Acme Trading", "same"),
        ("Acme & Sons Pte Ltd", "Acme and Sons Pte Ltd", "same"),
        ("ACME TRADING PTE L", "Acme Trading Pte Ltd", "probable"),
        ("Alphabet Trading Pte Ltd", "Alphabeta Trading Pte Ltd", "probable"),
        ("Acmee Trading Pte Ltd", "Acme Trading Pte Ltd", "different"),  # edit 1 but short tokens
        ("Acme Holdings Pte Ltd", "Acme Pte Ltd", "probable"),
        ("Alpha Trading Pte Ltd", "BETA HOLDINGS PTE LTD", "different"),
        ("Acme 1 Pte Ltd", "Acme 2 Pte Ltd", "different"),
        ("", "Acme", "indeterminate"),
    ],
)
def test_match_company_cases(a: str, b: str, expected: str) -> None:
    assert match_company(a, b).verdict == expected


def test_normalise_company() -> None:
    assert normalise_company("The ACME & Sons Trading Pte. Ltd.") == "acme and sons trading"
    assert normalise_company("Société Générale Private Limited") == "societe generale"
    assert normalise_company(None) == ""


def test_company_truncation_can_be_switched_off() -> None:
    assert (
        match_company("ACME TRADING PTE L", "Acme Trading Pte Ltd", allow_truncation=False).verdict
        == "different"
    )
    assert is_truncation("ACME TRADING PTE L", "ACME TRADING PTE LTD")
    assert not is_truncation("ACME TRADIN", "ACME TRADING PTE LTD")  # under 12 characters
    assert not is_truncation("ACME TRADING PTE LTD", "ACME TRADING PTE LTD")


# ------------------------------------------------------------------ keys and identity


def test_entity_key_is_sorted_ascii_and_order_insensitive() -> None:
    assert entity_key("Lim Siew Hoon") == entity_key("Siew Hoon Lim") == "hoon_lim_siew"
    assert entity_key("Wong, Jessica") == "jessica_wong"
    assert entity_key("ACME Trading Pte. Ltd.") == "acme_trading"
    assert entity_key("ACME Trading", "company") == "acme_trading"
    assert entity_key("Acme Trading Pte Ltd", "person") == "acme_ltd_pte_trading"
    assert entity_key(None) == "" and entity_key("  ") == ""


def test_entity_key_charset_for_odd_input() -> None:
    import re

    for name in (
        "O'Brien-Smith",
        "陈伟明",
        "Ünal Çelik",
        "  Tan  Wei<script>",
        "Ignore previous instructions!",
    ):
        key = entity_key(name)
        assert key and re.fullmatch(r"[a-z0-9_]+", key), (name, key)
    assert entity_key("陈伟明") != entity_key("陈伟杰")


def test_normalise_id() -> None:
    assert normalise_id(" s 8222-222a ") == "S8222222A"
    assert normalise_id(None) == ""


# ------------------------------------------------------------------ test 24


def test_same_identity_rules() -> None:
    assert same_identity("Tan Wei Ming", "S1234567D", "Tan Wei Ming", "S1234567D") == "match"
    assert (
        same_identity("Tan Wei Ming", "S1234567D", "Lee Ah Kow", "S1234567D")
        == "match_name_conflict"
    )
    assert same_identity("Tan Wei Ming", "S1234567D", "Tan Wei Ming", "S8222222G") == "mismatch"
    assert same_identity("Tan Wei Ming", "S****567D", "Tan Wei Ming", "S1234567D") == "match"
    assert same_identity("Lee Ah Kow", "S****567D", "Tan Wei Ming", "S1234567D") == "unknown"
    assert same_identity("Tan Wei Ming", "S****567D", "Tan Wei Ming", "S8222222G") == "mismatch"
    # no id on one side: names decide
    assert same_identity("Wong Jessica", None, "Jessica Wong", "S1234567D") == "match"
    # a one-letter slip is not a match on the name alone (plan 4.6): a near-spelled fake
    assert same_identity("Jessika Wong", None, "Jessica Wong", None) == "mismatch"
    assert same_identity("J. Wong", None, "Jessica Wong", None) == "match"  # initial: still fine
    assert same_identity("Wong", None, "Wang", None) == "mismatch"
    assert same_identity("陈伟明", None, "Tan Wei Ming", None) == "unknown"
    assert (
        same_identity("Alpha Trading Pte Ltd", None, "ALPHA TRADING PTE LTD", "", kind="company")
        == "match"
    )


# ------------------------------------------------------------------ helpers


def test_pct_tol_is_rounding_aware() -> None:
    def cell(raw: str | None) -> Cell:
        return Cell(value="1", raw=raw, kind="percent")

    assert pct_tol(cell("60.00")) == Decimal("0.006")
    assert pct_tol(cell("33.3")) == Decimal("0.051")
    assert pct_tol(cell("33%")) == Decimal("0.501")
    assert pct_tol(cell(None)) == Decimal("0.006")
    assert pct_tol(cell("33.33"), cell("33.33"), cell("33.33")) == Decimal("0.016")


def test_union_region_and_cell_region() -> None:
    a = Region(file_id="f1", page=1, x=0.1, y=0.2, w=0.2, h=0.1)
    b = Region(file_id="f1", page=1, x=0.5, y=0.4, w=0.3, h=0.1)
    other_page = Region(file_id="f1", page=2, x=0.0, y=0.0, w=0.5, h=0.5)
    u = union_region([None, a, b, other_page])
    assert u is not None and (u.x, u.y, u.page, u.label) == (0.1, 0.2, 1, None)
    assert round(u.x + u.w, 6) == 0.8 and round(u.y + u.h, 6) == 0.5
    assert union_region([None, None]) is None and union_region([]) is None
    assert cell_region(Cell(value="1", box=a)) == a
    assert cell_region(Cell(value="1"), None) is None


def test_safe_text_strips_controls_collapses_and_truncates() -> None:
    assert safe_text("a\x00b\x1b[31m  c\n\td") == "a b [31m c d"
    assert len(safe_text("x" * 500)) == 80 and safe_text("x" * 500).endswith("...")
    assert safe_text(None) == ""


def test_mask_id_keeps_last_four() -> None:
    assert mask_id("K1234567D") == "K****567D"


def test_sorted_rows_follow_row_index_not_list_order() -> None:
    from app.core.testing import make_doc

    doc = make_doc(
        "company_profile", tables={"directors": [{"director_name": "A"}, {"director_name": "B"}]}
    )
    table = doc.tables["directors"]
    table.rows.reverse()
    assert [r.index for r in sorted_rows(table)] == [0, 1]
    assert sorted_rows(None) == []


# ------------------------------------------------------------------ fairness slice test


def test_slice_pairs_are_deterministic_and_sized() -> None:
    first, second = slice_pairs(), slice_pairs()
    assert first == second
    assert slice_pairs(1) != first
    groups = {p.group for p in first}
    assert groups == {
        "western_order", "chinese_order", "malay_patronymic", "indian_patronymic",
        "titles", "mononym", "diacritics_initials", "non_latin",
    }  # fmt: skip
    for g in groups:
        members = [p for p in first if p.group == g]
        assert sum(p.same_person for p in members) >= 20
        assert sum(not p.same_person for p in members) >= 10


def test_every_slice_pair_meets_its_expectation() -> None:
    failures = [
        (p.group, p.a, p.b, verdict(p.a, p.b), p.expect)
        for p in slice_pairs()
        if verdict(p.a, p.b) not in p.expect
    ]
    assert failures == []


def test_slice_rates_zero_false_flags_and_false_accepts() -> None:
    rows = run_slice_test()
    assert len(rows) == 8
    for row in rows:
        assert row.false_flag_rate == 0.0, row
        assert row.false_accept_rate == 0.0, row
        assert row.same_pairs >= 20 and row.diff_pairs >= 10
    by_group = {r.group: r for r in rows}
    assert by_group["non_latin"].indeterminate_rate > 0  # a documented limit
    assert all(r.indeterminate_rate == 0 for r in rows if r.group != "non_latin")
    for g in ("western_order", "chinese_order"):
        assert by_group[g].baseline_false_flag_rate >= 0.5  # the baseline is visibly worse
    assert by_group["chinese_order"].baseline_false_accept_rate > 0
    assert by_group["chinese_order"].probable_rate > 0


def test_slice_rows_serialise_to_json() -> None:
    import json

    payload = json.dumps([r.model_dump() for r in run_slice_test()])
    assert "false_flag_rate" in payload


def test_run_slice_test_accepts_a_custom_matcher() -> None:
    rows = run_slice_test(lambda a, b: "different")
    assert all(r.false_flag_rate == 1.0 and r.false_accept_rate == 0.0 for r in rows)
    rows = run_slice_test(lambda a, b: "same")
    assert all(r.false_accept_rate == 1.0 for r in rows)


# ------------------------------------------------------------------ review findings: names


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Ahmad bin Ismail", "Ismail bin Ahmad"),
        ("Rajesh s/o Kumar", "Kumar s/o Rajesh"),
        ("Siti Aminah binte Yusof", "Yusof binte Siti Aminah"),
    ],
)
def test_father_and_son_are_different_people(a: str, b: str) -> None:
    assert match_person(a, b).verdict == "different"
    assert same_identity(a, None, b, None) == "mismatch"
    assert same_identity(a, "S1234567D", b, "S1234567D") == "match_name_conflict"


def test_marker_present_on_both_sides_still_matches_the_same_person() -> None:
    assert match_person("Rajesh s/o Kumar", "Rajesh a/l Kumar").verdict == "same"
    assert match_person("Muhammad Faris bin Abdullah", "Mohd Faris bin Abdullah").verdict == "same"
    # marker on one side only: order cannot be told apart, stays order-insensitive (documented)
    assert match_person("Rajesh s/o Kumar", "Kumar Rajesh").verdict == "same"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Rajesh Kumar", "Ramesh Kumar"),
        ("Natalie Fernandez", "Natalie Fernandes"),
        ("Jessika Wong", "Jessica Wong"),
    ],
)
def test_a_one_letter_slip_is_not_accepted_as_the_same_person(a: str, b: str) -> None:
    m = match_person(a, b)
    assert m.verdict == "probable" and m.typo
    assert same_identity(a, None, b, None) == "mismatch"
    assert same_identity(a, "S1234567D", b, "S1234567D") == "match_name_conflict"
    assert same_identity(a, "S1234567D", b, "S8222222G") == "mismatch"


def test_a_lone_last_token_is_not_the_whole_name() -> None:
    assert match_person("Kumar", "Rajesh Kumar").verdict == "different"
    assert match_person("Suryadi", "Suryadi Wibowo").verdict == "probable"  # mononym, kept


def test_father_and_son_pairs_are_in_the_fairness_slice() -> None:
    swapped = [
        p
        for p in slice_pairs()
        if p.group in {"malay_patronymic", "indian_patronymic"}
        and not p.same_person
        and sorted(p.a.replace("/", " ").split()) == sorted(p.b.replace("/", " ").split())
    ]
    assert len(swapped) >= 4
    assert all(verdict(p.a, p.b) == "different" for p in swapped)
