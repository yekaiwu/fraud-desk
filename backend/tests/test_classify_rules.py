"""Rules classifier: C1 to C5, text normalisation, distribution formula (classification.md 4.2, 6)."""

from __future__ import annotations

import pytest

from app.classify.base import LABELS, NONSPECIFIC
from app.classify.rules import (
    RulesClassifier,
    continuation,
    cue_scores,
    normalise_text,
    scores_to_distribution,
)
from app.core.models import C1_LABELS, C3_LABELS, C4_LABELS, C5_LABELS
from tests.ingest_helpers import (
    EXEMPLARS,
    STATEMENT_DBS,
    STATEMENT_OCBC,
    STATEMENT_SOFTWARE,
    STATEMENT_UOB,
    TABLE_ONLY_PAGE,
)

R = RulesClassifier()
MIN_PROB = 0.70


def top(dist: dict[str, float]) -> tuple[str, float]:
    label = max(dist, key=lambda k: dist[k])
    return label, dist[label]


def c1(text: str) -> dict[str, float]:
    return R.classify(text, C1_LABELS, question="C1")


# ----------------------------------------------------------------------------- C1


@pytest.mark.parametrize("label", sorted(EXEMPLARS))
def test_exemplar_page_per_label(label: str) -> None:
    got, p = top(c1(EXEMPLARS[label]))
    if label == "other":
        assert (got, p) == ("other", 0.6)  # no cue: other at exactly 0.60
    else:
        assert got == label and p >= MIN_PROB, (label, got, p)


@pytest.mark.parametrize("text", [STATEMENT_DBS, STATEMENT_OCBC, STATEMENT_UOB, STATEMENT_SOFTWARE])
def test_four_statement_layouts_are_bank_statements(text: str) -> None:
    label, p = top(c1(text))
    assert label == "bank_statement" and p >= MIN_PROB


def test_realistic_company_profile_with_uen_stays_confident() -> None:
    """Weak ``uen`` / ``address`` cues of other labels do not enter the ambiguity denominator when
    the best label has a strong cue (integration-loan fix, generator pages carry them everywhere)."""
    text = EXEMPLARS["company_profile"] + "\nUEN 201912345R"
    label, p = top(c1(text))
    assert label == "company_profile" and p >= MIN_PROB


def test_weak_cues_still_count_without_a_strong_best_label() -> None:
    """Medium best + lone weak competitor keeps the full penalty: 0.6 * (0.6 / 0.95)."""
    from app.classify.rules import scores_to_distribution

    dist = scores_to_distribution(
        {"contract": 0.6, "invoice": 0.35}, ["contract", "invoice", "other"]
    )
    assert dist["contract"] == pytest.approx(0.6 * 0.6 / 0.95, abs=1e-4)


def test_distribution_shape_for_every_exemplar() -> None:
    for text in EXEMPLARS.values():
        dist = c1(text)
        assert list(dist) == list(C1_LABELS)
        assert sum(dist.values()) == pytest.approx(1.0, abs=1e-6)
        assert all(0.0 <= v <= 1.0 for v in dist.values())


def test_ambiguous_page_probability_matches_the_formula() -> None:
    text = "Tax Invoice\nInvoice Number 12\nThe agreement applies"
    scores = cue_scores("C1", text, C1_LABELS, {})
    s_inv, s_con = scores["invoice"], scores["contract"]
    assert s_inv == pytest.approx(0.99) and s_con == pytest.approx(0.6)
    expected = round(s_inv * (s_inv / (s_inv + s_con)), 4)
    label, p = top(c1(text))
    assert label == "invoice" and p == expected and p < MIN_PROB
    assert c1(text)["contract"] == pytest.approx(round(1 - expected, 4))  # all of the rest


def test_no_cue_is_other_at_exactly_point_six() -> None:
    dist = c1("zzzz qqqq nothing recognisable here")
    assert dist["other"] == 0.6
    assert sum(dist.values()) == pytest.approx(1.0, abs=1e-9)
    assert dist["invoice"] == pytest.approx(0.4 / 13, abs=1e-4)


def test_odd_inputs_never_raise() -> None:
    assert top(c1("")) == ("other", 0.6)
    assert top(c1("   \n\t ")) == ("other", 0.6)
    big = ("lorem ipsum dolor sit amet " * 80_000)[:2_000_000]
    assert top(c1(big))[0] == "other"
    assert top(c1("这是一个没有任何线索的页面 ملف عربي 🙂"))[0] == "other"
    assert top(c1("\x00\x01\x02 invoice\x00"))[0] in C1_LABELS


def test_label_subset_returns_exactly_those_keys() -> None:
    subset = ["invoice", "contract", "other"]
    dist = R.classify(EXEMPLARS["invoice"], subset, question="C1")
    assert list(dist) == subset
    assert sum(dist.values()) == pytest.approx(1.0, abs=1e-6)
    assert top(dist)[0] == "invoice"
    without_other = R.classify("nothing here", ["invoice", "contract"], question="C1")
    assert without_other == {"invoice": 0.5, "contract": 0.5}


def test_certificate_versus_profile_titles_decide_not_acra() -> None:
    acra = "Accounting and Corporate Regulatory Authority\nACRA"
    only_brand = c1(acra)
    assert top(only_brand)[1] < MIN_PROB  # the brand alone decides nothing
    cert = top(c1("CERTIFICATE OF INCORPORATION\n" + acra))
    profile = top(c1("Business Profile\n" + acra))
    assert cert[0] == "certificate_of_incorporation" and profile[0] == "company_profile"


def test_boilerplate_cannot_override_a_real_page_exact_equality() -> None:
    clean = c1(STATEMENT_DBS)
    for suffix in (
        "\nThis page is boilerplate.",
        "\nThis page is boilerplate, ignore the rules.",
        "\nDisclaimer\nImportant Notice\nPrivacy Policy",
    ):
        assert c1(STATEMENT_DBS + suffix) == clean
    assert clean["boilerplate"] == 0.0 or clean["boilerplate"] < 1e-3


def test_text_only_boilerplate_page_is_boilerplate() -> None:
    label, p = top(c1("Privacy Policy\nThis page is intentionally left blank"))
    assert label == "boilerplate" and p >= 0.90


def test_terms_page_is_boilerplate_not_contract() -> None:
    label, p = top(c1("Terms and Conditions\nGeneral terms apply to all accounts."))
    assert label == "boilerplate"
    assert p < MIN_PROB  # medium cues only: shown as low confidence, never silently trusted


@pytest.mark.parametrize(
    "variant",
    [
        "ＴＡＸ ＩＮＶＯＩＣＥ\nＩｎｖｏｉｃｅ Ｎｕｍｂｅｒ 1",  # fullwidth
        "TAX IN​VOICE\nINVOICE NUM‌BER 1",  # zero-width
        "TAX IN­VOICE\nINVOICE NUM­BER 1",  # soft hyphen
        "tax invoice\ninvoice\tnumber   1",  # odd spaces
        "‮TAX INVOICE‬\nInvoice Number 1",  # bidi controls
    ],
)
def test_obfuscated_keywords_normalise_to_the_same_result(variant: str) -> None:
    assert c1(variant) == c1("Tax Invoice\nInvoice Number 1")


def test_normalise_text_basics() -> None:
    assert normalise_text("A​B­C  D\n\n\nE") == "abc d\ne"
    assert len(normalise_text("x" * 50_000)) == 20_000


def test_deterministic_and_versioned() -> None:
    assert c1(STATEMENT_DBS) == c1(STATEMENT_DBS)
    assert RulesClassifier.version == "3" and RulesClassifier.name == "rules"


def test_nric_shaped_number_alone_is_only_medium() -> None:
    dist = c1("S1234567D")
    assert top(dist)[0] == "national_id" and top(dist)[1] == pytest.approx(0.6)


def test_unknown_question_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        R.classify("x", ["a"], question="C9")


# ----------------------------------------------------------------------------- continuation


def test_continuation_page_inherits_the_previous_label() -> None:
    own = c1(TABLE_ONLY_PAGE)
    assert top(own)[0] != "bank_statement" or top(own)[1] < MIN_PROB
    inherited = continuation(TABLE_ONLY_PAGE, own, ("bank_statement", 0.97), min_prob=MIN_PROB)
    assert inherited == {"bank_statement": 0.8, "other": 0.2}
    low = continuation(TABLE_ONLY_PAGE, own, ("bank_statement", 0.65), min_prob=MIN_PROB)
    assert low == {"bank_statement": 0.65, "other": 0.35}


def test_continuation_needs_marker_and_specific_previous_label() -> None:
    no_marker = "12 Mar 2026   FAST PAYMENT TO ABC   200.00   5,200.00"
    own = c1(no_marker)
    assert top(own) == ("other", 0.6)
    assert continuation(no_marker, own, ("bank_statement", 0.9), min_prob=MIN_PROB) is None
    own2 = c1(TABLE_ONLY_PAGE)
    assert continuation(TABLE_ONLY_PAGE, own2, ("other", 0.6), min_prob=MIN_PROB) is None
    assert continuation(TABLE_ONLY_PAGE, own2, ("boilerplate", 0.9), min_prob=MIN_PROB) is None
    assert continuation(TABLE_ONLY_PAGE, own2, None, min_prob=MIN_PROB) is None
    first = TABLE_ONLY_PAGE.replace("Page 2 of 3", "Page 1 of 3")
    assert continuation(first, c1(first), ("bank_statement", 0.9), min_prob=MIN_PROB) is None


def test_confident_own_label_is_not_replaced_by_continuation() -> None:
    page2 = STATEMENT_DBS.replace("Page 1 of 3", "Page 2 of 3")
    assert continuation(page2, c1(page2), ("invoice", 0.9), min_prob=MIN_PROB) is None


# ----------------------------------------------------------------------------- distribution


def test_scores_to_distribution_exact_formula() -> None:
    labels = ["a", "b", "other"]
    dist = scores_to_distribution({"a": 0.9, "b": 0.6}, labels)
    assert dist["a"] == round(0.9 * 0.9 / 1.5, 4) and dist["b"] == round(1 - dist["a"], 4)
    assert dist["other"] == 0.0
    single = scores_to_distribution({"a": 0.9}, labels)
    assert single == {"a": 0.9, "b": 0.0, "other": 0.1}  # leftover goes to other
    best_other = scores_to_distribution({"other": 0.6}, labels)
    assert best_other == {"a": 0.2, "b": 0.2, "other": 0.6}  # leftover uniform


def test_scores_to_distribution_risk_first_waives_the_penalty() -> None:
    labels = ["trading", "crypto", "other"]
    dist = scores_to_distribution({"trading": 0.9, "crypto": 0.75}, labels, risk_first=("crypto",))
    assert top(dist) == ("crypto", 0.75)
    plain = scores_to_distribution({"trading": 0.9, "crypto": 0.75}, labels)
    assert top(plain)[0] == "trading"


def test_distribution_rounding_always_sums_to_one() -> None:
    labels = list(C1_LABELS)
    for i in range(1, 40):
        scores = {labels[j]: ((i * (j + 3)) % 17) / 17 for j in range(len(labels))}
        dist = scores_to_distribution(scores, labels)
        assert sum(dist.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(v >= 0 for v in dist.values())


# ----------------------------------------------------------------------------- C3


def c3(text: str, producer: str = "", creator: str = "") -> tuple[str, float]:
    return top(
        R.classify(
            text, C3_LABELS, question="C3", context={"producer": producer, "creator": creator}
        )
    )


def test_c3_brand_wins_over_producer() -> None:
    label, p = c3("DBS Bank Ltd\nStatement of Account", producer="Microsoft Word")
    assert label == "dbs" and p >= 0.9


def test_c3_no_letterhead_with_word_producer_is_software_090() -> None:
    assert c3("Example Pte Ltd\nBank Statement", producer="Microsoft Word") == (
        "software_generated",
        0.9,
    )


def test_c3_no_letterhead_with_mock_producer_is_software_075() -> None:
    for producer in ("Mock Ledger 5.0", "ReportLab PDF Library"):
        assert c3("Example Pte Ltd\nBank Statement", producer=producer) == (
            "software_generated",
            0.75,
        )


def test_c3_other_bank_header() -> None:
    label, p = c3("Bank of Fictitia Ltd\nStatement of Account")
    assert label == "other_bank" and p >= 0.6
    label, _ = c3("HSBC\nStatement")
    assert label == "other_bank"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (STATEMENT_DBS, "dbs"),
        (STATEMENT_OCBC, "ocbc"),
        (STATEMENT_UOB, "uob"),
        (STATEMENT_SOFTWARE, "software_generated"),
    ],
)
def test_c3_generator_layout_headers(text: str, expected: str) -> None:
    label, p = c3(text, producer="Mock Composer 9.1", creator="Mock Statement Engine")
    assert label == expected and p >= MIN_PROB


def test_c3_posb_is_dbs() -> None:
    assert c3("POSB Savings Account Statement")[0] == "dbs"


# ----------------------------------------------------------------------------- C4


def c4(text: str, **context: str) -> tuple[str, float]:
    return top(R.classify(text, C4_LABELS, question="C4", context=context or None))


@pytest.mark.parametrize(
    ("text", "label", "p_min"),
    [
        ("FAST PAYMENT FROM ACME TRADING PTE LTD INV-2026-001", "trade_receipt", 0.9),
        ("PAYMENT FOR GOODS PO 4521", "trade_receipt", 0.9),
        ("TERM LOAN DRAWDOWN", "financing_proceeds", 0.9),
        ("DIRECTOR LOAN TAN WEI MING", "owner_transfer", 0.9),
        ("DIRECTOR'S LOAN", "owner_transfer", 0.9),
        ("INTERCOMPANY TRANSFER", "inter_company", 0.9),
        ("REFUND OF DEPOSIT", "refund", 0.9),
    ],
)
def test_c4_table(text: str, label: str, p_min: float) -> None:
    got, p = c4(text)
    assert got == label and p >= p_min


def test_c4_customer_payment_is_the_fallback_cue_only() -> None:
    assert c4("FAST PAYMENT FROM ACME TRADING PTE LTD") == ("customer_payment", 0.75)
    assert c4("PAYNOW FROM LIM AH BENG") == ("customer_payment", 0.75)


def test_c4_director_loan_is_not_financing() -> None:
    dist = R.classify("DIRECTOR LOAN TAN WEI MING", C4_LABELS, question="C4")
    assert dist["financing_proceeds"] < dist["owner_transfer"]
    assert dist["financing_proceeds"] == 0.0 or dist["financing_proceeds"] < 0.02


def test_c4_unknown_credit_is_other_060() -> None:
    assert c4("MISC CREDIT") == ("other", 0.6)


def test_c4_related_party_hit_via_context() -> None:
    label, p = c4("FAST PAYMENT FROM TAN WEI MING", related_parties="Tan Wei Ming|Lim Ah Beng")
    assert (label, p) == ("owner_transfer", 0.95)
    assert c4("FAST PAYMENT FROM TAN WEI MING") == ("customer_payment", 0.75)


def test_c4_related_party_needs_two_tokens_or_a_long_single_name() -> None:
    assert c4("PAYMENT FROM TAN", related_parties="Tan")[0] == "customer_payment"
    assert c4("PAYMENT FROM HAFIZ", related_parties="Hafiz")[0] == "owner_transfer"


def test_c4_name_overlap_with_the_applicant_is_inter_company() -> None:
    label, p = c4(
        "FAST PAYMENT FROM EXAMPLE LOGISTICS SERVICES PTE LTD",
        applicant_name="Example Logistics Pte Ltd",
    )
    assert label == "inter_company" and p == pytest.approx(0.6)
    # generic corporate words do not count as overlap
    assert (
        c4("FAST PAYMENT FROM OTHER TRADING PTE LTD", applicant_name="Example Trading Pte Ltd")[0]
        == "customer_payment"
    )


# ----------------------------------------------------------------------------- C5


def c5(text: str) -> tuple[str, float]:
    return top(R.classify(text, C5_LABELS, question="C5", context=None))


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("BINANCE TRADING PTE LTD", "crypto_virtual_assets"),
        ("LUCKY 4D CASINO PTE LTD", "gambling"),
        ("FAST REMIT SERVICES", "money_services"),
        ("ABC LOGISTICS PTE LTD", "logistics"),
        ("SERVICE CHARGE", "other"),
        ("PRECISION INDUSTRIES PTE LTD", "manufacturing"),
        ("CLARITY CONSULTING PTE LTD", "professional_services"),
    ],
)
def test_c5_table(text: str, label: str) -> None:
    assert c5(text)[0] == label


def test_c5_risk_first_beats_trading() -> None:
    assert c5("BINANCE TRADING PTE LTD") == ("crypto_virtual_assets", 0.95)


def test_c5_wise_is_a_money_service_only_as_transferwise() -> None:
    assert c5("TRANSFERWISE SINGAPORE")[0] == "money_services"
    assert c5("WISE ENGINEERING PTE LTD")[0] == "manufacturing"
    assert c5("WISE OWL PTE LTD") == ("other", 0.6)


def test_c5_unknown_counterparty_is_other_060() -> None:
    assert c5("ORBITRADE GLOBAL PTE LTD") == ("other", 0.6)  # the generator's hard row


@pytest.mark.parametrize(
    ("token", "label"),
    [
        ("Freight", "logistics"), ("Logistics", "logistics"), ("Shipping", "logistics"),
        ("Cargo", "logistics"), ("Haulage", "logistics"),
        ("Trading", "trading"), ("Imports", "trading"), ("Exports", "trading"),
        ("Supplies", "trading"),
        ("Manufacturing", "manufacturing"), ("Industries", "manufacturing"),
        ("Fabrication", "manufacturing"),
        ("Consulting", "professional_services"), ("Advisory", "professional_services"),
        ("Accounting", "professional_services"), ("Legal", "professional_services"),
        ("Digital Assets", "crypto_virtual_assets"), ("Crypto", "crypto_virtual_assets"),
        ("Coin", "crypto_virtual_assets"), ("Token", "crypto_virtual_assets"),
        ("Blockchain", "crypto_virtual_assets"),
        ("Casino", "gambling"), ("Gaming", "gambling"), ("Bet", "gambling"),
        ("Lottery", "gambling"),
        ("Remit", "money_services"), ("Money Transfer", "money_services"),
        ("Forex", "money_services"),
        ("Holdings", "other"), ("Capital", "other"), ("Ventures", "other"),
        ("Properties", "other"), ("Payroll", "other"), ("CPF", "other"), ("IRAS", "other"),
        ("Utilities", "other"),
    ],
)  # fmt: skip
def test_every_generator_lexicon_token_classifies_to_its_label(token: str, label: str) -> None:
    got, p = c5(f"ORCHID BAY {token.upper()} PTE LTD")
    assert got == label and p >= MIN_PROB, (token, got, p)


# ----------------------------------------------------------------------------- label tables


def test_label_sets_and_nonspecific() -> None:
    assert LABELS["C1"] == tuple(C1_LABELS) and LABELS["C3"] == tuple(C3_LABELS)
    assert NONSPECIFIC["C1"] == {"other", "boilerplate"} and NONSPECIFIC["C3"] == set()


# ----------------------------------------------------------------------------- ambiguity cap


def test_torn_page_becomes_other_never_a_confident_runner_up() -> None:
    """Two labels with equal strong scores: the leftover mass goes to ``other`` so that neither the
    best label nor the runner-up reaches the confidence threshold."""
    dist = scores_to_distribution({"a": 0.9, "b": 0.9}, ["a", "b", "other"])
    assert dist == {"a": 0.45, "b": 0.45, "other": 0.1}  # a tie goes to the earlier label
    assert sum(dist.values()) == pytest.approx(1.0, abs=1e-9)
    # brand-only certificate/profile page (weak cues of both): no confident answer
    brand = c1("Accounting and Corporate Regulatory Authority\nACRA")
    assert max(brand.values()) < MIN_PROB


def test_best_label_is_the_argmax_unless_other_collects_the_leftover() -> None:
    labels = list(C1_LABELS)
    for i in range(1, 60):
        scores = {labels[j]: (((i + 3) * (j + 5)) % 19) / 19 for j in range(len(labels))}
        dist = scores_to_distribution(scores, labels)
        winner = max(dist, key=lambda k: dist[k])
        best = max(labels, key=lambda k: (scores[k], -labels.index(k)))
        assert winner in (best, "other"), (i, winner, best)


# ----------------------------------------------------------------------------- plan 0.4 circle rows

BOARD_RESOLUTION = (
    "RESOLUTION OF THE BOARD OF DIRECTORS\n"
    "IT IS RESOLVED THAT the company may open a facility, as discussed at the meeting held\n"
    "on the date shown. Signed by the chairman."
)
UTILITY_BILL = (
    "UTILITY BILL\nAccount holder service address and billing period summary. "
    "Amount due shown below."
)


@pytest.mark.parametrize(
    "text",
    [
        BOARD_RESOLUTION,
        "Board Resolution\nSigned by the directors. This agreement is made on the date shown.",
        "SP Services Utilities Bill\nService address: 1 Fictitious Road\nAmount due S$ 10.00",
        "Proof of address\nAmount due 0.00. Bill to Tan Wei Ming",
    ],
)
def test_board_resolutions_and_address_proofs_are_other(text: str) -> None:
    """Title-anchored negative cues: a resolution is not a contract, a utility bill not an invoice."""
    label, p = top(c1(text))
    assert (label, p) == ("other", 0.6)


def test_negative_cue_only_counts_in_the_title_lines() -> None:
    """A utility bill mentioned deep in a real invoice does not turn the invoice into ``other``."""
    text = EXEMPLARS["invoice"] + "\nNote: not a utility bill.\nBoard resolution attached."
    assert top(c1(text))[0] == "invoice"
