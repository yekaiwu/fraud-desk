"""Grouping of correlated flags into findings (docs/scoring-and-queue.md 4.1 to 4.3, tests 2 to 7)."""

from __future__ import annotations

import random

import pytest

from app.checks.registry import make_flag
from app.core.models import Case, Flag, Region
from app.core.testing import make_ctx, make_doc
from app.findings import (
    AML_ROUTING_RULES,
    FAMILIES,
    HARD_STOP_RULES,
    REGION_OVERLAP_MIN,
    SEVERITY_RANK,
    TITLE_MAX_CHARS,
    cap_words,
    flag_discriminator,
    group_flags,
    group_key,
    region_overlap,
    sentence,
)


def fl(
    rule: str,
    docs: list[str],
    sev: str = "high",
    *,
    disc: str | None = None,
    expl: str = "A thing is wrong here. Second sentence.",
    **kw,  # noqa: ANN003
) -> Flag:
    return make_flag(
        rule,
        docs=docs,
        evidence=f"evidence for {rule}",
        explanation=expl,
        severity=sev,  # type: ignore[arg-type]
        discriminator=disc,
        **kw,
    )


def case_with(*docs) -> Case:  # noqa: ANN002
    return make_ctx(list(docs)).case


def box(x: float, y: float, w: float = 0.25, h: float = 0.25, file_id: str = "f1", page: int = 1):
    return Region(file_id=file_id, page=page, x=x, y=y, w=w, h=h)


# ------------------------------------------------------------------------- discriminator parsing


def test_flag_discriminator_forms() -> None:
    assert flag_discriminator(fl("x_directors", ["d1"], disc="Tan Wei")) == "tan_wei"
    assert flag_discriminator(fl("x_directors", ["d1"])) is None
    flag = fl("x_directors", ["d1"], disc="abc")
    assert flag_discriminator(flag.model_copy(update={"id": flag.id + "#2"})) == "abc"
    assert flag_discriminator(flag.model_copy(update={"id": "r.d1.-~  Mixed Case ~x"})) == (
        "mixed case ~x"
    )
    assert flag_discriminator(flag.model_copy(update={"id": "r.d1.-~#3"})) is None
    assert flag_discriminator(flag.model_copy(update={"id": "r.d1.-~ "})) is None


# --------------------------------------------------------------------------- test 2 and 3: shared


def three_holder_flags(disc: str | None) -> list[Flag]:
    return [fl("x_ownership_vs_registry", [f"d{n}"], disc=disc) for n in (1, 2, 3)]


def test_same_missing_shareholder_on_three_documents_is_one_finding() -> None:
    case = case_with(*[make_doc("ownership_chart", id=f"d{n}", file_id=f"f{n}") for n in (1, 2, 3)])
    flags = three_holder_flags("lim_ah_kow")
    (finding,) = group_flags(case, flags)
    assert finding.weight == 30 and finding.severity == "high"
    assert finding.group_key == "holder:lim_ah_kow"
    assert sorted(finding.flag_ids) == sorted(f.id for f in flags) and len(finding.flag_ids) == 3
    assert finding.document_ids == ["d1", "d2", "d3"]
    assert finding.title.endswith("(seen on 3 documents)")
    assert finding.title.startswith("A thing is wrong here (seen")  # first sentence only
    assert finding.rules == ["x_ownership_vs_registry"]


def test_holder_family_joins_chart_register_and_profile_rules() -> None:
    case = case_with()
    flags = [
        fl("x_ownership_vs_registry", ["d1"], disc="lim"),
        fl("kyc_shareholdings", ["d2"], disc="lim"),
        fl("kyc_shareholdings", ["d3"], disc="other_person"),
    ]
    keys = sorted(f.group_key for f in group_flags(case, flags))
    assert keys == ["holder:lim", "holder:other_person"]


def test_without_discriminator_findings_degrade_to_one_per_document() -> None:
    case = case_with()
    findings = group_flags(case, three_holder_flags(None))
    assert len(findings) == 3
    assert sum(f.weight for f in findings) == 90
    assert {f.group_key for f in findings} == {
        f"rule:x_ownership_vs_registry:d{n}" for n in (1, 2, 3)
    }


@pytest.mark.parametrize(
    ("rule", "key_prefix"),
    [
        ("x_directors", "director"),
        ("x_entity", "entity"),
        ("fs_entity", "entity"),
        ("bs_holder", "entity"),
        ("x_remitter_vs_buyer", "remitter"),
        ("inv_duplicate", "dup"),
        ("signature_reuse", "dup"),
        ("file_reuse", "dup"),
    ],
)
def test_shared_families_key_on_discriminator(rule: str, key_prefix: str) -> None:
    case = case_with()
    a, b = fl(rule, ["d1"], disc="abc"), fl(rule, ["d2"], disc="abc")
    (finding,) = group_flags(case, [a, b])
    assert finding.group_key == f"{key_prefix}:abc"
    assert group_key(fl(rule, ["d1"]), case) == f"rule:{rule}:d1"


def test_duplicate_pair_is_one_finding_when_both_copies_are_flagged() -> None:
    case = case_with()
    flags = [
        fl("inv_duplicate", ["d1"], disc="a1b2c3d4e5f6"),
        fl("inv_duplicate", ["d2"], disc="a1b2c3d4e5f6"),
    ]
    (finding,) = group_flags(case, flags)
    assert finding.document_ids == ["d1", "d2"] and finding.hard_stop


# ------------------------------------------------------------------------------ test 4: per doc


def test_statement_arithmetic_is_one_finding_per_document() -> None:
    case = case_with()
    on_d3 = [fl("bs_totals", ["d3"]), fl("bs_running_balance", ["d3"])]
    (finding,) = group_flags(case, on_d3)
    assert finding.group_key == "stmt_arith:d3" and finding.weight == 30
    assert sorted(finding.rules) == ["bs_running_balance", "bs_totals"]

    both = [*on_d3, fl("bs_totals", ["d4"]), fl("bs_running_balance", ["d4"])]
    findings = group_flags(case, both)
    assert sorted(f.group_key for f in findings) == ["stmt_arith:d3", "stmt_arith:d4"]
    assert sum(f.weight for f in findings) == 60


@pytest.mark.parametrize(
    ("rules", "key"),
    [
        (["inv_lines", "inv_gst", "inv_total"], "inv_arith:d2"),
        (["fs_balance", "fs_subtotals", "fs_cash_tie"], "fs_arith:d2"),
        (["id_format", "id_checksum", "passport_mrz", "date_valid"], "id_doc:d2"),
    ],
)
def test_other_per_document_families(rules: list[str], key: str) -> None:
    case = case_with()
    (finding,) = group_flags(case, [fl(r, ["d2"]) for r in rules])
    assert finding.group_key == key


def test_unlisted_rule_defaults_to_rule_and_document_set() -> None:
    case = case_with()
    flags = [fl("bs_dates", ["d1"], "medium", row=1), fl("bs_dates", ["d1"], "medium", row=5)]
    (finding,) = group_flags(case, flags)
    assert finding.group_key == "rule:bs_dates:d1" and finding.weight == 15
    other = group_flags(case, [fl("bs_dates", ["d2"], "medium")])
    assert other[0].group_key == "rule:bs_dates:d2"


def test_flag_without_documents_uses_region_file_then_case() -> None:
    case = case_with()
    assert group_key(fl("some_rule", [], "low", region=box(0.1, 0.1)), case) == "rule:some_rule:f1"
    assert group_key(fl("some_rule", [], "low"), case) == "rule:some_rule:case"


# -------------------------------------------------------------------------------- per file meta


def test_metadata_flags_of_one_file_are_one_finding() -> None:
    d1 = make_doc("bank_statement", id="d1", file_id="f1")
    d2 = make_doc("invoice", id="d2", file_id="f1")
    d3 = make_doc("invoice", id="d3", file_id="f2")
    case = case_with(d1, d2, d3)
    flags = [
        fl("pdf_editor_software", ["d1"], "medium"),
        fl("pdf_modified", ["d2"], "low"),
        fl("pdf_producer_unexpected", ["d3"], "medium"),
    ]
    findings = group_flags(case, flags)
    assert sorted(f.group_key for f in findings) == ["meta:f1", "meta:f2"]
    f1 = next(f for f in findings if f.group_key == "meta:f1")
    assert f1.severity == "medium" and len(f1.flag_ids) == 2


def test_unknown_document_ids_never_raise() -> None:
    case = case_with()
    flag = fl("pdf_modified", ["d99"], "low")
    assert group_key(flag, case) == "meta:case"
    assert group_flags(case, [flag])[0].group_key == "meta:case"
    with_region = fl("pdf_modified", ["d99"], "low", region=box(0.1, 0.1, file_id="f7"))
    assert group_key(with_region, case) == "meta:f7"  # no document resolves -> region file


# ------------------------------------------------------------------------------ test 5: pattern


def test_ten_unpaid_invoices_are_one_pattern_finding() -> None:
    case = case_with()
    flags = [fl("x_invoice_vs_receipts", [f"d{n}"], "medium") for n in range(1, 11)]
    (finding,) = group_flags(case, flags)
    assert finding.group_key == "pattern:x_invoice_vs_receipts"
    assert finding.weight == 15 and len(finding.flag_ids) == 10
    assert finding.title.endswith("(+9 similar)") and "seen on" not in finding.title


def test_pattern_with_one_member_has_no_suffix() -> None:
    (finding,) = group_flags(case_with(), [fl("inv_sequence", ["d1"], "low")])
    assert finding.title == "A thing is wrong here"


# -------------------------------------------------------------------------------- test 6: anchor


def test_invoices_citing_one_contract_are_one_finding_per_contract() -> None:
    docs = [make_doc("invoice", id=f"d{n}") for n in (1, 2, 3, 4)]
    docs += [make_doc("contract", id="d7"), make_doc("purchase_order", id="d8")]
    case = case_with(*docs)
    flags = [fl("x_invoice_vs_contract", [f"d{n}", "d7"], "medium") for n in (1, 2, 3)]
    flags.append(fl("x_invoice_vs_contract", ["d4", "d8"], "medium"))
    findings = group_flags(case, flags)
    keys = sorted(f.group_key for f in findings)
    assert keys == ["anchor:x_invoice_vs_contract:d7", "anchor:x_invoice_vs_contract:d8"]
    d7 = next(f for f in findings if f.group_key.endswith(":d7"))
    assert len(d7.flag_ids) == 3


def test_anchor_without_contract_document_falls_back_to_default() -> None:
    case = case_with(make_doc("invoice", id="d1"))
    flag = fl("x_invoice_vs_contract", ["d1", "d9"], "medium")
    assert group_key(flag, case) == "rule:x_invoice_vs_contract:d1+d9"


# --------------------------------------------------------------------------- test 7: regions


def shift_box(fraction: float) -> Region:
    # a 0.25 box overlapping box(0.25, 0.25) by ``fraction`` of its own width
    return box(0.25 + 0.25 * (1 - fraction), 0.25)


@pytest.mark.parametrize(
    ("second", "findings"),
    [
        (shift_box(0.6), 1),
        (box(0.375, 0.25), 1),  # overlap exactly 0.5
        (box(0.3775, 0.25), 2),  # 0.49
        (box(0.5, 0.25), 2),  # boxes only touch: 0.0
        (box(0.7, 0.7), 2),
    ],
)
def test_region_clustering_threshold(second: Region, findings: int) -> None:
    case = case_with()
    flags = [
        fl("ela_region", ["d1"], "medium", region=box(0.25, 0.25)),
        fl("copy_move", ["d1"], "medium", region=second),
    ]
    result = group_flags(case, flags)
    assert len(result) == findings
    if findings == 1:
        assert result[0].group_key == "region:f1:p1:1" and result[0].severity == "medium"
        assert sorted(result[0].rules) == ["copy_move", "ela_region"]  # severity never raised


def test_lone_low_ela_and_copy_move_regions_count_once_per_rule() -> None:
    """A clean scanned bundle makes ELA fire page after page: one noisy check counts once."""
    from app.core.models import SCORE_WEIGHTS

    docs = [make_doc("bank_statement", id=f"d{n}", file_id=f"f{n}") for n in range(1, 7)]
    case = case_with(*docs)
    flags = [
        fl("ela_region", [f"d{n}"], "low", region=box(0.1 * n, 0.2, file_id=f"f{n}", page=p))
        for n in range(1, 7)
        for p in (1, 2)
    ] + [fl("copy_move", [f"d{n}"], "low", region=box(0.3, 0.3, file_id=f"f{n}")) for n in (1, 2)]
    findings = group_flags(case, flags)
    assert sorted(f.group_key for f in findings) == ["noisy:copy_move", "noisy:ela_region"]
    assert sum(f.weight for f in findings) == 2 * SCORE_WEIGHTS["low"]
    assert not any(f.hard_stop for f in findings)
    assert group_key(flags[0], case) == "noisy:ela_region"


def test_a_corroborated_region_keeps_its_cluster_and_is_not_noise() -> None:
    case = case_with(make_doc("bank_statement", id="d1", file_id="f1"))
    flags = [
        fl("ela_region", ["d1"], "high", region=box(0.25, 0.25)),
        fl("copy_move", ["d1"], "high", region=shift_box(0.6)),
        fl("ela_region", ["d1"], "low", region=box(0.7, 0.7)),
    ]
    assert sorted(f.group_key for f in group_flags(case, flags)) == [
        "noisy:ela_region",
        "region:f1:p1:1",
    ]


def test_region_overlap_values() -> None:
    a = box(0.25, 0.25)
    assert region_overlap(a, box(0.25, 0.25)) == 1.0
    assert region_overlap(a, box(0.3, 0.3, 0.05, 0.05)) == pytest.approx(1.0)  # containment
    assert region_overlap(a, box(0.5, 0.25)) == 0.0
    assert REGION_OVERLAP_MIN == 0.5


def test_region_clusters_are_numbered_by_position_per_page() -> None:
    case = case_with()
    low = fl("ela_region", ["d1"], "medium", region=box(0.1, 0.7, 0.1, 0.1))
    high = fl("copy_move", ["d1"], "medium", region=box(0.6, 0.1, 0.1, 0.1))
    other_page = fl("noise_mismatch", ["d1"], "medium", region=box(0.1, 0.9, 0.1, 0.05, page=2))
    no_region = fl("font_span_outlier", ["d1"], "medium")
    findings = group_flags(case, [low, high, other_page, no_region])
    assert {f.group_key for f in findings} == {
        "region:f1:p1:1",
        "region:f1:p1:2",
        "region:f1:p2:1",
        "rule:font_span_outlier:d1",
    }
    by_key = {f.group_key: f for f in findings}
    assert by_key["region:f1:p1:1"].flag_ids == [high.id]  # smaller y first


def test_region_chain_links_through_a_middle_box() -> None:
    a = box(0.10, 0.1, 0.2, 0.2)
    b = box(0.20, 0.1, 0.2, 0.2)  # overlaps a by 0.5 and c by 0.5
    c = box(0.30, 0.1, 0.2, 0.2)  # does not touch a
    flags = [
        fl("ela_region", ["d1"], "medium", region=a),
        fl("copy_move", ["d1"], "medium", region=b),
        fl("noise_mismatch", ["d1"], "medium", region=c),
    ]
    assert len(group_flags(case_with(), flags)) == 1


# ----------------------------------------------------------------------- finding fields, ranking


def test_title_suffix_cap_and_word_boundary() -> None:
    long = "word " * 60
    flags = [fl("x_directors", [f"d{n}"], disc="abc", expl=long) for n in (1, 2)]
    (finding,) = group_flags(case_with(), flags)
    assert len(finding.title) <= TITLE_MAX_CHARS
    assert finding.title.endswith("…" + " (seen on 2 documents)")
    assert "wor…" not in finding.title  # cut at a word boundary
    assert len(finding.summary) <= 300


def test_cap_words_edges() -> None:
    assert cap_words("short", 10) == "short"
    assert cap_words("alpha beta gamma", 12) == "alpha beta…"
    assert cap_words("abcdefghijkl", 6) == "abcde…"
    assert cap_words("x", 0) == ""


def test_sentence_keeps_decimals() -> None:
    assert sentence("Revenue 2.4M vs 0.9M.  Second one.") == "Revenue 2.4M vs 0.9M"
    assert sentence("  spaced \n out text ") == "spaced out text"
    assert sentence("No capital after. lower case continues") == (
        "No capital after. lower case continues"
    )


def test_summary_joins_explanation_and_evidence() -> None:
    flag = fl("x_directors", ["d1"], expl="Line one.\n  Line   two.")
    (finding,) = group_flags(case_with(), [flag])
    assert finding.summary == "Line one. Line two. Evidence: evidence for x_directors"


def test_severity_is_the_max_over_members_without_a_bump() -> None:
    flags = [fl("bs_totals", ["d1"], "low"), fl("bs_running_balance", ["d1"], "medium")]
    (finding,) = group_flags(case_with(), flags)
    assert finding.severity == "medium" and finding.weight == 15
    assert finding.flag_ids[0].startswith("bs_running_balance")  # strongest member first
    assert finding.title == "A thing is wrong here"


def test_info_flags_are_grouped_with_weight_zero() -> None:
    (finding,) = group_flags(case_with(), [fl("doc_unclassified", ["d1"], "info")])
    assert finding.weight == 0 and finding.severity == "info" and not finding.hard_stop


def test_hard_stop_rules() -> None:
    case = case_with()
    medium_editor = group_flags(case, [fl("pdf_editor_software", ["d1"], "medium")])[0]
    assert medium_editor.hard_stop
    medium_entity = group_flags(case, [fl("fs_entity", ["d1"], "medium", disc="abc")])[0]
    assert not medium_entity.hard_stop
    low_reuse = group_flags(case, [fl("file_reuse", ["d1"], "low")])[0]
    assert not low_reuse.hard_stop  # low/info never hard-stop
    assert group_flags(case, [fl("bs_totals", ["d1"], "high")])[0].hard_stop


def test_constants_shape() -> None:
    assert len(HARD_STOP_RULES) == 15 and "file_reuse" in HARD_STOP_RULES
    assert {"x_sof_support", "x_remitter_vs_buyer", "bs_pass_through"} == AML_ROUTING_RULES
    assert SEVERITY_RANK == {"high": 0, "medium": 1, "low": 2, "info": 3}
    rules = [r for fam in FAMILIES for r in fam.rules]
    assert len(rules) == len(set(rules)), "a rule belongs to exactly one family"
    assert [fam.kind for fam in FAMILIES][:4] == ["per_doc"] * 4


def test_ranking_ids_and_tie_breaks() -> None:
    case = case_with()
    flags = [
        fl("bs_dates", ["d1"], "low"),
        fl("bs_dates", ["d2"], "medium"),
        fl("pdf_editor_software", ["d3"], "medium"),  # hard stop beats the plain medium
        fl("x_entity", ["d4"], "high", disc="abc"),
        fl("x_entity", ["d5"], "high", disc="abc"),  # more documents wins among highs
        fl("bs_totals", ["d6"], "high"),
        fl("doc_unclassified", ["d7"], "info"),
    ]
    findings = group_flags(case, flags)
    assert [f.id for f in findings] == [f"fnd_{n}" for n in range(1, 7)]
    assert findings[0].group_key == "entity:abc"
    assert findings[1].group_key == "stmt_arith:d6"
    sev = [SEVERITY_RANK[f.severity] for f in findings]
    assert sev == sorted(sev)
    assert findings[2].group_key == "meta:case"  # the hard-stop medium before the plain medium
    assert findings[3].group_key == "rule:bs_dates:d2"


def test_input_order_is_irrelevant() -> None:
    case = case_with(make_doc("contract", id="d7"))
    flags = [
        fl("x_ownership_vs_registry", ["d1"], disc="lim"),
        fl("x_ownership_vs_registry", ["d2"], disc="lim"),
        fl("bs_totals", ["d3"]),
        fl("bs_running_balance", ["d3"]),
        fl("x_invoice_vs_receipts", ["d4"], "medium"),
        fl("x_invoice_vs_receipts", ["d5"], "medium"),
        fl("x_invoice_vs_contract", ["d4", "d7"], "medium"),
        fl("ela_region", ["d1"], "medium", region=box(0.25, 0.25)),
        fl("copy_move", ["d1"], "medium", region=shift_box(0.6)),
        fl("pdf_modified", ["d1"], "low"),
        fl("doc_unclassified", ["d6"], "info"),
    ]
    baseline = [f.model_dump() for f in group_flags(case, flags)]
    rng = random.Random(4002)
    for _ in range(20):
        shuffled = flags[:]
        rng.shuffle(shuffled)
        assert [f.model_dump() for f in group_flags(case, shuffled)] == baseline


def test_every_flag_lands_in_exactly_one_finding() -> None:
    flags = three_holder_flags("a") + [
        fl("bs_totals", ["d9"]),
        fl("doc_unclassified", ["d1"], "info"),
    ]
    findings = group_flags(case_with(), flags)
    members = [fid for f in findings for fid in f.flag_ids]
    assert sorted(members) == sorted(f.id for f in flags)


def test_untrusted_text_stays_plain_text() -> None:
    evil = '<script>alert(1)</script> "; DROP TABLE cases; -- {{7*7}}'
    (finding,) = group_flags(case_with(), [fl("x_directors", ["d1"], expl=evil)])
    assert finding.title == evil  # untouched, not interpreted; the UI renders it as text


def test_sentence_does_not_end_at_a_company_form_abbreviation() -> None:
    from app.findings import sentence

    text = "Kestrel Capital Holdings Pte. Ltd. holds 25.00% per the registry. Not on the chart."
    assert sentence(text) == "Kestrel Capital Holdings Pte. Ltd. holds 25.00% per the registry"
    assert sentence("Mr. Tan owns it. He signed.") == "Mr. Tan owns it"
    assert sentence("One. Two") == "One"
