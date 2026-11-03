"""Two OCR readers on one photo: when do they agree, and what is a disagreement
(docs/ingest-and-extraction.md 4.8)."""

from __future__ import annotations

from app.core.models import Region, Word
from app.extract.agreement import agreement, squash


def word(text: str, x: float, y: float = 0.40, w: float = 0.08, h: float = 0.012) -> Word:
    return Word(page=1, text=text, x=x, y=y, w=w, h=h)


def box(x: float, y: float = 0.40, w: float = 0.08, h: float = 0.012) -> Region:
    return Region(file_id="f1", page=1, x=x, y=y, w=w, h=h)


ROW = [
    word("03", 0.05, w=0.02),
    word("Jul", 0.08, w=0.03),
    word("RENT", 0.20, w=0.05),
    word("4,500.00", 0.55),
    word("77,596.43", 0.75, w=0.09),
]


def test_the_same_figure_on_the_same_line_agrees() -> None:
    assert agreement("4,500.00", "money", box(0.55), ROW).state == "agree"
    assert agreement("77,596.43", "money", box(0.75, w=0.09), ROW).state == "agree"


def test_other_digits_where_the_value_stands_is_a_disagreement_with_both_readings() -> None:
    other = [*ROW[:3], word("4,560.00", 0.55), ROW[4]]
    got = agreement("4,500.00", "money", box(0.55), other)
    assert got.state == "differ"
    assert got.other == "4,560.00"


def test_a_second_reader_that_saw_nothing_there_is_unread_not_a_disagreement() -> None:
    elsewhere = [word("4,500.00", 0.55, y=0.80)]  # another line of the page
    assert agreement("4,500.00", "money", box(0.55), elsewhere).state == "unread"


def test_enclosing_punctuation_and_a_currency_sign_do_not_make_a_difference() -> None:
    other = [word("S$4,500.00,", 0.55)]
    assert agreement("4,500.00", "money", box(0.55), other).state == "agree"
    other = [word("(4,500.00)", 0.55)]
    assert agreement("4,500.00", "money", box(0.55), other).state == "agree"


def test_a_date_split_over_three_words_agrees_and_a_changed_day_differs() -> None:
    row = [word("03", 0.05, w=0.02), word("Jul", 0.08, w=0.03), word("2025", 0.12, w=0.04)]
    assert agreement("03 Jul 2025", "date", box(0.05, w=0.11), row).state == "agree"
    bad = [word("08", 0.05, w=0.02), word("Jul", 0.08, w=0.03), word("2025", 0.12, w=0.04)]
    got = agreement("03 Jul 2025", "date", box(0.05, w=0.11), bad)
    assert got.state == "differ" and got.other == "08 Jul 2025"


def test_an_identifier_read_with_a_different_check_letter_differs() -> None:
    other = [word("S1234567D", 0.30, w=0.10)]
    assert agreement("S1234567D", "id", box(0.30, w=0.10), other).state == "agree"
    other = [word("S1234567O", 0.30, w=0.10)]
    assert agreement("S1234567D", "id", box(0.30, w=0.10), other).state == "differ"


def test_does_not_apply_to_text_or_to_values_without_digits_or_without_a_second_reading() -> None:
    assert agreement("ACME PTE LTD", "text", box(0.1), ROW) is None
    assert agreement("SGD", "id", box(0.1), ROW) is None
    assert agreement("4,500.00", "money", None, ROW) is None
    assert agreement("4,500.00", "money", box(0.55), []) is None


def test_a_word_on_another_line_never_agrees_for_this_one() -> None:
    row_above = [word("4,500.00", 0.55, y=0.30)]
    assert agreement("4,500.00", "money", box(0.55), row_above).state == "unread"


def test_squash_keeps_the_digits_and_letters() -> None:
    assert squash("(1,234.56)") == "1234.56"
    assert squash("S1234567D;") == "s1234567d"
    assert squash("66.576.50") == "66576.50"  # Vision writes thousands separators as full stops
    assert squash("48,254.95\u0142") == "48254.95"  # a stray mark read off the table border
    assert squash("4,5O0.00") == "4,5o0.00"  # a letter inside a figure is never "corrected"


def test_a_reader_that_writes_the_thousands_separator_differently_agrees() -> None:
    other = [word("66.576.50", 0.55), word("RENT", 0.20, w=0.05)]
    assert agreement("66,576.50", "money", box(0.55), other).state == "agree"
    other = [word("66,576.50\u0142", 0.55)]
    assert agreement("66,576.50", "money", box(0.55), other).state == "agree"


def test_a_damaged_second_reading_is_not_a_figure_to_compare() -> None:
    other = [word("176,860.0\u20ac\u0142", 0.55)]  # the last digit lost: not a well-formed figure
    assert agreement("176,860.06", "money", box(0.55), other).state == "unread"


# ----------------------------------------------------------------------------- in the extractor


def _reading(second: list[Word] | None):  # noqa: ANN202
    from app.core.testing import make_doc
    from app.extract.llm_extractor import build_reading

    doc = make_doc("bank_statement")
    first = [
        word("Opening", 0.05, y=0.20, w=0.07),
        word("82,096.43", 0.55, y=0.20),
        word("03", 0.05, w=0.02),
        word("Jul", 0.08, w=0.03),
        word("RENT", 0.20, w=0.05),
        word("4,500.00", 0.55),
        word("77,596.43", 0.75, w=0.09),
    ]
    data = {
        "fields": {"opening_balance": "82,096.43"},
        "tables": {
            "transactions": [
                {
                    "date": "03 Jul",
                    "description": "RENT",
                    "debit": "4,500.00",
                    "credit": None,
                    "balance": "77,596.43",
                }
            ],
            "page_chain": [],
        },
    }
    return build_reading(
        doc, 1, data, role="cheap", model="m", mode="ocr_text", words=first, second=second
    )


def test_the_extractor_marks_a_figure_the_second_reader_prints_differently() -> None:
    second = [
        word("Opening", 0.05, y=0.20, w=0.07),
        word("82,096.43", 0.55, y=0.20),
        word("03", 0.05, w=0.02),
        word("Jul", 0.08, w=0.03),
        word("RENT", 0.20, w=0.05),
        word("4,560.00", 0.55),
        word("77,596.43", 0.75, w=0.09),
    ]
    row = _reading(second).rows["transactions"][0].cells
    assert row["debit"].agreement == "differ" and row["debit"].other_raw == "4,560.00"
    assert row["debit"].verified is False  # the existing "unverified reading" machinery
    assert row["balance"].agreement == "agree" and row["balance"].verified is True
    assert _reading(second).fields["opening_balance"].agreement == "agree"


def test_without_a_second_reading_nothing_changes() -> None:
    row = _reading(None).rows["transactions"][0].cells
    assert row["debit"].agreement is None and row["debit"].other_raw is None
    assert row["debit"].verified is True
    dumped = row["debit"].model_dump(mode="json")
    assert "agreement" not in dumped and "other_raw" not in dumped  # JSON of old cases unchanged


def test_a_garbled_second_reading_is_not_a_disagreement() -> None:
    second = [
        word("03", 0.05, w=0.02),
        word("RENT", 0.20, w=0.05),
        word("4,5O0.00", 0.55),  # the letter O: not a figure
        word("77,596.43", 0.75, w=0.09),
    ]
    cell = _reading(second).rows["transactions"][0].cells["debit"]
    assert cell.agreement == "unread" and cell.verified is True


def test_the_same_figure_written_another_way_is_not_a_disagreement() -> None:
    second = [word("03", 0.05, w=0.02), word("4500.00", 0.55), word("77,596.43", 0.75, w=0.09)]
    cell = _reading(second).rows["transactions"][0].cells["debit"]
    assert cell.agreement == "agree" and cell.verified is True


def test_a_value_the_first_reader_does_not_print_but_the_second_does_is_supported() -> None:
    from app.core.testing import make_doc
    from app.extract.llm_extractor import build_reading

    doc = make_doc("bank_statement")
    first = [word("Opening", 0.05, y=0.20, w=0.07), word("82,O96.43", 0.55, y=0.20)]
    second = [word("Opening", 0.05, y=0.20, w=0.07), word("82,096.43", 0.55, y=0.20)]
    data = {"fields": {"opening_balance": "82,096.43"}, "tables": {"transactions": []}}
    reading = build_reading(
        doc, 1, data, role="cheap", model="m", mode="ocr_text", words=first, second=second
    )
    cell = reading.fields["opening_balance"]
    assert cell.verified is True and cell.agreement == "agree"


def test_a_neighbouring_figure_is_not_a_reading_of_this_one() -> None:
    """A reader that left the amount out but printed the balance beside it says nothing about it."""
    row = [word("RENT", 0.20, w=0.05), word("77,596.43", 0.75, w=0.09)]
    assert agreement("4,500.00", "money", box(0.55), row).state == "unread"


def test_a_value_neither_reader_prints_is_unsupported_and_stays_unverified() -> None:
    from app.core.testing import make_doc
    from app.extract.llm_extractor import build_reading

    doc = make_doc("bank_statement")
    first = [word("Opening", 0.05, y=0.20, w=0.07), word("82,O96.43", 0.55, y=0.20)]
    second = [word("Opening", 0.05, y=0.20, w=0.07), word("82,096.48", 0.55, y=0.20)]
    data = {"fields": {"opening_balance": "82,096.43"}, "tables": {"transactions": []}}
    reading = build_reading(
        doc, 1, data, role="cheap", model="m", mode="ocr_text", words=first, second=second
    )
    cell = reading.fields["opening_balance"]
    assert cell.verified is False and cell.agreement == "unsupported"


def test_an_identifier_with_a_character_lost_is_a_damaged_reading_not_a_disagreement() -> None:
    other = [word("S123456D", 0.30, w=0.10)]
    assert agreement("S1234567D", "id", box(0.30, w=0.10), other).state == "unread"
    other = [word("6825340924¾š", 0.30, w=0.10)]
    assert agreement("6825340924", "id", box(0.30, w=0.10), other).state == "agree"
