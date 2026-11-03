"""The independent red team's attacks as tests (docs/red-team.md).

Three groups:

* ``FIXED``: attacks that evaded the pipeline when they were found and now must be detected.
* ``KNOWN_EVASIONS``: attacks that still pass. Each is an ``xfail(strict=True)``: it shows in the
  test report as an expected failure with the reason, and the day a check catches it the test
  turns red ("unexpectedly passed") so the document and the list are updated.
* controls: the digital clean bases raise no flag at medium or above, and the calibration attacks
  (an obvious overlay, a broken running balance, reused pixels) are detected, so a silent harness
  cannot pass for a strong pipeline.

Everything runs offline with the fake LLM, semantic checks off, on fictitious documents.
"""

from __future__ import annotations

import pytest

from scripts.red_team import attacks  # noqa: F401  (registers the attacks)
from scripts.red_team.core import REGISTRY, Runner

ATTACKS = {a.id: a for a in REGISTRY}

FIXED = {
    "R23": "ToUnicode map contradicts the glyphs (pdf_text_vs_render)",
    "R36": "unsigned incremental update replaces text (pdf_modified)",
    "R50": "signature copied with a rotation, rescale and noise (signature_reuse)",
    "R102": "balance column unreadable beside siblings (bs_running_balance)",
}
NOT_FAST = {
    "R90": "instruction text on the page must not reach the fast lane",
    "R104": "rotated page the rules never read must not reach the fast lane",
    "R101": "relabelled invoice totals the rules never read must not reach the fast lane",
    "R103": "renamed financial statement headings must not reach the fast lane",
}
CALIBRATION = ("R12", "R13", "R24", "R38", "R53")

KNOWN_EVASIONS = {
    "R01": "credit inflated inside the 5% year-end cash tolerance",
    "R03": "revenue and cost of sales grossed up together: profit, balance sheet, cash unchanged",
    "R05": "earlier-year statement left out: the comparatives check has nothing to compare",
    "R11": "contract reference line deleted: the invoice-versus-contract check has nothing to read",
    "R14": "account holder spelled as a near match with the applicant's UEN kept",
    "R30": "statement rebuilt with the issuer's own producer string (public on every statement)",
    "R31": "metadata scrubbed from an invoice: no producer rule applies to invoices",
    "R33": "edited in an editor whose producer string is not on the editor list",
    "R37": "edited statement signed with the forger's own certificate",
    "R39": "signatures stripped from every edited statement before editing",
    "R54": "freshly drawn signature: no donor image to match",
    "R74": "mirror invoice from the customer back to the applicant",
    "R77": "fabricated supplier invoice with a checksum-valid UEN and correct arithmetic",
    "R81": "invented company, no registry to disagree",
    "R86": "incorporation date moved; the registry is not the live register",
    "R87": "boomerang through one counterparty (below the pass-through thresholds)",
    "R91": "instruction paraphrased past the pattern list",
}
# detected only by a medium cross-document flag that does not close the fast lane
KNOWN_FAST_LANE = {
    "R16": "credits hijacked: only x_cash_at_year_end fires and the case stays in the fast lane",
    "R75": "tripled paid invoice: only x_invoice_vs_receipts fires and the case stays fast",
}


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    r = Runner(tmp_path_factory.mktemp("red"))
    yield r
    r.close()


@pytest.mark.parametrize("scenario", ["loan", "kyc", "aml"])
def test_clean_digital_base_raises_nothing_at_medium_or_above(runner, scenario):
    assert runner.control(scenario).medhigh() == []


@pytest.mark.parametrize("attack_id", CALIBRATION)
def test_calibration_attacks_are_detected(runner, attack_id):
    res = runner.run(ATTACKS[attack_id])
    assert res.error is None, res.error
    assert res.verdict == "detected", res.attributable


@pytest.mark.parametrize("attack_id", sorted(FIXED), ids=lambda i: f"{i}-{FIXED[i][:30]}")
def test_fixed_attack_is_detected(runner, attack_id):
    res = runner.run(ATTACKS[attack_id])
    assert res.error is None, res.error
    assert res.verdict == "detected", res.attributable


@pytest.mark.parametrize("attack_id", sorted(NOT_FAST))
def test_unflagged_forgery_does_not_reach_the_fast_lane(runner, attack_id):
    res = runner.run(ATTACKS[attack_id])
    assert res.error is None, res.error
    assert res.control_lane == "fast"
    assert res.lane != "fast", f"{attack_id}: {NOT_FAST[attack_id]}"


@pytest.mark.parametrize(
    "attack_id",
    [
        pytest.param(i, marks=pytest.mark.xfail(strict=True, reason=f"known evasion: {why}"))
        for i, why in sorted(KNOWN_EVASIONS.items())
    ],
)
def test_known_evasion(runner, attack_id):
    res = runner.run(ATTACKS[attack_id])
    assert res.error is None, res.error
    assert res.verdict == "detected"


@pytest.mark.parametrize(
    "attack_id",
    [
        pytest.param(i, marks=pytest.mark.xfail(strict=True, reason=f"known gap: {why}"))
        for i, why in sorted(KNOWN_FAST_LANE.items())
    ],
)
def test_known_fast_lane_gap(runner, attack_id):
    res = runner.run(ATTACKS[attack_id])
    assert res.error is None, res.error
    assert res.verdict == "detected"
    assert res.lane != "fast"
