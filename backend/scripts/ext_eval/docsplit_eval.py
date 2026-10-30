"""DocSplit packet topologies against the statement-assembly code (``app.assemble``).

SCOPE, STATED FIRST. The DocSplit subset obtained here is labels only: no page images and no OCR
text (they live in a 1.25 GB archive that was not fetched), and its documents are generic (resumes,
letters, memos, scientific papers), not bank statements. The assembler only groups scanned
BANK STATEMENT pages on account number, "page x of y" markers, period and running-balance hand-over,
so it cannot be run on DocSplit documents at all, and a DocSplit "boundary" between a resume and a
letter is not something it detects (document type is the classifier's job and is not measured here).

What can honestly be done: use DocSplit as a source of REAL UPLOAD TOPOLOGIES. Each packet says how
many documents there are, how many pages each has, in which order the pages arrive (in order,
shuffled within a document, interleaved round-robin, or fully random) and which page belongs to
which document. This script maps every sub-document of every packet to one synthetic scanned bank
statement of the same page count, uploads its pages one image per page in the packet's page order,
and asks the assembler to put them back. The page CONTENT (account numbers, markers, balances) is
generated here under stated cue conditions, so the numbers below measure the assembly logic under
DocSplit's arrangements. They do not measure OCR, extraction or any property of DocSplit's own
pages, and they are an upper bound for real scans because the cues are assumed to be read
correctly except where a scenario says otherwise.

Truth for a sub-document is its pages ranked by ``local_doc_id_page_ordinal`` (the page number in
the source document; the packet row order is the upload order). A statement is *recovered* when the
assembler returns one assembled group holding exactly its pages in that order. Anything else is
*abstained* (incomplete or ambiguous, "pages not reviewed": safe) or *wrong* (assembled with other
members or in another order: the error that matters).

    uv run python backend/scripts/ext_eval/docsplit_eval.py run \
        --root $EXTERNAL_DATA/docsplit --out docs/eval-results/external
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.assemble import assemble_statements  # noqa: E402
from app.core.models import Document, FileRef, PageInfo  # noqa: E402
from app.core.testing import make_ctx, make_doc  # noqa: E402
from scripts._eval import metrics as mx  # noqa: E402
from scripts._eval.runner import run_pool  # noqa: E402

STRATEGIES = ("mono_seq", "mono_rand", "poly_seq", "poly_int", "poly_rand")
SEED = 20261029
MAX_PAGES = 20  # packets in the "small" variant have 5 to 20 pages, so no sub-document is longer
SCENARIOS: dict[str, str] = {
    "distinct_accounts": "each sub-document is a statement of its own account; markers and header read correctly",
    "same_account_months": "all statements of a packet are consecutive months of ONE account (identity cues equal; markers, period and balances must separate them)",
    "no_markers": "page markers not printed or not read; the account number and the balance hand-over are all there is",
    "markers_lost_30pct": "each page independently loses its marker with probability 0.3 (stand-in for OCR misreads)",
    "account_lost_30pct": "each page independently loses its account number with probability 0.3; markers kept",
    "noisy_cues": "per page: marker lost 0.2, account number lost 0.2, last printed balance misread 0.1 (independent)",
    "page_missing": "one page of every statement of 3 or more pages is not uploaded (truth: not assemblable)",
    "duplicate_upload": "the first statement of a packet is uploaded twice (every cue identical): must not be merged or guessed",
}
# per-condition probabilities of losing a cue on a page: (marker, account, balance misread)
LOSS: dict[str, tuple[float, float, float]] = {
    "markers_lost_30pct": (0.3, 0.0, 0.0),
    "account_lost_30pct": (0.0, 0.3, 0.0),
    "noisy_cues": (0.2, 0.2, 0.1),
}
SENTENCE = (
    "DocSplit supplies only packet structure here (labels, no page images); page content is synthetic "
    "and the DocSplit documents are not bank statements."
)

# ------------------------------------------------------------------------------ reading DocSplit


@dataclass(frozen=True)
class Page:
    position: int  # 1-based upload position in the packet
    doc: str  # local_doc_id
    doc_type: str
    ordinal: int  # page number inside the source document


@dataclass(frozen=True)
class Packet:
    strategy: str
    packet_id: str
    pages: tuple[Page, ...]

    def docs(self) -> dict[str, list[Page]]:
        out: dict[str, list[Page]] = defaultdict(list)
        for p in self.pages:
            out[p.doc].append(p)
        return out


def load_packets(root: Path, strategy: str) -> list[Packet]:
    path = root / "small" / strategy / "test.csv"
    packets: dict[str, list[Page]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            packets[row["parent_doc_name"]].append(
                Page(
                    int(row["page"]),
                    row["local_doc_id"],
                    row["doc_type"],
                    int(row["local_doc_id_page_ordinal"]),
                )
            )
    out = []
    for pid, pages in packets.items():
        pages.sort(key=lambda p: p.position)
        # positions are global in the CSV; re-number within the packet
        renum = tuple(Page(i + 1, p.doc, p.doc_type, p.ordinal) for i, p in enumerate(pages))
        out.append(Packet(strategy, pid, renum))
    out.sort(key=lambda p: p.packet_id)
    return out


def topology_stats(packets: Sequence[Packet]) -> dict[str, Any]:
    docs_per_packet = [len(p.docs()) for p in packets]
    pages_per_doc = Counter()
    shuffled = contiguous_broken = interleaved_packets = n_docs = 0
    for p in packets:
        broke_here = False
        for pages in p.docs().values():
            n_docs += 1
            pages_per_doc[min(len(pages), 12)] += 1
            ords = [x.ordinal for x in pages]
            if ords != sorted(ords):
                shuffled += 1
            pos = [x.position for x in pages]
            if pos != list(range(pos[0], pos[0] + len(pos))):
                contiguous_broken += 1
                broke_here = True
        interleaved_packets += broke_here
    return {
        "packets": len(packets),
        "pages": sum(len(p.pages) for p in packets),
        "documents": n_docs,
        "documents_per_packet_mean": round(sum(docs_per_packet) / len(docs_per_packet), 2)
        if docs_per_packet
        else 0.0,
        "single_page_documents": pages_per_doc[1],
        "documents_with_pages_out_of_order": shuffled,
        "documents_whose_pages_are_not_contiguous": contiguous_broken,
        "packets_with_a_split_document": interleaved_packets,
        "pages_per_document": {str(k): v for k, v in sorted(pages_per_doc.items())},
    }


# ------------------------------------------------------------------------------ synthetic pages


@dataclass
class Spec:
    """One statement to be uploaded as ``k`` page images."""

    key: str
    account: str
    year_month: str
    opening: Decimal
    pages: list[list[dict[str, str | None]]]  # rows per page
    closing: Decimal


def make_spec(
    rng: random.Random, key: str, k: int, account: str, month: int, opening: Decimal
) -> Spec:
    balance = opening
    pages: list[list[dict[str, str | None]]] = []
    day = 1
    for p in range(k):
        rows = []
        for _ in range(rng.randint(2, 4)):
            day = min(28, day + rng.randint(0, 2))
            if rng.random() < 0.5 or balance < Decimal("2000"):
                credit = Decimal(rng.randint(100, 4000)) + Decimal(rng.randint(1, 99)) / 100
                balance += credit
                debit_s, credit_s = None, f"{credit:.2f}"
            else:
                debit = Decimal(rng.randint(50, 1500)) + Decimal(rng.randint(1, 99)) / 100
                balance -= debit
                debit_s, credit_s = f"{debit:.2f}", None
            rows.append(
                {
                    "date": f"2026-{month:02d}-{day:02d}",
                    "description": f"TXN {key} {p}.{len(rows)}",
                    "debit": debit_s,
                    "credit": credit_s,
                    "balance": f"{balance:.2f}",
                }
            )
        pages.append(rows)
    return Spec(key, account, f"2026-{month:02d}", opening, pages, balance)


def page_document(
    n: int,
    spec: Spec,
    index: int,
    *,
    marker: bool,
    header_on_all: bool,
    account: bool = True,
    misread: bool = False,
) -> Document:
    k = len(spec.pages)
    first, last = index == 0, index == k - 1
    fields: dict[str, str | None] = {}
    if first or header_on_all:
        month = int(spec.year_month[5:])
        end_day = {2: 28, 4: 30, 6: 30, 9: 30, 11: 30}.get(month, 31)
        fields |= {
            "bank_name": "EXAMPLE BANK",
            "account_holder": "EXAMPLE LOGISTICS PTE LTD",
            "account_number": spec.account if account else None,
            "period_start": f"{spec.year_month}-01",
            "period_end": f"{spec.year_month}-{end_day:02d}",
            "currency": "SGD",
        }
    if first:
        fields["opening_balance"] = f"{spec.opening:.2f}"
    if last:
        fields["closing_balance"] = f"{spec.closing:.2f}"
    chain: dict[str, str | None] = {
        "page": "1",
        "account_number": spec.account if account else None,
    }
    if marker:
        chain |= {"page_x": str(index + 1), "page_y": str(k)}
    rows = [dict(r) for r in spec.pages[index]]
    if misread:  # the last printed balance read with one digit wrong
        bal = Decimal(rows[-1]["balance"] or "0")
        rows[-1]["balance"] = f"{bal + Decimal('10.00'):.2f}"
    return make_doc(
        "bank_statement",
        id=f"d{n}",
        file_id=f"f{n}",
        fields=fields,
        tables={"transactions": rows, "page_chain": [chain]},
    )


def file_ref(n: int) -> FileRef:
    return FileRef(
        id=f"f{n}",
        name=f"page{n}.jpg",
        sha256=f"{n:064x}",
        size_bytes=10,
        mime="image/jpeg",
        pages=[
            PageInfo(
                page=1,
                width_pt=595,
                height_pt=842,
                has_text_layer=False,
                is_scan=True,
                word_count=50,
            )
        ],
    )


@dataclass
class Upload:
    docs: list[Document]
    truth: dict[str, list[str]]  # statement key -> document ids in true page order
    expect_assembled: dict[str, bool]  # statement key -> should it be recoverable
    duplicates: set[str]  # statement keys that are exact copies of another (must not be merged)


def build_upload(packet: Packet, scenario: str, rng: random.Random) -> Upload | None:
    """Map the packet's multi-page sub-documents to synthetic statements; single-page documents
    are whole by themselves (the assembler leaves them alone) and are skipped."""
    docs = {d: sorted(p, key=lambda x: x.ordinal) for d, p in packet.docs().items()}
    multi = {d: ps for d, ps in docs.items() if 2 <= len(ps) <= MAX_PAGES}
    if not multi:
        return None
    specs: dict[str, Spec] = {}
    month0 = rng.randint(1, 6)
    account0 = f"{rng.randint(1000, 9999)}-{rng.randint(100000, 999999)}"
    opening: Decimal | None = None
    for i, (d, ps) in enumerate(sorted(multi.items())):
        if scenario == "same_account_months":
            opening = opening if opening is not None else Decimal(rng.randint(20_000, 90_000))
            specs[d] = make_spec(rng, d, len(ps), account0, month0 + i, opening)
            opening = specs[d].closing
        else:
            account = f"{rng.randint(1000, 9999)}-{rng.randint(100000, 999999)}"
            specs[d] = make_spec(
                rng, d, len(ps), account, rng.randint(1, 9), Decimal(rng.randint(10_000, 90_000))
            )
    # which page images are uploaded (a missing page is simply absent)
    arrivals: list[tuple[str, int, dict[str, bool]]] = []  # (statement key, page index, cues)
    expect: dict[str, bool] = {}
    dropped: dict[str, int] = {}
    for d, ps in multi.items():
        expect[d] = True
        if scenario == "page_missing" and len(ps) >= 3:
            dropped[d] = rng.randrange(len(ps))
            expect[d] = False
    rank = {(d, p.position): i for d, ps in multi.items() for i, p in enumerate(ps)}
    for page in packet.pages:
        if page.doc not in multi:
            continue
        idx = rank[(page.doc, page.position)]
        if dropped.get(page.doc) == idx:
            continue
        p_marker, p_account, p_misread = LOSS.get(scenario, (0.0, 0.0, 0.0))
        cues = {
            "marker": scenario != "no_markers" and rng.random() >= p_marker,
            "account": rng.random() >= p_account,
            "misread": rng.random() < p_misread,
        }
        arrivals.append((page.doc, idx, cues))
    dup_keys: set[str] = set()
    if scenario == "duplicate_upload":
        first = next(iter(sorted(multi)))
        copies = [(f"{first}#copy", idx, cues) for (d, idx, cues) in arrivals if d == first]
        specs[f"{first}#copy"] = specs[first]
        arrivals += copies
        expect[first] = False  # two identical candidates: ambiguity is the right answer
        expect[f"{first}#copy"] = False
        dup_keys = {first, f"{first}#copy"}
    header_on_all = scenario == "no_markers"
    out_docs: list[Document] = []
    by_stmt: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for n, (d, idx, cues) in enumerate(arrivals, start=1):
        out_docs.append(
            page_document(
                n,
                specs[d],
                idx,
                marker=cues["marker"],
                header_on_all=header_on_all,
                account=cues["account"],
                misread=cues["misread"],
            )
        )
        by_stmt[d].append((idx, f"d{n}"))
    truth = {d: [doc_id for _, doc_id in sorted(v)] for d, v in by_stmt.items()}
    return Upload(out_docs, truth, expect, dup_keys)


# ------------------------------------------------------------------------------ scoring one upload


def evaluate_upload(up: Upload) -> dict[str, Any]:
    ctx = make_ctx(up.docs, files_extra=[file_ref(int(d.id[1:])) for d in up.docs])
    assemble_statements(ctx)
    state = {d.id: d.statement for d in ctx.case.documents}
    groups: dict[str, list[str]] = {}
    for d in ctx.case.documents:
        link = d.statement
        if link is not None:
            groups.setdefault(link.group, list(link.members))
    pred_of: dict[str, str] = {}
    for g, members in groups.items():
        for m in members:
            pred_of[m] = g
    out = {
        "statements": 0,
        "recovered": 0,
        "abstained": 0,
        "wrong": 0,
        "should_assemble": 0,
        "should_abstain": 0,
        "wrongly_assembled_when_should_abstain": 0,
        "right_for_abstain": 0,
        "pairs_tp": 0,
        "pairs_fp": 0,
        "pairs_fn": 0,
        "order_groups_correct_members": 0,
        "order_groups_right_order": 0,
        "pages": 0,
        "pages_abstained": 0,
        "pages_in_wrong_group": 0,
    }
    truth_group: dict[str, str] = {}
    for key, ids in up.truth.items():
        for doc_id in ids:
            truth_group[doc_id] = key
    for key, ids in up.truth.items():
        out["statements"] += 1
        out["pages"] += len(ids)
        links = [state[i] for i in ids]
        assembled = [lk for lk in links if lk is not None and lk.state == "assembled"]
        group_members = list(assembled[0].members) if assembled else None
        same_group = (
            bool(assembled)
            and len({lk.group for lk in assembled}) == 1
            and len(assembled) == len(ids)
        )
        if up.expect_assembled.get(key, True):
            out["should_assemble"] += 1
            if same_group and group_members == ids:
                out["recovered"] += 1
            elif assembled:
                out["wrong"] += 1
                out["pages_in_wrong_group"] += len(ids)
            else:
                out["abstained"] += 1
                out["pages_abstained"] += len(ids)
        else:
            out["should_abstain"] += 1
            if assembled:
                out["wrongly_assembled_when_should_abstain"] += 1
                out["pages_in_wrong_group"] += len(ids)
            else:
                out["right_for_abstain"] += 1
                out["pages_abstained"] += len(ids)
        if same_group and set(group_members) == set(ids):
            out["order_groups_correct_members"] += 1
            out["order_groups_right_order"] += int(group_members == ids)
    # boundary detection as a pair task: are two uploaded pages of the same statement? Pages the
    # assembler declined to join count as separate. The truth for duplicate copies is "different".
    ids_all = [d.id for d in ctx.case.documents]
    for a, b in combinations(ids_all, 2):
        ga = truth_group.get(a)
        truth_same = (
            ga is not None and ga == truth_group.get(b) and up.expect_assembled.get(ga, True)
        )
        la, lb = state[a], state[b]
        pred_same = (
            la is not None
            and lb is not None
            and la.state == "assembled"
            and lb.state == "assembled"
            and la.group == lb.group
        )
        if truth_same and pred_same:
            out["pairs_tp"] += 1
        elif pred_same and not truth_same:
            out["pairs_fp"] += 1
        elif truth_same and not pred_same:
            out["pairs_fn"] += 1
    return out


def run_packet(job: dict[str, Any]) -> dict[str, Any]:
    packet: Packet = job["packet"]
    scenario: str = job["scenario"]
    rng = random.Random(f"{SEED}:{packet.strategy}:{packet.packet_id}:{scenario}")
    up = build_upload(packet, scenario, rng)
    if up is None:
        return {"strategy": packet.strategy, "scenario": scenario, "skipped": True}
    try:
        res = evaluate_upload(up)
    except Exception as exc:  # reported, never swallowed silently
        return {
            "strategy": packet.strategy,
            "scenario": scenario,
            "error": f"{type(exc).__name__}: {exc}",
            "packet": packet.packet_id,
        }
    res |= {"strategy": packet.strategy, "scenario": scenario, "skipped": False}
    res["packet_exact"] = int(
        res["recovered"] + res["right_for_abstain"] == res["statements"]
        and res["wrong"] == 0
        and res["wrongly_assembled_when_should_abstain"] == 0
    )
    return res


# ------------------------------------------------------------------------------ aggregate


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = mx.wilson(k, n)
    return {
        "k": k,
        "n": n,
        "rate": round(k / n, 4) if n else None,
        "lo": round(lo, 4),
        "hi": round(hi, 4),
    }


SUM_KEYS = [
    "statements",
    "recovered",
    "abstained",
    "wrong",
    "should_assemble",
    "should_abstain",
    "wrongly_assembled_when_should_abstain",
    "right_for_abstain",
    "pairs_tp",
    "pairs_fp",
    "pairs_fn",
    "order_groups_correct_members",
    "order_groups_right_order",
    "pages",
    "pages_abstained",
    "pages_in_wrong_group",
    "packet_exact",
]


def aggregate(
    results: Sequence[dict[str, Any]], stats: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    errors = [r for r in results if r.get("error")]
    table: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        for scenario in SCENARIOS:
            rs = [
                r
                for r in results
                if r["strategy"] == strategy and r["scenario"] == scenario and not r.get("error")
            ]
            skipped = sum(1 for r in rs if r.get("skipped"))
            used = [r for r in rs if not r.get("skipped")]
            tot = {k: sum(r[k] for r in used) for k in SUM_KEYS}
            tp, fp, fn = tot["pairs_tp"], tot["pairs_fp"], tot["pairs_fn"]
            table[f"{strategy}/{scenario}"] = {
                "packets_used": len(used),
                "packets_without_multi_page_documents": skipped,
                **tot,
                "recovered_rate": _rate(tot["recovered"], tot["should_assemble"])
                if tot["should_assemble"]
                else None,
                "wrong_rate": _rate(tot["wrong"], tot["should_assemble"])
                if tot["should_assemble"]
                else None,
                "abstain_correct_rate": _rate(tot["right_for_abstain"], tot["should_abstain"])
                if tot["should_abstain"]
                else None,
                "wrongly_assembled_rate": _rate(
                    tot["wrongly_assembled_when_should_abstain"], tot["should_abstain"]
                )
                if tot["should_abstain"]
                else None,
                "pair_precision": _rate(tp, tp + fp) if tp + fp else None,
                "pair_recall": _rate(tp, tp + fn) if tp + fn else None,
                "order_correct": _rate(
                    tot["order_groups_right_order"], tot["order_groups_correct_members"]
                )
                if tot["order_groups_correct_members"]
                else None,
                "packet_exact": _rate(tot["packet_exact"], len(used)) if used else None,
            }
    return {
        "seed": SEED,
        "topology": stats,
        "table": table,
        "errors": errors[:10],
        "n_errors": len(errors),
    }


def _f(r: dict[str, Any] | None) -> str:
    return "n/a" if not r else f"{r['k']}/{r['n']} = {r['rate']:.3f}"


def render_markdown(p: dict[str, Any]) -> str:
    out: list[str] = []
    a = out.append
    a("# DocSplit packet topologies against the statement assembler\n")
    a(f"> {SENTENCE}\n")
    a(
        "Generated by `backend/scripts/ext_eval/docsplit_eval.py`. **Scope.** The DocSplit subset is label files only "
        "(no page images, no OCR text) for the `small` test split of five strategies, and its documents are generic "
        "(resumes, letters, memos, scientific papers). The assembler (`app/assemble.py`) groups scanned **bank statement** "
        "pages on account number, `page x of y` markers, period and balance hand-over, so it cannot be run on DocSplit's "
        "documents, and a boundary between a resume and a letter is a classification question that is not measured here. "
        "What is measured: every multi-page sub-document of every packet becomes one synthetic scanned statement with the "
        "same page count, uploaded one image per page in the packet's real page order, under the cue conditions below. "
        "The result shows whether the assembly logic copes with DocSplit's arrangements (in order, shuffled inside a document, "
        "interleaved, fully random). It says nothing about OCR quality, real documents, or DocSplit's own pages.\n"
    )
    a("## Packet structure actually used\n")
    a(
        "| Strategy | Packets | Pages | Documents | Single-page documents (assembler not involved) | Documents with pages out of order | Documents split non-contiguously |"
    )
    a("|---|---|---|---|---|---|---|")
    for s in STRATEGIES:
        t = p["topology"][s]
        a(
            f"| `{s}` | {t['packets']} | {t['pages']} | {t['documents']} | {t['single_page_documents']} | {t['documents_with_pages_out_of_order']} | {t['documents_whose_pages_are_not_contiguous']} |"
        )
    a("")
    a("## Cue conditions\n")
    for k, v in SCENARIOS.items():
        a(f"- `{k}`: {v}")
    a("")
    a("## Results\n")
    a(
        "`Recovered` = the statement came back as one assembled group with exactly its pages in the true order (of statements that should assemble). `Wrong` = assembled, but with other members or in another order (the error that matters). `Abstained` = reported as incomplete or ambiguous (pages not reviewed; safe, never flagged as tampering). For the two conditions where abstaining is the right answer (a page missing, a duplicate upload) the columns are `correct abstention` and `wrongly assembled`. Pair precision and recall treat the task as boundary detection: are two uploaded pages of the same statement (a page left unjoined counts as separate).\n"
    )
    a(
        "| Strategy | Condition | Packets | Recovered | Wrong | Abstained | Correct abstention | Wrongly assembled | Pair precision | Pair recall | Order right (of correct groups) | Packets fully right |"
    )
    a("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in STRATEGIES:
        for sc in SCENARIOS:
            r = p["table"][f"{s}/{sc}"]
            n = r["should_assemble"]
            ab = r["abstained"]
            a(
                f"| `{s}` | `{sc}` | {r['packets_used']} | {_f(r['recovered_rate'])} | {r['wrong']}/{n if n else 0} | {ab}/{n if n else 0} | "
                f"{_f(r['abstain_correct_rate'])} | {r['wrongly_assembled_when_should_abstain']}/{r['should_abstain'] if r['should_abstain'] else 0} | "
                f"{_f(r['pair_precision'])} | {_f(r['pair_recall'])} | {_f(r['order_correct'])} | {_f(r['packet_exact'])} |"
            )
    a("")
    if p["n_errors"]:
        a(f"**{p['n_errors']} packets raised an error** (first ones: {p['errors'][:3]}).\n")
    a("## Limits\n")
    a(
        "- Not a measurement on DocSplit documents: its pages are not bank statements and were not available. The synthetic pages carry exactly the cues the assembler reads, so this is an upper bound for real scans, where an account number or marker can be misread (see the `markers_lost_30pct`, `account_lost_30pct` and `noisy_cues` conditions for crude stand-ins)."
    )
    a(
        "- A packet fully randomised across documents (`poly_rand`) is the same problem as shuffled uploads of several statements; what makes it solvable here is that every page carries a marker and an account number. Pages that carry neither would be left unassembled, not guessed."
    )
    a(
        "- Single-page documents are 'whole' by themselves and never pass through the assembler, so they are excluded from every rate."
    )
    a(
        "- A forged page that carries the right markers and a consistent balance is accepted like any other; assembly is not a tamper detector."
    )
    return "\n".join(out) + "\n"


def write_outputs(out: Path, payload: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "docsplit-topology.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "docsplit-topology.md").write_text(render_markdown(payload), encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root)
    jobs: list[dict[str, Any]] = []
    stats: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        packets = load_packets(root, strategy)
        stats[strategy] = topology_stats(packets)
        if args.limit:
            packets = packets[: args.limit]
        for pk in packets:
            for sc in SCENARIOS:
                jobs.append({"packet": pk, "scenario": sc})
    print(f"{len(jobs)} packet x condition jobs", file=sys.stderr)
    results = run_pool(run_packet, jobs, args.workers)
    payload = aggregate(results, stats)
    payload["sentence"] = SENTENCE
    write_outputs(Path(args.out), payload)
    print(render_markdown(payload))
    return 0 if not payload["n_errors"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument(
        "--root", required=True, help="the docsplit folder (contains small/<strategy>/test.csv)"
    )
    r.add_argument("--out", default="docs/eval-results/external")
    r.add_argument("--limit", type=int, default=0, help="packets per strategy (smoke test)")
    r.add_argument("--workers", type=int, default=4)
    r.set_defaults(fn=cmd_run)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
