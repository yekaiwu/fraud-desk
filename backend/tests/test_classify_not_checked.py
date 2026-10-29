"""'Received, not checked' families (plan 0.4 circle rows): nothing is silently dropped.

Real PDFs go through ingest and classify. Each page is a Document and is flagged by
``doc_unclassified`` (label other/boilerplate, or probability below the threshold).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.classify.stage import run_stage as classify
from app.core.builtin_checks import doc_unclassified
from app.ingest.stage import run_stage as ingest
from tests.ingest_helpers import EXEMPLARS, ctx_with_files, lines_page, make_pdf

NOT_CHECKED = {
    "board_resolution": [
        "RESOLUTION OF THE BOARD OF DIRECTORS",
        "Be it resolved that Tan Wei Ming is authorised to open a bank account",
        "in the name of the Company and to sign all related forms.",
    ],
    "proof_of_address": [
        "SP Services Utilities Bill",
        "Service address: 1 Fictitious Road, Singapore 000001",
        "Account holder: Tan Wei Ming",
        "Amount payable S$ 123.45 by 15 March 2026",
    ],
    "business_licence": [
        "LICENCE TO OPERATE",
        "This licence is granted to Example Logistics Pte Ltd to carry on the business of",
        "freight forwarding. Valid until 31 December 2027.",
    ],
    "wealth_evidence": [
        "Letter from Fictitious Wealth Advisers",
        "We confirm that investments held for the client have a market value of",
        "S$ 2,000,000.00 as at 30 June 2026.",
    ],
    "trade_document": [
        "BILL OF LADING",
        "Shipper: Example Logistics Pte Ltd   Consignee: Orchid Bay",
        "Port of loading: Singapore   Container FCIU1234567",
    ],
}


def test_five_unsupported_families_pass_through_and_are_listed(tmp_path: Path) -> None:
    pdf = make_pdf([lines_page(lines) for lines in NOT_CHECKED.values()])
    ctx = ctx_with_files(tmp_path, [pdf])
    ingest(ctx)
    outcome = classify(ctx)

    docs = ctx.case.documents
    # consecutive `other` pages share one label, so they form one document (documented limit)
    covered = sorted(p for d in docs for p in range(d.page_start, d.page_end + 1))
    assert covered == [1, 2, 3, 4, 5]
    others = [d for d in docs if d.doc_type in ("other", "boilerplate")]
    assert sum(d.page_end - d.page_start + 1 for d in others) >= 4
    for doc in others:
        assert doc.status == "received_not_checked" and doc.classification.classifier == "rules"

    flagged = {doc_id for flag in doc_unclassified(ctx) for doc_id in flag.document_ids}
    assert flagged == {d.id for d in docs}  # nothing is silent
    assert outcome.partial is False
    assert outcome.detail["escalation_unavailable"] >= 4  # rules mode: shown, not escalated


def test_a_weak_single_cue_page_is_shown_as_unsure_not_trusted(tmp_path: Path) -> None:
    """The word 'address' alone is a weak national_id cue: low probability, flagged either way."""
    pdf = make_pdf([lines_page(NOT_CHECKED["proof_of_address"])])
    ctx = ctx_with_files(tmp_path, [pdf])
    ingest(ctx)
    classify(ctx)
    doc = ctx.case.documents[0]
    assert doc.classification.probability < ctx.settings.classifier_min_prob
    assert [f.document_ids for f in doc_unclassified(ctx)] == [[doc.id]]


def test_recognised_documents_next_to_unsupported_ones_are_not_flagged(tmp_path: Path) -> None:
    pages = [
        lines_page(EXEMPLARS["invoice"].split("\n")),
        lines_page(NOT_CHECKED["trade_document"]),
    ]
    ctx = ctx_with_files(tmp_path, [make_pdf(pages)])
    ingest(ctx)
    classify(ctx)
    invoice, trade = ctx.case.documents
    assert invoice.doc_type == "invoice" and invoice.status == "ok"
    flagged = {i for f in doc_unclassified(ctx) for i in f.document_ids}
    assert flagged == {trade.id}


@pytest.mark.parametrize("family", sorted(NOT_CHECKED))
def test_each_family_alone_is_a_visible_document(tmp_path: Path, family: str) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(NOT_CHECKED[family])])])
    ingest(ctx)
    classify(ctx)
    assert len(ctx.case.documents) == 1
    assert ctx.case.documents[0].words  # the text layer travels with the document
