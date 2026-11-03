"""The five verification checks (app/checks/verification.py, bank_details.py) against hand-built
documents and faked source outcomes. No network, no snapshot download.

For every source-backed check: the answer is turned into the right flag; "not configured" and private
mode skip with a code that keeps coverage unchanged; "configured but unreachable" skips without a code
(it counts as not run) and never produces a flag.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.checks import bank_details as bank
from app.checks import verification as v
from app.checks.registry import CheckSkipped, run_stage_checks
from app.core.config import Settings
from app.core.models import CheckRun, Word
from app.core.registry import MockRegistry, RegistryEntity
from app.core.testing import make_ctx, make_doc
from app.score import compute_coverage
from app.sources.common import SourceOutcome
from app.sources.gleif import GleifParent, GleifRecord
from app.sources.iras import GstStatus
from app.sources.onemap import PostalRecord
from app.sources.sanctions import SOURCE_ORDER, Screener, Snapshot
from tests.test_checks_kyc_fixtures import (
    APPLICANT,
    APPLICANT_NAME,
    UEN,
    company_profile_doc,
    invoice_doc,
    passport_doc,
)


def settings(tmp_path: Path, **kw) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake", **kw)


def private(s: Settings) -> Settings:
    object.__setattr__(
        s, "private_mode", "on"
    )  # the setting a sibling branch adds; read with getattr
    return s


def ctx(docs, s: Settings, sources=None, **kw):  # noqa: ANN001, ANN003, ANN201
    return make_ctx(
        docs, use_case="kyc", applicant=APPLICANT, settings=s, sources=sources or {}, **kw
    )


def skipped(fn, c) -> CheckSkipped:  # noqa: ANN001
    with pytest.raises(CheckSkipped) as exc:
        fn(c)
    return exc.value


# ---------------------------------------------------------------------------- sanctions


def entry(i: str, names: list[str], kind: str = "person", **kw) -> dict:  # noqa: ANN003
    return {"id": f"un:{i}", "list": "un", "ref": f"IQi.{i}", "kind": kind, "names": names, "dob": [],
            "nationality": [], "ids": [], "programs": [], "listed_on": None, **kw}  # fmt: skip


def screener(
    entries: list[dict], *, missing: str | None = None, old_days: int = 0
) -> SourceOutcome:
    now = datetime.now(UTC)
    srcs = [{"id": s, "status": "ok", "downloaded_at": now.isoformat()} for s in SOURCE_ORDER]
    if missing:
        srcs = [s for s in srcs if s["id"] != missing]
    snap = Snapshot(Path("x.json"), now, srcs, entries)
    problems = snap.problems(now, 7)
    return SourceOutcome("ok", "; ".join(problems), Screener(snap))


LISTED = entry("25", ["Ravi Kumar Subramaniam"], dob=["1990-02-02"], nationality=["india"])


def test_a_clean_bundle_screens_to_nothing(tmp_path: Path) -> None:
    c = ctx(
        [company_profile_doc()],
        settings(tmp_path, sanctions_screen="on"),
        {"sanctions": screener([LISTED])},
    )
    assert v.x_sanctions_screen(c) == []


def test_an_exact_name_alone_is_a_medium_possible_match_for_a_person_to_adjudicate(
    tmp_path: Path,
) -> None:
    docs = [company_profile_doc(directors=[("Ravi Kumar Subramaniam", None)], shareholders=[])]
    c = ctx(docs, settings(tmp_path, sanctions_screen="on"), {"sanctions": screener([LISTED])})
    (f,) = v.x_sanctions_screen(c)
    assert f.rule == "x_sanctions_screen" and f.severity == "medium" and f.table == "directors"
    assert "Possible match, a person must adjudicate" in f.explanation
    assert "UN Security Council consolidated list entry IQi.25" in f.evidence
    assert "exact name" in f.evidence and "Ravi Kumar Subramaniam" in f.evidence


def test_name_plus_the_date_of_birth_on_the_passport_is_high(tmp_path: Path) -> None:
    docs = [company_profile_doc(directors=[("Ravi Kumar Subramaniam", None)], shareholders=[]),
            passport_doc("d6", holder_name="Ravi Kumar Subramaniam", date_of_birth="1990-02-02", nationality="Indian")]  # fmt: skip
    c = ctx(docs, settings(tmp_path, sanctions_screen="on"), {"sanctions": screener([LISTED])})
    flags = v.x_sanctions_screen(c)
    assert [f.severity for f in flags] == ["high"]
    assert "date of birth" in flags[0].evidence and set(flags[0].document_ids) == {"d1", "d6"}


def test_a_listed_company_is_found_among_counterparties(tmp_path: Path) -> None:
    inv = invoice_doc("d4", supplier="Northern Trading Company Pte Ltd")
    listed = entry("9", ["Northern Trading Company"], kind="entity")
    c = ctx([inv], settings(tmp_path, sanctions_screen="on"), {"sanctions": screener([listed])})
    (f,) = v.x_sanctions_screen(c)
    assert f.severity == "medium" and "invoice supplier" in f.evidence


def test_an_incomplete_snapshot_is_never_a_clean_result(tmp_path: Path) -> None:
    c = ctx([company_profile_doc()], settings(tmp_path, sanctions_screen="on"),
            {"sanctions": screener([LISTED], missing="ofac")})  # fmt: skip
    e = skipped(v.x_sanctions_screen, c)
    assert "OFAC" in e.reason and e.code is None  # counts as not run: coverage drops, never a pass
    docs = [company_profile_doc(directors=[("Ravi Kumar Subramaniam", None)], shareholders=[])]
    c = ctx(
        docs,
        settings(tmp_path, sanctions_screen="on"),
        {"sanctions": screener([LISTED], missing="ofac")},
    )
    assert [f.severity for f in v.x_sanctions_screen(c)] == ["medium"]  # a hit is still reported


def test_sanctions_off_is_not_configured_and_a_missing_snapshot_is_unavailable(
    tmp_path: Path,
) -> None:
    e = skipped(v.x_sanctions_screen, ctx([company_profile_doc()], settings(tmp_path)))
    assert e.code == "not_configured" and "GLDB_SANCTIONS_SCREEN=on" in e.reason
    bad = SourceOutcome("unavailable", "no sanctions snapshot at current.json")
    e = skipped(
        v.x_sanctions_screen,
        ctx([company_profile_doc()], settings(tmp_path, sanctions_screen="on"), {"sanctions": bad}),
    )
    assert e.code is None


def test_sanctions_screening_is_offline_so_private_mode_does_not_stop_it(tmp_path: Path) -> None:
    s = private(settings(tmp_path, sanctions_screen="on"))
    assert (
        v.x_sanctions_screen(ctx([company_profile_doc()], s, {"sanctions": screener([LISTED])}))
        == []
    )


# -------------------------------------------------------------------------------- GLEIF


def gleif(**kw) -> GleifRecord:  # noqa: ANN003
    base = {"lei": "549300ZG62N2599PWR15", "legal_name": APPLICANT_NAME, "entity_status": "ACTIVE",
            "registration_status": "ISSUED", "registered_as": UEN, "jurisdiction": "SG",
            "address": "1 Raffles Place, Singapore", "postal_code": "048616"}  # fmt: skip
    return GleifRecord(**{**base, **kw})


def profile(address: str | None = "1 Raffles Place, Singapore 048616", name: str = APPLICANT_NAME):  # noqa: ANN201
    doc = company_profile_doc()
    doc.fields["company_name"] = doc.fields["company_name"].model_copy(update={"value": name})
    doc.fields["registered_address"] = doc.fields["registered_address"].model_copy(
        update={"value": address}
    )
    return doc


def test_gleif_a_matching_record_raises_nothing_and_no_lei_is_normal(tmp_path: Path) -> None:
    s = settings(tmp_path, gleif="on")
    assert (
        v.x_entity_vs_gleif(ctx([profile()], s, {"gleif": SourceOutcome("ok", data=gleif())})) == []
    )
    assert v.x_entity_vs_gleif(ctx([profile()], s, {"gleif": SourceOutcome("ok", data=None)})) == []


def test_gleif_a_different_name_is_medium_a_different_postal_code_low_inactive_medium(
    tmp_path: Path,
) -> None:
    s = settings(tmp_path, gleif="on")
    out = lambda rec: {"gleif": SourceOutcome("ok", data=rec)}  # noqa: E731
    (f,) = v.x_entity_vs_gleif(ctx([profile(name="Orchid Logistics Pte Ltd")], s, out(gleif())))
    assert (f.severity, f.id.rsplit("~", 1)[-1]) == ("medium", "name")
    (f,) = v.x_entity_vs_gleif(ctx([profile()], s, out(gleif(postal_code="819829"))))
    assert (f.severity, f.id.rsplit("~", 1)[-1]) == ("low", "address")
    (f,) = v.x_entity_vs_gleif(ctx([profile()], s, out(gleif(entity_status="INACTIVE"))))
    assert (f.severity, f.id.rsplit("~", 1)[-1]) == ("medium", "status")


def test_gleif_parents_are_context_only(tmp_path: Path) -> None:
    rec = gleif(
        direct_parent=GleifParent("L1", "Temasek Holdings"),
        ultimate_parent=GleifParent("L2", "Government"),
    )
    (f,) = v.x_entity_vs_gleif(
        ctx([profile()], settings(tmp_path, gleif="on"), {"gleif": SourceOutcome("ok", data=rec)})
    )
    assert (
        f.severity == "info"
        and "Temasek Holdings" in f.evidence
        and "Context, not a finding" in f.explanation
    )


# ------------------------------------------------------------------------------- OneMap

ROW = PostalRecord(
    "048616", "1", "RAFFLES PLACE", "ONE RAFFLES PLACE", "1 RAFFLES PLACE SINGAPORE 048616"
)


def onemap_ctx(tmp_path: Path, address: str, records, registry=None):  # noqa: ANN001, ANN201
    s = settings(tmp_path, onemap_token="tok")
    postal = address.split()[-1]
    return ctx(
        [profile(address)],
        s,
        {f"onemap:{postal}": SourceOutcome("ok", data=records)},
        registry=registry,
    )


def test_onemap_a_real_address_with_its_own_postal_code_raises_nothing(tmp_path: Path) -> None:
    assert (
        v.x_address_vs_registry(onemap_ctx(tmp_path, "1 Raffles Pl, Singapore 048616", [ROW])) == []
    )


def test_onemap_a_postal_code_that_does_not_exist_is_flagged(tmp_path: Path) -> None:
    (f,) = v.x_address_vs_registry(onemap_ctx(tmp_path, "1 Raffles Place, Singapore 999999", []))
    assert f.severity == "medium" and f.id.endswith("~postal") and "999999" in f.evidence


def test_onemap_a_road_that_does_not_belong_to_the_postal_code_is_flagged(tmp_path: Path) -> None:
    (f,) = v.x_address_vs_registry(onemap_ctx(tmp_path, "88 Orchard Road, Singapore 048616", [ROW]))
    assert f.id.endswith("~street") and "RAFFLES PLACE" in f.evidence


def test_onemap_a_different_postal_code_in_the_register_is_low(tmp_path: Path) -> None:
    ent = RegistryEntity(uen=UEN, entity_name=APPLICANT_NAME, entity_type="x", status="Live", incorporation_date=date(2019, 1, 1),
                         principal_activity="x", registered_address="5 Shenton Way, Singapore 068808", source="acra")  # fmt: skip
    reg = MockRegistry(
        schema_version="1", generated_by="t", as_of=date(2026, 11, 2), entities=[ent]
    )
    (f,) = v.x_address_vs_registry(
        onemap_ctx(tmp_path, "1 Raffles Place, Singapore 048616", [ROW], reg)
    )
    assert f.severity == "low" and f.id.endswith("~registry")


# --------------------------------------------------------------------------------- IRAS


def gst_inv(gst: str | None = "M2-1234567-8", gst_amount: str = "100.00"):  # noqa: ANN201
    doc = invoice_doc("d4", supplier="Acme Trading Pte Ltd", supplier_uen="201512345K")
    doc.fields["supplier_gst_no"] = doc.fields["supplier_gst_no"].model_copy(update={"value": gst})
    doc.fields["gst_amount"] = doc.fields["gst_amount"].model_copy(update={"value": gst_amount})
    return doc


def gst(**kw) -> GstStatus:  # noqa: ANN003
    base = {"reg_id": "M2-1234567-8", "registered": True, "name": "ACME TRADING PTE. LTD.",
            "gst_number": "M2-1234567-8", "registered_from": "2015-01-01", "removed_on": None}  # fmt: skip
    return GstStatus(**{**base, **kw})


def iras_ctx(tmp_path: Path, status, doc=None):  # noqa: ANN001, ANN201
    return ctx(
        [doc or gst_inv()],
        settings(tmp_path, iras_api_key="key"),
        {"iras:M2-1234567-8": SourceOutcome("ok", data=status)},
    )


def test_iras_a_registered_supplier_raises_nothing(tmp_path: Path) -> None:
    assert v.inv_gst_registered(iras_ctx(tmp_path, gst())) == []


def test_iras_not_registered_while_charging_gst_is_flagged(tmp_path: Path) -> None:
    (f,) = v.inv_gst_registered(iras_ctx(tmp_path, gst(registered=False)))
    assert f.severity == "medium" and f.id.endswith("~not_registered")


def test_iras_a_name_that_differs_and_dates_that_do_not_fit_are_flagged(tmp_path: Path) -> None:
    (f,) = v.inv_gst_registered(iras_ctx(tmp_path, gst(name="ORCHID FREIGHT PTE. LTD.")))
    assert f.id.endswith("~name")
    (f,) = v.inv_gst_registered(iras_ctx(tmp_path, gst(registered_from="2026-06-01")))
    assert f.severity == "low" and f.id.endswith("~before_registration")
    (f,) = v.inv_gst_registered(iras_ctx(tmp_path, gst(removed_on="2025-12-31")))
    assert f.id.endswith("~after_removal")


def test_iras_disabled_without_a_key(tmp_path: Path) -> None:
    e = skipped(v.inv_gst_registered, ctx([gst_inv()], settings(tmp_path)))
    assert e.code == "not_configured" and "GLDB_IRAS_API_KEY" in e.reason


# ------------------------------------------------- not configured / private / unavailable

CASES = [
    ("x_entity_vs_gleif", v.x_entity_vs_gleif, "gleif", {"gleif": "on"}, lambda: [profile()], "gleif"),
    ("x_address_vs_registry", v.x_address_vs_registry, "onemap", {"onemap_token": "tok"}, lambda: [profile()], "onemap:048616"),
    ("inv_gst_registered", v.inv_gst_registered, "iras", {"iras_api_key": "key"}, lambda: [gst_inv()], "iras:M2-1234567-8"),
]  # fmt: skip


@pytest.mark.parametrize(("rule", "fn", "source", "kw", "docs", "key"), CASES)
def test_not_configured_private_mode_and_unavailable_never_flag(
    tmp_path, rule, fn, source, kw, docs, key
) -> None:  # noqa: ANN001
    off = skipped(fn, ctx(docs(), settings(tmp_path)))
    assert off.code == "not_configured"  # no switch, token or key: outside coverage
    blocked = skipped(fn, ctx(docs(), private(settings(tmp_path, **kw))))
    assert blocked.code == "private_mode" and "GLDB_PRIVATE_MODE" in blocked.reason
    down = skipped(
        fn,
        ctx(
            docs(),
            settings(tmp_path, **kw),
            {key: SourceOutcome("unavailable", "could not be asked")},
        ),
    )
    assert down.code is None  # configured but unreachable: counts as not run, never a flag


@pytest.mark.parametrize(("rule", "fn", "source", "kw", "docs", "key"), CASES)
def test_the_runner_records_the_skip_code_and_coverage_ignores_it(
    tmp_path, rule, fn, source, kw, docs, key
) -> None:  # noqa: ANN001
    c = ctx(docs(), settings(tmp_path))
    runs = {r.rule: r for r in run_stage_checks(c, "crossdoc") + run_stage_checks(c, "rules")}
    assert runs[rule].status == "skipped" and runs[rule].code == "not_configured"
    c.case.check_runs = [
        CheckRun(rule=rule, stage="rules", status="skipped", reason="x", code="not_configured")
    ]
    assert compute_coverage(c.case).applicable == 0
    c.case.check_runs = [CheckRun(rule=rule, stage="rules", status="skipped", reason="x")]
    assert compute_coverage(c.case).applicable == 1  # the same skip without the code counts


# --------------------------------------------------------------------------- bank details

IBAN_OK = "GB82 WEST 1234 5698 7654 32"
IBAN_BAD = "GB82 WEST 1234 5698 7654 33"


def bank_ctx(tmp_path: Path, text: str, doc_type: str = "invoice", **fields):  # noqa: ANN003, ANN201
    doc = make_doc(doc_type, id="d4", fields=fields, words=[Word(page=1, text=t, x=0, y=0, w=1, h=1) for t in text.split()])  # fmt: skip
    return ctx([doc], settings(tmp_path))


def test_a_valid_iban_and_bic_raise_nothing(tmp_path: Path) -> None:
    assert (
        bank.inv_bank_details_valid(
            bank_ctx(tmp_path, f"Pay to IBAN {IBAN_OK} BIC NWBKGB2L total 100")
        )
        == []
    )


def test_nothing_printed_is_a_pass_not_a_skip(tmp_path: Path) -> None:
    c = bank_ctx(tmp_path, "Pay by cheque")
    assert bank.inv_bank_details_valid(c) == []
    (run,) = [r for r in run_stage_checks(c, "rules") if r.rule == "inv_bank_details_valid"]
    assert run.status == "ran"


def test_a_wrong_check_digit_is_flagged_low(tmp_path: Path) -> None:
    (f,) = bank.inv_bank_details_valid(bank_ctx(tmp_path, f"IBAN: {IBAN_BAD}"))
    assert (
        f.severity == "low"
        and "mod 97" in f.evidence
        and IBAN_BAD.replace(" ", "")[-4:] in f.evidence
    )


def test_an_iban_shaped_string_of_the_wrong_length_for_its_country_is_flagged_only_when_labelled(
    tmp_path: Path,
) -> None:
    short = "GB82 WEST 1234 5698 7654"
    (f,) = bank.inv_bank_details_valid(bank_ctx(tmp_path, f"IBAN {short}"))
    assert "22 characters" in f.evidence
    assert bank.inv_bank_details_valid(bank_ctx(tmp_path, f"ref {short} end")) == []


def test_a_bad_bic_and_a_bic_of_another_country_are_flagged(tmp_path: Path) -> None:
    (f,) = bank.inv_bank_details_valid(bank_ctx(tmp_path, "SWIFT: DBSSXXSG"))
    assert "BIC/SWIFT" in f.evidence
    (f,) = bank.inv_bank_details_valid(bank_ctx(tmp_path, f"IBAN {IBAN_OK} BIC DBSSSGSG"))
    assert "different countries" in f.explanation


def test_a_statement_account_number_that_is_an_iban_is_checked_and_plain_numbers_are_not(
    tmp_path: Path,
) -> None:
    (f,) = bank.inv_bank_details_valid(
        bank_ctx(tmp_path, "", "bank_statement", account_number="GB82WEST12345698765433")
    )
    assert f.rule == "inv_bank_details_valid"
    assert (
        bank.inv_bank_details_valid(
            bank_ctx(tmp_path, "", "bank_statement", account_number="123-456-789")
        )
        == []
    )


def test_an_ordinary_reference_is_not_taken_for_an_iban(tmp_path: Path) -> None:
    text = "Invoice INV2024001234567 PO IN24ABCD12345678 ref SG12 3456 7890 total 1,000.00 GST M2-1234567-8"
    assert bank.inv_bank_details_valid(bank_ctx(tmp_path, text)) == []


def test_sanctions_and_gleif_leave_a_note_of_what_they_asked(tmp_path: Path) -> None:
    c = ctx(
        [company_profile_doc()],
        settings(tmp_path, sanctions_screen="on"),
        {"sanctions": screener([LISTED])},
    )
    assert v.x_sanctions_screen(c) == []
    note = c.run_notes["x_sanctions_screen"]
    assert note.startswith("Screened ") and "name(s) against" in note and note.endswith("no match.")
    docs = [company_profile_doc(directors=[("Ravi Kumar Subramaniam", None)], shareholders=[])]
    c2 = ctx(docs, settings(tmp_path, sanctions_screen="on"), {"sanctions": screener([LISTED])})
    v.x_sanctions_screen(c2)
    assert "possible match(es) for a person to adjudicate" in c2.run_notes["x_sanctions_screen"]

    s = settings(tmp_path, gleif="on")
    c3 = ctx([profile()], s, {"gleif": SourceOutcome("ok", data=gleif())})
    v.x_entity_vs_gleif(c3)
    assert c3.run_notes["x_entity_vs_gleif"].startswith("Asked GLEIF for ") and c3.run_notes[
        "x_entity_vs_gleif"
    ].endswith("The documents agree.")
    c4 = ctx([profile()], s, {"gleif": SourceOutcome("ok", data=None)})
    v.x_entity_vs_gleif(c4)
    assert "holds no legal-entity record" in c4.run_notes["x_entity_vs_gleif"]


def test_a_possible_match_does_not_say_names_are_shared_by_people_when_the_entry_is_a_company(
    tmp_path: Path,
) -> None:
    inv = invoice_doc("d4", supplier="Northern Trading Company Pte Ltd")
    listed = entry("9", ["Northern Trading Company"], kind="entity")
    listed["programs"] = ["DPRK"]
    listed["listed_on"] = "2016-03-02"
    c = ctx([inv], settings(tmp_path, sanctions_screen="on"), {"sanctions": screener([listed])})
    (f,) = v.x_sanctions_screen(c)
    assert "A name alone does not identify a company" in f.explanation
    assert "registration number, country and address" in f.explanation
    assert (
        "shared by many people" not in f.explanation and "the person is listed" not in f.explanation
    )
    assert "the list gives: programme DPRK, listed 2016-03-02" in f.evidence


def test_a_possible_match_on_a_person_says_what_to_compare(tmp_path: Path) -> None:
    docs = [company_profile_doc(directors=[("Ravi Kumar Subramaniam", None)], shareholders=[])]
    c = ctx(docs, settings(tmp_path, sanctions_screen="on"), {"sanctions": screener([LISTED])})
    (f,) = v.x_sanctions_screen(c)
    assert "A name alone does not identify a person: many people share one." in f.explanation
    assert "date of birth, nationality and ID number" in f.explanation
