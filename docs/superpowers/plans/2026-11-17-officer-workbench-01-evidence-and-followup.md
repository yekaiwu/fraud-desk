# Officer Workbench 01: Evidence Map and Follow-up Planner

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Read `2026-11-17-officer-workbench-00-master.md` first (global constraints apply to every task here).

**Goal:** A new **Evidence** tab per case: one row per review item with a code-decided status and the evidence behind it; gaps (what is missing) listed under their item; the officer can pick gaps, draft a neutral request, record it, and after the next upload see which gaps closed.

**Architecture:** `build_evidence_map(case, previous)` is pure: it reads `case.documents`, `case.check_runs`, `case.flags` only. No model call, no new DB column for the map itself (it is recomputed on read). Only recorded follow-up requests are stored (`follow_ups` table).

**Tech Stack:** as master.

## Facts this plan relies on (from the code maps; CONFIRM if a test disagrees)

- `DocType` values (`backend/app/core/models.py:33`): `bank_statement, invoice, receipt, financial_statement, contract, purchase_order, company_profile, certificate_of_incorporation, ownership_chart, shareholder_register, source_of_funds_declaration, national_id, passport, boilerplate, other`. `DocStatus`: `ok, partial, failed, received_not_checked`. `Severity`: `high, medium, low, info`.
- `Document.tables["holders"]` rows (ownership_chart) have cells `holder_name, holder_type ("individual"|"company"|""), id_or_uen, percent, parent_name, is_ubo`. An empty `parent_name` means the holder owns directly in the applicant.
- `Document.tables["directors"]` rows (company_profile) have `director_name, id_number, nationality, position, appointment_date`.
- `CheckRun(rule, stage, status "ran"|"skipped"|"error", reason, code, flags, note)` is stored per rule in `case.check_runs`. A check that finds nothing is `status="ran", flags=0`: that is how "ran and found nothing" is told apart from "did not run".
- Registry/sanctions payloads are NOT stored on the case. Only `CheckRun.note`/`reason` survive. So "not independently confirmed" is derived from the outside rule's `CheckRun` being skipped or absent.
- `x_directors` emits an `info` flag with `table="directors"`, `row=<index>`, `evidence="no ID document supplied for director <name>"` (`backend/app/checks/kyc.py:1478-1497`).
- `x_remitter_vs_buyer` and `x_sof_support` are the Yishun Anchor flags (`docs/demo-script.md` 3c). In v2 the challenger lowers `x_remitter_vs_buyer` to `info`, which must close the gap.
- Reusable helpers: `app.core.textnorm.company_key`, `app.names.entity_key`, `app.graph.counterparty_of(row)`, `Cell.text()`, `Cell.box: Region | None`.
- Routers in `backend/app/api/*.py` are auto-discovered if they expose `router`; copy the imports of `api/effectiveness.py` (smallest). Errors: `raise ApiError(status, "code", "message")`. Deps: `DbDep`, `SettingsDep`, `load_case_or_404(db, id, version)`, `signed_in_reviewer(request)` from `app/api/_common.py`.
- Test harness: `backend/tests/conftest.py` (`settings`, `db`), `core/testing.py::make_ctx/make_doc` (CONFIRM kwargs), `tests/test_api_support.py::make_env` and `Env.seed(...)`, `tests/test_api_effectiveness.py` as the API test template.

---

### Task 1: Flag, `/me`, router skeleton, branch

**Files:**
- Modify: `backend/app/core/config.py` (add `workbench: Literal["off","on"] = "off"`, `workbench_link_key: SecretStr = SecretStr("")`; follow how `custom_rules` is declared at l.167 and its before-validator style at l.257)
- Modify: `backend/app/api/health.py:35` (`/me` dict gets `"workbench": settings.workbench == "on"`)
- Modify: `.env.example` (near the custom-rules entries)
- Create: `backend/app/workbench/__init__.py` (empty), `backend/app/api/workbench.py`
- Test: `backend/tests/test_workbench_flag.py`

**Interfaces:**
- Produces: `require_workbench(settings: Settings) -> None` in `backend/app/api/workbench.py` (raises 404 `feature_off`), and `router`.

- [ ] **Step 1: Branch and baseline**

```bash
# Run from the Fraud Desk repository root.
git checkout -b feature/officer-workbench
uv sync --dev && uv run pytest -x -q 2>&1 | tail -5
(cd frontend && npm ci && npm test --silent 2>&1 | tail -5)
```
Expected: both pass. If anything fails before you change code, stop and report: do not fix unrelated failures.

- [ ] **Step 2: Failing test**

```python
# backend/tests/test_workbench_flag.py
from app.core.config import Settings

def test_workbench_defaults_off():
    assert Settings(_env_file=None, llm_backend="fake").workbench == "off"

def test_me_reports_workbench_flag(settings, db):
    # copy the client/env construction from tests/test_api_effectiveness.py (make_env)
    on = make_env(settings.model_copy(update={"workbench": "on"}), db, FakeClock())
    assert on.client.get("/me").json()["workbench"] is True
    off = make_env(settings, db, FakeClock())
    assert off.client.get("/me").json()["workbench"] is False

def test_routes_404_when_off(settings, db):
    env = make_env(settings, db, FakeClock())
    r = env.client.get("/cases/anything/evidence-map")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "feature_off"
```
Import `make_env`/`FakeClock` exactly as `tests/test_api_effectiveness.py` does. If `/me` needs a proxy header in tests, copy the headers that file's other tests send.

- [ ] **Step 3: Run, expect FAIL** (`uv run pytest backend/tests/test_workbench_flag.py -q`).

- [ ] **Step 4: Implement**

```python
# backend/app/api/workbench.py  (imports: copy the header of api/effectiveness.py)
from app.core.errors import ApiError

def require_workbench(settings) -> None:
    if settings.workbench != "on":
        raise ApiError(404, "feature_off", "The workbench is switched off.")
```
Add the setting, the `/me` key and the `.env.example` lines (`GLDB_WORKBENCH=off`, `GLDB_WORKBENCH_LINK_KEY=`). Add `router = APIRouter(tags=["workbench"], route_class=ApiRoute)` plus a placeholder `GET /cases/{case_id}/evidence-map` that calls `require_workbench` and returns 501 for now.

- [ ] **Step 5: Run tests, expect PASS; run `uv run ruff check . && uv run ruff format .`**
- [ ] **Step 6: Commit** `git commit -m "feat(workbench): feature flag, /me key, router skeleton"`

---

### Task 2: Models

**Files:** Create `backend/app/workbench/models.py`; Test `backend/tests/test_workbench_models.py`

**Interfaces:** Produces every class below; later tasks import them by these exact names.

- [ ] **Step 1: Failing test**

```python
from app.workbench.models import EvidenceMap, EvidenceRow, Gap

def test_models_round_trip_and_forbid_extra():
    gap = Gap(key="document_missing:ownership", kind="document_missing", item_id="ownership",
              subject="ownership", ask="applicant", title="t", detail="d")
    row = EvidenceRow(item_id="ownership", title="Ownership", status="missing", summary="s",
                      gap_keys=[gap.key])
    m = EvidenceMap(case_id="c1", version=1, use_case="kyc", rows=[row], gaps=[gap], counts={"missing": 1})
    assert EvidenceMap.model_validate(m.model_dump(mode="json")) == m
    import pytest
    with pytest.raises(Exception):
        Gap(key="k", kind="document_missing", item_id="i", subject="s", ask="applicant",
            title="t", detail="d", surprise=1)
```

- [ ] **Step 2: Run, expect FAIL (import error).**
- [ ] **Step 3: Implement**

```python
# backend/app/workbench/models.py
"""Shapes returned by the workbench routes. Views only: nothing here changes a flag, score or lane."""

from __future__ import annotations

from typing import Literal

from app.core.models import Region, Strict

ItemStatus = Literal["found", "review", "missing", "unconfirmed", "not_checked"]
GapKind = Literal[
    "holder_no_onward_owner",
    "director_no_id",
    "payer_buyer_unlinked",
    "sof_unsupported",
    "document_missing",
    "outside_check_unavailable",
]


class EvidenceRef(Strict):
    document_id: str
    label: str
    page: int | None = None
    flag_id: str | None = None
    region: Region | None = None


class RequestOption(Strict):
    id: str
    label: str


class Gap(Strict):
    key: str  # "<kind>:<subject_key>"; never contains a document id, so it survives reruns
    kind: GapKind
    item_id: str
    subject: str
    ask: Literal["applicant", "bank"]
    title: str
    detail: str
    options: list[RequestOption] = []
    refs: list[EvidenceRef] = []
    follow_up_id: str | None = None  # set when a recorded request names this gap


class EvidenceRow(Strict):
    item_id: str
    title: str
    status: ItemStatus
    summary: str
    refs: list[EvidenceRef] = []
    gap_keys: list[str] = []
    rules: list[str] = []


class RowChange(Strict):
    item_id: str
    before: ItemStatus | None
    after: ItemStatus
    note: str


class EvidenceMap(Strict):
    case_id: str
    version: int
    use_case: str
    rows: list[EvidenceRow]
    gaps: list[Gap]
    counts: dict[str, int]
    previous_version: int | None = None
    changes: list[RowChange] = []
    resolved_gap_keys: list[str] = []
```

- [ ] **Step 4: Run tests PASS. Step 5: Commit** `feat(workbench): view models`.

---

### Task 3: Review item registry (data only) with a drift test

**Files:** Create `backend/app/workbench/items.py`; Test `backend/tests/test_workbench_items.py`

**Interfaces:** Produces `ItemDef` (frozen dataclass) and `ITEMS: tuple[ItemDef, ...]`, `items_for(use_case: str) -> list[ItemDef]`.

- [ ] **Step 1: Failing test** (this is the guard against typos in rule and doc-type names)

```python
import typing
from app.checks import registry
from app.core.models import DocType
from app.workbench.items import ITEMS, items_for

def test_every_rule_exists_and_every_doc_type_is_real():
    registry.discover()                      # CONFIRM: how other tests load the registry
    known_rules = set(registry.checks_by_rule())
    known_types = set(typing.get_args(DocType))
    for item in ITEMS:
        for rule in (*item.rules, *item.outside_rules):
            assert rule in known_rules, (item.id, rule)
        for t in item.docs_any:
            assert t in known_types, (item.id, t)

def test_use_case_filtering():
    assert "ownership" in [i.id for i in items_for("kyc")]
    assert "ownership" not in [i.id for i in items_for("aml")]
    assert "payments" in [i.id for i in items_for("aml")]
```

- [ ] **Step 2: Run, expect FAIL. Step 3: Implement**

```python
# backend/app/workbench/items.py
"""What an officer reviews, per use case. Data only. A rule listed under ``rules`` checks the
documents against each other; one under ``outside_rules`` checks them against an outside record."""

from __future__ import annotations

from dataclasses import dataclass

from app.workbench.models import RequestOption


@dataclass(frozen=True)
class ItemDef:
    id: str
    title: str
    use_cases: tuple[str, ...]
    docs_any: tuple[str, ...] = ()  # at least one readable document of these types is expected
    rules: tuple[str, ...] = ()
    outside_rules: tuple[str, ...] = ()
    found_text: str = "The documents agree."
    missing_options: tuple[RequestOption, ...] = ()


ITEMS: tuple[ItemDef, ...] = (
    ItemDef(
        "company_identity", "Company identity", ("kyc", "aml", "loan"),
        docs_any=("company_profile", "certificate_of_incorporation"),
        rules=("x_entity",), outside_rules=("x_entity_vs_registry", "x_entity_vs_gleif"),
        found_text="The name and company number agree across the documents.",
        missing_options=(RequestOption(id="company_profile", label="Company profile or certificate of incorporation"),),
    ),
    ItemDef(
        "ownership", "Ownership", ("kyc",),
        docs_any=("ownership_chart", "shareholder_register"),
        rules=("kyc_shareholdings",), outside_rules=("x_ownership_vs_registry",),
        found_text="The ownership documents list the owners and their percentages.",
        missing_options=(RequestOption(id="ownership_chart", label="Ownership chart showing owners and percentages"),),
    ),
    ItemDef(
        "directors", "Directors", ("kyc",),
        docs_any=("company_profile",), rules=("x_directors",),
        found_text="The listed directors agree with the identity documents supplied.",
    ),
    ItemDef(
        "identity_documents", "Identity documents", ("kyc",),
        docs_any=("national_id", "passport"),
        rules=("id_format", "id_checksum", "date_valid", "passport_mrz"),
        found_text="The identity documents are readable and internally consistent.",
        missing_options=(RequestOption(id="identity_documents", label="Identity documents of the directors and owners"),),
    ),
    ItemDef(
        "sanctions", "Sanctions screening", ("kyc", "aml", "loan"),
        outside_rules=("x_sanctions_screen",),
        found_text="The names were screened against the sanctions lists.",
    ),
    ItemDef(
        "source_of_funds", "Source of funds", ("aml",),
        docs_any=("source_of_funds_declaration",),
        rules=("x_sof_support", "x_declared_vs_activity"),
        found_text="The declared source of funds is supported by the documents.",
        missing_options=(RequestOption(id="sof_declaration", label="Source of funds declaration"),),
    ),
    ItemDef(
        "payments", "Payments and counterparties", ("aml",),
        docs_any=("bank_statement",), rules=("x_remitter_vs_buyer", "bs_pass_through"),
        found_text="Payers and payees match the invoices and declared business.",
        missing_options=(RequestOption(id="bank_statements", label="Bank statements for the period"),),
    ),
    ItemDef(
        "invoices", "Invoices", ("aml",),
        docs_any=("invoice",), rules=("inv_gst",),
        found_text="The invoices are internally consistent.",
    ),
    ItemDef(
        "financials", "Financial statements", ("loan",),
        docs_any=("financial_statement",),
        rules=(),  # CONFIRM: add the fs_* rule ids from docs/check-catalogue.md; the drift test will reject typos
        found_text="The financial statements are readable and consistent.",
        missing_options=(RequestOption(id="financial_statements", label="Latest financial statements"),),
    ),
)


def items_for(use_case: str) -> list[ItemDef]:
    return [i for i in ITEMS if use_case in i.use_cases]
```
Run the test; if a rule id does not exist, fix the id from `docs/check-catalogue.md`, not the test.

- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): review item registry`.

---

### Task 4: Gap detection

**Files:** Create `backend/app/workbench/gaps.py`; Test `backend/tests/test_workbench_gaps.py`

**Interfaces:**
- Consumes: `Gap`, `EvidenceRef`, `RequestOption` (T2).
- Produces: `detect_gaps(case: Case) -> list[Gap]` (the flag- and document-based gaps; missing-document and outside-check gaps are added by T5 because they need item status).

- [ ] **Step 1: Failing tests.** Build cases with the project helpers (`make_ctx(docs=[...])`, then `ctx.case`; CONFIRM `make_doc` kwargs in `core/testing.py`). Test table:

| Test | Given | Expect |
|---|---|---|
| `test_corporate_holder_without_onward_owner` | ownership_chart with holders: `Alice Tan` individual 60 (no parent), `XYZ Holdings Pte Ltd` company 40 (no parent) | one gap, kind `holder_no_onward_owner`, subject `XYZ Holdings Pte Ltd`, `ask="applicant"`, key `holder_no_onward_owner:` + `company_key("XYZ Holdings Pte Ltd")`, options ids `holder_ownership_chart`, `holder_register_of_members`, one ref with `document_id` of the chart |
| `test_corporate_holder_with_onward_owner_is_not_a_gap` | same, plus a row `Bob Lim` individual 100 with `parent_name="XYZ Holdings Pte Ltd"` | no gap |
| `test_same_holder_spelled_differently_is_one_gap` | two charts listing `XYZ Holdings Pte. Ltd.` and `XYZ HOLDINGS PTE LTD` | one gap |
| `test_unreadable_chart_gives_no_gap` | chart with `status="failed"` | none |
| `test_director_without_id` | `x_directors` info flag with `table="directors"`, `row=0`, evidence `"no ID document supplied for director Carol Ng"`; profile directors row 0 is `Carol Ng` | one gap `director_no_id`, subject `Carol Ng`, option `director_id_document` |
| `test_other_x_directors_info_flag_is_not_a_gap` | `x_directors` info flag with evidence not starting with that phrase | none |
| `test_payer_buyer_gap_closes_when_flag_is_info` | `x_remitter_vs_buyer` medium flag -> gap `payer_buyer_unlinked`; same flag lowered to `info` -> no gap | as stated |
| `test_sof_gap` | `x_sof_support` high flag | gap `sof_unsupported`, option `sof_supporting_document` |
| `test_gap_key_contains_no_document_id` | any gap | `"d1" not in gap.key` |

- [ ] **Step 2: Run, expect FAIL. Step 3: Implement**

```python
# backend/app/workbench/gaps.py
"""Evidence gaps found by code: a fact the documents leave open. Gap keys are built from what the
gap is about (a company key, a person key, a kind), never from document or flag ids, because those
change on every rerun and the keys are how a later version recognises a gap as closed."""

from __future__ import annotations

from app.core.models import Case, Document, Flag, Row
from app.core.textnorm import company_key
from app.graph import counterparty_of
from app.names import entity_key
from app.workbench.models import EvidenceRef, Gap, RequestOption

READABLE = ("ok", "partial")
_DIRECTOR_NO_ID = "no ID document supplied for director "


def _text(row: Row, column: str) -> str:
    cell = row.cells.get(column)
    return cell.text().strip() if cell is not None else ""


def _ref(doc: Document, row: Row, column: str, label: str, flag_id: str | None = None) -> EvidenceRef:
    cell = row.cells.get(column)
    box = cell.box if cell is not None else None
    return EvidenceRef(
        document_id=doc.id, label=label, page=box.page if box else None, flag_id=flag_id, region=box
    )


def _row_of(case: Case, flag: Flag) -> tuple[Document, Row] | None:
    if not flag.document_ids or flag.table is None or flag.row is None:
        return None
    doc = next((d for d in case.documents if d.id == flag.document_ids[0]), None)
    table = doc.tables.get(flag.table) if doc else None
    row = next((r for r in table.rows if r.index == flag.row), None) if table else None
    return (doc, row) if doc and row else None


def holder_gaps(case: Case) -> list[Gap]:
    """A company listed as an owner whose own owners appear nowhere in the ownership chart."""
    out: list[Gap] = []
    for doc in case.documents:
        if doc.doc_type != "ownership_chart" or doc.status not in READABLE:
            continue
        table = doc.tables.get("holders")
        if table is None:
            continue
        parents = {company_key(_text(r, "parent_name")) for r in table.rows if _text(r, "parent_name")}
        for row in table.rows:
            name = _text(row, "holder_name")
            if not name or _text(row, "holder_type") != "company" or company_key(name) in parents:
                continue
            pct = _text(row, "percent").rstrip("%").strip()
            held = f" ({pct}%)" if pct else ""
            out.append(
                Gap(
                    key=f"holder_no_onward_owner:{company_key(name)}",
                    kind="holder_no_onward_owner", item_id="ownership", subject=name, ask="applicant",
                    title=f"Who owns {name}?",
                    detail=f"{name}{held} is listed as an owner. The documents do not show who owns {name}.",
                    options=[
                        RequestOption(id="holder_ownership_chart",
                                      label=f"Ownership chart or company record for {name}"),
                        RequestOption(id="holder_register_of_members",
                                      label=f"Register of members of {name}"),
                    ],
                    refs=[_ref(doc, row, "holder_name", f"Ownership chart, {name}")],
                )
            )
    return out


def director_gaps(case: Case) -> list[Gap]:
    out: list[Gap] = []
    for f in case.flags:
        if f.rule != "x_directors" or f.severity != "info" or not f.evidence.startswith(_DIRECTOR_NO_ID):
            continue
        located = _row_of(case, f)
        name = _text(located[1], "director_name") if located else f.evidence[len(_DIRECTOR_NO_ID):].strip()
        if not name:
            continue
        out.append(
            Gap(
                key=f"director_no_id:{entity_key(name, 'person')}",
                kind="director_no_id", item_id="directors", subject=name, ask="applicant",
                title=f"No identity document for {name}",
                detail=f"{name} is listed as a director. No identity document for {name} was supplied.",
                options=[RequestOption(id="director_id_document",
                                       label=f"Identity document of {name} (director)")],
                refs=[_ref(located[0], located[1], "director_name", f"Directors, {name}", f.id)] if located else [],
            )
        )
    return out


def flag_gaps(case: Case) -> list[Gap]:
    """Gaps that exist because a cross-document check could not tie two documents together. Once the
    challenger lowers the flag to info (a document explained it), the gap is gone."""
    out: list[Gap] = []
    for f in case.flags:
        if f.severity == "info":
            continue
        located = _row_of(case, f)
        refs = [_ref(located[0], located[1], "description", "Bank statement line", f.id)] if located else []
        if f.rule == "x_remitter_vs_buyer":
            payer = (counterparty_of(located[1]) if located else None) or "the paying party"
            out.append(
                Gap(
                    key=f"payer_buyer_unlinked:{company_key(payer) or 'payer'}",
                    kind="payer_buyer_unlinked", item_id="payments", subject=payer, ask="applicant",
                    title="Payer is not the invoice buyer",
                    detail=f"The payment came from {payer}, who is not the buyer on the invoice. No document links them.",
                    options=[RequestOption(
                        id="payer_relationship_document",
                        label=f"Agreement, authorisation letter or other document showing why {payer} paid the invoice")],
                    refs=refs,
                )
            )
        elif f.rule == "x_sof_support":
            out.append(
                Gap(
                    key="sof_unsupported:declared",
                    kind="sof_unsupported", item_id="source_of_funds", subject="the declared source of funds",
                    ask="applicant", title="Declared source of funds is not supported",
                    detail="The declared source of funds is not backed by the documents supplied.",
                    options=[RequestOption(
                        id="sof_supporting_document",
                        label="Document supporting the declared source of funds, such as a contract, sale agreement or statement")],
                    refs=refs,
                )
            )
    return out


def detect_gaps(case: Case) -> list[Gap]:
    seen: dict[str, Gap] = {}
    for g in (*holder_gaps(case), *director_gaps(case), *flag_gaps(case)):
        seen.setdefault(g.key, g)
    return list(seen.values())
```
`x_remitter_vs_buyer` may point at a different table/column than `description`; CONFIRM against `docs/demo-script.md` 3c and the flag in the real challenger bundle (T12) and fix the column name, not the test.

- [ ] **Step 4: Run tests PASS. Step 5: Commit** `feat(workbench): deterministic evidence gaps`.

---

### Task 5: Build the Evidence Map

**Files:** Create `backend/app/workbench/evidence.py`; Test `backend/tests/test_workbench_evidence.py`

**Interfaces:**
- Consumes: `ITEMS`/`items_for`, `detect_gaps`, models.
- Produces: `build_evidence_map(case: Case, *, previous: EvidenceMap | None = None, follow_ups: dict[str, str] | None = None) -> EvidenceMap` where `follow_ups` maps gap key to follow-up id.

Status rules (first match wins), per item:
1. Item has `docs_any` and no readable document of those types: `not_checked` if a document of that type exists but is unreadable, else `missing`.
2. Any non-info flag whose rule is in `rules + outside_rules`: `review`.
3. Item has `rules`, none ran, and no outside rule ran: `not_checked`.
4. Item has `outside_rules`, none ran: `unconfirmed`.
5. Otherwise `found`.

- [ ] **Step 1: Failing tests**

| Test | Given | Expect |
|---|---|---|
| `test_found_when_documents_present_and_checks_ran_clean` | kyc case: company_profile ok, `x_entity` ran 0 flags, `x_entity_vs_registry` ran | `company_identity` row `found` |
| `test_unconfirmed_when_registry_check_skipped` | same, `x_entity_vs_registry` and `x_entity_vs_gleif` skipped | `unconfirmed`, summary ends with "No outside record was available to confirm this." |
| `test_missing_when_no_document` | kyc case without ownership documents | `ownership` `missing`; a gap `document_missing:ownership` with the item's `missing_options` |
| `test_review_when_flag` | medium `kyc_shareholdings` flag | `ownership` `review`, `refs` contain the flag id |
| `test_not_checked_when_unreadable` | ownership_chart `status="failed"` | `not_checked` |
| `test_info_flag_does_not_trigger_review` | info flag on `directors` | not `review` |
| `test_outside_unavailable_gap_is_for_the_bank` | `x_entity_vs_registry` skipped | gap kind `outside_check_unavailable`, `ask="bank"`, no options |
| `test_counts_cover_rows` | any | `sum(counts.values()) == len(rows)` |
| `test_use_case_limits_rows` | aml case | no `ownership` row |
| `test_previous_comparison` | previous map has `ownership=missing` and gap `holder_no_onward_owner:x`; current has `ownership=unconfirmed` and no such gap | `changes` has `RowChange(before="missing", after="unconfirmed", note="Information provided; independent confirmation is still needed.")` and `resolved_gap_keys == ["holder_no_onward_owner:x"]` |
| `test_follow_up_id_is_attached` | `follow_ups={gap.key: "c1/f1"}` | that gap has `follow_up_id == "c1/f1"` |
| `test_pure` | call twice | equal results; no clock, no network |

- [ ] **Step 2: Run, expect FAIL. Step 3: Implement**

```python
# backend/app/workbench/evidence.py
from __future__ import annotations

from app.core.models import Case, Flag
from app.workbench.gaps import READABLE, detect_gaps
from app.workbench.items import ItemDef, items_for
from app.workbench.models import (EvidenceMap, EvidenceRef, EvidenceRow, Gap, ItemStatus, RowChange)

_NOTES: dict[tuple[str | None, str], str] = {
    ("missing", "unconfirmed"): "Information provided; independent confirmation is still needed.",
    ("missing", "found"): "Information provided.",
    ("missing", "review"): "Information provided; it needs review.",
    ("review", "found"): "No longer needs review.",
    ("not_checked", "found"): "Now readable and checked.",
    ("not_checked", "unconfirmed"): "Now readable; independent confirmation is still needed.",
}


def _status(case: Case, item: ItemDef, ran: set[str]) -> tuple[ItemStatus, list[Flag]]:
    typed = [d for d in case.documents if d.doc_type in item.docs_any]
    if item.docs_any and not any(d.status in READABLE for d in typed):
        return ("not_checked" if typed else "missing"), []
    mine = {*item.rules, *item.outside_rules}
    flags = [f for f in case.flags if f.rule in mine and f.severity != "info"]
    if flags:
        return "review", flags
    inside = [r for r in item.rules if r in ran]
    outside = [r for r in item.outside_rules if r in ran]
    if item.rules and not inside and not outside:
        return "not_checked", []
    if item.outside_rules and not outside:
        return "unconfirmed", []
    return "found", []


def _summary(item: ItemDef, status: ItemStatus, flags: list[Flag]) -> str:
    if status == "review":
        first = flags[0].explanation.strip()
        more = f" (and {len(flags) - 1} more)" if len(flags) > 1 else ""
        return f"{first}{more}"
    if status == "missing":
        return "No document for this item was found in the case."
    if status == "not_checked":
        return "The document could not be read, or the checks did not run."
    if status == "unconfirmed":
        return f"{item.found_text} No outside record was available to confirm this."
    return item.found_text


def compare(prev: EvidenceMap, cur: EvidenceMap) -> tuple[list[RowChange], list[str]]:
    before = {r.item_id: r.status for r in prev.rows}
    changes = [
        RowChange(item_id=r.item_id, before=before.get(r.item_id), after=r.status,
                  note=_NOTES.get((before.get(r.item_id), r.status), "Status changed."))
        for r in cur.rows if before.get(r.item_id) != r.status
    ]
    resolved = sorted({g.key for g in prev.gaps} - {g.key for g in cur.gaps})
    return changes, resolved


def build_evidence_map(case: Case, *, previous: EvidenceMap | None = None,
                       follow_ups: dict[str, str] | None = None) -> EvidenceMap:
    runs = {r.rule: r for r in case.check_runs}
    ran = {rule for rule, r in runs.items() if r.status == "ran"}
    detected = detect_gaps(case)
    rows: list[EvidenceRow] = []
    extra: list[Gap] = []
    for item in items_for(case.use_case):
        status, flags = _status(case, item, ran)
        keys = [g.key for g in detected if g.item_id == item.id]
        if status == "missing" and item.missing_options:
            gap = Gap(key=f"document_missing:{item.id}", kind="document_missing", item_id=item.id,
                      subject=item.title, ask="applicant", title=f"No document for {item.title.lower()}",
                      detail="Nothing in the case covers this item.", options=list(item.missing_options))
            extra.append(gap)
            keys.append(gap.key)
        if status == "unconfirmed":
            skipped = [r for r in item.outside_rules if r in runs and runs[r].status != "ran"] or list(item.outside_rules)
            gap = Gap(key=f"outside_check_unavailable:{item.id}", kind="outside_check_unavailable",
                      item_id=item.id, subject=item.title, ask="bank",
                      title=f"No outside record for {item.title.lower()}",
                      detail="The registry or screening source was not available for this case. "
                             "This is a step for the bank team, not a request for the applicant.")
            extra.append(gap)
            keys.append(gap.key)
        refs = [EvidenceRef(document_id=f.document_ids[0], label=f.explanation[:80], flag_id=f.id,
                            page=f.region.page if f.region else None, region=f.region)
                for f in flags if f.document_ids]
        rows.append(EvidenceRow(item_id=item.id, title=item.title, status=status,
                                summary=_summary(item, status, flags), refs=refs, gap_keys=keys,
                                rules=[*item.rules, *item.outside_rules]))
    gaps = [*detected, *extra]
    for g in gaps:
        g.follow_up_id = (follow_ups or {}).get(g.key)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    result = EvidenceMap(case_id=case.id, version=case.version, use_case=case.use_case,
                         rows=rows, gaps=gaps, counts=counts)
    if previous is not None:
        result.previous_version = previous.version
        result.changes, result.resolved_gap_keys = compare(previous, result)
    return result
```
Remove the unused `skipped` variable if ruff flags it. If `Gap` is frozen by `Strict`, build with the `follow_up_id` at construction instead of assigning afterwards (CONFIRM `Strict` config).

- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): evidence map builder`.

---

### Task 6: API endpoint

**Files:** Modify `backend/app/api/workbench.py`; Test `backend/tests/test_api_workbench.py`

**Interfaces:** Produces `GET /cases/{case_id}/evidence-map?version=N`.

- [ ] **Step 1: Failing test** (template: `tests/test_api_effectiveness.py`). Seed a case with `env.seed(...)` and flags; with `workbench="on"` assert 200, `json["rows"]` non-empty, `json["case_id"]`. With version 2 and a stored version 1, assert `previous_version == 1` and `changes` present. Unknown case -> 404 `not_found`. Flag off -> 404 `feature_off`.
- [ ] **Step 2: Run FAIL. Step 3: Implement**

```python
@router.get("/cases/{case_id}/evidence-map")
def evidence_map(case_id: str, db: DbDep, settings: SettingsDep, version: int | None = None) -> dict[str, Any]:
    require_workbench(settings)
    case = load_case_or_404(db, case_id, version)
    previous = None
    if case.version > 1:
        prev_case = db.load_case(case_id, case.version - 1)  # CONFIRM it returns None for a missing snapshot
        previous = build_evidence_map(prev_case) if prev_case else None
    follow_ups = {key: fu.id for fu in db.list_follow_ups(case_id) for key in fu.gap_keys}  # added in T9; use {} until then
    return build_evidence_map(case, previous=previous, follow_ups=follow_ups).model_dump(mode="json")
```
- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): evidence-map route`.

---

### Task 7: Frontend Evidence tab

**Files:**
- Create: `frontend/src/lib/workbenchTypes.ts`, `frontend/src/lib/workbench.ts`, `frontend/src/components/workbench/EvidenceMapPanel.tsx`, `frontend/src/components/workbench/EvidenceMapPanel.test.tsx`
- Modify: `frontend/src/lib/api.ts` (add `getEvidenceMap`, `Me.workbench`), `frontend/src/app/cases/[id]/page.tsx` (tab at the `useMemo` near l.71-77 and the `panels` record near l.302)

**Interfaces:** Consumes the T2 JSON shape. Produces `EvidenceMapPanel` props `{ map: EvidenceMap; selected: string[]; onToggleGap(key: string): void; onDraft(): void; onShowEvidence(ref: EvidenceRef): void }`.

- [ ] **Step 1: Types and labels**

```ts
// frontend/src/lib/workbenchTypes.ts  (mirror backend/app/workbench/models.py)
export type ItemStatus = "found" | "review" | "missing" | "unconfirmed" | "not_checked";
export type EvidenceRef = { document_id: string; label: string; page: number | null; flag_id: string | null; region: unknown | null };
export type RequestOption = { id: string; label: string };
export type Gap = { key: string; kind: string; item_id: string; subject: string; ask: "applicant" | "bank";
  title: string; detail: string; options: RequestOption[]; refs: EvidenceRef[]; follow_up_id: string | null };
export type EvidenceRow = { item_id: string; title: string; status: ItemStatus; summary: string; refs: EvidenceRef[]; gap_keys: string[]; rules: string[] };
export type RowChange = { item_id: string; before: ItemStatus | null; after: ItemStatus; note: string };
export type EvidenceMap = { case_id: string; version: number; use_case: string; rows: EvidenceRow[]; gaps: Gap[];
  counts: Record<string, number>; previous_version: number | null; changes: RowChange[]; resolved_gap_keys: string[] };
```
```ts
// frontend/src/lib/workbench.ts
import type { ItemStatus } from "./workbenchTypes";
export const STATUS_LABEL: Record<ItemStatus, string> = {
  found: "Evidence found",
  review: "Needs review",
  missing: "Information may be needed",
  unconfirmed: "Not independently confirmed",
  not_checked: "Not checked",
};
```
Never write the word "verified" in any frontend file (wording test).

- [ ] **Step 2: Failing component test** (template: `components/case/EntityGraph.test.tsx`; use `renderWithSWR`/`setViewport` from `@/test/utils` if you need them)

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { EvidenceMapPanel } from "./EvidenceMapPanel";
import type { EvidenceMap } from "@/lib/workbenchTypes";

afterEach(cleanup);
const map: EvidenceMap = {
  case_id: "c1", version: 1, use_case: "kyc", counts: { found: 1, unconfirmed: 1 },
  previous_version: null, changes: [], resolved_gap_keys: [],
  rows: [
    { item_id: "company_identity", title: "Company identity", status: "found", summary: "The name and company number agree across the documents.", refs: [], gap_keys: [], rules: [] },
    { item_id: "ownership", title: "Ownership", status: "unconfirmed", summary: "x", refs: [], gap_keys: ["holder_no_onward_owner:xyz"], rules: [] },
  ],
  gaps: [{ key: "holder_no_onward_owner:xyz", kind: "holder_no_onward_owner", item_id: "ownership", subject: "XYZ Holdings Pte Ltd",
    ask: "applicant", title: "Who owns XYZ Holdings Pte Ltd?", detail: "d", options: [], refs: [], follow_up_id: null }],
};

it("shows each status with text, not colour alone", () => {
  render(<EvidenceMapPanel map={map} selected={[]} onToggleGap={vi.fn()} onDraft={vi.fn()} onShowEvidence={vi.fn()} />);
  expect(screen.getByText("Evidence found")).toBeInTheDocument();
  expect(screen.getByText("Not independently confirmed")).toBeInTheDocument();
  expect(screen.getByText("Who owns XYZ Holdings Pte Ltd?")).toBeInTheDocument();
});

it("lets the officer select an applicant gap and start a draft", async () => {
  const user = userEvent.setup(); const toggle = vi.fn(); const draft = vi.fn();
  render(<EvidenceMapPanel map={map} selected={["holder_no_onward_owner:xyz"]} onToggleGap={toggle} onDraft={draft} onShowEvidence={vi.fn()} />);
  await user.click(screen.getByRole("checkbox", { name: /Who owns XYZ/ }));
  expect(toggle).toHaveBeenCalledWith("holder_no_onward_owner:xyz");
  await user.click(screen.getByRole("button", { name: /Draft request/ }));
  expect(draft).toHaveBeenCalled();
});
```
- [ ] **Step 3: Run `npx vitest run src/components/workbench` FAIL. Step 4: Implement** `EvidenceMapPanel.tsx`: a `Panel` titled "Evidence" with a count strip (one `Badge` per status present in `map.counts`); a table (`overflow-x-auto`, columns "What to review", "What Fraud Desk found", "Status"); under each item row its gaps as indented rows with a checkbox (only when `gap.ask === "applicant"`; bank gaps show a "For the bank team" label instead), the gap `title`, `detail` and a "Requested" tag when `follow_up_id` is set; a "Draft request" `Button` disabled while `selected` is empty; when `map.changes` is non-empty a "Since version N" list with each `note`. Use `lucide-react` icons (`CheckCircle2`, `Eye`, `CircleDashed`, `ShieldQuestion`, `MinusCircle`) with `aria-hidden className="h-4 w-4"`, status text always visible. Styling per `frontend/DESIGN.md`: token classes only; red/amber/green are for risk, so do not use `sev-*` here; use `brand`, `brass-deep`, `ink-2`, `muted`. Touch targets `min-h-11`. CONFIRM the props of `Panel`, `Badge`, `Button`, `EmptyState` in `components/ui/index.ts` before using them.
- [ ] **Step 5: Wire the tab** in `page.tsx`: `useMe()` exposes `workbench`; add `if (me?.workbench) t.push({ id: "evidence", label: "Evidence" })` next to the existing `t.push` calls and a `panels.evidence` entry rendering `<EvidenceTab caseId=... version=... />`, a small component that does `useSWR(["evidence", id, version], () => getEvidenceMap(id, version))`, shows `CaseSkeleton`-style loading with `role="status"`, `CaseErrorNotice` on error, and holds `selected` state. Add `getEvidenceMap` to `lib/api.ts` beside `getCase` (it must use `request<EvidenceMap>(...)`; in `USE_MOCK` mode return a rejected `ApiErrorException` or hide the tab: mock mode simply has `workbench: false`).
- [ ] **Step 6: Run** `npm run lint && npx tsc --noEmit && npm test && npm run build` (all must pass). Check 360, 390, 768, 1440 px in the browser against a local backend with `GLDB_WORKBENCH=on`.
- [ ] **Step 7: Commit** `feat(workbench): Evidence tab`.

---

### Task 8: Follow-up draft (template) and message verifier

**Files:** Create `backend/app/workbench/followup.py`; Test `backend/tests/test_workbench_followup.py`

**Interfaces:**
- Consumes: `Gap`, `RequestOption`.
- Produces: `Selection` (`gap_key: str`, `option_ids: list[str]`), `draft_template(gaps: list[Gap], selections: list[Selection], applicant_name: str | None) -> str`, `verify_message(message: str, wanted: list[RequestOption]) -> list[str]` (empty list means clean), `MAX_MESSAGE = 900`.

- [ ] **Step 1: Failing tests**

```python
from app.workbench.followup import MAX_MESSAGE, Selection, draft_template, verify_message
from app.workbench.models import Gap, RequestOption

def _gap():
    return Gap(key="holder_no_onward_owner:xyz", kind="holder_no_onward_owner", item_id="ownership",
               subject="XYZ Holdings Pte Ltd", ask="applicant", title="t", detail="d",
               options=[RequestOption(id="holder_ownership_chart", label="Ownership chart or company record for XYZ Holdings Pte Ltd"),
                        RequestOption(id="holder_register_of_members", label="Register of members of XYZ Holdings Pte Ltd")])

def test_template_lists_only_selected_options_and_is_clean():
    g = _gap()
    msg = draft_template([g], [Selection(gap_key=g.key, option_ids=["holder_ownership_chart"])], "ABC Trading Pte Ltd")
    assert "Ownership chart or company record for XYZ Holdings Pte Ltd" in msg
    assert "Register of members" not in msg
    assert msg.startswith("Dear ABC Trading Pte Ltd")
    assert verify_message(msg, [g.options[0]]) == []

def test_template_never_includes_bank_gaps():
    g = _gap().model_copy(update={"ask": "bank"})
    assert "Ownership chart" not in draft_template([g], [Selection(gap_key=g.key, option_ids=["holder_ownership_chart"])], None)

def test_verifier_rejects_disclosure_and_missing_asks():
    wanted = [RequestOption(id="x", label="Ownership chart for XYZ")]
    bad = "Our fraud check flagged x_remitter_vs_buyer; please send the ownership chart."
    problems = verify_message(bad, wanted)
    assert any("fraud" in p for p in problems) and any("flagged" in p or "flag" in p for p in problems)
    assert any("internal id" in p for p in problems)
    assert any("does not ask for" in p for p in verify_message("Please send something.", wanted))

def test_verifier_rejects_overlong_and_empty():
    assert verify_message("", []) == ["empty"]
    assert any("longer than" in p for p in verify_message("a" * (MAX_MESSAGE + 1), []))
```
- [ ] **Step 2: Run FAIL. Step 3: Implement**

```python
# backend/app/workbench/followup.py
"""Draft a request for missing documents. Code decides WHAT is asked (the options come from the
gap); a model, if used (task 11), may only reword. Code verifies the result. Nothing is sent."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core.models import Strict
from app.workbench.models import Gap, RequestOption

MAX_MESSAGE = 900
_BANNED = re.compile(
    r"\b(fraud\w*|suspicio\w*|suspect\w*|flag\w*|sanction\w*|aml|launder\w*|score\w*|forg\w*|fake|"
    r"tamper\w*|alert\w*|investigat\w*|red flag|fraud desk)\b",
    re.IGNORECASE,
)
_INTERNAL_ID = re.compile(r"\b[a-z]{2,}_[a-z_]{2,}\b")


class Selection(Strict):
    gap_key: str
    option_ids: list[str]


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def chosen_options(gaps: Sequence[Gap], selections: Sequence[Selection]) -> list[RequestOption]:
    """The options the officer ticked, for applicant-owned gaps only, in gap order."""
    wanted = {s.gap_key: set(s.option_ids) for s in selections}
    return [o for g in gaps if g.ask == "applicant" and g.key in wanted
            for o in g.options if o.id in wanted[g.key]]


def draft_template(gaps: Sequence[Gap], selections: Sequence[Selection], applicant_name: str | None) -> str:
    asks = chosen_options(gaps, selections)
    lines = "\n".join(f"{i}. {o.label}" for i, o in enumerate(asks, 1))
    return (
        f"Dear {applicant_name or 'Sir or Madam'},\n\n"
        "To complete our review of your application, please send us the following:\n\n"
        f"{lines}\n\n"
        "If any of these are not available, please tell us why and we will consider what else may help.\n\n"
        "Thank you."
    )


def verify_message(message: str, wanted: Sequence[RequestOption]) -> list[str]:
    if not message.strip():
        return ["empty"]
    problems: list[str] = []
    if len(message) > MAX_MESSAGE:
        problems.append(f"longer than {MAX_MESSAGE} characters")
    for word in sorted({m.group(0).lower() for m in _BANNED.finditer(message)}):
        problems.append(f"contains '{word}'")
    if _INTERNAL_ID.search(message):
        problems.append("contains an internal id")
    folded = _fold(message)
    problems += [f"does not ask for: {o.label}" for o in wanted if _fold(o.label) not in folded]
    return problems
```
Note `_INTERNAL_ID` must not match ordinary hyphen-free words; add a test with "ownership chart" and "register of members" (must be clean).

- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): follow-up draft and verifier`.

---

### Task 9: Persist recorded requests, routes, tracking

**Files:**
- Modify: `backend/app/core/db.py` (DDL + methods; mirror `review_entries` at l.91 and its sealing, and add the delete to `delete_case` near l.906), `backend/app/api/workbench.py`
- Create: model `FollowUp` in `backend/app/workbench/models.py`
- Test: `backend/tests/test_api_workbench_followups.py`

**Interfaces:**
- Produces: `FollowUp(Strict)`: `id: str` (`"<case_id>/f<n>"`), `case_id`, `case_version: int`, `created_at: UtcDatetime`, `created_by: str`, `gap_keys: list[str]`, `message: str`, `review_entry_id: str | None`, `drafted_by: Literal["template","model"]`.
- DB: `Database.add_follow_up(fu: FollowUp) -> None`, `Database.list_follow_ups(case_id: str) -> list[FollowUp]`.
- Routes: `POST /cases/{id}/follow-ups/draft`, `POST /cases/{id}/follow-ups`, `GET /cases/{id}/follow-ups`.

```sql
CREATE TABLE IF NOT EXISTS follow_ups (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    case_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL            -- sealed like review_entries.body when encryption is on
);
CREATE INDEX IF NOT EXISTS idx_follow_ups_case ON follow_ups (case_id);
```

- [ ] **Step 1: Failing tests** (template: `test_api_effectiveness.py` plus review tests for the reviewer header)
  - draft: `POST /cases/{id}/follow-ups/draft` body `{"selections":[{"gap_key":..., "option_ids":[...]}], "use_model": false}` returns `{"message": str, "drafted_by": "template", "problems": []}`; a selection naming an unknown gap key -> 422 `validation_error`; a bank-owned gap key -> 422.
  - record: `POST /cases/{id}/follow-ups` with `{"gap_keys": [...], "message": "...", "review_entry_id": null, "drafted_by": "template"}` stores a row; returns the `FollowUp`; a message that fails `verify_message` -> 422 with `details.problems`; case status other than `open`/`awaiting_documents` -> 409 `case_not_open` (use `require_state`).
  - list returns recorded rows in creation order.
  - after recording, `GET /cases/{id}/evidence-map` shows `follow_up_id` on those gaps.
  - delete the case (`DELETE /cases/{id}`): rows gone.
  - audit: recording writes an audit row `workbench.follow_up_recorded` (CONFIRM how `review.py` writes audit rows and copy it; store only `len` and a sha256 of the message, never the text, as review does).
- [ ] **Step 2: Run FAIL. Step 3: Implement** DDL, the two methods (copy the sealing pattern and context string style from review entries), the routes (reviewer from `signed_in_reviewer(request)` or a typed `reviewer` in the body when nobody is signed in, exactly as `api/review.py` does), and `verify_message(...)` on record. In the evidence-map route replace the `{}` placeholder with the real map.
- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): record follow-up requests`.

---

### Task 10: Frontend composer and tracking

**Files:** Create `frontend/src/components/workbench/FollowUpComposer.tsx` and its test; Modify `lib/api.ts` (`draftFollowUp`, `recordFollowUp`, `getFollowUps`, `postReview` already exists: reuse it), `EvidenceTab` (open composer from "Draft request").

- [ ] **Step 1: Failing test.** Render the composer with a draft `{message, problems: []}`; it shows a read-only notice "Nothing is sent from here. Copy the request into the bank's own channel."; the textarea is editable; **Record request** is disabled when `problems` is non-empty, listing them; **Copy** button copies the text. Editing the text re-checks with a client-side mirror of the banned words list is NOT done: the server verifies on record, and the composer shows the server's `problems` after a failed record.
- [ ] **Step 2: Run FAIL. Step 3: Implement.** Flow on **Record request**: (1) `postReview(...)` with `kind="verdict"`, `decision="request_documents"`, `reason=message` (the existing 5-1000 character rule; our `MAX_MESSAGE` of 900 is within it) so the case moves to `awaiting_documents` through the existing audited path and `AddDocuments` appears; (2) `recordFollowUp({gap_keys, message, review_entry_id, drafted_by})`; (3) revalidate the evidence SWR key. Show a "Requested on <date>" tag on the gaps afterwards.
- [ ] **Step 4: Tracking UI.** When `map.previous_version != null`, show under the strip: "Since version N: K gap(s) closed" (from `resolved_gaps`: add `resolved_gaps: list[Gap] = []` to `EvidenceMap` and set it in `build_evidence_map` as `[g for g in previous.gaps if g.key in result.resolved_gap_keys]`; add a test in `test_workbench_evidence.py::test_previous_comparison`) and each `changes[].note`. This is the "information provided; independent confirmation still needed" moment of the demo.
- [ ] **Step 5: Run all four frontend commands. Step 6: Commit** `feat(workbench): follow-up composer and tracking`.

---

### Task 11 (optional, Tier C): Model wording

**Files:** Modify `backend/app/workbench/followup.py`, `backend/app/api/workbench.py`, `docs/ai-inventory.md`; Test `backend/tests/test_workbench_followup_model.py`

**Interfaces:** Produces `polish(settings, db, llm, gaps, selections, applicant_name, account) -> tuple[str, Literal["template","model"]]`. It never raises: any model error, budget error or verifier failure returns `(draft_template(...), "template")`.

- [ ] **Step 1: Failing tests** with `FakeLLMClient({"workbench:followup": lambda req: {"message": "..."}})`:
  1. clean model text that asks for every chosen option -> returned, `"model"`.
  2. model text containing "fraud" -> falls back to the template.
  3. model text missing one chosen option -> falls back.
  4. `LLMError` -> falls back; a budget error -> falls back.
  5. `use_model=false` -> `llm.calls == []`.
  6. the user message contains the wrapped items sheet and no document text other than the gap subjects and option labels.
- [ ] **Step 2: Implement.** Prompt (do NOT `register_prompt` at import; store its sha on the response only): "Write a short, polite request in plain language asking for exactly these items. Do not mention checks, risk, suspicion, scores or internal systems. Do not add items. Reply with JSON {message}." Items go in via `wrap_untrusted(sheet, label="request_items")`, where `sheet` lists only option labels and the applicant name, from code. Request: `LLMRequest(purpose="workbench:followup", model_role="cheap", system=..., user=..., schema_name="FollowUp", json_schema={"type":"object","properties":{"message":{"type":"string"}},"required":["message"],"additionalProperties":False})` (CONFIRM schema keywords supported by `validate_json_schema`, core/llm.py:269). Meter: copy `custom_rules/author.py:_ask` (l.1098) with `max_calls=1`, `max_usd=0.02`, scope stage `"rules"`. No response cache (a draft is one officer's turn). Map `LLMBudgetExceeded` to fallback, not to an error.
- [ ] **Step 3: PASS. Step 4: Docs.** Add `workbench:followup` to the AI component table in `docs/ai-inventory.md` (model, purpose, data sent: gap subjects and option labels only, what the code verifies). `tests/test_compliance_docs.py` must still pass.
- [ ] **Step 5: Commit** `feat(workbench): optional model wording for follow-up requests`.

---

### Task 12: Acceptance on the real demo bundles

**Files:** Test `backend/tests/test_workbench_demo_cases.py`

- [ ] **Step 1: Write the test** using the same bundle builders and runner as `backend/tests/test_showcase_bundles.py:126` (challenger demo) and the KYC fraud bundle from `backend/scripts/_gen/bundle_kyc.py`, with the fake LLM backend:
  - Yishun v1: the evidence map has `payments` and `source_of_funds` rows not `found`, and gaps include `payer_buyer_unlinked` and `sof_unsupported` with `ask="applicant"`.
  - Yishun v2 (add `supply_agreement_C-2026-002.pdf`): `payer_buyer_unlinked:*` is in `resolved_gap_keys`.
  - Sembrook KYC fraud: `ownership` row is `review` or has a `holder_no_onward_owner` gap (CONFIRM from the bundle; if neither appears, report it and pick the right variant for the demo rather than changing detection).
  - Sembrook KYC clean: no `review` rows; with registry unavailable, `company_identity` is `unconfirmed`.
- [ ] **Step 2: Run.** Failures here are findings about the real data: fix `gaps.py` column names or `items.py` rule lists, or report that the demo bundle does not show what the script promises.
- [ ] **Step 3: Full suite, ruff, catalogue check, four frontend commands. Step 4: Commit** `test(workbench): demo bundles acceptance`.

**CUT LINE 1.** Stop and report: tests run, commands and their pass counts, screenshots at 390 and 1440 px of the Evidence tab for Yishun v1/v2 and Sembrook, anything that differed from this plan.
