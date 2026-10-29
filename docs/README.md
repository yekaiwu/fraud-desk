# Docs index

Specs for the GLDB document-fraud analysis tool. Source plan: the master plan (not included in this repo) (section numbers such as "plan 4.1" refer to it). Read the three contract docs first; every other doc builds against them and must not contradict them (gaps go in a "Contract requests" list at the end of the doc that found them).

## Contracts (read first)

- [architecture.md](architecture.md): stack decisions D1-D6, repo layout, pipeline and stage contract, check framework and registries, extraction backends, storage, full uv dependency list, settings (`GLDB_*`), parallel-work rules, error policy, agent roster with file ownership.
- [data-model.md](data-model.md): exact Pydantic models, money/date conventions, exact `Document.fields` keys and `Table` columns for all 12 document types, mock registry JSON shape.
- [api.md](api.md): every HTTP endpoint with JSON shapes, status codes, queue filters, upload, job polling, review rules, delete-case, TypeScript types.

## Feature specs

- [ingest-and-extraction.md](ingest-and-extraction.md): hashing, page render, text layer, OCR/vision, `TextLayerExtractor`, `LLMExtractor`, cascade, per-document extraction (plan 5, 6, 8.5).
- [classification.md](classification.md): C1-C5 questions, rules classifier, local JEV-style stub, hosted fallback, guardrails and canaries (plan 5).
- [cost-efficiency.md](cost-efficiency.md): cost telemetry, cache, budget guard, model choice, the twelve levers (plan 6).
- [checks-financial.md](checks-financial.md): bank statement, invoice, financial statement and cross-document rules (plan 4.1-4.4).
- [checks-kyc-aml.md](checks-kyc-aml.md): ID, ownership, directors, source of funds, pass-through, name matching, entity graph (plan 4.6).
- [forensics.md](forensics.md): PDF and image forensics checks (plan 4.5).
- [scoring-and-queue.md](scoring-and-queue.md): findings grouping, score, coverage, lanes, ranked queue (plan 8.3, 8.4).
- [review-workflow-and-audit.md](review-workflow-and-audit.md): verdicts, mandatory reasons, second approver, audit log, case pack, retention, rule effectiveness (plan 7.2, 7.3).
- [frontend.md](frontend.md): queue, case page with highlighted regions, upload, effectiveness page, phone-width behaviour.
- [test-data-and-eval.md](test-data-and-eval.md): bundle generator, three demo bundles, tamper catalogue, metrics, ranking and fairness tests (plan 9, 0.5).
- [compliance.md](compliance.md): Singapore regulatory alignment, obligation to feature to evidence map, scope of the three compliance documents (plan 7).
- [deploy.md](deploy.md): self-hosting the app on an always-on machine: shared-login gate, same-origin `/api` proxy, backend proxy secret and rate limits, launchd install scripts in `deploy/`, security checklist.
- [anthropic-haiku.md](anthropic-haiku.md): the optional paid Anthropic backend (official SDK, Claude Haiku 5.5 only): setup, workspace id, request settings, pricing tiers, spend caps, privacy, refusals, which stages call the model.

## Work tracking

- [tasks.md](tasks.md): every work item by wave and agent, with status. Section 4.2 is the contract-request register; the R0 review outcomes (including the new items CR-72 to CR-80) are recorded there and at the top of each doc's "Contract requests" section.

## Delivered by later waves (created by the owning agent, not specs)

- [classifier-gate.md](classifier-gate.md): local classifier gate result (classifier-local, wave 2).
- [ai-inventory.md](ai-inventory.md): AI inventory entry and materiality (compliance-docs, wave 2).
- [regulatory-mapping.md](regulatory-mapping.md): instruments mapped to features (compliance-docs, wave 2).
- [check-catalogue.md](check-catalogue.md): generated from the registered checks (compliance-docs, wave 2).
- [validation-report.md](validation-report.md): skeleton in wave 2, filled with eval numbers in wave 3.
- [traceability.md](traceability.md): plan 0.4 matrix re-scored from the eval (eval, wave 3).
- [demo-script.md](demo-script.md): three bundles then the ranked queue (eval, wave 3).
- `eval-results/`: machine-written eval outputs (eval, wave 3).

## Ground rules in one paragraph

Python 3.12 with `uv` only; nobody edits `pyproject.toml` or `uv.lock` after the foundation; registries auto-discover new files so agents add files and never edit shared indexes; today's date is injected through `CaseContext.today`; all documents are untrusted input; all data is fictitious; nothing is auto-approved or auto-rejected; anything missing a check, a UI flag or an eval row is reported as a gap (plan 0.6). Do not git commit.
