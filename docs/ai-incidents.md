# Monitoring and AI incidents

What the tool watches, who is accountable, and what to do when it misbehaves. Written for MAS AI Risk Management Guidelines 5.23; the thresholds are starting values, not calibrated on real cases.

## Accountable owner

`GLDB_AI_OWNER` holds the name and role of the person accountable for this AI system. It is empty by default and the software never fills it in. `GET /monitoring` reports `owner_named: false` until it is set, and `GET /monitoring/readiness` lists it as missing. The bank must name that person; the tool cannot.

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

`GET /monitoring` only reads. Telling someone is the next section; the review log's rule-effectiveness page (`GET /rules/effectiveness`) shows how reviewers decided on each rule.

## What is automated now: alerts, acknowledgement and silence

| Piece | What it does | Switch |
|---|---|---|
| Evaluation | snapshot, then update the alert state, then deliver | `GLDB_MONITOR_INTERVAL_MINUTES` (0 = off, the default) runs it in the API process; or run `backend/scripts/monitor_once.py` from the bank's scheduler |
| Alert state | one row per metric (and `monitor_stale`): `open`, `acknowledged` or `resolved`, with first and last seen, last notified, value, threshold | automatic |
| Delivery | webhook, e-mail, Telegram, local file; each optional; a failing one is recorded and never blocks the others; bounded retries with back-off | `GLDB_ALERT_*` |
| Acknowledgement | `POST /monitoring/alerts/{key}/acknowledge` with a reason; written to the audit log | `GLDB_PERSONAL_ACCOUNTS` |
| Silence check | `monitor_stale` in `GET /monitoring`, and an alert on the next run | needs the schedule on |
| Readiness | `GET /monitoring/readiness`: the three 5.23 items, each met or not, with what is missing | always on |

**Rules, so nobody is spammed.** A metric that goes to `alert` opens an alert and is announced once. If it is still open and nobody acknowledged it after `GLDB_ALERT_RENOTIFY_HOURS` (default 24) it is announced again as a reminder. An acknowledged alert stays quiet. When the metric is back to `ok` the alert resolves and that is announced once. `unknown` (too few cases) is never announced as fine and never clears an open alert. If every sink fails, nothing is marked as announced and the next run tries again; with no sink configured alerts are still tracked (and shown in `GET /monitoring/alerts`) but no one is told. A medium or high incident (`POST /monitoring/incidents`) is also announced; the message gives severity, id and reporter but not the free-text summary, which stays in the audit log (nothing typed by an analyst is sent to an outside channel).

**Acknowledging.** `POST /monitoring/alerts/{key}/acknowledge` with `{"reason": "...", "reviewer": "name"}`. A signed-in account replaces a typed name. Set `GLDB_PERSONAL_ACCOUNTS=true` when the front uses personal accounts (`GLDB_SITE_USERS`): then a request without a signed-in account is refused (`403 account_required`). Incidents take a note and an assignee the same way (`POST /monitoring/incidents/{id}/acknowledge`); that does not close them, only a report with `resolves` does.

**Silence.** With a schedule on, every successful evaluation stamps the time. `GET /monitoring` reports `monitor_stale: true` when the stamp is older than two intervals (or, before the first run, the schedule has been on that long), and the next run raises a `monitor_stale` alert through the sinks, saying how long the monitor was silent. With the schedule off it says `scheduler: "off"` and a note; it is never shown as healthy. A process that is down cannot send anything: to catch that, the bank's own monitoring should poll `GET /monitoring` and fail when `monitor_stale` is true, or when the call itself fails.

### Wiring each sink

Put the values in the real `.env` (they are secrets; `.env.example` has the empty names). Then send a test: `POST /monitoring/alerts/test` (or `uv run python backend/scripts/monitor_once.py --test`) and check that a person receives it. Failures show up per sink in the answer and in `GET /monitoring/alerts` as an error class or `HTTP_<status>`; the URL, token and password are never logged, returned or stored.

| Sink | Settings | Notes |
|---|---|---|
| Webhook | `GLDB_ALERT_WEBHOOK_URL`, optional `GLDB_ALERT_WEBHOOK_SECRET` | POST of `{"text", "event", "key", "title", "sent_at", "alert": {...}}`. `text` is what a Slack or Teams incoming webhook shows. With a secret, `X-Gldb-Signature: sha256=<hmac-sha256 of the raw body>`; the body holds `sent_at`, so a receiver can reject old copies. Redirects are not followed |
| E-mail | `GLDB_ALERT_SMTP_HOST`, `_PORT` (587), `_STARTTLS`, `_USER`, `_PASSWORD`, `GLDB_ALERT_EMAIL_FROM`, `GLDB_ALERT_EMAIL_TO` (comma-separated) | STARTTLS by default; port 465 uses TLS from the start. Needs host, from and at least one recipient |
| Telegram | `GLDB_ALERT_TELEGRAM_BOT_TOKEN`, `GLDB_ALERT_TELEGRAM_CHAT_ID` | Create the bot with @BotFather, add it to the chat, take the chat id. The token sits in the request URL, so the HTTP client's own request log is silenced |
| Local file | `GLDB_ALERT_FILE_PATH` | JSON lines, appended. A record, not a way to tell anyone: it is only a place someone watches if the bank's tooling tails it |

Retries: `GLDB_ALERT_RETRIES` attempts per sink per message (default 3), waits of `GLDB_ALERT_RETRY_BACKOFF_S * 2^n`, `GLDB_ALERT_TIMEOUT_S` per attempt. A refusal such as 401 or 404 (wrong URL or credentials) is not retried.

### Running it from the bank's scheduler instead

```
GLDB_MONITOR_INTERVAL_MINUTES=15      # the cadence the scheduler runs at
GLDB_MONITOR_IN_PROCESS=false         # this API process starts no thread; the silence check watches the script
*/15 * * * *  cd /path/to/app && uv run python backend/scripts/monitor_once.py
```
Exit code 0 is done, 1 the evaluation failed, 2 done but a sink could not deliver. `--json` prints the result, `--test` sends a test alert.

## What counts as an incident

Any of: a flag or summary that was wrong in a way that could have changed a decision; a case that should have been flagged and was not; the model reading a document as an instruction; data sent where it should not go; a control that failed (second approver, caps, the gate); an alert in `GET /monitoring` that nobody can explain.

## Process

1. **Report.** `POST /monitoring/incidents` with a severity (`low`, `medium`, `high`), a summary of at least 10 characters and, if there is one, the case id. The report is written to the append-only audit log under the signed-in account. `GET /monitoring/incidents` lists them; one is `open` until another report names it in `resolves`. Medium and high reports are also announced through the alert sinks, and `POST /monitoring/incidents/{id}/acknowledge` records who has seen an incident and who it is assigned to.
2. **Contain.** Switch off what is misbehaving, without a redeploy of code: `GLDB_AGENTS=false` (review team), `GLDB_SEMANTIC=off` (model-based checks), `GLDB_VERIFY_SCAN_FLAGS=false` (second reading), or `GLDB_LLM_BACKEND=fake` (no model at all; the rule checks still run). Cases already analysed keep their provenance, so they can be re-run after the fix.
3. **Investigate.** Every case carries the ruleset version, model ids, prompt hashes, settings hash and a digest of its flags, and the audit log has who did what. Re-run the case; compare.
4. **Fix and record.** Fix, add a test, and report a `resolves` entry saying what was done. A change that alters detection goes through the validation report again.
5. **Tell.** Whether the incident must be reported to the regulator or to affected customers is the bank's decision; this tool does not file anything.

## Readiness for 5.23 (a) to (c): `GET /monitoring/readiness`

The same list is summarised under `readiness` in `GET /monitoring`. `ready` is true only when all three hold.

| Item | Met when | The software cannot check |
|---|---|---|
| (a) Accountable person named | `GLDB_AI_OWNER` is not empty | that it is the right person, or that they accept the role |
| (b) Thresholds approved | an approver recorded the current values with `POST /monitoring/thresholds/approve` (approver, role, note). The audit log keeps a hash of the seven threshold values (window days, minimum cases and the five limits); the approval counts only while the current values hash the same. Change one and it says "thresholds changed since approval" until someone approves again (only the latest approval counts, so going back to old values also needs a new approval) | that the values are sensible, or that the approver has the authority |
| (c) Alerts reach a watched place | at least one sink is configured and the last test alert was delivered by a sink that is configured the same way now, within `GLDB_ALERT_TEST_MAX_AGE_DAYS` (30) | that a person reads it. A local file alone passes the check but the answer says someone must watch it |

These are decisions for the bank. The software records them and checks that they are recorded and still current; it fills in nothing (no default owner, no default approver, no default thresholds).

## What is still the bank's decision

- **Who is accountable** (5.23(a)): the name and role in `GLDB_AI_OWNER`. Software can only show that the field is empty.
- **Which thresholds are right** (5.23(b)): the starting values here are not calibrated on real cases. The bank agrees them, and a named approver records that; the tool detects any later change.
- **Where alerts land and who reads them** (5.23(c)): which channel, which people, what they must do within what time. The tool delivers, tracks acknowledgement and tests the path; it cannot make anyone look.
- The bank's own incident classification and escalation path, and whether an incident is reported to the regulator or to customers.
