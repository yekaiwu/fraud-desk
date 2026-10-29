# AI inventory entry: GLDB document-fraud analysis tool
> Our reading as students, not legal advice. Decision support only; a human decides. All data is fictitious.

Marks as in [regulatory-mapping.md](regulatory-mapping.md): `[found]` read in a named source, `[found, secondary]` read in a secondary source, `[confirm]` a person must check. Statements about the tool name an artefact; anything not built is a gap. Measured numbers live only in [validation-report.md](validation-report.md).

## 1 Entry

| Field | Content |
|---|---|
| System name, version | GLDB document-fraud analysis tool; `app_version` and the current `ruleset_version` are returned by `GET /health` and printed in the header of `docs/check-catalogue.md` |
| Owner | Project team (named role: team lead). Business owner: to be confirmed with the sponsor |
| Purpose | Triage and explain document-fraud signals in KYC, AML source-of-funds and loan bundles (plan 2) |
| Process it supports | Corporate onboarding, source-of-funds review, SME and supply-chain lending |
| Users | Compliance analysts and credit officers |
| Decision role | Decision support and ranking only; no auto-approve, no auto-reject (plan principle 2) |
| Inputs | Company documents (PDF, JPEG, PNG), applicant name and UEN if typed. Data classes: company data; personal data (names, NRIC, addresses, DOB, passport MRZ) |
| Outputs | Flags with evidence, grouped findings, score with coverage, lane, case pack. None is shown to the applicant |
| Components | Table in section 2 |
| Out of scope | Trade Finance Registry; live ACRA, MyInfo Business or IRAS calls; training our own forgery model or detecting fully AI-generated documents (provenance hints only); live transaction monitoring and periodic KYC reviews; filing STRs, the visa use case, production hardening, multi-tenant security (plan 2) |
| Materiality | Proposed medium; reasoning in section 3 (our proposal, no sign-off) |
| Validation status | The `Status:` line of [validation-report.md](validation-report.md) (currently NOT VALIDATED until the eval has run) |
| Known limits | The "Known gaps and honest limits" section of each feature doc under `docs/` (for example architecture.md section 14 and api.md section 15), the Gap column of [regulatory-mapping.md](regulatory-mapping.md) section 2, and section 13 of the validation report |
| Review date, next review | Not set: the date is the `--today` of the last eval run, and none has run yet. Next review on any ruleset, prompt or model change |

## 2 AI and model components

| Component | Model or software | Provider and location | Data it receives | Minimisation and control | Where configured | Used in demo |
|---|---|---|---|---|---|---|
| Extraction (cascade) | Hosted LLM through an OpenAI-compatible API (OpenRouter or a provider), cheap and strong model roles | Third party, location unknown `[confirm]` | One page of text, or one grayscale page image at `GLDB_VISION_DPI`, only when the text layer fails validation or the page is a scan | Fictitious data only in demos; schema-constrained output with one retry; no tools; response cache; budget guard (`GLDB_MAX_*`); output cross-checked against the text layer | `GLDB_LLM_BACKEND`, `GLDB_LLM_BASE_URL`, `GLDB_LLM_API_KEY`, `GLDB_LLM_MODEL_CHEAP`, `GLDB_LLM_MODEL_STRONG`, `GLDB_LLM_TIMEOUT_S`, `GLDB_LLM_SCHEMA_RETRIES` | `fake` unless a key is set |
| Anthropic API (optional paid backend) | Claude Haiku 5.5 (`claude-haiku-5-5`, the only model that may be used) through the official `anthropic` SDK | Third party (Anthropic), location unknown `[confirm]` | The same payloads as the hosted LLM, including grayscale page images for scans (Haiku 5.5 reads images) | Opt-in only (`GLDB_LLM_BACKEND=anthropic`, never chosen by `auto`); the model is checked at start-up, at construction and on every call; **fictitious documents only**; schema-constrained output with one retry; no thinking blocks read; per-case budget guard and a global per-day dollar cap; refusals surface as an unread page, never a crash | `GLDB_LLM_BACKEND`, `GLDB_ANTHROPIC_API_KEY` (secret, never logged), `GLDB_ANTHROPIC_MODEL`, `GLDB_ANTHROPIC_WORKSPACE_ID`, `GLDB_ANTHROPIC_TIMEOUT_S`, `GLDB_MAX_USD_PER_DAY`, `GLDB_MAX_USD_PER_CASE` | No (off unless the owner opts in) |
| Classification C1-C5 | Rules (local code); optional local JEV-style model; hosted small LLM fallback | Local; hosted fallback as above | Page text, or counterparty description lines | Rules baseline first; local model only if it passes the gate; classifier cannot lower risk; hosted fallback only below `GLDB_CLASSIFIER_MIN_PROB` | `GLDB_CLASSIFIER_MODE`, `GLDB_LOCAL_CLASSIFIER_MODEL` | Rules |
| Rules and cross-document checks | Deterministic Python, no AI | Local | Extracted fields and tables | Versioned (`CheckMeta.version`); catalogue generated from the registry | none | Yes |
| Forensics | PyMuPDF, pikepdf, OpenCV, imagehash; no learned model | Local | The raw file | Local only | none | Yes |
| Name matching | rapidfuzz plus token rules; no learned model | Local | Names | Local only | none | Yes |
| Intermediary | OpenRouter, if used | Third party | The same payloads as the hosted LLM | Second third-party hop; a private-endpoint requirement is documented for production | `GLDB_LLM_BASE_URL` | No key available |

Settings that govern the hosted path: `GLDB_LLM_BACKEND` (`auto` uses the fake client unless `GLDB_LLM_API_KEY` is set), `GLDB_LLM_BASE_URL`, `GLDB_LLM_API_KEY` (secret, never logged), `GLDB_LLM_MODEL_CHEAP`, `GLDB_LLM_MODEL_STRONG`, `GLDB_LLM_TIMEOUT_S`, `GLDB_LLM_SCHEMA_RETRIES`. Artefacts: `backend/app/core/llm.py` (`get_llm_client`, `OpenAICompatClient`, `FakeLLMClient`), `backend/app/core/costs.py` (`CostMeter`), `backend/app/classify/local.py`.

Settings for the Anthropic backend (see [anthropic-haiku.md](anthropic-haiku.md)): `GLDB_LLM_BACKEND=anthropic` (explicit opt-in), `GLDB_ANTHROPIC_API_KEY` (secret, never logged), `GLDB_ANTHROPIC_MODEL` (must be exactly `claude-haiku-5-5`, anything else stops the application at start-up), `GLDB_ANTHROPIC_WORKSPACE_ID` (sent as the `anthropic-workspace-id` header when the key is user-scoped), `GLDB_ANTHROPIC_TIMEOUT_S`, and the spend caps `GLDB_MAX_USD_PER_CASE` and `GLDB_MAX_USD_PER_DAY` (all cases together per UTC day; `0` disables; paid backends only). Page text and page images go to Anthropic, a third party, so this backend is for fictitious documents only. Artefacts: `backend/app/core/llm_anthropic.py` (`AnthropicLLMClient`), `backend/app/core/costs.py` (`DailySpend`).

Settings for the self-hosted alternative (the owner's own machine, see [local-llm.md](local-llm.md)): `GLDB_LLM_ENABLE_THINKING`, `GLDB_LLM_SUPPORTS_VISION` (`false` for a text-only model: no image is ever sent, scans are read by on-device OCR), `GLDB_LLM_MAX_CONCURRENCY`, `GLDB_LLM_MAX_OUTPUT_TOKENS`, `GLDB_LLM_RETRY_503_ATTEMPTS`, `GLDB_LLM_RETRY_503_BACKOFF_S`, `GLDB_LLM_RETRY_503_MAX_WAIT_S`. With that setup page text goes over the owner's tailnet to the owner's machine and to no third party; the "third party" and "location unknown" entries above then do not apply to the extraction and classification paths.

- Provider data-retention and training terms are unknown for the chosen provider `[confirm]`.
- The licences of the base weights of the local model and of PyMuPDF (AGPL or commercial) must be checked before any production use `[confirm]`.
- Pseudonymisation of outbound text is **not** implemented, only minimisation (one page at a time, never other documents, no tools).

## 3 Materiality assessment

Proposed rating: **medium**. The output informs but never makes decisions, volumes are small for a wholesale bank and errors are reversible by a human; but errors can mislead credit and AML decisions and the system touches personal data and customer information. The dimensions are our working set; the Guidelines' own dimensions are `[confirm]` because only the headline structure was read. GLDB's method is unknown (plan 11 Q9).

| Dimension | Assessment | Mitigation in the tool |
|---|---|---|
| Impact on customers and bank decisions | Informs approve / refer / reject; a missed forgery can cause loss, a false flag can delay a customer | No automation; ranking; second approver; mandatory reasons (`backend/app/review.py`) |
| Reliance and automation bias | Analysts may trust a low score | Coverage beside the score; "silence is not safety"; not-checked list (`backend/app/pack.py::not_checked_documents`) |
| Complexity and opacity | Deterministic rules plus an LLM used only for extraction | Rules explain themselves; model output is cross-checked against the text layer |
| Data sensitivity | Personal data and customer information | Fictitious demo data; minimisation; retention; pseudonymised logs (`backend/app/retention.py`, `backend/app/audit.py`) |
| Reversibility | Decisions are human and logged; case versions kept | Audit log and review log |
| Scale | Small compliance team, digital-only bank | Ranked queue; cost telemetry (`backend/app/core/costs.py`) |

## 4 MAS Guidelines, by area

Published 7 Oct 2026, effective 7 Oct 2027. Areas 1 and 2 apply by 7 Oct 2027, areas 3 and 4 by 7 Oct 2028 `[found]`. Area names `[found]`; every clause-level expectation `[confirm]` until the 648 KB PDF is read.

### Area 1: Board and senior management oversight

| Expectation (headline) | What the project provides | Artefact | Gap |
|---|---|---|---|
| Accountable humans oversee AI `[confirm]` | Owner role and sign-off table in this entry and in the validation report; a verdict workflow that keeps accountability with named humans (O2) `[confirm]` | `backend/app/review.py`, `backend/app/core/models.py::ReviewEntry` | No board, no real governance: placeholder only |

### Area 2: AI risk management systems, policies and procedures

| Expectation (headline) | What the project provides | Artefact | Gap |
|---|---|---|---|
| Inventory, materiality, policies `[confirm]` | This inventory; materiality assessment (section 3); third-party register (section 2); risk table (section 6); change history | `docs/ai-inventory.md` | GLDB template unknown (plan 11 Q9) |

### Area 3: AI life-cycle controls

| Expectation (headline) | What the project provides | Artefact | Gap |
|---|---|---|---|
| Data, development, validation, deployment, monitoring, change, retirement `[confirm]` | Data: fictitious generator. Development: `docs/` specs and tests. Validation: eval and the validation report. Deployment: CI. Monitoring: rule effectiveness page. Change: ruleset version in provenance. Retirement: delete-case and retention | `backend/scripts/make_bundle.py`, `.github/workflows/ci.yml`, `backend/app/effectiveness.py`, `backend/app/core/provenance.py::build_provenance`, `backend/app/retention.py` | No production monitoring beyond the effectiveness page; eval not yet run (NOT BUILT: backend/scripts/eval.py) |

### Area 4: AI capability and capacity

| Expectation (headline) | What the project provides | Artefact | Gap |
|---|---|---|---|
| Skills and resourcing for AI `[confirm]` | Documentation and runbooks (`README.md`, demo script); analyst capacity addressed by the ranked queue | `README.md`, `backend/app/queue.py` | No training programme; skills inventory absent; NOT BUILT: docs/demo-script.md |

## 5 Model risk checklist

MAS Information Paper on AI Model Risk Management (5 Dec 2024): area names `[found]`; detailed practices `[confirm]` until the paper is read.

| Good-practice area | Evidence | Artefact | Mark |
|---|---|---|---|
| Governance | Verdict workflow, named reviewers, this inventory (O2, O6) | `backend/app/review.py`, `docs/ai-inventory.md` | `[found]` area, `[confirm]` practices |
| Key processes | Reproducible runs and labelled validation (O4, O5) | `backend/app/core/provenance.py::build_provenance`, `backend/scripts/make_bundle.py` | `[found]` area, `[confirm]` practices |
| Development and deployment | Version stamps, CI, change control through ruleset version (O4, O13) | `.github/workflows/ci.yml`, `backend/app/checks/registry.py::CheckMeta` | `[found]` area, `[confirm]` practices |
| Generative AI | Schema-constrained output, no tools, canaries, "model copies values, never corrects" (O7, O10) | `backend/app/core/llm.py::OpenAICompatClient`, `backend/app/extract/cascade.py`, `backend/tests/test_classify_canaries.py` | `[found]` area, `[confirm]` practices |
| Third-party AI | Register in section 2, fake client by default, budget guard (O7) | `backend/app/core/llm.py::get_llm_client`, `backend/app/core/costs.py::CostMeter` | `[found]` area, `[confirm]` practices |

## 6 Risks and controls

The full risk table is plan 12. The risks that touch AI governance, with the control that exists:

| Risk (plan 12) | Control that exists | Artefact |
|---|---|---|
| Rules fire on honest quirks | Tolerances as settings; both GST methods accepted; false-alarm rate to be measured by the eval | `backend/app/core/config.py::Settings.money_tolerance` |
| JEV-style models are new, licences unstated, weak under injection | Rules baseline; canaries; classifier cannot lower risk; local model adopted only after a gate | `backend/app/classify/stage.py`, `backend/tests/test_classify_canaries.py` |
| Cheap model escalation hides real mismatches | Both readings kept in evidence | `backend/app/extract/cascade.py` |
| Sensitive data sent to a third-party model | Fictitious demo data; fake client by default; one page at a time; budget guard; private endpoint required for production | `backend/app/core/llm.py::get_llm_client` |
| Compliance paperwork eats build time | Three short documents plus a generated catalogue | `backend/scripts/gen_check_catalogue.py` |
| Hand-made tampers and a ranked queue on hand-made data look better than they are | Results labelled as an upper bound; hard case and misses reported | `docs/validation-report.md` |

## 7 Open questions for the sponsor

- Q9: Does GLDB have an AI inventory or risk-materiality template we should fill in? Which internal policy governs escalating a suspected forgery for an STR assessment?
- Q10: May we use a hosted model with fictitious documents for the demo, or must everything run locally?
- Q11: For the demo, may we use a mock registry in an ACRA-profile shape, or can GLDB share a sample extract?

## 8 Change history

| Version | Date | Change |
|---|---|---|
| 0.1 | not set (no eval run yet) | First draft written by the compliance-docs agent from the specification in `docs/compliance.md`; no mark upgraded from the plan |
