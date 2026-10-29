from app.checks.registry import run_stage_checks
from app.core.builtin_checks import doc_unclassified, doc_unreadable
from app.core.models import Classification, FileRef, PageInfo
from app.core.testing import make_ctx, make_doc


def page(
    n: int, *, error: str | None = None, label: str | None = None, prob: float = 0.95
) -> PageInfo:
    return PageInfo(
        page=n,
        width_pt=595,
        height_pt=842,
        has_text_layer=True,
        is_scan=False,
        error=error,
        classification=Classification(
            label=label, probability=prob, classifier="rules", version="1"
        )
        if label
        else None,
    )


def file(*pages: PageInfo, id: str = "f1") -> FileRef:  # noqa: A002
    return FileRef(
        id=id,
        name="a.pdf",
        sha256="a" * 64,
        size_bytes=1,
        mime="application/pdf",
        pages=list(pages),
    )


def test_clean_case_has_no_info_flags() -> None:
    doc = make_doc("bank_statement", id="d1", page_end=2)
    ctx = make_ctx(
        [doc], files_extra=[file(page(1, label="bank_statement"), page(2, label="bank_statement"))]
    )
    assert doc_unreadable(ctx) == [] and doc_unclassified(ctx) == []


def test_unreadable_fires_on_page_error_failed_document_and_unreadable_warning() -> None:
    d1 = make_doc("bank_statement", id="d1", page_end=2)
    d2 = make_doc("invoice", id="d2", file_id="f2", status="failed", error="corrupt_pdf")
    d3 = make_doc(
        "contract", id="d3", file_id="f3", warnings=["unreadable: page 2: schema", "other"]
    )
    ctx = make_ctx(
        [d1, d2, d3],
        files_extra=[file(page(1), page(2, error="ocr_unavailable")), file(id="f2"), file(id="f3")],
    )
    flags = doc_unreadable(ctx)
    by_doc = {f.document_ids[0]: f for f in flags}
    assert set(by_doc) == {"d1", "d2", "d3"}
    assert "f1 page 2: ocr_unavailable" in by_doc["d1"].evidence
    assert "document failed: corrupt_pdf" in by_doc["d2"].evidence
    assert by_doc["d3"].evidence == "unreadable: page 2: schema"
    assert all(f.severity == "info" and f.rule == "doc_unreadable" for f in flags)
    assert by_doc["d1"].region is not None and by_doc["d1"].region.page == 1


def test_unreadable_page_outside_any_document_still_reported() -> None:
    ctx = make_ctx([], files_extra=[file(page(1, error="corrupt_pdf"))])
    (flag,) = doc_unreadable(ctx)
    assert flag.document_ids == [] and "corrupt_pdf" in flag.evidence
    assert flag.id == "doc_unreadable..-~f1p1"


def test_unclassified_conditions() -> None:
    other = make_doc("other", id="d1")
    boiler = make_doc("boilerplate", id="d2")
    unsure = make_doc(
        "invoice",
        id="d3",
        classification=Classification(
            label="invoice", probability=0.5, classifier="rules", version="1"
        ),
    )
    mixed = make_doc("bank_statement", id="d4", file_id="f2", page_end=2)
    ctx = make_ctx(
        [other, boiler, unsure, mixed],
        files_extra=[
            file(page(1)),
            file(page(1, label="bank_statement"), page(2, label="boilerplate"), id="f2"),
        ],
    )
    flags = {f.document_ids[0]: f for f in doc_unclassified(ctx)}
    assert set(flags) == {"d1", "d2", "d3", "d4"}
    assert "classified as other" in flags["d1"].evidence
    assert "below 0.70" in flags["d3"].evidence
    assert "boilerplate page(s) 2" in flags["d4"].evidence
    assert all(f.severity == "info" for f in flags.values())


def test_run_through_the_registry_counts_as_ran_even_when_silent() -> None:
    ctx = make_ctx([make_doc("invoice", id="d1")])
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    assert runs["doc_unreadable"].status == "ran" and runs["doc_unclassified"].status == "ran"
    assert ctx.case.flags == []
