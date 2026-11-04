"""Plain-English labels (app/custom_rules/labels.py): they must cover the schemas, so nothing drifts."""

from __future__ import annotations

from app.core.schemas import EXTRACTABLE_TYPES
from app.custom_rules import labels
from app.custom_rules.spec import PROTECTED_FIELD_NAMES, addressable_fields, fs_lines


def test_every_addressable_field_has_a_meaning() -> None:
    missing = [p for p in addressable_fields() if p not in labels.FIELD_MEANINGS]
    assert missing == [], f"add a meaning for: {missing}"


def test_every_financial_statement_line_has_a_label() -> None:
    missing = [p for p in fs_lines() if p not in labels.FS_LINE_LABELS]
    assert missing == [], f"add a label for: {missing}"


def test_no_stale_labels_for_fields_that_no_longer_exist() -> None:
    assert set(labels.FIELD_MEANINGS) <= set(addressable_fields())
    assert set(labels.FS_LINE_LABELS) <= set(fs_lines())


def test_no_protected_attribute_is_labelled() -> None:
    for key in [*labels.FIELD_MEANINGS, *labels.FS_LINE_LABELS]:
        assert key.partition(".")[2] not in PROTECTED_FIELD_NAMES


def test_every_extractable_document_has_a_label() -> None:
    assert set(labels.DOC_LABELS) == set(EXTRACTABLE_TYPES)
    assert labels.complete() == []


def test_labels_are_plain_text() -> None:
    for text in [
        *labels.FIELD_MEANINGS.values(),
        *labels.FS_LINE_LABELS.values(),
        *labels.DOC_LABELS.values(),
    ]:
        assert text and text == " ".join(text.split()) and "<" not in text


def test_field_labels_read_naturally() -> None:
    assert labels.field_label("invoice.total") == "invoice total"
    assert labels.field_label("invoice.invoice_number") == "invoice number"
    assert labels.field_label("receipt.receipt_number") == "receipt number"
    assert labels.field_label("passport.date_of_expiry") == "passport date of expiry"
    assert labels.field_label("national_id.id_number") == "national ID number"
    assert labels.field_label("shareholder_register.register_date") == "shareholder register date"
    assert labels.field_label("invoice.supplier_uen") == "invoice supplier UEN"
    assert labels.field_label("certificate_of_incorporation.incorporation_date") == (
        "certificate of incorporation date of incorporation"
    )


def test_field_names_drop_the_repeated_document_word() -> None:
    assert labels.field_name("invoice.total") == "total"
    assert labels.field_name("invoice.invoice_date") == "date"
    assert labels.field_name("passport.date_of_expiry") == "date of expiry"
    assert labels.field_name("bank_statement.bank_name") == "bank name"
    assert labels.field_name("company_profile.company_name") == "company name"


def test_meaning_and_line_label_fall_back_to_a_humanised_name() -> None:
    assert labels.field_meaning("invoice.total") == labels.FIELD_MEANINGS["invoice.total"]
    assert labels.field_meaning("invoice.unknown_thing") == "invoice unknown thing"
    assert labels.line_label("balance_sheet.total_equity") == "total equity"
    assert labels.line_label("balance_sheet.some_new_line") == "some new line"


def test_articles_and_plurals() -> None:
    assert labels.article("invoice") == "an" and labels.article("receipt") == "a"
    assert labels.article("ownership chart") == "an" and labels.article("national ID") == "a"
    assert labels.doc_plural("invoice") == "invoices"
    assert labels.doc_plural("certificate_of_incorporation") == "certificates of incorporation"
    assert labels.doc_label("unknown_type") == "unknown type"
