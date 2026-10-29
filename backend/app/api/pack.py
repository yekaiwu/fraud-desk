"""Evidence pack (api.md section 8): JSON download or one printable HTML page."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.api._common import ANONYMOUS, DbDep, audit_for, get_clock
from app.pack import build_pack, build_page_images, render_pack_html

router = APIRouter(tags=["pack"])


@router.get("/cases/{case_id}/pack")
def get_pack(
    case_id: str,
    request: Request,
    *,
    format: Literal["json", "html"] = "json",  # noqa: A002 - the documented query name
    version: Annotated[int | None, Query(ge=1)] = None,
    db: DbDep,
) -> Response:
    pack = build_pack(db, case_id, version=version, clock=get_clock(request))
    audit_for(request).write(
        actor=ANONYMOUS,
        action="pack.exported",
        case_id=case_id,
        detail={"format": format, "version": pack["case"]["version"]},
    )
    if format == "html":
        html = render_pack_html(pack, page_images=build_page_images(db, pack))
        return HTMLResponse(html)
    filename = f"case-{case_id}-v{pack['case']['version']}.json"
    return Response(
        json.dumps(pack, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
