"""Builders shared by the rule-assistant and custom-rule API tests: model answers, scripted fake
models, an app with the feature on and two or three signed-in accounts. Not a test module."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.core.db import Database
from app.core.llm import FakeLLMClient, LLMRequest
from app.core.models import Case, Document
from app.core.testing import FIXED_NOW, FIXED_TODAY
from app.custom_rules import capabilities
from tests.custom_rules_helpers import scan_file

SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
PROXY = {"X-Gldb-Proxy-Secret": SECRET}
LOOPBACK = ("127.0.0.1", 50000)

DEBT_REQUEST = "flag as medium if the firm has a lot of debt from different places"
TOTAL_REQUEST = "flag as low if an invoice is for more than 100000"


def example(request: str) -> dict[str, Any]:
    """One of the worked examples of the capability prompt (they are valid by a test of their own)."""
    return dict(capabilities.worked_examples())[request]


def debt_answer() -> dict[str, Any]:
    return example(DEBT_REQUEST)


def total_answer() -> dict[str, Any]:
    return example(TOTAL_REQUEST)


def t1_spec(
    field: str = "invoice.total", operator: str = "gt", value: str = "100000", severity: str = "low"
) -> dict[str, Any]:
    return {
        "spec_version": 1,
        "severity": severity,
        "condition": {
            "template": "field_vs_constant",
            "field": field,
            "operator": operator,
            "value": value,
        },
    }


def t3_spec(field: str = "receipt.merchant_name", severity: str = "info") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "severity": severity,
        "condition": {"template": "field_missing", "field": field},
    }


def answer(
    status: str = "ready",
    spec: dict[str, Any] | None = None,
    *,
    phrases: list[tuple[str, str | None]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A model answer (the closed schema). ``phrases`` is the phrase map as (phrase, slot) pairs."""
    out: dict[str, Any] = {
        "status": status,
        "understood": "Flag what was asked.",
        "spec": spec,
        "phrase_map": [{"phrase": p, "slot": s} for p, s in (phrases or [])],
    }
    out.update(extra)
    return out


def sequence(*answers: dict[str, Any] | Exception) -> Callable[[LLMRequest], dict[str, Any]]:
    """A fixture that gives the answers one by one (the last one repeats); an exception is raised."""
    queue = list(answers)

    def fixture(_: LLMRequest) -> dict[str, Any]:
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return fixture


def fake(
    draft: Callable[[LLMRequest], dict[str, Any]] | dict[str, Any] | None = None,
    repair: Callable[[LLMRequest], dict[str, Any]] | dict[str, Any] | None = None,
) -> FakeLLMClient:
    fixtures: dict[str, Any] = {}
    if draft is not None:
        fixtures[capabilities.PURPOSE_DRAFT] = draft
    if repair is not None:
        fixtures[capabilities.PURPOSE_REPAIR] = repair
    return FakeLLMClient(fixtures)


def purposes(llm: FakeLLMClient) -> list[str]:
    return [c.purpose for c in llm.calls]


def on_settings(settings: Settings, **update: Any) -> Settings:
    return settings.model_copy(update={"custom_rules": "on", **update})


def store_case(
    db: Database,
    case_id: str,
    docs: list[Document],
    *,
    minutes: int = 0,
    scan: bool = False,
) -> Case:
    stamp = FIXED_NOW + timedelta(minutes=minutes)
    case = Case(
        id=case_id,
        use_case="loan",
        status="open",
        today=FIXED_TODAY,
        created_at=stamp,
        updated_at=stamp,
        documents=docs,
        files=[scan_file("f1")] if scan else [],
    )
    db.create_case(case)
    return case


class As:
    """A client that sends the headers of one signed-in account (or none)."""

    def __init__(self, client: TestClient, who: str | None):
        self.client = client
        self.headers = {**PROXY, **({"X-Gldb-Reviewer": who} if who else {})}

    def request(self, method: str, path: str, **kw: Any):  # noqa: ANN201
        return self.client.request(method, path, headers=self.headers, **kw)

    def get(self, path: str, **kw: Any):  # noqa: ANN201
        return self.request("GET", path, **kw)

    def post(self, path: str, json: Any = None, **kw: Any):  # noqa: ANN201
        return self.request("POST", path, json=json if json is not None else {}, **kw)


@dataclass
class Api:
    settings: Settings
    db: Database
    app: Any
    client: TestClient
    llm: FakeLLMClient

    def as_(self, who: str | None) -> As:
        return As(self.client, who)


def make_api(
    settings: Settings,
    db: Database,
    llm: FakeLLMClient | None = None,
    **update: Any,
) -> Api:
    settings = on_settings(settings, admin_users="root", **update)
    llm = llm or fake()
    application = main.create_app(settings, db=db, llm=llm)
    client = TestClient(application, client=LOOPBACK, raise_server_exceptions=False)
    return Api(settings, db, application, client, llm)


def start(who: As, text: str = DEBT_REQUEST, **extra: Any) -> dict[str, Any]:
    r = who.post("/custom-rules/drafts", {"text": text, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def finish(
    who: As,
    draft_id: str,
    name: str = "My rule",
    *,
    reason: str = "Asked for by the desk",
    **extra: Any,
):  # noqa: ANN201
    """Dry-run then save. Returns the save response."""
    r = who.post(f"/custom-rules/drafts/{draft_id}/dry-run")
    assert r.status_code == 200, r.text
    body = {
        "draft_id": draft_id,
        "name": name,
        "reason": reason,
        "accept_narrower": True,
        "confirm_warnings": True,
        **extra,
    }
    return who.post("/custom-rules", body)
