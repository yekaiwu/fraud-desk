"""FIT evaluation data: semantic tampers in otherwise clean bundles, matched controls (docs 9.3).

No model is called. The documents have the shape of ``ground_truth.json`` documents and are loaded
into ``Document`` objects with the loader the evaluation uses; the exact evidence of every tampered
case is checked with the product's own citation verifier, and the deterministic checks are run to
show that the bases really are clean.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.checks.registry import run_stage_checks
from app.core.schemas import DOC_SCHEMAS
from app.core.testing import make_ctx
from app.semantic.claims import verify_claims
from scripts._eval import sem_data as sd
from scripts._gen import sem_fit as f
from scripts._gen.sem_common import SEEDS, SPLITS
from scripts._gen.sem_industry import (
    GENERIC_LINES,
    INDUSTRIES,
    OCC_BY_ID,
    OCCUPATIONS,
    industries_of,
    occupations_of,
)

D = Decimal


@pytest.fixture(scope="module")
def sets() -> dict[str, list[dict]]:
    return {s: f.build_cases(s) for s in SPLITS}


def _docs(case: dict, doc_type: str) -> list[dict]:
    return [d for d in case["documents"] if d["doc_type"] == doc_type]


def _val(doc: dict, name: str) -> str | None:
    return doc["fields"][name]["value"]


def _rows(doc: dict, table: str) -> list[dict]:
    return [r["cells"] for r in doc["tables"][table]["rows"]]


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {c["case_id"]: c for c in rows}


# ------------------------------------------------------------------------------------ shape


def test_seeds_counts_and_difficulty(sets: dict[str, list[dict]]) -> None:
    assert SEEDS == {"dev": 5101, "held_out": 7001}
    for rows in sets.values():
        assert len(rows) == 3 * (24 + 24 + 16)
        by = Counter((c["family"], c["variant"]) for c in rows)
        for fam in f.FAMILIES:
            assert by[(fam, "tampered")] == 24
            assert by[(fam, "unusual_legit")] == 24
            assert by[(fam, "ordinary")] == 16
            tam = Counter(
                c["difficulty"] for c in rows if c["family"] == fam and c["label"] == "tampered"
            )
            assert tam == {"easy": 8, "medium": 8, "hard": 8}
        assert len({c["case_id"] for c in rows}) == len(rows)


def test_reproducible_from_the_seed(sets: dict[str, list[dict]]) -> None:
    assert f.build_cases("dev") == sets["dev"]
    assert f.build_cases("dev", seed=3) != sets["dev"]
    assert sets["dev"] != sets["held_out"]


def test_ids_do_not_tell_the_variant(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for c in rows:
            assert re.fullmatch(
                r"fit-(dev|held_out)-(goods|income|coher)-[0-9a-f]{8}", c["case_id"]
            )
        # shuffled: the first dozen cases are not one variant
        assert len({c["variant"] for c in rows[:12]}) >= 2


def test_helper_tables_agree_with_the_product_schema() -> None:
    for doc_type, names in f.FIELDS.items():
        schema = DOC_SCHEMAS[doc_type]
        assert list(names) == list(schema.fields), doc_type
        assert set(f.TABLES[doc_type]) == set(schema.tables), doc_type
        for table, cols in f.TABLES[doc_type].items():
            assert list(cols) == list(schema.tables[table].columns), (doc_type, table)


# --------------------------------------------------------------------------- clean arithmetic


def test_every_document_is_arithmetically_exact(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for c in rows:
            (stmt,) = _docs(c, "bank_statement")
            trans = _rows(stmt, "transactions")
            bal = D(_val(stmt, "opening_balance"))
            deb = cre = D(0)
            for r in trans:
                d = D(r["debit"]["value"] or 0)
                k = D(r["credit"]["value"] or 0)
                bal += k - d
                deb, cre = deb + d, cre + k
                assert D(r["balance"]["value"]) == bal
                assert bal >= 0
            assert bal == D(_val(stmt, "closing_balance"))
            assert deb == D(_val(stmt, "total_debits")) and cre == D(_val(stmt, "total_credits"))
            credits = [r["description"]["value"] for r in trans if r["credit"]["value"]]
            for inv in _docs(c, "invoice"):
                lines = _rows(inv, "lines")
                for ln in lines:
                    assert D(ln["amount"]["value"]) == D(ln["quantity"]["value"]) * D(
                        ln["unit_price"]["value"]
                    )
                sub = sum((D(ln["amount"]["value"]) for ln in lines), D(0))
                assert sub == D(_val(inv, "subtotal"))
                gst = (sub * D("0.09")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
                assert gst == D(_val(inv, "gst_amount"))
                assert sub + gst == D(_val(inv, "total"))
                # the statement shows the payment of this invoice, for exactly its total
                paid = [
                    r for r in trans if _val(inv, "invoice_number") in r["description"]["value"]
                ]
                assert len(paid) == 1 and D(paid[0]["credit"]["value"]) == D(_val(inv, "total"))
            assert any("INV-2025" in d for d in credits)
            (prof,) = _docs(c, "company_profile")
            assert sum(D(r["percent"]["value"]) for r in _rows(prof, "shareholders")) == 100


def test_the_deterministic_checks_find_nothing_in_any_case(sets: dict[str, list[dict]]) -> None:
    """The bases are clean: no existing rule flags a tampered, control or ordinary case, so a
    semantic signal is the only thing that can tell them apart."""
    for rows in sets.values():
        for c in rows:
            for use_case in ("loan", "aml"):
                ctx = make_ctx(sd.case_documents(c), use_case=use_case)
                for stage in ("rules", "crossdoc"):
                    run_stage_checks(ctx, stage)
                assert not ctx.case.flags, (
                    c["case_id"],
                    use_case,
                    [x.rule for x in ctx.case.flags],
                )


# ----------------------------------------------------------------------------- the evidence


def _claim_for(case: dict, loc: dict) -> dict:
    cit = {"document_id": loc["document"], "quote": loc["quote"]}
    if "field" in loc:
        cit["field"] = loc["field"]
    else:
        cit.update(table=loc["table"], row=loc["row"], column=loc["column"])
    return {"kind": "x", "severity": "medium", "statement": "s", "citations": [cit]}


def test_every_evidence_and_watch_locator_is_verified_by_the_product_verifier(
    sets: dict[str, list[dict]],
) -> None:
    for rows in sets.values():
        for c in rows:
            locs = c["expected"]["evidence"] if c["label"] == "tampered" else c["watch"]
            if c["variant"] == "ordinary":
                assert not locs
                continue
            assert locs, c["case_id"]
            docs = sd.case_documents(c)
            v = verify_claims(
                {"claims": [_claim_for(c, loc) for loc in locs]}, docs, kinds=("x",), max_claims=50
            )
            assert not v.dropped, (c["case_id"], v.dropped)
            assert v.proposed == len(locs) == len(v.claims)


def test_labels_and_the_shape_of_the_answer_key(sets: dict[str, list[dict]]) -> None:
    by = {s: _by_id(rows) for s, rows in sets.items()}
    for split, rows in sets.items():
        for c in rows:
            tampered = c["label"] == "tampered"
            assert c["variant"] == "tampered" if tampered else c["variant"] != "tampered"
            assert bool(c["expected"]["kind"]) == tampered
            assert (c["expected"]["kind"] == f.KIND_OF[c["family"]]) if tampered else True
            if c["variant"] == "unusual_legit":
                m = by[split][c["matched_to"]]
                assert m["label"] == "tampered" and m["family"] == c["family"]
                assert m["base_id"] == c["base_id"] and m["difficulty"] == c["difficulty"]
                # same company, same statement account: the matched pair share one base
                pa, pb = _docs(c, "company_profile")[0], _docs(m, "company_profile")[0]
                assert _val(pa, "company_name") == _val(pb, "company_name")
                assert _val(pa, "uen") == _val(pb, "uen")
                sa, sb = _docs(c, "bank_statement")[0], _docs(m, "bank_statement")[0]
                assert _val(sa, "account_number") == _val(sb, "account_number")
                assert c["documents"] != m["documents"]
            else:
                assert c["matched_to"] is None
        # every tampered case has exactly one matched unusual control
        matches = Counter(c["matched_to"] for c in rows if c["matched_to"])
        tampered_ids = {c["case_id"] for c in rows if c["label"] == "tampered"}
        assert set(matches) == tampered_ids and set(matches.values()) == {1}


# ------------------------------------------------------------------------ split hygiene


def test_industries_and_occupations_are_split_by_hand_and_never_cross(
    sets: dict[str, list[dict]],
) -> None:
    for split in SPLITS:
        assert len(industries_of(split)) == 8 and len(occupations_of(split)) == 12
    ids = {s: {i.id for i in industries_of(s)} for s in SPLITS}
    assert not ids["dev"] & ids["held_out"]
    sells = {
        s: {item[0] for i in industries_of(s) for item in (*i.sells, *i.sidelines)} for s in SPLITS
    }
    assert not sells["dev"] & sells["held_out"]
    occ = {s: {o.id for o in occupations_of(s)} for s in SPLITS}
    assert not occ["dev"] & occ["held_out"]
    assert {i.id for i in INDUSTRIES} == ids["dev"] | ids["held_out"]
    assert len(OCCUPATIONS) == 24
    for split, rows in sets.items():
        used = Counter(c["industry"] for c in rows)
        assert set(used) == ids[split]
        assert max(used.values()) - min(used.values()) <= 6
        for c in rows:
            donor = c["details"].get("donor_industry")
            assert donor is None or donor in ids[split]
            occupation = c["details"].get("occupation")
            assert occupation is None or occupation in occ[split]
            # nothing from the other split's catalogue appears in the case's text
            text = str(c["documents"])
            for other in SPLITS:
                if other != split:
                    assert not any(item in text for item in list(sells[other])[:400])


# -------------------------------------------------------------------- goods_fit, by variant


def test_goods_tampers_use_the_donors_goods_and_the_right_number_of_lines(
    sets: dict[str, list[dict]],
) -> None:
    from scripts._gen.sem_industry import BY_ID

    for rows in sets.values():
        for c in rows:
            if c["family"] != "goods_fit" or c["variant"] != "tampered":
                continue
            own, donor = BY_ID[c["industry"]], BY_ID[c["details"]["donor_industry"]]
            assert donor.family != own.family
            ev = c["expected"]["evidence"]
            own_items = {i[0] for i in own.sells}
            for loc in ev:
                assert loc["quote"] in {i[0] for i in donor.sells}
                assert loc["quote"] not in own_items
            (inv_key,) = {loc["document"] for loc in ev}
            inv = next(d for d in c["documents"] if d["key"] == inv_key)
            n = len(_rows(inv, "lines"))
            want = {"easy": n, "medium": max(2, n // 2), "hard": 1}[c["difficulty"]]
            assert len(ev) == want
            # every other line of every invoice belongs to the declared business
            alien = {(loc["document"], loc["row"]) for loc in ev}
            for d in _docs(c, "invoice"):
                for i, ln in enumerate(_rows(d, "lines")):
                    if (d["key"], i) not in alien:
                        assert ln["description"]["value"] in own_items


def test_goods_controls_are_legitimate_twists(sets: dict[str, list[dict]]) -> None:
    from scripts._gen.sem_industry import BY_ID

    generic = {g[0] for g in GENERIC_LINES}
    seen = Counter()
    for rows in sets.values():
        by = _by_id(rows)
        for c in rows:
            if c["family"] != "goods_fit" or c["variant"] != "unusual_legit":
                continue
            seen[c["subtype"]] += 1
            own = BY_ID[c["industry"]]
            quotes = {w["quote"] for w in c["watch"]}
            prof = _docs(c, "company_profile")[0]
            if c["subtype"] == "generic_service_lines":
                assert quotes <= generic
                assert _val(prof, "principal_activity") in own.activity
            elif c["subtype"] == "adjacent_sideline":
                assert quotes <= {s[0] for s in own.sidelines}
                assert _val(prof, "principal_activity") in own.activity
            else:  # the tamper's own alien lines, under a general-trade profile
                t = by[c["matched_to"]]
                assert quotes == {e["quote"] for e in t["expected"]["evidence"]}
                assert _val(prof, "principal_activity") == f.GENERAL_ACTIVITY[0]
                assert _val(prof, "ssic_code") == f.GENERAL_ACTIVITY[1]
                inv_c = {d["key"]: d for d in _docs(c, "invoice")}
                inv_t = {d["key"]: d for d in _docs(t, "invoice")}
                assert inv_c == inv_t  # identical invoices: only the profile differs
    assert set(seen) == set(f.GOODS_CONTROLS) and min(seen.values()) >= 10


# ----------------------------------------------------------------------------- income_fit


def test_income_tampers_are_far_outside_the_plausible_range_and_controls_are_not(
    sets: dict[str, list[dict]],
) -> None:
    lo_by = {"easy": 10.0, "medium": 6.0, "hard": 3.5}
    seen = Counter()
    for rows in sets.values():
        for c in rows:
            if c["family"] != "income_fit":
                continue
            (sof,) = _docs(c, "source_of_funds_declaration")
            amount = D(_val(sof, "declared_amount"))
            (stmt,) = _docs(c, "bank_statement")
            loans = [
                D(r["credit"]["value"])
                for r in _rows(stmt, "transactions")
                if "DIRECTOR LOAN" in r["description"]["value"]
            ]
            assert loans == [amount]  # the statement shows exactly the declared transfer
            desc = _val(sof, "origin_description")
            if c["variant"] == "tampered":
                ratio = c["details"]["multiple_of_plausible_max"]
                assert ratio >= lo_by[c["difficulty"]] * 0.9, c["case_id"]
                occ = OCC_BY_ID[c["details"]["occupation"]]
                assert occ.title in desc and _val(sof, "origin_category") == "salary"
                seen[c["subtype"]] += 1
                quotes = [e["quote"] for e in c["expected"]["evidence"]]
                assert quotes == [occ.title, f"{amount:.2f}"]
            elif c["variant"] == "unusual_legit":
                ratio = c["details"]["multiple_of_plausible_max"]
                if c["subtype"] == "non_salary_source":
                    assert ratio is None and _val(sof, "origin_category") in (
                        "asset_sale",
                        "inheritance",
                    )
                    assert "not from my salary" in desc
                else:
                    assert 0.1 < ratio <= 1.0, (c["case_id"], ratio)
                seen[c["subtype"]] += 1
            else:
                assert _val(sof, "origin_category") == "salary" and c["subtype"] == "ordinary"
    assert set(seen) >= {"salary_too_high", "savings_too_high", *f.INCOME_CONTROLS}


# --------------------------------------------------------------------------- coherence_fit


def test_coherence_tampers_change_only_the_odd_document(sets: dict[str, list[dict]]) -> None:
    from scripts._gen.sem_industry import BY_ID

    seen = Counter()
    for rows in sets.values():
        for c in rows:
            if c["family"] != "coherence_fit" or c["variant"] != "tampered":
                continue
            own, donor = BY_ID[c["industry"]], BY_ID[c["details"]["donor_industry"]]
            seen[c["subtype"]] += 1
            prof = _docs(c, "company_profile")[0]
            own_items = {i[0] for i in own.sells}
            for d in _docs(c, "invoice"):  # the invoices always describe the declared trade
                assert {ln["description"]["value"] for ln in _rows(d, "lines")} <= own_items
            odd = c["expected"]["odd_document"]
            if c["subtype"] == "profile_odd":
                assert odd == "company_profile:-"
                assert _val(prof, "principal_activity") == donor.activity[0]
                assert _val(prof, "principal_activity") not in own.activity
                (loc,) = c["expected"]["evidence"]
                assert loc["field"] == "principal_activity"
            else:
                assert odd.startswith("bank_statement:")
                assert _val(prof, "principal_activity") in own.activity
                n = {"easy": 7, "medium": 5, "hard": 2}[c["difficulty"]]
                assert len(c["expected"]["evidence"]) == n
                words = " ".join(donor.supplier_words + donor.customer_words).upper()
                for loc in c["expected"]["evidence"]:
                    assert any(w.upper() in loc["quote"] for w in words.split())
    assert set(seen) == {"profile_odd", "statement_odd"}


def test_coherence_controls_are_legitimate_twists(sets: dict[str, list[dict]]) -> None:
    seen = Counter()
    for rows in sets.values():
        for c in rows:
            if c["family"] != "coherence_fit" or c["variant"] != "unusual_legit":
                continue
            seen[c["subtype"]] += 1
            prof = _docs(c, "company_profile")[0]
            act = _val(prof, "principal_activity")
            if c["subtype"] == "diversified_profile":
                assert ";" in act  # the profile declares the second line itself
            elif c["subtype"] == "generic_profile":
                assert act == f.GENERAL_ACTIVITY[0]
            elif c["subtype"] == "profile_with_sideline":
                assert "; and supply of" in act
            else:
                assert c["subtype"] == "one_off_expense"
                memos = {m for m, _w in f.ONE_OFFS}
                (stmt,) = _docs(c, "bank_statement")
                descs = " ".join(r["description"]["value"] for r in _rows(stmt, "transactions"))
                assert sum(m in descs for m in memos) >= 2
    assert set(seen) == {
        "diversified_profile",
        "generic_profile",
        "profile_with_sideline",
        "one_off_expense",
    }


def test_ordinary_cases_describe_one_business(sets: dict[str, list[dict]]) -> None:
    from scripts._gen.sem_industry import BY_ID

    for rows in sets.values():
        for c in rows:
            if c["variant"] != "ordinary":
                continue
            own = BY_ID[c["industry"]]
            assert _val(_docs(c, "company_profile")[0], "principal_activity") in own.activity
            items = {i[0] for i in own.sells}
            for d in _docs(c, "invoice"):
                assert {ln["description"]["value"] for ln in _rows(d, "lines")} <= items


# ----------------------------------------------------------------------------- views / loader


def test_model_views_hold_no_label(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for c in rows[:20]:
            view = sd.fit_view(c)
            assert set(view) == {"case_id", "documents"}
            text = str(view)
            for secret in ("tampered", "expected", "unusual_legit", "donor_industry", "evidence"):
                assert secret not in text, secret
    txn = {
        "row_id": "r",
        "date": "d",
        "description": "x",
        "debit": None,
        "credit": "1",
        "truth": {},
    }
    assert set(sd.txn_view(txn)) == {"row_id", "date", "description", "debit", "credit"}
    pair = {"pair_id": "p", "a": "A", "b": "B", "label": "same", "class": "typo"}
    assert set(sd.entity_view(pair)) == {"pair_id", "a", "b"}


def test_document_ids_are_the_keys_and_cells_keep_printed_forms(
    sets: dict[str, list[dict]],
) -> None:
    case = next(
        c for c in sets["dev"] if c["family"] == "income_fit" and c["variant"] == "tampered"
    )
    docs = sd.case_documents(case)
    assert [d.id for d in docs] == [d["key"] for d in case["documents"]]
    sof = next(d for d in docs if d.doc_type == "source_of_funds_declaration")
    assert sof.fields["declared_amount"].raw.startswith("S$ ")
    assert sof.fields["declared_amount"].money() is not None
