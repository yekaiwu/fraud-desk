"""Exact copy, then verify (ingest-and-extraction.md 6.5)."""

from __future__ import annotations

import pytest

from app.core.models import Word
from app.extract.verify import PageVerifier, locate, normalise_token, union_box


def words(*texts: str, page: int = 1, y: float = 0.1) -> list[Word]:
    out = []
    x = 0.05
    for t in texts:
        out.append(Word(page=page, text=t, x=x, y=y, w=0.05, h=0.01))
        x += 0.06
    return out


def test_normalise_token() -> None:
    assert normalise_token("Ｓ$1,234.56") == "s$1,234.56"  # NFKC + casefold
    assert normalise_token("a​b") == "ab"
    assert normalise_token("INV-001.") == "inv-001."  # punctuation kept


def test_contiguous_match_and_cursor() -> None:
    ws = words("500.00", "A", "500.00", "B", "500.00")
    assert locate("500.00", ws, kind="money") == (0, 1)
    assert locate("500.00", ws, kind="money", start=1) == (2, 3)
    assert locate("500.00", ws, kind="money", start=3) == (4, 5)
    assert locate("500.00", ws, kind="money", start=5) is None


def test_split_currency_symbol_matches_second_pass() -> None:
    ws = words("Total", "S$", "1,234.56")
    assert locate("S$1,234.56", ws, kind="money") == (1, 3)
    assert locate("S$ 1,234.56", ws, kind="money") == (1, 3)


def test_text_tokens_in_order_or_as_a_bag() -> None:
    ws = words("FAST", "PAYMENT", "99.00", "FROM", "ACME", "TRADING")
    assert locate("FAST PAYMENT FROM ACME TRADING", ws, kind="text") == (0, 6)
    # a number that is not printed is not found, whatever the kind
    assert locate("FAST PAYMENT 100.00", ws, kind="text") is None
    assert locate("100.00", ws, kind="money") is None


def test_union_box_and_page_check() -> None:
    ws = words("a", "b")
    box = union_box(ws, "f1")
    assert (box.file_id, box.page) == ("f1", 1)
    assert box.x == pytest.approx(0.05)
    assert box.x + box.w == pytest.approx(0.16)
    with pytest.raises(ValueError):
        union_box([*ws, *words("c", page=2)], "f1")
    with pytest.raises(ValueError):
        union_box([], "f1")


def test_verifier_found_not_found_and_no_reader() -> None:
    ws = words("Closing", "Balance", "1,000.00")
    v = PageVerifier(ws, "f1", 1)
    hit = v.check("1,000.00", "money", use_cursor=False)
    assert hit.verified is True
    assert hit.box is not None
    miss = v.check("9,999.00", "money", use_cursor=False)
    assert miss.verified is False
    assert miss.box is None
    blind = PageVerifier([], "f1", 1)
    assert blind.has_reader is False
    assert blind.check("1,000.00", "money").verified is None
    assert v.check("", "money").verified is None  # nothing to verify


def test_row_cursor_binds_repeated_amounts_to_the_right_row() -> None:
    ws = words("500.00", "x", "500.00", "y", "500.00")
    v = PageVerifier(ws, "f1", 1)
    boxes = [v.check("500.00", "money").box for _ in range(3)]
    assert [round(b.x, 2) for b in boxes if b] == [0.05, 0.17, 0.29]
    # a fourth lookup falls back to the whole page (still verified, nearest the cursor)
    again = v.check("500.00", "money")
    assert again.verified is True
