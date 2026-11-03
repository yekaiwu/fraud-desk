# External verification sources

> Our reading as students, not legal advice. Decision support only; a human decides. All data in the demos is fictitious.

Five checks compare the documents with something outside the bundle: sanctions lists, GLEIF, OneMap, the IRAS GST register, and the form of an IBAN or BIC. All follow the pattern of the live company register (`backend/app/registry_live.py`, `docs/checks-kyc-aml.md` 4.5a).

## 1 The contract every source keeps

1. **Three kinds of answer.** A result; "the source says there is no such thing" (`None`, or an empty list); or `SourceUnavailable` (could not ask). Unavailable is never read as "not found" and never becomes a flag: the check is skipped, it counts as **not run**, and the case is marked **partial** (`gleif_unavailable`, `onemap_unavailable`, `iras_unavailable`, `sanctions_unavailable`, `sanctions_incomplete`).
2. **Opt-in.** Every source is off until it is configured (section 8). The network is asked once per case by `app/sources/runner.py::attach_sources`, before the rules run; the checks only read `ctx.sources` and never open a connection.
3. **Private mode.** `app/core/outbound.py::outbound_blocked(settings)` returns a reason when `GLDB_PRIVATE_MODE=on` (the setting is added by another branch; it is read with `getattr`, so this build works with or without it). Every adapter, the existing ACRA lookup and the sanctions download go through it. A blocked source is a skip with the code `private_mode`: never an error, never a flag. The sanctions *screen* reads a local file and is not blocked; only its update script is.
4. **Time and politeness.** Timeouts (12 s a call), two retries with back-off on 429/5xx only, one total budget per case (`GLDB_SOURCES_BUDGET_S`, 45 s), an honest user agent (`fraud-desk/0.1 (document-fraud research prototype)`).
5. **On-disk cache** under `<data dir>/verify-cache`, sealed by the same vault as the rest of the data when encryption at rest is on. A found record keeps a week, "none" a day, an unavailable answer is never stored. Tokens and keys are never written to the cache.
6. **No secrets in logs.** Failure messages carry a status code or an exception class, never a URL, header or response body. A test sets a recognisable token, key and password, fails every source in every way, and searches every log line and message for them.

### Coverage: what counts as "not run"

Coverage is `ran / listed` and the fast lane needs 0.80 (`docs/scoring-and-queue.md`). In wave 1 six new receipt rules listed for every bundle lowered a clean bundle from 48/55 to 48/61 and closed the fast lane. Here:

| Situation | Skip code | In coverage? |
|---|---|---|
| Source not configured (no switch, token, key or snapshot setting) | `not_configured` | no |
| Blocked by private mode | `private_mode` | no |
| Configured, but unreachable, stale or incomplete | none | yes: counts as not run, case partial |
| Checked, nothing found | (ran) | yes |

The codes live in `app/semantic/policy.py::NOT_ASKED_CODES` and are applied by `counts_in_coverage` and by the fast-lane gate. Four of the five checks are registered with `use_cases=frozenset()`, so they are listed **only when their document is present** (a profile for GLEIF and OneMap, an invoice for IRAS and the form check); `x_sanctions_screen` is listed for every use case but is `not_configured` until switched on.

On the generated clean loan bundle (`backend/tests/test_verification_coverage.py`): with all five checks registered and unconfigured, coverage is 49/56 (it was 48/55); the only addition is the offline form check, which has an invoice to read and ran. The fast lane stays open and no flag at medium or above appears. With GLEIF and OneMap switched on and answered by a fake transport the same bundle is 51/58, fast lane, no medium or high flag. With GLEIF returning 503 it is partial, not fast lane, the check counts as not run and there is no flag. In private mode with everything configured the numbers are those of the unconfigured run (the existing live-register check, if `GLDB_REGISTRY_LIVE` is on, also becomes a coded skip).

## 2 Sanctions and watchlist screening: `x_sanctions_screen`

### 2.1 The lists (official only)

`backend/scripts/update_sanctions.py` downloads the lists, parses them into one entry shape and writes one snapshot under `<data dir>/sanctions/` (`snapshot-<UTC time>.json`, and `current.json`, which the check reads). Every source records its URL, download time, sha256, byte count and record count. **Not used: OpenSanctions bulk data** (CC BY-NC; a bank needs a paid licence).

| List | URL | Format | Fetched live? |
|---|---|---|---|
| UN Security Council consolidated list | `https://scsanctions.un.org/resources/xml/en/consolidated.xml` | XML | yes, 2026-11-03 |
| OFAC SDN list | `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML` | XML (the old `treasury.gov/ofac/downloads/sdn.xml` path also answers) | yes |
| EU consolidated financial sanctions list | `https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw` (the public token printed in the EU's documentation) | XML 1.1, 26 MB, slow | yes |
| UK Sanctions List (FCDO) | `https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml` | XML | yes |
| UK OFSI consolidated list (`ofsistorage.blob.core.windows.net/publishlive/ConList.csv`) | | CSV | **no**: answered HTTP 409; the UK Sanctions List above is the UK's own current list and is used instead |
| MAS targeted financial sanctions (`mas.gov.sg/regulation/anti-money-laundering/targeted-financial-sanctions/lists-of-designated-individuals-and-entities`) | | web page | **reference only**: the page links to the UN Security Council lists (already in the snapshot) and offers no machine-readable list of its own; on 2026-11-03 it answered with a maintenance page. MAS's domestic designations are therefore **not screened** |

Formats and URLs change; the script reports a list it cannot fetch and keeps going. A source that fails is carried over from the previous snapshot with its **old** download time (so it goes stale on its own clock) or, with nothing to carry over, recorded as `failed`. The script exits 1 if a required list (UN, OFAC, EU, UK) is missing.

Counts in the snapshot built from the 2026-11-03 downloads: UN 1,007, OFAC 19,484, EU 6,269, UK 6,420 entries (33,180). Weak aliases (OFAC "weak", UN and UK "low quality") are counted and **not used**. Files are untrusted data: XML that declares a DTD or an entity is refused; parsing is incremental.

### 2.2 What is screened

The applicant company, directors, shareholders and owners (profile, register, chart), ID holders, the source-of-funds declarant and named counterparty, invoice, purchase-order and contract parties, and the bank account holder, at most 60 names a case, de-duplicated. A date of birth, nationality and ID number are taken from the passport or national ID of the same name, and from the director row.

### 2.3 The matcher (high precision)

A name is a weak key, and a list of 33,000 names is not a company register, so the matcher is deliberately narrower than the registry one (`backend/app/sources/sanctions.py`):

- A person matches when `app.names.match_person` says **same** (the same words in any order, joined or split words, known transliteration aliases such as Mohd/Muhammad) on at least **two** name words (three Chinese characters), or says **probable because of a one-letter slip** in a word of six letters or more. Its "initials", "the shorter name is contained in the longer" and "single name" verdicts are not used; a mononym is never screened on the name alone; `Ahmad bin Ismail` is not `Ismail bin Ahmad`.
- A company matches when its name without legal-form words is equal (or one word of six letters or more is one letter off); a one-word name under ten letters is not screened.
- Vessels and aircraft are not screened.

Levels (a match is **never a decision**; the check never blocks or approves anything):

| Level | When | Text |
|---|---|---|
| high | exact name **and** an identifier that agrees: an ID number, a full date of birth, or a year of birth with the nationality | "matches ... on the name and on at least one other identifier. Check the list entry against the person; this tool makes no decision" |
| medium | exact name alone; a name one letter off (with or without an identifier); an identifier alone (the name differs) | "Possible match, a person must adjudicate" |
| low | exact name, but the listed date of birth contradicts the document | probably a different person; kept visible, not hidden |

Evidence names the list, the entry reference, the listed name and the fields matched; ID numbers are masked. `x_sanctions_screen` is a hard-stop rule, so even a medium flag keeps the case out of the fast lane. It is not in the challenger's arguable list.

### 2.4 Stale or missing snapshot

`GLDB_SANCTIONS_SCREEN=on` with no snapshot, an unreadable one, a list missing from it, or a list older than `GLDB_SANCTIONS_MAX_AGE_DAYS` (7) is **not a clean result**: with no hit the check is skipped (counts as not run) and the case is partial; a hit that was found is still reported. With the switch off the check is `not_configured`.

### 2.5 Fairness: false positives by name group

Sanctions screening is a name-matching task, so it falls under `docs/name-fairness.md`. `backend/scripts/sanctions_fairness.py` measures how often a person who is on no list is told "possible match", with two kinds of pseudo-customer, no date of birth or ID (so any medium-or-higher hit counts as a false positive), against the real snapshot of 2026-11-03 (33,180 entries). Wilson 95% intervals.

**Fictitious customers** (every distinct name the generator draws for the TEST split):

| Group | Customers hit | Rate | 95% interval |
|---|---|---|---|
| western order | 0 / 751 | 0.0% | 0.0 to 0.5% |
| Chinese order | 0 / 859 | 0.0% | 0.0 to 0.5% |
| Malay patronymic | 0 / 976 | 0.0% | 0.0 to 0.4% |
| Indian patronymic | 0 / 849 | 0.0% | 0.0 to 0.5% |
| mononym | 0 / 466 | 0.0% | 0.0 to 0.8% |
| titled | 0 / 862 | 0.0% | 0.0 to 0.4% |

Gap between groups 0.0 (the worst group's upper bound is 0.8%).

**Real draws**: 3,000 random persons of the lists, each screened against the snapshot without their own record, without any record with the same name, and without records that share an identifier or a year of birth with theirs (the same person listed by two lists in two spellings is not a false positive). Grouped by the nationality the list gives (groups of at least 150 draws; the rest are one group). This is a stress test with the hardest names, not a customer population, and it is an upper bound: a listed person without a date of birth on either side stays counted, although many are the same person listed twice.

| Group (nationality on the list) | Hit | Rate | 95% interval |
|---|---|---|---|
| Iran | 2 / 155 | 1.3% | 0.4 to 4.6% |
| Russia | 9 / 526 | 1.7% | 0.9 to 3.2% |
| Russian Federation | 4 / 209 | 1.9% | 0.8 to 4.8% |
| other or not stated | 12 / 2,110 | 0.6% | 0.3 to 1.0% |
| all | 27 / 3,000 | 0.9% | |

Gap between highest and lowest group 1.3 points (bootstrap interval 0.8 to 3.5). The groups are the ones the data gives us; they are not a validated taxonomy or protected attributes, and the bank defines those. **Recall probe**: a listed person written in another order is found 298 of 300 times (99%); with one letter slipped 181 of 191 (95%, interval 91 to 97%). Both are on our own perturbations of list names, not real customer data.

What this does not show: a real customer population (a Singapore bank's customers include common names that happen to be listed, for example Arabic two-word names; those become medium flags that a person dismisses in seconds and are the price of screening), whether the cut-offs above are right for the bank, or any name type the generator does not draw. The regression test `backend/tests/test_sanctions_fairness.py` pins the numbers on a 500 KB offline fixture (`backend/tests/data/sanctions_fairness_fixture.json`: public official list records plus 900 fictitious customers and 600 draws) and fails when the matcher hits more people who are on no list, hits one group more than another, or stops finding a re-ordered or slipped listed name.

## 3 GLEIF: `x_entity_vs_gleif`

`api.gleif.org/api/v1/lei-records`, no key. The record is found by registered number (`filter[entity.registeredAs]` = the UEN, jurisdiction SG; GLEIF returns the registration number as `registeredAs`, for example Singapore Airlines `197200078R`), or, when there is none, by exact legal name (a record that names another registration number is rejected). Direct and ultimate parents are fetched when the record links them and shown as context.

| Finding | Severity |
|---|---|
| the profile's company name is not the legal name GLEIF holds (profile only; a certificate carries the name at incorporation) | medium |
| entity status INACTIVE | medium |
| the postal code of the registered address differs from the legal address | low |
| a parent company exists | info (context, no score) |
| no LEI | nothing: most companies have none |

Listed only when a profile or certificate is present; off until `GLDB_GLEIF=on`.

## 4 OneMap: `x_address_vs_registry`

Singapore Land Authority OneMap search (`onemap.gov.sg/api/common/elastic/search`). Needs a free token from developers.onemap.sg, which expires within days: set `GLDB_ONEMAP_TOKEN`, or `GLDB_ONEMAP_EMAIL` and `GLDB_ONEMAP_PASSWORD` and the token is fetched and renewed in memory (never written anywhere). Neither set = the check is off. The token is sent as `Authorization: Bearer <token>`; this form could not be verified without a token (the endpoint also answered an anonymous request on 2026-11-03, which we do not rely on).

The printed postal code (the one after "Singapore" first) is looked up. Findings: the postal code does not exist in OneMap (medium); the block or road printed is not the one OneMap holds for that code (medium; road abbreviations such as Rd, Ave, Jln are expanded and the unit number is ignored); the postal code differs from the registered address held in the registry, ACRA open data or the mock file (low: companies move). Listed only when a profile is present.

## 5 IRAS GST register: `inv_gst_registered`

**Built against a fake transport from the public listing; never run against the live service.** IRAS API Marketplace "Check GST Register" needs an account and a key; the portal documentation page answered "API Marketplace is unavailable" when we looked on 2026-11-03, so the request and response shapes in `backend/app/sources/iras.py` are our reading of the public description and may differ. Anything the reader cannot parse with certainty (an unknown status, a non-success `returnCode`) is `SourceUnavailable`, never "not registered". **Access terms for a bank's use are unconfirmed.** Disabled unless `GLDB_IRAS_API_KEY` is set (`GLDB_IRAS_API_SECRET` and `GLDB_IRAS_BASE_URL` optional).

The supplier's GST number, or its UEN when no GST number is printed, is asked. Findings: the invoice shows GST but the supplier is not on the register (medium); the register holds the number under a different name (medium); the invoice is dated after the registration was removed (medium) or before it began (low).

## 6 Offline bank-detail checks: `inv_bank_details_valid`

No network, no source, low severity. IBAN: country length (a table of 77 IBAN countries) and the ISO 7064 mod-97 check (`python-stdnum`); an IBAN-shaped string counts only when its country and length fit, and a string labelled "IBAN" is checked whatever it looks like. BIC/SWIFT: 8 or 11 characters with a real country, when labelled "BIC" or "SWIFT". A BIC and an IBAN printed together must name the same country. Read from the words of an invoice and from a statement's `account_number`. Nothing printed is a pass (the check ran), not a skip. **Singapore bank and branch codes are not checked**: no authoritative public table was verified for this build. The check cannot say a number is right, only that it cannot be; on a scan, an OCR slip in a printed IBAN looks the same as a wrong one (low severity, check the page image).

## 7 What still needs credentials, the bank, or a person

- OneMap token (free): not available in this build's environment, so the live OneMap path is untested; the `Bearer` form of the header is unverified.
- IRAS API Marketplace account, key, the live request and response shapes, and the terms of use for a bank.
- A decision by the bank on which lists, which cut-offs and which groups count for fairness; the screen is not an approved sanctions-screening system and does not replace one.
- A schedule for `update_sanctions.py` (a stale snapshot makes the check "not run").
- MAS domestic designations (no machine-readable list was found).
- Singapore bank and branch code tables for the form check.

## 8 Settings

| Setting | Default | Meaning |
|---|---|---|
| `GLDB_SANCTIONS_SCREEN` | `off` | screen against the local snapshot |
| `GLDB_SANCTIONS_SNAPSHOT` | empty | path; empty = `<data dir>/sanctions/current.json` |
| `GLDB_SANCTIONS_MAX_AGE_DAYS` | 7 | older = not run |
| `GLDB_GLEIF` | `off` | GLEIF lookup |
| `GLDB_ONEMAP_TOKEN`, `GLDB_ONEMAP_EMAIL`, `GLDB_ONEMAP_PASSWORD` | empty | OneMap; none = off |
| `GLDB_IRAS_API_KEY`, `GLDB_IRAS_API_SECRET`, `GLDB_IRAS_BASE_URL` | empty | IRAS; no key = off |
| `GLDB_SOURCES_BUDGET_S` | 45 | total seconds a case may spend asking these sources |
| `GLDB_PRIVATE_MODE` | (other branch) | `on` blocks every outbound call above |

A source that is on is listed in the settings hash of the case's provenance; one that is off changes nothing.

## 9 Tests

`test_sources_adapters.py` (GLEIF, OneMap, IRAS against fake transports: results, "none", every failure as unavailable, caching, token renewal, no secrets in logs, time budget), `test_sources_runner.py` (private mode blocks every source, the existing ACRA lookup included; unreachable sources make the case partial; snapshot states), `test_sanctions.py` (the four parsers on samples in the real formats, dates, matcher levels, snapshot staleness, update script with a fake transport), `test_checks_verification.py` (the five checks; not configured, private mode and unavailable never flag), `test_verification_coverage.py` (a full pipeline run of the clean loan bundle keeps its fast lane in every configuration), `test_sanctions_fairness.py`. No test touches the network.

## 10 Live smoke test (3 November 2026, once)

| Source | Result |
|---|---|
| `update_sanctions.py` against the real internet | UN 1,007, OFAC 19,484, EU 6,269 and UK 6,420 entries downloaded and parsed in 37 s (2.2, 29.3, 25.9 and 22.1 MB); snapshot of 33,180 entries written with URL, time and sha256 per list; no problem reported for a 7-day limit. The MAS page was fetched and recorded as a reference with its maintenance notice. The OFSI CSV (`ConList.csv`) answered HTTP 409 on an earlier look and is not used. |
| GLEIF (`GleifLookup`) | Singapore Airlines Limited found by UEN `197200078R`: LEI `549300ZG62N2599PWR15`, status ACTIVE, registered as `197200078R`, postal code 819829, no parent. A made-up UEN and name: none, not an error. |
| OneMap | **Not tested**: no token was available (`GLDB_ONEMAP_TOKEN` unset). The search endpoint answered an anonymous request for postal code 819829 (Airline House) while the file was being studied, but the token-authenticated path, the `Bearer` header form and the email/password renewal have not been run live. |
| IRAS GST register | **Not tested and not testable**: needs an API Marketplace account; the documentation page was down. |
| Sanctions screen on the live snapshot | Loads and screens; a re-ordered name of a listed person is found in the EU, OFAC and UK lists. |

Nothing was sent to a hosted model and nothing was spent.

## Where the lookups show on the case

A lookup that finds nothing wrong would otherwise leave no trace. Each of `x_entity_vs_registry` (the live ACRA register), `x_entity_vs_gleif` and `x_sanctions_screen` writes one sentence about what it asked and what came back into `CheckRun.note` (`ctx.run_notes`, moved onto the run by `run_stage_checks`), and the Coverage panel lists them under "Looked up outside the documents". The flags themselves name the source (`ACRA register: 2021-05-05`).

