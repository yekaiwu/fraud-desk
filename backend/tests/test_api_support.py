"""Helpers shared by the ``test_api_*`` modules (api-backend). Not a test module itself.

Cases are seeded straight into the database (a hand-built ``Case`` with flags, findings and a score)
so review, pack and effectiveness tests do not depend on any other agent's pipeline stage. The one
end-to-end walking-skeleton test goes through the real upload and job runner.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import app.main as main
from app.core.cases import add_file, new_case
from app.core.config import Settings
from app.core.db import Database
from app.core.models import (
    BAND_THRESHOLDS,
    SCORE_WEIGHTS,
    Case,
    CheckRun,
    Coverage,
    Finding,
    Flag,
    Provenance,
    Region,
    Score,
    Severity,
)

FIXED_NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).parent / "_out" / "api_fixtures"


class FakeClock:
    """A settable, deterministic clock (``app.state.clock`` and the services accept it)."""

    def __init__(self, now: datetime = FIXED_NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


class TickingClock(FakeClock):
    """Advances one second per call so every entry has a distinct timestamp."""

    def __call__(self) -> datetime:
        self.now = self.now + timedelta(seconds=1)
        return self.now


def make_client(
    settings: Settings, db: Database, clock: FakeClock | None = None
) -> tuple[TestClient, Any]:
    application = main.create_app(settings, db=db)
    if clock is not None:
        application.state.clock = clock
    return TestClient(application, raise_server_exceptions=False), application


@dataclass
class Env:
    settings: Settings
    db: Database
    client: TestClient
    app: Any
    clock: FakeClock

    def seed(self, **kw: Any) -> Case:
        return seed_case(self.db, self.settings, **kw)

    def audit(self, case_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/cases/{case_id}/audit").json()["entries"]

    def audit_rows(self) -> list[dict[str, Any]]:
        """Every audit row, including those of deleted cases (raw SELECT)."""
        with self.db.conn() as c:
            rows = c.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def review(self, case_id: str, **body: Any):  # noqa: ANN201
        return self.client.post(f"/cases/{case_id}/review", json=body)


def make_env(settings: Settings, db: Database, clock: FakeClock | None = None) -> Env:
    clock = clock or TickingClock()
    client, application = make_client(settings, db, clock)
    return Env(settings, db, client, application, clock)


def mk_flag(
    rule: str = "bs_running_balance",
    severity: Severity = "high",
    *,
    doc: str = "d1",
    locator: str = "transactions:3",
    disc: str | None = None,
    evidence: str = "previous 100.00 + credit 50.00 = 150.00, printed 250.00",
    explanation: str = "The balance on this row is 100.00 higher than expected.",
    region: Region | None = None,
) -> Flag:
    flag_id = f"{rule}.{doc}.{locator}" + (f"~{disc}" if disc else "")
    return Flag(
        id=flag_id,
        rule=rule,
        stage="rules",
        severity=severity,
        document_ids=[doc],
        evidence=evidence,
        explanation=explanation,
        region=region,
    )


def mk_provenance(created: datetime = FIXED_NOW, digest: str = "d" * 64) -> Provenance:
    return Provenance(
        app_version="0.1.0",
        ruleset_version="rs-000000000000",
        models={},
        prompt_hash="p" * 64,
        prompt_hashes={},
        classifier_versions={},
        extractor_versions={},
        input_hashes={},
        settings_hash="s" * 64,
        today=date(2026, 10, 27),
        flags_digest=digest,
        created_at=created,
    )


def band_of(value: int) -> str:
    if value >= BAND_THRESHOLDS["high"]:
        return "high"
    if value >= BAND_THRESHOLDS["medium"]:
        return "medium"
    return "low"


def seed_case(
    db: Database,
    settings: Settings,
    *,
    use_case: str = "loan",
    flags: Sequence[Flag] = (),
    status: str = "open",
    applicant_name: str | None = None,
    applicant_uen: str | None = None,
    reference: str | None = None,
    files: Sequence[tuple[str, bytes]] = (),
    partial: bool = False,
    check_runs: Sequence[CheckRun] | None = None,
    created_at: datetime = FIXED_NOW,
    updated_at: datetime | None = None,
    provenance_created: datetime | None = FIXED_NOW,
    escalated_at: datetime | None = None,
    decision: str | None = None,
    group: bool = False,
) -> Case:
    """Insert an analysed case. One finding per flag unless ``group`` (then one finding for all)."""
    case = new_case(
        settings,
        use_case,  # type: ignore[arg-type]
        reference=reference,
        applicant_name=applicant_name,
        applicant_uen=applicant_uen,
        today=date(2026, 10, 27),
    )
    for name, data in files:
        add_file(case, db.files, name=name, data=data)
    case.created_at = created_at
    case.updated_at = updated_at or created_at
    case.status = status  # type: ignore[assignment]
    case.flags = list(flags)
    findings: list[Finding] = []
    if group and flags:
        top = sorted(flags, key=lambda f: ["high", "medium", "low", "info"].index(f.severity))[0]
        findings.append(
            Finding(
                id="fnd_1",
                group_key="group:all",
                title=top.explanation,
                severity=top.severity,
                flag_ids=[f.id for f in flags],
                document_ids=sorted({d for f in flags for d in f.document_ids}),
                rules=sorted({f.rule for f in flags}),
                weight=SCORE_WEIGHTS[top.severity],
                summary=top.explanation,
            )
        )
    else:
        for n, f in enumerate(flags, start=1):
            findings.append(
                Finding(
                    id=f"fnd_{n}",
                    group_key=f"flag:{f.id}",
                    title=f.explanation,
                    severity=f.severity,
                    flag_ids=[f.id],
                    document_ids=list(f.document_ids),
                    rules=[f.rule],
                    weight=SCORE_WEIGHTS[f.severity],
                    summary=f.explanation,
                )
            )
    case.findings = findings
    value = min(100, sum(f.weight for f in findings))
    case.score = Score(value=value, band=band_of(value), partial=partial)  # type: ignore[arg-type]
    case.partial = partial
    runs = (
        list(check_runs)
        if check_runs is not None
        else [CheckRun(rule="bs_running_balance", stage="rules", status="ran", flags=len(flags))]
    )
    case.check_runs = runs
    ran = sum(1 for r in runs if r.status == "ran")
    case.coverage = Coverage(
        applicable=len(runs),
        ran=ran,
        ratio=round(ran / len(runs), 4) if runs else 1.0,
        skipped=[
            {"rule": r.rule, "status": r.status, "reason": r.reason or ""}
            for r in runs
            if r.status != "ran"
        ],
    )
    case.lane = "loan" if use_case == "loan" else ("edd" if use_case == "kyc" else "aml")
    case.top_finding = findings[0].title if findings else "No flags raised"
    if provenance_created is not None:
        case.provenance = mk_provenance(provenance_created)
    case.decision = decision  # type: ignore[assignment]
    case.escalated_at = escalated_at
    db.create_case(case)
    return case


def review_body(kind: str, decision: str, case_version: int = 1, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": kind,
        "decision": decision,
        "reviewer": "a.tan",
        "reason": "Checked against the source document.",
        "case_version": case_version,
    }
    body.update(extra)
    return body


def post_review(client: TestClient, case_id: str, **body: Any):  # noqa: ANN201
    return client.post(f"/cases/{case_id}/review", json=body)


def tiny_pdf(text: str = "Hello fictitious bank statement", pages: int = 1) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"{text} page {n + 1}", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def tiny_png(width: int = 120, height: int = 80) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buf, "PNG")
    return buf.getvalue()


def write_fixture(name: str, payload: Any) -> None:
    """JSON responses for the frontend contract test (T-api-backend-11)."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / f"{name}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
