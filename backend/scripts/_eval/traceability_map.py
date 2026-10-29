"""Plan 0.4 traceability matrix, re-scored from the eval (plan 0.6, docs 4.10).

Each item maps to rules and tamper ids (and acceptance bundles). Measured status per plan 0.6:

* ● iff (1) a check exists and ran on generated data in the digital eval, (2) the flag carried
  evidence and an explanation and, for row or field rules, a region or row, and (3) the item has an
  eval row with at least one catch and core recall of at least 0.8 on digital;
* ◐ iff caught at least once but a limit applies (recall below 0.8, scan misses, no region);
* ○ if never caught, not built or ``rule not implemented``, with the reason.

The measured status never exceeds the planned one (limits are stated), and a downgrade is printed
as "downgraded from X". Plan circle items are copied with their reason and proved by rows O1..O8.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pymupdf

from scripts._eval.tamper_defs import TAMPERS
from scripts._eval.tamper_pdf import normalise_pdf, pin_trailer_id

FULL, HALF, NONE = "●", "◐", "○"
RANK = {NONE: 0, HALF: 1, FULL: 2}


def item(
    section: str,
    name: str,
    planned: str,
    rules: list[str],
    tampers: list[str] | None = None,
    accept: list[str] | None = None,
    kind: str = "rule",
    note: str = "",
    orow: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "section": section,
        "name": name,
        "planned": planned,
        "rules": rules,
        "tampers": tampers or [],
        "accept": accept or [],
        "kind": kind,
        "note": note,
        "orow": orow,
        "reason": reason,
    }


ITEMS: list[dict[str, Any]] = [
    # six process themes (slide 2)
    item(
        "Process themes",
        "1 Digital architecture and straight-through processing",
        HALF,
        [],
        accept=["loan-clean", "kyc-clean", "aml-clean"],
        kind="theme",
        note="API-first backend and a fast-lane label; a human still confirms, no auto-approval. Proof: the clean bundles pass acceptance (lane in fast or the scenario lane) and the ranked queue exists.",
    ),
    item(
        "Process themes",
        "2 Onboarding, risk classification, ongoing KYC",
        HALF,
        [],
        accept=["kyc-clean", "kyc-fraud", "kyc-forger"],
        kind="theme",
        note="KYC bundle type with a risk band; a rerun keeps the old version. No scheduled periodic review.",
    ),
    item(
        "Process themes",
        "3 Transaction monitoring and behavioural analytics",
        HALF,
        ["bs_pass_through", "x_declared_vs_activity", "bs_round_numbers"],
        ["T60", "T57", "T08"],
        note="Only inside uploaded statements; no live monitoring.",
    ),
    item(
        "Process themes",
        "4 Network analysis, alert prioritisation, investigation support",
        HALF,
        [],
        accept=["aml-fraud"],
        kind="theme",
        note="Ranked queue, grouped findings, a small in-case entity graph. Proof: the prioritisation sweep and the AML bundle.",
    ),
    item(
        "Process themes",
        "5 Manual review, override, escalation",
        FULL,
        [],
        accept=[f"{s}-{v}" for s in ("kyc", "aml", "loan") for v in ("clean", "fraud", "forger")],
        kind="theme",
        note="Review step with audit log, mandatory reasons and outcome-specific verdicts. Proof: all nine acceptance rows close through the API, and row A:effectiveness.",
    ),
    item(
        "Process themes",
        "6 Control effectiveness and regulatory reporting",
        HALF,
        [],
        accept=["loan-fraud"],
        kind="theme",
        note="Rule-effectiveness page and an exportable case pack. Proof: effectiveness counts and the pack are consistent after the acceptance run (E1).",
    ),
    # fraud scenarios (slide 11)
    item(
        "Fraud scenarios",
        "Edited bank statement balances",
        FULL,
        ["bs_running_balance", "bs_totals", "bs_period_continuity"],
        ["T01", "T02a", "T03"],
        note="Hard case T02b (consistent recomputation) is reported apart.",
    ),
    item(
        "Fraud scenarios",
        "Manipulated revenue figures",
        HALF,
        ["fs_balance", "fs_subtotals", "fs_cash_tie", "fs_comparatives", "x_revenue_vs_credits"],
        ["T20a", "T20b", "T21", "T22", "T23"],
        ["loan-fraud", "loan-forger"],
    ),
    item(
        "Fraud scenarios",
        "Fake invoices",
        FULL,
        [
            "inv_lines",
            "inv_total",
            "inv_gst",
            "inv_ids",
            "inv_dates",
            "inv_parties",
            "inv_duplicate",
            "x_invoice_vs_receipts",
        ],
        ["T10", "T11", "T12", "T13", "T14a", "T14b", "T15", "T16a", "T16b", "T16c", "T19"],
        note="T17 (sequence gap) is weak and listed apart.",
    ),
    item(
        "Fraud scenarios",
        "Forged contracts",
        HALF,
        [
            "x_invoice_vs_contract",
            "pdf_text_vs_render",
            "pdf_bytes_after_signature",
            "signature_reuse",
        ],
        ["T18a", "T18b", "T30a", "T31", "T34"],
        note="No judgement about who signed.",
    ),
    item(
        "Fraud scenarios",
        "Altered shareholder records",
        HALF,
        ["kyc_shareholdings", "x_ownership_vs_registry"],
        ["T53", "T54", "T55"],
        ["kyc-fraud", "kyc-forger"],
    ),
    item(
        "Fraud scenarios",
        "Modified passports and IDs",
        HALF,
        [
            "id_format",
            "id_checksum",
            "passport_mrz",
            "date_valid",
            "ela_region",
            "image_editor_software",
            "image_modified",
        ],
        ["T50a", "T50b", "T51", "T52", "T41", "T40a", "T40b"],
        note="T56b (an ID for a non-director) is not built: the generator renders an ID card only for profile directors.",
    ),
    item(
        "Fraud scenarios",
        "False source-of-funds evidence",
        HALF,
        ["x_sof_support", "x_remitter_vs_buyer"],
        ["T58", "T59"],
        ["aml-fraud", "aml-forger"],
    ),
    # detection techniques (slide 12)
    item(
        "Detection techniques",
        "PDF metadata analysis",
        FULL,
        [
            "pdf_editor_software",
            "pdf_modified",
            "pdf_bytes_after_signature",
            "pdf_producer_unexpected",
        ],
        ["T31", "T32a", "T32b", "T32c"],
    ),
    item(
        "Detection techniques",
        "Font inconsistency detection",
        HALF,
        ["font_mix", "font_span_outlier"],
        ["T30b", "T35"],
        note="T35 (font_mix) is weak and reported apart.",
    ),
    item(
        "Detection techniques",
        "Image manipulation detection",
        HALF,
        ["ela_region", "copy_move", "noise_mismatch"],
        ["T41", "T42", "T43"],
        note="`noise_mismatch` is a stretch check. JPEG renders only.",
    ),
    item(
        "Detection techniques",
        "Signature / stamp anomalies",
        HALF,
        ["signature_reuse"],
        ["T34"],
        note="No claim about who signed.",
    ),
    item(
        "Detection techniques",
        "OCR vs visual mismatch",
        HALF,
        ["pdf_text_vs_render"],
        ["T30a"],
        note="Reader disagreement is kept as `alt_raw`; no dedicated rule (gap).",
    ),
    item(
        "Detection techniques",
        "Template comparison",
        HALF,
        ["pdf_producer_unexpected"],
        ["T32c"],
        note="Issuer classification C3 and the producer table for the mock layouts; there is no layout-fingerprint rule (gap).",
    ),
    item(
        "Detection techniques",
        "Duplicate document detection",
        FULL,
        ["inv_duplicate", "file_reuse"],
        ["T16a", "T16b", "T16c"],
    ),
    item(
        "Detection techniques",
        "AI-generated image detection",
        HALF,
        ["ai_provenance_hint"],
        ["T33"],
        note="Provenance hints only, reported as not detected and never as a clearance.",
    ),
    # cross-document (slide 13)
    item(
        "Cross-document",
        "Invoice and contract amount",
        HALF,
        ["x_invoice_vs_contract"],
        ["T18a", "T18b"],
        ["loan-fraud"],
    ),
    item(
        "Cross-document",
        "Invoice and bank payment",
        FULL,
        ["x_invoice_vs_receipts"],
        ["T19"],
        ["loan-fraud"],
    ),
    item(
        "Cross-document",
        "Revenue and bank credits",
        FULL,
        ["x_revenue_vs_credits"],
        ["T23"],
        ["loan-fraud", "loan-forger"],
        note="Hard case T02b is reported apart.",
    ),
    item(
        "Cross-document",
        "Ownership chart and registry",
        HALF,
        ["x_ownership_vs_registry"],
        ["T53"],
        ["kyc-fraud", "kyc-forger"],
    ),
    item(
        "Cross-document",
        "Industry profile and financial performance",
        HALF,
        ["x_declared_vs_activity"],
        ["T57"],
        ["kyc-fraud"],
        note="Ratio benchmarking against peers is not built.",
    ),
    item(
        "Cross-document",
        "Source of funds and transaction evidence",
        HALF,
        ["x_sof_support"],
        ["T58"],
        ["aml-fraud"],
    ),
    # families
    item("Family A: KYC", "ACRA / Biz profile", HALF, ["x_directors"], ["T56a"], ["kyc-fraud"]),
    item(
        "Family A: KYC",
        "Certificate of incorporation",
        HALF,
        ["x_entity"],
        [],
        ["kyc-clean"],
        kind="document",
        note="No tamper row: only silence on the clean bundle is measured.",
    ),
    item(
        "Family A: KYC",
        "Ownership chart",
        HALF,
        ["kyc_shareholdings", "x_ownership_vs_registry"],
        ["T53", "T54", "T55"],
    ),
    item(
        "Family A: KYC",
        "Shareholder register",
        HALF,
        ["kyc_shareholdings", "x_ownership_vs_registry"],
        ["T54", "T55"],
    ),
    item(
        "Family A: KYC",
        "Directors' and UBO IDs",
        FULL,
        [
            "id_format",
            "id_checksum",
            "passport_mrz",
            "date_valid",
            "x_directors",
            "image_editor_software",
            "ela_region",
        ],
        ["T50a", "T50b", "T51", "T52", "T40a", "T40b", "T41"],
    ),
    item(
        "Family A: KYC",
        "Board resolution",
        NONE,
        [],
        kind="o_row",
        orow="O1",
        reason="Classified as other and listed as received, not checked.",
    ),
    item(
        "Family A: KYC",
        "Proof of address",
        NONE,
        [],
        kind="o_row",
        orow="O2",
        reason="Classified as other and listed as received, not checked.",
    ),
    item(
        "Family A: KYC",
        "Business licences",
        NONE,
        [],
        kind="o_row",
        orow="O3",
        reason="Classified as other and listed as received, not checked.",
    ),
    item(
        "Family B: AML",
        "Bank statements",
        FULL,
        [
            "bs_running_balance",
            "bs_totals",
            "bs_period_continuity",
            "bs_dates",
            "bs_holder",
            "bs_page_chain",
            "bs_duplicates",
        ],
        ["T01", "T02a", "T03", "T04", "T05", "T06", "T07"],
    ),
    item(
        "Family B: AML",
        "Invoices",
        FULL,
        [
            "inv_lines",
            "inv_total",
            "inv_gst",
            "inv_ids",
            "inv_dates",
            "inv_parties",
            "inv_duplicate",
        ],
        ["T10", "T11", "T12", "T13", "T14a", "T14b", "T15", "T16a", "T16b", "T16c"],
    ),
    item(
        "Family B: AML",
        "Contracts",
        HALF,
        ["x_invoice_vs_contract", "signature_reuse"],
        ["T18a", "T34"],
    ),
    item(
        "Family B: AML",
        "Tax returns",
        NONE,
        [],
        kind="o_row",
        orow="O4",
        reason="Corporate returns not built; classified as other and listed as received, not checked.",
    ),
    item(
        "Family B: AML",
        "Financial statements",
        HALF,
        ["fs_balance", "fs_subtotals", "fs_cash_tie", "fs_comparatives", "fs_entity"],
        ["T20a", "T20b", "T21", "T22", "T24"],
    ),
    item(
        "Family B: AML",
        "Sale / purchase agreements",
        HALF,
        ["x_invoice_vs_contract"],
        ["T18a", "T18b"],
    ),
    item(
        "Family B: AML",
        "Wealth evidence",
        NONE,
        [],
        kind="o_row",
        orow="O5",
        reason="Classified as other and listed as received, not checked.",
    ),
    item(
        "Family B: AML",
        "RFI supporting documents",
        NONE,
        [],
        kind="o_row",
        orow="O6",
        reason="Classified as other and listed as received, not checked.",
    ),
    item(
        "Family C: Loan",
        "Financial statements",
        HALF,
        ["fs_balance", "fs_subtotals", "fs_cash_tie", "fs_comparatives"],
        ["T20a", "T20b", "T21", "T22"],
    ),
    item(
        "Family C: Loan",
        "Management accounts",
        HALF,
        ["fs_subtotals"],
        [],
        ["loan-clean"],
        kind="document",
        note="Same parser, marked unaudited (generator quirk Q14); measured as silence on the odd-index clean bundles.",
    ),
    item(
        "Family C: Loan",
        "Bank statements",
        FULL,
        ["bs_running_balance", "bs_totals", "bs_period_continuity"],
        ["T01", "T02a", "T03"],
    ),
    item(
        "Family C: Loan",
        "Contracts",
        HALF,
        ["x_invoice_vs_contract", "signature_reuse"],
        ["T18a", "T34"],
    ),
    item(
        "Family C: Loan",
        "Invoices",
        FULL,
        ["inv_lines", "inv_total", "inv_gst", "inv_duplicate"],
        ["T10", "T11", "T12", "T16a"],
    ),
    item(
        "Family C: Loan",
        "Purchase orders",
        HALF,
        ["x_invoice_vs_contract"],
        ["T18b"],
        note="PO to invoice amount.",
    ),
    item(
        "Family C: Loan",
        "Collateral documents",
        NONE,
        [],
        kind="o_row",
        orow="O7",
        reason="Classified as other and listed as received, not checked.",
    ),
    item(
        "Family C: Loan",
        "Trade documents",
        NONE,
        [],
        kind="o_row",
        orow="O8",
        reason="Classified as other and listed as received, not checked.",
    ),
]

O_DOCS = {
    "O1": (
        "Board resolution",
        "RESOLUTION OF THE BOARD OF DIRECTORS",
        "IT IS RESOLVED THAT the company may open a facility, as discussed at the meeting held on the date shown. Signed by the chairman. Specimen only: fictitious.",
    ),
    "O2": (
        "Proof of address",
        "UTILITY BILL",
        "Account holder service address and billing period summary. Amount due shown below. Specimen only: fictitious.",
    ),
    "O3": (
        "Business licence",
        "LICENCE TO OPERATE",
        "This licence permits the named business to carry on the activity stated. Valid until the date shown. Specimen only: fictitious.",
    ),
    "O4": (
        "Corporate tax return",
        "ESTIMATED CHARGEABLE INCOME NOTICE",
        "Notice of assessment for the year of assessment shown. Chargeable income and tax payable summary. Specimen only: fictitious.",
    ),
    "O5": (
        "Wealth evidence",
        "STATEMENT OF PERSONAL WEALTH",
        "Schedule of assets held by the declarant: property, investments and savings. Specimen only: fictitious.",
    ),
    "O6": (
        "RFI supporting document",
        "RESPONSE TO REQUEST FOR INFORMATION",
        "Supporting note provided in reply to the bank's questions. Specimen only: fictitious.",
    ),
    "O7": (
        "Collateral document",
        "VALUATION REPORT",
        "Valuation of the pledged asset as at the date shown. Specimen only: fictitious.",
    ),
    "O8": (
        "Trade document",
        "BILL OF LADING",
        "Shipment details, consignee and description of goods. Specimen only: fictitious.",
    ),
}


def make_o_documents(out_dir: Path) -> dict[str, Path]:
    """The eight plain 'received, not checked' documents (one page of generic text each)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for oid, (_name, title, body) in O_DOCS.items():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 90), title, fontsize=16)
        used = page.insert_textbox(pymupdf.Rect(72, 120, 520, 700), body, fontsize=11)
        if used < 0:  # pragma: no cover - the text did not fit: an empty page would be unreadable
            raise RuntimeError(f"{oid}: body text does not fit the page")
        page.insert_text((72, 800), "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT", fontsize=8)
        doc.set_metadata(
            {
                "producer": "Mock Word Processor 3.1",
                "creator": "Mock Word Processor",
                "title": title,
            }
        )
        p = out_dir / f"{oid}_{title.lower().replace(' ', '_')}.pdf"
        p.write_bytes(pin_trailer_id(normalise_pdf(doc.tobytes(deflate=True))))
        doc.close()
        paths[oid] = p
    return paths


def o_row_result(base: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Assertions for one O row: the extra document is classified ``other``, listed as received,
    not checked, with exactly one new ``doc_unclassified`` info flag and no other flag changed.
    Documents and flags are compared with the base run as multisets (a document that is not in the
    manifest has no document key, so keys alone would collide with the base's own blank page)."""
    from collections import Counter

    reasons: list[str] = []

    def docs(r: dict[str, Any]) -> Counter[tuple[str, str]]:
        return Counter((d["doc_type"], d["status"]) for d in r["detail"]["documents"])

    extra = docs(run) - docs(base)
    if sum(extra.values()) != 1:
        reasons.append(f"expected one extra document, found {sum(extra.values())}: {dict(extra)}")
    else:
        ((doc_type, status),) = extra
        if doc_type != "other":
            reasons.append(f"classified {doc_type}, not other")
        if status != "received_not_checked":
            reasons.append(f"status {status}, not received_not_checked")

    def flags(r: dict[str, Any]) -> Counter[tuple[str, str, tuple[str, ...]]]:
        return Counter(
            (f["rule"], f["severity"], tuple(sorted(f.get("doc_keys") or []))) for f in r["flags"]
        )

    new = flags(run) - flags(base)
    lost = flags(base) - flags(run)
    if not (sum(new.values()) == 1 and next(iter(new))[:2] == ("doc_unclassified", "info")):
        reasons.append("new flags: " + (", ".join(f"{r}/{s}" for (r, s, _k) in new) or "none"))
    if lost:
        reasons.append(f"flags changed or lost: {sorted(r for (r, _s, _k) in lost)}")
    return {"passed": not reasons, "reasons": reasons}


# ------------------------------------------------------------------------------ scoring


def _pool(rows: list[dict[str, Any]], ids: list[str], render: str) -> tuple[int, int]:
    n = c = 0
    for r in rows:
        if r["tamper_id"] in ids and r["render"] == render and r.get("status") == "measured":
            n += r["n"]
            c += r["caught"]
    return n, c


def score_items(
    recall_rows: list[dict[str, Any]],
    recs: list[dict[str, Any]],
    acceptance: dict[str, Any],
    o_rows: dict[str, dict[str, Any]],
    registered: set[str],
    extras: dict[str, Any],
) -> list[dict[str, Any]]:
    """Measured status for every item (see the module docstring)."""
    acc_pass = {r["bundle_id"]: r["pass"] for r in acceptance["digital"]}
    results = []
    for it in ITEMS:
        res = dict(it)
        measured, limits, evidence = it["planned"], [], []
        if it["kind"] == "o_row":
            o = o_rows.get(it["orow"])
            if o is None:
                measured, limits = NONE, ["row not run"]
            else:
                measured = NONE
                evidence.append(
                    f"{it['orow']}: {'passed' if o['passed'] else 'FAILED: ' + '; '.join(o['reasons'])}"
                )
                if not o["passed"]:
                    limits.append(f"{it['orow']} assertions failed")
        elif it["kind"] == "theme":
            need = it["accept"]
            ok = [b for b in need if acc_pass.get(b)]
            evidence.append(f"acceptance rows passing: {len(ok)}/{len(need)}")
            eff = extras.get("effectiveness_ok")
            if it["name"].startswith("5") or it["name"].startswith("6"):
                evidence.append(f"A:effectiveness consistent: {eff}")
                ok_all = len(ok) == len(need) and bool(eff)
            elif it["name"].startswith("4"):
                ok_all = len(ok) == len(need) and bool(extras.get("ranking_ran"))
            else:
                ok_all = len(ok) == len(need)
            if it["name"].startswith("3"):
                n, c = (
                    sum(_pool(recall_rows, [t], "digital")[0] for t in ("T60", "T57")),
                    sum(_pool(recall_rows, [t], "digital")[1] for t in ("T60", "T57")),
                )
                evidence.append(f"T60/T57 digital caught {c}/{n}")
                ok_all = c >= 1
            if ok_all:
                measured = it["planned"]
            else:
                measured = HALF if len(ok) and RANK[it["planned"]] > 1 else NONE
                limits.append("an acceptance or proof row did not pass")
        else:
            rules = it["rules"]
            exist = [r for r in rules if r in registered] if registered else rules
            missing = [r for r in rules if registered and r not in registered]
            core_ids = [
                t
                for t in it["tampers"]
                if t in TAMPERS
                and not (TAMPERS[t].weak or TAMPERS[t].hard or TAMPERS[t].known_miss)
            ]
            n = c = 0
            for t in core_ids:
                a, b = _pool(recall_rows, [t], "digital")
                n, c = n + a, c + b
            scan_n = scan_c = 0
            for t in core_ids:
                for r in ("scan_hq", "scan_lq", "photo"):
                    a, b = _pool(recall_rows, [t], r)
                    scan_n, scan_c = scan_n + a, scan_c + b
            image_only = bool(core_ids) and all(
                TAMPERS[t].renders and "digital" not in TAMPERS[t].renders for t in core_ids
            )
            if image_only:
                n, c = scan_n, scan_c  # image-channel tampers exist only on JPEG renders
            digital_recs = [
                r
                for r in recs
                if r["tamper_id"] in core_ids
                and r.get("ok")
                and (r["render"] == "digital" or image_only)
            ]
            ran = any(
                any(r.get("coverage", {}).get(x, {}).get("status") == "ran" for x in r["expected"])
                for r in digital_recs
            ) or (it["accept"] and any(acc_pass.get(b) for b in it["accept"]))
            caught_recs = [r for r in digital_recs if r["caught"]]
            ev_ok = bool(caught_recs) and all(r.get("caught_evidence") for r in caught_recs)
            loc_needed = [r for r in caught_recs if r["localisable"]]
            loc_ok = all(r.get("caught_with_location") for r in loc_needed)
            if n:
                label = "JPEG renders" if image_only else "digital"
                evidence.append(
                    f"{label} caught {c}/{n}"
                    + (f"; scans {scan_c}/{scan_n}" if scan_n and not image_only else "")
                )
            acc_fail = [b for b in it["accept"] if b in acc_pass and not acc_pass[b]]
            if not exist and rules:
                measured = NONE
                limits.append("rule not implemented: " + ", ".join(missing))
            elif not core_ids and it["accept"]:
                okn = sum(1 for b in it["accept"] if acc_pass.get(b))
                measured = HALF if okn else NONE
                limits.append("no tamper row; measured through the acceptance bundles only")
                evidence.append(f"acceptance rows passing: {okn}/{len(it['accept'])}")
            elif c == 0:
                measured = NONE
                limits.append("never caught on digital data" if n else "no digital eval row ran")
            else:
                recall = c / n
                full = recall >= 0.8 and ran and ev_ok and loc_ok
                measured = FULL if full else HALF
                if recall < 0.8:
                    limits.append(f"core recall {recall:.2f} is below 0.8")
                if not ev_ok:
                    limits.append("a caught flag lacked evidence or explanation")
                if not loc_ok:
                    limits.append("a caught flag has no region, row or field")
                if not ran:
                    limits.append("the check did not run on digital data")
            if scan_n and scan_c < scan_n:
                limits.append(f"scan misses ({scan_c}/{scan_n} caught on degraded renders)")
            if acc_fail:
                limits.append("acceptance row(s) failing: " + ", ".join(acc_fail))
                if measured == FULL:
                    measured = HALF
            hard = [t for t in it["tampers"] if t in TAMPERS and TAMPERS[t].hard]
            if hard:
                evidence.append("hard case " + ", ".join(hard) + " reported apart")
        measured_capped = measured if RANK[measured] <= RANK[it["planned"]] else it["planned"]
        res["measured"] = measured_capped
        res["limits"] = limits
        res["evidence"] = evidence
        res["downgraded"] = RANK[measured_capped] < RANK[it["planned"]]
        results.append(res)
    return results


def traceability_md(results: list[dict[str, Any]], gaps: list[str], header: str) -> str:
    out = [
        "# Traceability: plan 0.4 re-scored from the eval",
        "",
        "> Our reading as students, not legal advice. Decision support only; a human decides. All data is fictitious.",
        "",
        header,
        "",
        'Status legend (plan 0.6). Planned status comes from plan 0.4; measured status is decided here from the eval: **●** = a check ran on generated digital data, the flag carried evidence and a region or row, and core recall for the item is at least 0.8 with at least one catch; **◐** = caught at least once but a limit applies; **○** = never caught, not built, or rule not implemented. Measured status never exceeds the planned status. A row where measured is below planned says "downgraded from X".',
        "",
    ]
    sections: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        sections.setdefault(r["section"], []).append(r)
    for sec, items in sections.items():
        out += [
            f"## {sec}",
            "",
            "| item | planned | measured | evidence | limits or reason |",
            "|---|---|---|---|---|",
        ]
        for r in items:
            m = r["measured"] + (f" (downgraded from {r['planned']})" if r["downgraded"] else "")
            lim = "; ".join(r["limits"]) or (r["reason"] if r["kind"] == "o_row" else "-")
            if r["kind"] == "o_row" and r["limits"] == []:
                lim = r["reason"]
            note = f" {r['note']}" if r["note"] else ""
            out.append(
                f"| {r['name']} | {r['planned']} | {m} | {'; '.join(r['evidence']) or '-'} | {lim}.{note} |".replace(
                    "..", "."
                )
            )
        out.append("")
    counts: dict[str, int] = {}
    for r in results:
        counts[r["measured"]] = counts.get(r["measured"], 0) + 1
    out += [
        "## Summary",
        "",
        f"Items: {len(results)}; measured ● {counts.get(FULL, 0)}, ◐ {counts.get(HALF, 0)}, ○ {counts.get(NONE, 0)}; downgraded: {sum(1 for r in results if r['downgraded'])}.",
        "",
        "## Gaps (plan 0.6)",
        "",
    ]
    out += [f"- {g}" for g in gaps] or ["- none"]
    return "\n".join(out) + "\n"


_ = tempfile
