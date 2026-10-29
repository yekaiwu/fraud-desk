import pytest

from app.core.models import Word
from app.core.textlayout import layout_text, page_marker


def w(text: str, x: float, y: float, wd: float = 0.05, page: int = 1) -> Word:
    return Word(page=page, text=text, x=x, y=y, w=wd, h=0.012)


def test_reading_order_lines_and_gaps() -> None:
    words = [
        w("Balance", 0.7, 0.30),
        w("Date", 0.06, 0.30),
        w("Description", 0.2, 0.30, 0.1),
        w("05", 0.06, 0.32, 0.02),
        w("Mar", 0.085, 0.32, 0.03),
        w("2026", 0.12, 0.32, 0.04),
        w("1,000.00", 0.7, 0.32, 0.08),
        w("Statement", 0.06, 0.10, 0.08),
        w("of", 0.145, 0.10, 0.02),
        w("Account", 0.17, 0.10, 0.07),
    ]
    text = layout_text(words)
    assert text.split("\n") == [
        "Statement of Account",
        "Date   Description   Balance",
        "05 Mar 2026   1,000.00",
    ]


def test_slightly_misaligned_words_share_a_line() -> None:
    assert layout_text([w("a", 0.1, 0.5), w("b", 0.16, 0.5035)]) == "a b"


def test_pages_are_separated_and_empty_words_dropped() -> None:
    words = [w("two", 0.1, 0.1, page=2), w("one", 0.1, 0.1, page=1), w(" ", 0.3, 0.1)]
    assert layout_text(words) == "one\n\ntwo"
    assert layout_text([]) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Statement\nPage 1 of 3", (1, 3)),
        ("PAGE 2/3", (2, 3)),
        ("3 pages, this is page 1", (1, 3)),
        ("DBS\nrows\n1/3", (1, 3)),
        ("a\nb\nc\nd\n 2 / 5 ", (2, 5)),
        ("Date 05/03/2026", None),
        ("05/03/2026", None),
        ("nothing here", None),
        ("Page 0 of 3", None),
        ("Page 4 of 3", None),
        ("Page 1 of 0", None),
        ("", None),
    ],
)
def test_page_marker_vectors(text: str, expected: tuple[int, int] | None) -> None:
    assert page_marker(text) == expected


def test_page_marker_last_match_wins() -> None:
    assert page_marker("Page 1 of 3\nbody\nPage 2 of 3") == (2, 3)
    # bare n/m only counts in the last three non-empty lines
    assert page_marker("1/3\nl2\nl3\nl4\nl5") is None
