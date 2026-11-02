# Monitoring and AI incidents

What the tool watches, who is accountable, and what to do when it misbehaves. Written for MAS AI Risk Management Guidelines 5.23; the thresholds are starting values, not calibrated on real cases.

## Accountable owner

`GLDB_AI_OWNER` holds the name and role of the person accountable for this AI system. `GET /monitoring` reports `owner_named: false` until it is set, and the bank must name that person; the tool cannot.

## Monitoring (`GET /monitoring`)

Over the cases updated in the last `GLDB_MONITOR_WINDOW_DAYS` (default 7), compared with fixed limits:

| Metric | Alerts when above | What it means |
|---|---|---|
| `partial_rate` | 30% (`GLDB_MONITOR_PARTIAL_RATE_MAX`) | cases where a stage failed, a page could not be read or the budget ran out |
| `check_error_rate` | 5% | cases where a check raised an error instead of running |
| `review_team_failure_rate` | 10% | cases where the challenger or case officer could not run |
| `daily_spend_ratio` | 80% of the daily cap | model spend today against `GLDB_MAX_USD_PER_DAY` |
| `rule_drift` | 25 points | the largest change in how often one rule fires, this window against the last |

With fewer than `GLDB_MONITOR_MIN_CASES` (default 10) cases in a window the answer is `unknown`, never an all-clear. The response also reports whether the data volume is encrypted (`disk_encryption`: FileVault on macOS, otherwise "unknown" and the platform's control applies), whether the application encrypts what it stores (`encryption_at_rest`: mode, active key id, whether a bounded sample of stored items is all encrypted, and a plain statement when it is off; see [encryption-at-rest.md](encryption-at-rest.md)) and today's spend against the cap. The thresholds are in `.env.example`; change them in the environment, not in code.

Nothing here sends an alert by itself. A person (or the bank's monitoring tool polling the endpoint) reads it; the review log's rule-effectiveness page (`GET /rules/effectiveness`) shows how reviewers decided on each rule.

## What counts as an incident

Any of: a flag or summary that was wrong in a way that could have changed a decision; a case that should have been flagged and was not; the model reading a document as an instruction; data sent where it should not go; a control that failed (second approver, caps, the gate); an alert in `GET /monitoring` that nobody can explain.

## Process

1. **Report.** `POST /monitoring/incidents` with a severity (`low`, `medium`, `high`), a summary of at least 10 characters and, if there is one, the case id. The report is written to the append-only audit log under the signed-in account. `GET /monitoring/incidents` lists them; one is `open` until another report names it in `resolves`.
2. **Contain.** Switch off what is misbehaving, without a redeploy of code: `GLDB_AGENTS=false` (review team), `GLDB_SEMANTIC=off` (model-based checks), `GLDB_VERIFY_SCAN_FLAGS=false` (second reading), or `GLDB_LLM_BACKEND=fake` (no model at all; the rule checks still run). Cases already analysed keep their provenance, so they can be re-run after the fix.
3. **Investigate.** Every case carries the ruleset version, model ids, prompt hashes, settings hash and a digest of its flags, and the audit log has who did what. Re-run the case; compare.
4. **Fix and record.** Fix, add a test, and report a `resolves` entry saying what was done. A change that alters detection goes through the validation report again.
5. **Tell.** Whether the incident must be reported to the regulator or to affected customers is the bank's decision; this tool does not file anything.

## What is still needed from the bank

A named owner (`GLDB_AI_OWNER`), the bank's own incident classification and escalation path, agreement on the thresholds, and a place where alerts land that someone watches.
