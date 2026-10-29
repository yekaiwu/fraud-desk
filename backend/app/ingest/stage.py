"""Ingest stage (docs/ingest-and-extraction.md section 4).

Re-verifies each stored file, reads page facts, the text layer (words with boxes) and PDF metadata,
sends scanned pages through OCR, and records every per-page or per-file problem as
``PageInfo.error`` (never an exception). Idempotent: earlier output is replaced.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.core.context import CaseContext, natural_key
from app.core.costs import LLMBudgetExceeded
from app.core.errors import LLMNoFixture
from app.core.models import FileRef, PageInfo, StageOutcome, Word
from app.ingest.images import ImageProblem, declared_dpi, frame_count, open_image, upright
from app.ingest.ocr import OcrUnavailable, ocr_page_ex
from app.ingest.pdf import FileProblem, analyse_page, open_pdf, page_size, read_pdf_meta, short

FILE_DEADLINE_S = 60.0  # per file; read at call time so tests can shorten it
MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG",),
}
MAX_STAGE_WARNINGS = 20


@dataclass
class _State:
    warnings: list[str] = field(default_factory=list)
    scan_jobs: list[tuple[FileRef, PageInfo]] = field(default_factory=list)
    ocr_calls: int = 0
    partial_reasons: list[str] = field(default_factory=list)


def _placeholder(code: str, message: str) -> PageInfo:
    return PageInfo(
        page=1,
        width_pt=0.0,
        height_pt=0.0,
        has_text_layer=False,
        is_scan=False,
        error=f"{code}: {short(message, 190)}",
    )


def _error_page(page: int, size: tuple[float, float], error: str) -> PageInfo:
    return PageInfo(
        page=page,
        width_pt=size[0],
        height_pt=size[1],
        has_text_layer=False,
        is_scan=False,
        error=error,
    )


def _read_verified(ctx: CaseContext, file: FileRef) -> bytes:
    """Bytes of the stored file after hash and magic-byte re-verification."""
    try:
        data = ctx.file_path(file.id).read_bytes()
    except (OSError, ValueError) as exc:
        raise FileProblem("hash_mismatch", "stored file is missing or unreadable") from exc
    if hashlib.sha256(data).hexdigest() != file.sha256:
        raise FileProblem("hash_mismatch", "stored file differs from recorded hash")
    if not data.startswith(MAGIC[file.mime]):
        raise FileProblem("magic_mismatch", f"content does not look like {file.mime}")
    return data


def _ingest_pdf(
    ctx: CaseContext, file: FileRef, data: bytes, deadline: float, state: _State
) -> list[PageInfo]:
    doc = open_pdf(data)
    try:
        file.pdf_meta = read_pdf_meta(data, doc)
        pages: list[PageInfo] = []
        for index in range(doc.page_count):
            page_no = index + 1
            if time.monotonic() > deadline:
                err = f"timeout: file processing exceeded {FILE_DEADLINE_S:g} s"
                pages.append(_error_page(page_no, page_size(doc, index), err))
                continue
            if ctx.costs.claim_pages(1) < 1:
                pages.append(
                    _error_page(page_no, page_size(doc, index), "not_processed: budget max_pages")
                )
                continue
            try:
                info, words = analyse_page(doc, index)
            except Exception as exc:  # noqa: BLE001 - a damaged page stays local
                pages.append(
                    _error_page(page_no, (0.0, 0.0), f"page_load_failed: {short(exc, 150)}")
                )
                continue
            if words:
                ctx.page_words[(file.id, page_no)] = words
            pages.append(info)
            if info.is_scan:
                state.scan_jobs.append((file, info))
        return pages
    finally:
        doc.close()


def _ingest_image(
    ctx: CaseContext, file: FileRef, data: bytes, deadline: float, state: _State
) -> list[PageInfo]:
    img = open_image(data)
    if frame_count(img) > 1:
        state.warnings.append(f"ingest: {file.id}: only the first frame of the image was read")
    img = upright(img)
    dpi = declared_dpi(img)
    size = (img.width * 72.0 / dpi, img.height * 72.0 / dpi)
    if time.monotonic() > deadline:
        err = f"timeout: file processing exceeded {FILE_DEADLINE_S:g} s"
        return [_error_page(1, size, err)]
    if ctx.costs.claim_pages(1) < 1:
        return [_error_page(1, size, "not_processed: budget max_pages")]
    info = PageInfo(
        page=1,
        width_pt=size[0],
        height_pt=size[1],
        rotation=0,
        has_text_layer=False,
        is_scan=True,
        word_count=0,
    )
    state.scan_jobs.append((file, info))
    return [info]


def _ingest_file(ctx: CaseContext, file: FileRef, state: _State) -> list[PageInfo]:
    deadline = time.monotonic() + FILE_DEADLINE_S
    try:
        data = _read_verified(ctx, file)
        if file.mime == "application/pdf":
            return _ingest_pdf(ctx, file, data, deadline, state)
        return _ingest_image(ctx, file, data, deadline, state)
    except FileProblem as problem:
        return [_placeholder(problem.code, problem.message)]
    except ImageProblem as problem:
        return [_placeholder(problem.code, problem.message)]


# ----------------------------------------------------------------------------- OCR pass


def _ocr_one(ctx: CaseContext, file_id: str, page: int) -> tuple[list[Word], list[str]] | Exception:
    try:
        with ctx.costs.scope(page=page):
            return ocr_page_ex(ctx, file_id, page)
    except Exception as exc:  # noqa: BLE001 - converted to a page error by the caller
        return exc


def _run_ocr(ctx: CaseContext, state: _State) -> None:
    jobs = state.scan_jobs
    if not jobs:
        return
    workers = max(1, ctx.settings.max_page_workers)
    remaining_calls = ctx.settings.max_llm_calls_per_case - ctx.costs.calls_used
    if len(jobs) > remaining_calls:
        workers = 1  # a budget trip must hit the same pages on every run
    if workers == 1 or len(jobs) == 1:
        results = [_ocr_one(ctx, f.id, p.page) for f, p in jobs]
    else:
        wrapped = ctx.costs.wrap(_ocr_one)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(wrapped, ctx, f.id, p.page) for f, p in jobs]
            results = [fut.result() for fut in futures]

    budget_hits = 0
    first_budget: LLMBudgetExceeded | None = None
    for (file, info), result in zip(jobs, results, strict=True):
        if isinstance(result, LLMBudgetExceeded):
            budget_hits += 1
            first_budget = first_budget or result
            info.error = f"not_processed: budget {result.limit}"
            continue
        state.ocr_calls += 1
        if isinstance(result, LLMNoFixture):
            info.error = "ocr_unavailable: no fixture or API key for ocr:page"
        elif isinstance(result, OcrUnavailable):
            info.error = f"ocr_unavailable: {short(result, 150)}"
        elif isinstance(result, Exception):
            info.error = f"ocr_failed: {type(result).__name__}: {short(result, 120)}"
        else:
            words, notes = result
            state.warnings.extend(notes)
            info.word_count = len(words)
            if words:
                ctx.page_words[(file.id, info.page)] = words
    if first_budget is not None:
        state.partial_reasons.append(f"{first_budget}; {budget_hits} scanned pages not read")


# ----------------------------------------------------------------------------- stage


def run_stage(ctx: CaseContext) -> StageOutcome:
    with ctx.costs.scope(stage="ingest"):
        return _run(ctx)


def _run(ctx: CaseContext) -> StageOutcome:
    state = _State()
    ctx.page_words.clear()
    files = sorted(ctx.case.files, key=lambda f: natural_key(f.id))
    for file in files:
        file.pages = []
        file.pdf_meta = {}
    for file in files:
        file.pages = _ingest_file(ctx, file, state)
    _run_ocr(ctx, state)

    not_processed = sum(
        1 for f in files for p in f.pages if p.error and p.error.startswith("not_processed")
    )
    page_budget = sum(
        1 for f in files for p in f.pages if p.error == "not_processed: budget max_pages"
    )
    if page_budget:
        limits = ctx.costs.limits
        state.partial_reasons.insert(
            0,
            f"budget_exceeded: max_pages (limit {limits.max_pages}, used {ctx.costs.pages_used}); "
            f"{page_budget} pages not processed",
        )

    errors: list[str] = []
    unreadable = 0
    for file in files:
        for page in file.pages:
            if page.error:
                errors.append(f"ingest: {file.id} page {page.page}: {page.error}")
                if not page.error.startswith("not_processed"):
                    unreadable += 1
    warnings = list(dict.fromkeys(state.warnings))
    shown = errors[:MAX_STAGE_WARNINGS]
    warnings.extend(shown)
    if len(errors) > len(shown):
        warnings.append(f"ingest: +{len(errors) - len(shown)} more page problems")

    all_pages = [p for f in files for p in f.pages]
    detail: dict[str, int | float | str] = {
        "files": len(files),
        "pages": len(all_pages),
        "text_pages": sum(1 for p in all_pages if p.has_text_layer),
        "scan_pages": sum(1 for p in all_pages if p.is_scan),
        "ocr_calls": state.ocr_calls,
        "words": sum(p.word_count for p in all_pages),
        "unreadable_pages": unreadable,
        "not_processed_pages": not_processed,
    }
    partial = bool(errors) or bool(state.partial_reasons)
    reason = " | ".join(state.partial_reasons) if state.partial_reasons else None
    if partial and reason is None:
        codes = sorted({p.error.split(":", 1)[0] for f in files for p in f.pages if p.error})
        reason = f"{len(errors)} page(s) could not be read ({', '.join(codes)})"
    return StageOutcome(detail=detail, warnings=warnings, partial=partial, partial_reason=reason)
