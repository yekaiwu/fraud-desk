"""Page images and original files (api.md section 5)."""

from __future__ import annotations

import os
import re
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from app.api._common import ANONYMOUS, DbDep, audit_for, load_case_or_404, not_found
from app.core.errors import ApiError
from app.core.models import Case, FileRef
from app.pack import render_page_png

router = APIRouter(tags=["files"])

IMMUTABLE = "private, max-age=31536000, immutable"


def _find_file(case: Case, file_id: str | None) -> FileRef:
    if file_id is None:
        if len(case.files) == 1:
            return case.files[0]
        raise ApiError(
            422,
            "validation_error",
            "The query parameter file_id is required for a case with several files.",
            {"errors": [{"field": "file_id", "message": "field required", "type": "missing"}]},
        )
    ref = next((f for f in case.files if f.id == file_id), None)
    if ref is None:
        raise not_found("file", id=file_id)
    return ref


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    return any(part.strip().removeprefix("W/") == etag for part in header.split(","))


@router.get("/cases/{case_id}/pages/{page}")
def page_image(
    case_id: str,
    page: int,
    request: Request,
    *,
    file_id: str | None = None,
    w: Annotated[int, Query(ge=200, le=2000)] = 1000,
    db: DbDep,
) -> Response:
    case = load_case_or_404(db, case_id)
    ref = _find_file(case, file_id)
    if page < 1 or (ref.pages and page > len(ref.pages)):
        raise not_found("page", file_id=ref.id, page=page)
    etag = f'"{ref.sha256}-{page}-{w}"'
    headers = {"ETag": etag, "Cache-Control": IMMUTABLE}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    store = db.files
    source = store.path_for(ref.sha256)
    if not source.is_file():
        raise not_found("file", id=ref.id)
    cached = store.renders_dir / ref.sha256 / f"{page}_{w}.png"
    if cached.is_file():
        data = cached.read_bytes()
    else:
        try:
            data = render_page_png(source, ref.mime, page, w)
        except LookupError as exc:
            raise not_found("page", file_id=ref.id, page=page) from exc
        except ValueError as exc:
            raise ApiError(
                404,
                "not_found",
                "The page could not be read.",
                {"kind": "page", "file_id": ref.id, "page": page, "reason": "unreadable"},
            ) from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_name(f".{cached.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, cached)
        finally:
            tmp.unlink(missing_ok=True)
    return Response(data, media_type="image/png", headers=headers)


def _content_disposition(name: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name.encode("ascii", "ignore").decode()) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.get("/cases/{case_id}/files/{file_id}")
def download_file(case_id: str, file_id: str, request: Request, db: DbDep) -> Response:
    case = load_case_or_404(db, case_id)
    ref = next((f for f in case.files if f.id == file_id), None)
    if ref is None:
        raise not_found("file", id=file_id)
    path = db.files.path_for(ref.sha256)
    if not path.is_file():
        raise not_found("file", id=file_id)
    audit_for(request).write(
        actor=ANONYMOUS,
        action="file.downloaded",
        case_id=case_id,
        detail={"file_sha256": ref.sha256},
    )
    return Response(
        path.read_bytes(),
        media_type=ref.mime,
        headers={"Content-Disposition": _content_disposition(ref.name)},
    )
