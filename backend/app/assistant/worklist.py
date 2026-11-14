"""The open worklist, as citable lines (docs/case-assistant.md).

One short block per case that is still on the worklist (processing, open, or waiting for documents).
The model may quote those lines. It does not see the other cases' documents. Closed cases are left out.
"""

from __future__ import annotations

from typing import Any

from app.core.db import CaseFilters, Database
from app.core.llm import wrap_untrusted
from app.core.models import clean_text

WORKLIST_MAX = 40
WORKLIST_STATUSES = ["processing", "open", "awaiting_documents"]


def load_worklist(db: Database, *, on_screen: str) -> list[dict[str, Any]]:
    """Priority order, same as the worklist. ``text`` is the block the verifier checks a quote against."""
    rows, _total = db.list_cases(
        CaseFilters(status=WORKLIST_STATUSES),
        "priority",
        1,
        WORKLIST_MAX,
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        top = row["top_finding"]
        if top:
            top = db.open_top_finding(row["id"], top)
        item = {
            "case_id": row["id"],
            "reference": row["reference"] or "",
            "applicant": row["applicant_name"] or "",
            "use_case": row["use_case"],
            "status": row["status"],
            "score": None if row["status"] == "processing" else row["score"],
            "band": None if row["status"] == "processing" else row["band"],
            "lane": None if row["status"] == "processing" else row["lane"],
            "high_count": int(row["high_count"] or 0),
            "top_finding": clean_text(top or "", 160),
            "on_screen": row["id"] == on_screen,
        }
        item["text"] = _render(item)
        out.append(item)
    return out


def _render(item: dict[str, Any]) -> str:
    score = "none" if item["score"] is None else str(item["score"])
    band = item["band"] or "none"
    lane = item["lane"] or "none"
    lines = [
        f"case_id {item['case_id']}",
        f"reference {item['reference']}",
        f"applicant {item['applicant']}",
        f"use_case {item['use_case']}",
        f"status {item['status']}",
        f"score {score}",
        f"band {band}",
        f"lane {lane}",
        f"high_flags {item['high_count']}",
        f"on_screen {'yes' if item['on_screen'] else 'no'}",
    ]
    if item["top_finding"]:
        lines.append(f'top_finding: "{item["top_finding"]}"')
    return "\n".join(lines)


def blocks_for_worklist(items: list[dict[str, Any]]) -> str:
    """One untrusted block per worklist case."""
    parts: list[str] = []
    for item in items:
        parts.append(wrap_untrusted(item["text"], label=f"worklist.{item['case_id']}"))
    return "\n\n".join(parts)
