"""Extraction and classification metrics (M4, M5, M6, docs 4.4) computed from case summaries
(``detail=True``) against a bundle's ``ground_truth.json`` and ``labels.json``."""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

C1_LABELS = [
    "bank_statement",
    "invoice",
    "financial_statement",
    "contract",
    "purchase_order",
    "company_profile",
    "certificate_of_incorporation",
    "ownership_chart",
    "shareholder_register",
    "national_id",
    "passport",
    "source_of_funds_declaration",
    "boilerplate",
    "other",
]


def _d(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _key(r: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(r.get("date") or ""),
        str(r.get("debit") or ""),
        str(r.get("credit") or ""),
        str(r.get("balance") or ""),
    )


def _norm_desc(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def truth_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in doc["tables"]["transactions"]["rows"]:
        out.append(
            {
                "index": r["index"],
                **{
                    c: (r["cells"].get(c) or {}).get("value")
                    for c in ("date", "description", "debit", "credit", "balance")
                },
            }
        )
    return out


def chain_valid(rows: list[dict[str, Any]], tol: Decimal = Decimal("0.01")) -> bool:
    """Consecutive rows satisfy ``previous balance + credit - debit = balance`` (W1)."""
    prev: Decimal | None = None
    for r in rows:
        bal = _d(r.get("balance"))
        if bal is None:
            continue
        if prev is not None and (_d(r.get("debit")) is not None or _d(r.get("credit")) is not None):
            expect = prev + (_d(r.get("credit")) or Decimal(0)) - (_d(r.get("debit")) or Decimal(0))
            if abs(expect - bal) > tol:
                return False
        prev = bal
    return True


def align_rows(
    extracted: list[dict[str, Any]], truth: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    """Pairs ``(extracted index, truth index)`` aligned by ``SequenceMatcher`` over the key
    ``(date, debit, credit, balance)``."""
    a = [_key(r) for r in extracted]
    b = [_key(r) for r in truth]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pairs: list[tuple[int, int]] = []
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            pairs.append((blk.a + k, blk.b + k))
    return pairs


def statement_metrics(
    detail_docs: list[dict[str, Any]], truth: dict[str, Any]
) -> list[dict[str, Any]]:
    """One row per truth bank statement: recall, precision, exact, chain validity."""
    by_key = {d["doc_key"]: d for d in detail_docs if d["doc_type"] == "bank_statement"}
    out = []
    for gd in truth["documents"]:
        if gd["doc_type"] != "bank_statement":
            continue
        trows = truth_rows(gd)
        ex = by_key.get(gd["key"])
        erows = ex["rows"] if ex else []
        pairs = align_rows(erows, trows)
        exact = sum(
            1
            for i, j in pairs
            if _norm_desc(erows[i].get("description")) == _norm_desc(trows[j].get("description"))
        )
        out.append(
            {
                "doc_key": gd["key"],
                "truth_rows": len(trows),
                "extracted_rows": len(erows),
                "aligned": len(pairs),
                "exact": exact,
                "chain_valid": chain_valid(erows) if erows else False,
                "truth_chain_valid": chain_valid(trows),
                "found": ex is not None,
            }
        )
    return out


def field_accuracy(
    detail_docs: list[dict[str, Any]], truth: dict[str, Any]
) -> dict[tuple[str, str], tuple[int, int]]:
    """``(doc_type, field) -> (canonical-equal, present in truth)``."""
    by_key = {d["doc_key"]: d for d in detail_docs}
    acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for gd in truth["documents"]:
        ex = by_key.get(gd["key"])
        for name, f in gd["fields"].items():
            tv = f.get("value")
            if tv is None:
                continue
            acc[(gd["doc_type"], name)][1] += 1
            if ex is not None and _eq(ex["fields"].get(name), tv):
                acc[(gd["doc_type"], name)][0] += 1
    return {k: (v[0], v[1]) for k, v in acc.items()}


def _eq(a: Any, b: Any) -> bool:
    if a is None:
        return False
    da, db = _d(a), _d(b)
    if da is not None and db is not None:
        return da == db
    return (
        re.sub(r"\s+", " ", str(a)).strip().casefold()
        == re.sub(r"\s+", " ", str(b)).strip().casefold()
    )


TABLE_KEYS = {
    ("invoice", "lines"): "amount",
    ("financial_statement", "income_statement"): "line",
    ("financial_statement", "balance_sheet"): "line",
    ("financial_statement", "cash_flow"): "line",
    ("company_profile", "directors"): "director_name",
    ("company_profile", "shareholders"): "holder_name",
    ("ownership_chart", "holders"): "holder_name",
    ("shareholder_register", "shareholders"): "holder_name",
}


def table_recall(
    detail_docs: list[dict[str, Any]], truth: dict[str, Any]
) -> dict[tuple[str, str], tuple[int, int]]:
    """Row recall per ``(doc_type, table)`` by key column (multiset of normalised keys)."""
    by_key = {d["doc_key"]: d for d in detail_docs}
    acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for gd in truth["documents"]:
        ex = by_key.get(gd["key"])
        for tname, tv in gd["tables"].items():
            keycol = TABLE_KEYS.get((gd["doc_type"], tname))
            if keycol is None:
                continue
            want = Counter(_nk((r["cells"].get(keycol) or {}).get("value")) for r in tv["rows"])
            have = Counter(_nk(r.get(keycol)) for r in (ex["tables"].get(tname, []) if ex else []))
            acc[(gd["doc_type"], tname)][1] += sum(want.values())
            acc[(gd["doc_type"], tname)][0] += sum(min(c, have.get(k, 0)) for k, c in want.items())
    return {k: (v[0], v[1]) for k, v in acc.items()}


def _nk(v: Any) -> str:
    d = _d(v)
    return format(d, "f") if d is not None else re.sub(r"\s+", " ", str(v or "")).strip().casefold()


# -------------------------------------------------------------------------- classification


def c1_pairs(pages: list[dict[str, Any]], labels: dict[str, Any]) -> list[tuple[str, str | None]]:
    """``(truth label, predicted label)`` per page, joined on ``sha256`` + ``page``."""
    truth = {(r["sha256"], int(r["page"])): r["c1"] for r in labels["pages"]}
    out = []
    for p in pages:
        t = truth.get((p["sha"], int(p["page"])))
        if t is not None:
            out.append((t, p["label"]))
    return out


def accuracy(pairs: list[tuple[Any, Any]]) -> tuple[int, int]:
    return sum(1 for t, p in pairs if t == p), len(pairs)


def macro_f1(pairs: list[tuple[str, str | None]]) -> float | None:
    labels = sorted({t for t, _ in pairs})
    if not labels:
        return None
    f1s = []
    for lab in labels:
        tp = sum(1 for t, p in pairs if t == lab and p == lab)
        fp = sum(1 for t, p in pairs if t != lab and p == lab)
        fn = sum(1 for t, p in pairs if t == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s)


def c3_pairs(
    detail_docs: list[dict[str, Any]], labels: dict[str, Any]
) -> list[tuple[str, str | None]]:
    truth = {d["key"]: d.get("c3") for d in labels["documents"] if d.get("c3")}
    return [
        (truth[d["doc_key"]], d["c3"])
        for d in detail_docs
        if d["doc_type"] == "bank_statement" and d["doc_key"] in truth
    ]


def c45_pairs(
    detail_docs: list[dict[str, Any]], labels: dict[str, Any], truth: dict[str, Any]
) -> tuple[list[tuple[Any, Any]], list[tuple[Any, Any]]]:
    """C4 (credit rows) and C5 (all rows) ``(truth, predicted)`` pairs over aligned rows."""
    by_key = {d["doc_key"]: d for d in detail_docs if d["doc_type"] == "bank_statement"}
    lab = {(r["doc_key"], r["row"]): r for r in labels["rows"]}
    c4: list[tuple[Any, Any]] = []
    c5: list[tuple[Any, Any]] = []
    for gd in truth["documents"]:
        if gd["doc_type"] != "bank_statement" or gd["key"] not in by_key:
            continue
        trows = truth_rows(gd)
        erows = by_key[gd["key"]]["rows"]
        for i, j in align_rows(erows, trows):
            tl = lab.get((gd["key"], trows[j]["index"]))
            if tl is None:
                continue
            c5.append((tl["c5"], erows[i].get("c5")))
            if trows[j].get("credit"):
                c4.append((tl.get("c4"), erows[i].get("c4")))
    return c4, c5
