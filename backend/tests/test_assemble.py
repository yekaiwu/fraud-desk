"""Statement assembly (``app.assemble``): bank statements photographed or scanned page by page.

Each page below is its own file and its own ``bank_statement`` document, exactly as an image
upload arrives. Documents are built by hand so nothing depends on OCR or extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from app.assemble import AssembledStatement, assemble_statements, logical_docs
from app.checks import bank_statement as bs
from app.checks.registry import CheckSkipped, make_flag, run_stage_checks
from app.core.models import Document, FileRef, PageInfo, Region
from app.core.testing import make_ctx, make_doc
from app.queue import fast_lane_eligible, unchecked_pages
from app.score import collect_partial_reasons, compute_coverage

ACCT = "0012345678"


@dataclass
class Month:
    """One statement split into pages: ``pages`` is a list of lists of ``(day, debit, credit)``."""

    year_month: str  # "2026-03"
    pages: list[list[tuple[int, str | None, str | None]]]
    opening: str = "10000.00"
    account: str = ACCT
    bank: str = "EXAMPLE BANK"
    rows: list[list[dict]] = field(default_factory=list)
    closing: str = ""

    def __post_init__(self) -> None:
        balance = Decimal(self.opening)
        for page in self.pages:
            out = []
            for day, debit, credit in page:
                balance += Decimal(credit or "0") - Decimal(debit or "0")
                out.append(
                    {
                        "date": f"{self.year_month}-{day:02d}",
                        "description": f"PAYMENT {day}",
                        "debit": debit,
                        "credit": credit,
                        "balance": f"{balance:.2f}",
                    }
                )
            self.rows.append(out)
        self.closing = f"{balance:.2f}"


def march(opening: str = "10000.00", account: str = ACCT) -> Month:
    return Month(
        "2026-03",
        [
            [(2, None, "5000.00"), (5, "1200.00", None), (9, None, "300.00")],
            [(12, "450.00", None), (15, None, "2500.00"), (20, "75.50", None)],
            [(24, "600.00", None), (28, None, "900.00"), (31, "20.00", None)],
        ],
        opening=opening,
        account=account,
    )


def april(opening: str, account: str = ACCT) -> Month:
    return Month(
        "2026-04",
        [
            [(3, None, "4000.00"), (7, "2200.00", None)],
            [(14, "310.00", None), (21, None, "1800.00")],
            [(25, "99.00", None), (30, None, "50.00")],
        ],
        opening=opening,
        account=account,
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


def page_doc(
    n: int,
    month: Month,
    index: int,
    *,
    markers: bool = True,
    header: str = "first",
    parts: int | None = None,
    rows: list[dict] | None = None,
) -> Document:
    """Page ``index`` (0-based) of ``month`` as document ``d<n>`` in file ``f<n>``."""
    total = parts if parts is not None else len(month.pages)
    first, last = index == 0, index == len(month.pages) - 1
    fields: dict[str, str | None] = {}
    if header == "all" or first:
        fields.update(
            bank_name=month.bank,
            account_holder="EXAMPLE LOGISTICS PTE LTD",
            account_number=month.account,
            period_start=f"{month.year_month}-01",
            period_end=f"{month.year_month}-{'30' if month.year_month == '2026-04' else '31'}",
            currency="SGD",
        )
    if first:
        fields["opening_balance"] = month.opening
    if last:
        fields["closing_balance"] = month.closing
    chain = {"page": "1", "account_number": month.account}
    if markers:
        chain |= {"page_x": str(index + 1), "page_y": str(total)}
    doc = make_doc(
        "bank_statement",
        id=f"d{n}",
        file_id=f"f{n}",
        fields=fields,
        tables={
            "transactions": rows if rows is not None else month.rows[index],
            "page_chain": [chain],
        },
    )
    return doc


def upload(docs: list[Document]):  # noqa: ANN201
    numbers = [int(d.id[1:]) for d in docs]
    return make_ctx(docs, files_extra=[file_ref(n) for n in numbers])


def whole(ctx) -> list[AssembledStatement]:  # noqa: ANN001
    return [d for d in logical_docs(ctx, "bank_statement", whole_only=True)]


# ---------------------------------------------------------------- grouping


def test_pages_in_order_become_one_statement() -> None:
    m = march()
    ctx = upload([page_doc(i + 1, m, i) for i in range(3)])
    counts = assemble_statements(ctx)
    assert counts == {"assembled": 1}
    (stmt,) = whole(ctx)
    assert isinstance(stmt, AssembledStatement)
    assert stmt.member_ids == ["d1", "d2", "d3"]
    assert len(stmt.tables["transactions"].rows) == 9
    assert [r.index for r in stmt.tables["transactions"].rows] == list(range(9))
    assert stmt.fields["opening_balance"].value == "10000.00"
    assert stmt.fields["closing_balance"].value == m.closing
    assert all(d.statement and d.statement.state == "assembled" for d in ctx.case.documents)
    assert unchecked_pages(ctx.case) == []


def test_whole_statement_rules_are_quiet_on_an_assembled_statement() -> None:
    m = march()
    ctx = upload([page_doc(i + 1, m, i) for i in range(3)])
    assemble_statements(ctx)
    assert bs.bs_running_balance(ctx) == []
    assert bs.bs_totals(ctx) == []
    assert bs.bs_page_chain(ctx) == []


def test_shuffled_upload_is_put_in_page_order() -> None:
    m = march()
    # the files arrive as page 3, page 1, page 2
    ctx = upload([page_doc(n + 1, m, idx) for n, idx in enumerate([2, 0, 1])])
    assert assemble_statements(ctx) == {"assembled": 1}
    (stmt,) = whole(ctx)
    assert stmt.member_ids == ["d2", "d3", "d1"]  # pages 1, 2, 3
    assert stmt.id == "d2"
    got = [r.cells["description"].value for r in stmt.tables["transactions"].rows]
    assert got == [r["description"] for page in m.rows for r in page]
    assert bs.bs_totals(ctx) == []
    assert bs.bs_page_chain(ctx) == []
    assert bs.bs_running_balance(ctx) == []


def test_two_accounts_interleaved() -> None:
    a, b = march(), march(opening="500.00", account="0099887766")
    order = [(a, 0), (b, 0), (b, 1), (a, 1), (a, 2), (b, 2)]
    ctx = upload([page_doc(n + 1, m, i) for n, (m, i) in enumerate(order)])
    assert assemble_statements(ctx) == {"assembled": 2}
    stmts = whole(ctx)
    assert sorted(s.member_ids for s in stmts) == [["d1", "d4", "d5"], ["d2", "d3", "d6"]]
    assert bs.bs_totals(ctx) == []


def test_two_months_of_one_account_interleaved() -> None:
    mar = march()
    apr = april(opening=mar.closing)
    order = [(apr, 1), (mar, 0), (apr, 0), (mar, 2), (mar, 1), (apr, 2)]
    ctx = upload([page_doc(n + 1, m, i) for n, (m, i) in enumerate(order)])
    assert assemble_statements(ctx) == {"assembled": 2}
    assert sorted(s.member_ids for s in whole(ctx)) == [["d2", "d5", "d4"], ["d3", "d1", "d6"]]
    assert bs.bs_totals(ctx) == [] and bs.bs_page_chain(ctx) == []
    # the two months now form a contiguous pair, so continuity is judged on whole statements
    assert bs.bs_period_continuity(ctx) == []


def test_pages_without_page_markers_join_on_the_balance_hand_over() -> None:
    m = march()
    ctx = upload([page_doc(i + 1, m, i, markers=False, header="all") for i in range(3)])
    # no markers and the header on every page: only the first has an opening, the last a closing
    assert assemble_statements(ctx) == {"assembled": 1}
    (stmt,) = whole(ctx)
    assert stmt.member_ids == ["d1", "d2", "d3"]
    with pytest.raises(CheckSkipped):  # no markers: there is no chain to compare
        bs.bs_page_chain(ctx)
    assert bs.bs_totals(ctx) == []


# ---------------------------------------------------------------- not reviewed, never a pass


def test_missing_page_is_not_reviewed_and_not_flagged() -> None:
    m = march()
    ctx = upload([page_doc(1, m, 0), page_doc(2, m, 2)])  # page 2 of 3 never uploaded
    counts = assemble_statements(ctx)
    assert counts["incomplete"] >= 1 and "assembled" not in counts
    link = ctx.case.documents[0].statement
    assert link is not None and link.state == "incomplete"
    # the page that is missing is named; nothing is claimed about the pages that are there
    assert any(d.statement and 2 in d.statement.missing for d in ctx.case.documents)

    # whole-statement rules did not run on the pieces: no false high flags
    for rule in (bs.bs_totals, bs.bs_page_chain, bs.bs_period_continuity):
        with pytest.raises(CheckSkipped):  # nothing left to evaluate, so the rule did not run
            rule(ctx)
    # ... and the pieces are reported, with the reason, as pages nobody reviewed
    pages = unchecked_pages(ctx.case)
    assert {(p.file_id, p.reason) for p in pages} == {
        ("f1", "statement_incomplete"),
        ("f2", "statement_incomplete"),
    }
    assert any(r.startswith("statement_incomplete:") for r in collect_partial_reasons(ctx.case))


def test_missing_page_blocks_the_fast_lane_and_adds_an_info_flag() -> None:
    m = march()
    ctx = upload([page_doc(1, m, 0), page_doc(2, m, 2)])
    assemble_statements(ctx)
    run_stage_checks(ctx, "rules")
    info = [f for f in ctx.case.flags if f.rule == "doc_unreadable"]
    assert [f.document_ids for f in info] == [["d1"], ["d2"]]  # one note per piece that is left
    assert all(f.severity == "info" for f in info)
    assert all("not reviewed" in f.evidence for f in info)
    assert "missing or not matched" in info[0].evidence
    assert not [f for f in ctx.case.flags if f.severity in ("high", "medium")]

    from app.score import run_stage

    run_stage(ctx)
    assert ctx.case.score is not None and ctx.case.score.partial
    assert ctx.case.coverage is not None and ctx.case.coverage.pages_unchecked == 2
    assert not fast_lane_eligible(ctx.case)
    assert compute_coverage(ctx.case).unchecked_pages[0].reason == "statement_incomplete"


def test_only_the_first_page_uploaded_is_not_reviewed() -> None:
    m = march()
    ctx = upload([page_doc(1, m, 0)])
    assert assemble_statements(ctx) == {"incomplete": 1, "pages": 1}
    assert ctx.case.documents[0].statement.missing == [2, 3]  # type: ignore[union-attr]


def test_a_duplicated_page_is_ambiguous_and_not_guessed() -> None:
    m = march()
    docs = [page_doc(1, m, 0), page_doc(2, m, 1), page_doc(3, m, 1), page_doc(4, m, 2)]
    ctx = upload(docs)
    counts = assemble_statements(ctx)
    assert "assembled" not in counts
    states = {d.id: d.statement.state for d in ctx.case.documents if d.statement}
    assert "ambiguous" in states.values()
    with pytest.raises(CheckSkipped):  # nothing was assembled, so no statement to total
        bs.bs_totals(ctx)
    assert {p.reason for p in unchecked_pages(ctx.case)} == {"statement_incomplete"}


# ---------------------------------------------------------------- tampering is still caught


def test_an_edited_amount_on_page_two_is_flagged_on_that_page() -> None:
    m = march()
    docs = [page_doc(i + 1, m, i) for i in range(3)]
    docs[1].tables["transactions"].rows[1].cells["credit"].value = "9500.00"  # 2500 -> 9500
    ctx = upload(docs)
    assert assemble_statements(ctx) == {"assembled": 1}
    flags = bs.bs_running_balance(ctx)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.rule == "bs_running_balance"
    assert flag.document_ids == ["d2"] and flag.row == 1  # the page it is on, that page's row
    assert bs.bs_totals(ctx)  # the closing balance no longer follows from the rows


def test_a_changed_balance_between_pages_still_joins_and_is_flagged() -> None:
    m = march()
    docs = [page_doc(i + 1, m, i) for i in range(3)]
    docs[0].tables["transactions"].rows[-1].cells["balance"].value = "99999.00"
    ctx = upload(docs)
    assert assemble_statements(ctx) == {"assembled": 1}  # markers and period decide, not balance
    assert bs.bs_running_balance(ctx)


def test_a_page_with_the_wrong_total_in_its_marker_is_not_joined() -> None:
    m = march()
    docs = [page_doc(1, m, 0), page_doc(2, m, 1, parts=4), page_doc(3, m, 2)]
    ctx = upload(docs)
    counts = assemble_statements(ctx)
    assert "assembled" not in counts  # 1/3, 2/4, 3/3 do not describe one statement
    assert unchecked_pages(ctx.case)


def test_a_gap_inside_one_scanned_file_is_left_to_the_page_chain_rule() -> None:
    m = march()
    # one three-page scanned PDF whose markers read 1, 3: adjacent pages cannot skip a number
    doc = make_doc(
        "bank_statement",
        id="d1",
        file_id="f1",
        fields={
            "account_number": ACCT,
            "period_start": "2026-03-01",
            "period_end": "2026-03-31",
            "opening_balance": m.opening,
            "closing_balance": m.closing,
            "currency": "SGD",
        },
        tables={
            "transactions": [r for page in m.rows for r in page],
            "page_chain": [
                {"page": "1", "page_x": "1", "page_y": "3", "account_number": ACCT},
                {"page": "2", "page_x": "3", "page_y": "3", "account_number": ACCT},
            ],
        },
        page_end=2,
    )
    ref = file_ref(1)
    ref.pages.append(ref.pages[0].model_copy(update={"page": 2}))
    ctx = make_ctx([doc], files_extra=[ref])
    assert assemble_statements(ctx) == {}
    assert doc.statement is None
    (flag,) = [f for f in bs.bs_page_chain(ctx) if "missing page 2" in f.evidence]
    assert flag.rule == "bs_page_chain"


# ---------------------------------------------------------------- digital is untouched


def test_digital_statements_are_never_assembled() -> None:
    m = march()
    docs = [page_doc(1, m, 0), page_doc(2, m, 2)]
    refs = [file_ref(1), file_ref(2)]
    for ref in refs:
        ref.pages[0] = ref.pages[0].model_copy(update={"is_scan": False, "has_text_layer": True})
    ctx = make_ctx(docs, files_extra=refs)
    assert assemble_statements(ctx) == {}
    assert all(d.statement is None for d in docs)
    assert logical_docs(ctx, "bank_statement") == docs
    # the old behaviour stays: a digital statement with a missing page is flagged
    assert bs.bs_page_chain(ctx)


def test_a_complete_scanned_pdf_is_left_alone() -> None:
    m = march()
    doc = make_doc(
        "bank_statement",
        id="d1",
        file_id="f1",
        fields={
            "account_number": ACCT,
            "opening_balance": m.opening,
            "closing_balance": m.closing,
            "period_start": "2026-03-01",
            "period_end": "2026-03-31",
        },
        tables={
            "transactions": [r for page in m.rows for r in page],
            "page_chain": [
                {"page": str(i + 1), "page_x": str(i + 1), "page_y": "3", "account_number": ACCT}
                for i in range(3)
            ],
        },
        page_end=3,
    )
    ref = file_ref(1)
    ref.pages = [ref.pages[0].model_copy(update={"page": i + 1}) for i in range(3)]
    ctx = make_ctx([doc], files_extra=[ref])
    assert assemble_statements(ctx) == {}
    assert doc.statement is None


def test_assembly_is_idempotent_and_json_round_trips() -> None:
    m = march()
    ctx = upload([page_doc(i + 1, m, i) for i in range(3)])
    assemble_statements(ctx)
    first = [d.model_dump_json() for d in ctx.case.documents]
    assemble_statements(ctx)
    assert [d.model_dump_json() for d in ctx.case.documents] == first
    assert '"statement"' in first[0]
    plain = make_doc("bank_statement", id="d9")
    assert "statement" not in plain.model_dump(mode="json")  # absent unless a statement was split


def test_flags_on_assembled_statements_point_at_stored_documents() -> None:
    m = march()
    docs = [page_doc(i + 1, m, i) for i in range(3)]
    for d in docs:
        for row in d.tables["transactions"].rows:
            row.cells["debit"].box = Region(file_id=d.file_id, page=1, x=0.1, y=0.2, w=0.1, h=0.02)
    docs[2].tables["transactions"].rows[0].cells["debit"].value = "1.00"
    ctx = upload(docs)
    assemble_statements(ctx)
    flags = run_stage_checks(ctx, "rules")
    assert flags  # the rules ran
    stored = {d.id for d in ctx.case.documents}
    for flag in ctx.case.flags:
        assert set(flag.document_ids) <= stored  # never a made-up id
    broken = [f for f in ctx.case.flags if f.rule == "bs_running_balance"]
    assert broken and broken[0].document_ids == ["d3"] and broken[0].row == 0


def test_period_dates_keep_two_months_of_one_account_apart() -> None:
    mar, apr = march(), april(opening="1.00")  # april opening deliberately does not follow march
    order = [(mar, 0), (apr, 0), (mar, 1), (apr, 1), (mar, 2), (apr, 2)]
    ctx = upload([page_doc(n + 1, m, i) for n, (m, i) in enumerate(order)])
    assert assemble_statements(ctx) == {"assembled": 2}
    assert date(2026, 3, 1) == date.fromisoformat(
        sorted(whole(ctx), key=lambda s: s.id)[0].fields["period_start"].value
    )


def test_a_month_that_is_uploaded_but_not_assembled_is_not_a_gap() -> None:
    mar = march()
    apr = april(opening=mar.closing)
    may = Month(
        "2026-05",
        [[(4, None, "100.00")], [(9, None, "200.00")]],
        opening=apr.closing,
    )
    docs = [page_doc(i + 1, mar, i) for i in range(3)]
    docs += [page_doc(4, apr, 0), page_doc(5, apr, 2)]  # april: page 2 of 3 never uploaded
    docs += [page_doc(6 + i, may, i) for i in range(2)]
    # may covers 31 days in the helper's period, april's pieces carry the april period
    ctx = upload(docs)
    counts = assemble_statements(ctx)
    assert counts["assembled"] == 2 and counts["incomplete"] >= 1
    assert bs.bs_period_continuity(ctx) == []  # no "April is missing" high flag


def test_a_month_removed_from_the_upload_is_still_a_gap() -> None:
    mar = march()
    apr = april(opening=mar.closing)
    may = Month("2026-05", [[(4, None, "100.00")], [(9, None, "200.00")]], opening=apr.closing)
    docs = [page_doc(i + 1, mar, i) for i in range(3)]
    docs += [page_doc(4 + i, may, i) for i in range(2)]  # april never uploaded at all
    ctx = upload(docs)
    assert assemble_statements(ctx) == {"assembled": 2}
    flags = bs.bs_period_continuity(ctx)
    assert [f.rule for f in flags] == ["bs_period_continuity"] and "gap" in flags[0].evidence


def _jan_and_april() -> list[Document]:
    """Two whole one-page statements with February and March absent from the upload."""
    jan = Month("2026-01", [[(5, None, "100.00")]], opening="1000.00")
    apr = Month("2026-04", [[(5, None, "100.00")]], opening="1100.00")
    return [page_doc(1, jan, 0), page_doc(2, apr, 0)]


def test_gap_flag_survives_a_stray_page_without_a_period() -> None:
    docs = _jan_and_april()
    docs.append(page_doc(3, march(), 1))  # page 2 of 3 of March: no header, so no period
    ctx = upload(docs)
    assert assemble_statements(ctx)["incomplete"] == 1
    flags = bs.bs_period_continuity(ctx)
    assert [f.rule for f in flags] == ["bs_period_continuity"] and "gap" in flags[0].evidence


def test_gap_flag_survives_a_page_that_covers_only_part_of_the_gap() -> None:
    docs = _jan_and_april()
    docs.append(page_doc(3, march(), 1, header="all"))  # March only: February is still missing
    ctx = upload(docs)
    assert assemble_statements(ctx)["incomplete"] == 1
    flags = bs.bs_period_continuity(ctx)
    assert [f.rule for f in flags] == ["bs_period_continuity"] and "gap" in flags[0].evidence


def test_a_page_of_a_neighbouring_month_does_not_explain_the_gap() -> None:
    docs = _jan_and_april()
    jan_again = Month("2026-01", [[(6, None, "1.00")], [(7, None, "1.00")]], opening="5.00")
    docs.append(page_doc(3, jan_again, 0))  # an extra January page: lies before the gap
    ctx = upload(docs)
    assemble_statements(ctx)
    assert [f.rule for f in bs.bs_period_continuity(ctx)] == ["bs_period_continuity"]


def _markerless_page(n: int, debit: str | None, credit: str | None, balance: str) -> Document:
    return make_doc(
        "bank_statement",
        id=f"d{n}",
        file_id=f"f{n}",
        tables={
            "transactions": [
                {
                    "date": "2026-03-05",
                    "description": "X",
                    "debit": debit,
                    "credit": credit,
                    "balance": balance,
                }
            ]
        },
    )


def test_markerless_pages_that_hand_over_in_a_circle_are_not_silently_dropped() -> None:
    # A: 100.00 -> 150.00 after a credit of 50, B: 150.00 -> 100.00 after a debit of 50, so each
    # page follows the other and neither is a first page.
    ctx = upload(
        [_markerless_page(1, None, "50.00", "150.00"), _markerless_page(2, "50.00", None, "100.00")]
    )
    counts = assemble_statements(ctx)
    assert counts.get("assembled", 0) == 0 and counts == {"ambiguous": 1, "pages": 2}
    assert all(
        d.statement is not None and d.statement.state == "ambiguous" for d in ctx.case.documents
    )
    assert collect_partial_reasons(ctx.case)
    assert unchecked_pages(ctx.case)


def test_a_flag_row_of_a_plain_statement_is_not_remapped_through_an_assembled_one() -> None:
    m = march()
    ctx = upload([page_doc(i + 1, m, i) for i in range(3)])
    assemble_statements(ctx)
    (view,) = whole(ctx)
    plain = make_doc("bank_statement", id="d9")
    kw = {"table": "transactions", "evidence": "e", "explanation": "x"}
    flag = make_flag("x_remitter_vs_buyer", docs=[plain, view], row=7, row_doc=plain, **kw)
    assert flag.row == 7 and flag.document_ids == ["d1", "d2", "d3", "d9"]
    assert flag.id == "x_remitter_vs_buyer.d1+d2+d3+d9.transactions:7"
    # the same row, said to belong to the assembled view, is mapped to its page and page row
    page, local = view.origin_rows["transactions"][7]
    owned = make_flag("x_remitter_vs_buyer", docs=[plain, view], row=7, row_doc=view, **kw)
    assert owned.row == local and owned.document_ids == sorted([page, "d9"])
