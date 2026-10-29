# KYC and AML checks

Build spec for plan 4.6 (KYC and AML rules, deck use cases A and B), plus name matching (4.6 note, 7.2 fairness), the in-case entity graph (0.4 theme 4) and the NRIC/FIN algorithm (1.4). Contracts: [architecture.md](architecture.md) sections 3, 4, 11; [data-model.md](data-model.md) sections 2, 4, 6, 8. Scoring and lanes: [scoring-and-queue.md](scoring-and-queue.md). All data is fictitious. Do not git commit.

## 1. Purpose

Turn the KYC and AML documents of a bundle into deterministic, explainable flags: forged or malformed IDs, ownership and director records that disagree with the registry or with each other, a declared business that the bank statements contradict, a source-of-funds claim the credits do not support, a payer that is not the invoice customer, and money that passes through the account to other entities. Findings feed three outcomes (plan 7.3): refer for EDD (KYC), escalate for AML investigation (STR assessment), reject or escalate (loan). The tool recommends; a person decides.

## 2. Scope

| In (plan ref) | Out |
|---|---|
| 11 rule ids in section 3.1 (4.6) | Filing STRs; live ACRA/MyInfo/IRAS lookups (plan 2) |
| NRIC/FIN format and check letter (1.4); passport MRZ check digits, ICAO 9303 TD3 | M-prefix FIN check letters (table unconfirmed, plan 1.4); non-TD3 travel documents (TD1/TD2, visas) |
| Name matching with token sets, titles, patronymics, Chinese order, mononyms; fairness slice test (4.6, 7.2) | Script transliteration (Chinese characters to pinyin), dialect or pinyin surname equivalence, phonetic matching |
| In-case entity graph and `bs_pass_through` (4.6, 0.4 theme 4) | Cross-case graph, community detection, graph UI |
| Mock registry comparison only (`ctx.registry`, data-model.md section 8) | Board resolution, proof of address, business licences, wealth evidence (listed "received, not checked", plan 0.4) |

Not here: invoice/FS/bank arithmetic and `x_invoice_vs_receipts`, `x_revenue_vs_credits`, `x_entity` ([checks-financial.md](checks-financial.md)); file forensics on ID images ([forensics.md](forensics.md)); grouping and hard stops ([scoring-and-queue.md](scoring-and-queue.md)).

## 3. Interfaces

### 3.1 Registered checks

All use `@check(..., version="1")`, take `ctx: CaseContext`, return `list[Flag]` built only with `make_flag`, and read only the fields named in data-model.md section 6. "Lane" = the outcome lane the finding feeds (scoring doc 4.5): `edd` for kyc cases, `loan` for loan cases; the three AML rules route any case to `aml` (`AML_ROUTING_RULES`).

| Rule | Module | Stage | Severity | `applies_to` (any-of) | `requires` (all-of) | `use_cases` | Lane | Hard stop (scoring) |
|---|---|---|---|---|---|---|---|---|
| `id_format` | kyc.py | rules | high | `national_id` | | kyc, aml, loan | use-case lane | by severity |
| `id_checksum` | kyc.py | rules | high | `national_id` | | kyc, aml, loan | use-case lane | yes |
| `passport_mrz` | kyc.py | rules | high | `passport` | | kyc, aml, loan | use-case lane | yes |
| `date_valid` | kyc.py | rules | high | `national_id`, `passport` | | kyc, aml, loan | use-case lane | by severity |
| `kyc_shareholdings` | kyc.py | rules | high | `shareholder_register`, `ownership_chart` | | kyc | edd | by severity |
| `x_ownership_vs_registry` | kyc.py | crossdoc | high | `ownership_chart`, `shareholder_register`, `company_profile` | | kyc | edd | yes |
| `x_directors` | kyc.py | crossdoc | high | `company_profile` | `company_profile` | kyc | edd | yes |
| `x_declared_vs_activity` | kyc.py | crossdoc | medium | `company_profile` | `company_profile`, `bank_statement` | kyc | edd | yes |
| `x_sof_support` | aml.py | crossdoc | high | `source_of_funds_declaration` | `source_of_funds_declaration`, `bank_statement` | aml | aml | yes |
| `x_remitter_vs_buyer` | aml.py | crossdoc | high | `invoice` | `invoice`, `bank_statement` | aml, loan | aml | yes |
| `bs_pass_through` | aml.py | rules | medium | `bank_statement` | | aml, loan | aml | yes |

`description` and `tamper` strings (catalogue columns): `id_format` "NRIC/FIN has the form S/T/F/G/M + 7 digits + letter" / "modified IDs"; `id_checksum` "Check letter matches the 7 digits" / "modified IDs"; `passport_mrz` "MRZ check digits and visual fields agree" / "modified passports"; `date_valid` "Dates are real, not future, plausible age" / "modified IDs"; `kyc_shareholdings` "Shareholdings sum to 100% and holders above the UBO threshold are named" / "altered shareholder records"; `x_ownership_vs_registry` "Chart and register match the registry" / "ownership chart omits a shareholder"; `x_directors` "Directors match the registry and the ID documents" / "director not found in registry"; `x_declared_vs_activity` "Declared business against counterparty activity" / "declares logistics, statements show crypto"; `x_sof_support` "Declared source-of-funds amount is supported by credits" / "false source-of-funds evidence"; `x_remitter_vs_buyer` "The payer of an invoice is its customer" / "invoice customer differs from remitter"; `bs_pass_through` "Credits forwarded to other entities within days" / "funds routed through multiple companies".

### 3.2 Shared modules

```python
# backend/app/names.py
NameVerdict = Literal["same", "probable", "different", "indeterminate"]
IdentityMatch = Literal["match", "match_name_conflict", "mismatch", "unknown"]

@dataclass(frozen=True)
class PersonName:
    raw: str; tokens: tuple[str, ...]            # cleaned, alias-mapped, original order, patronymic marker removed
    given: tuple[str, ...]; patronym: tuple[str, ...]
    optional: tuple[str, ...]                    # tokens that were in brackets: "Wong Mei Ling (Jessica)"
    script: Literal["latin", "cjk", "mixed", "empty"]

@dataclass(frozen=True)
class NameMatch:
    verdict: NameVerdict; score: float           # 1.0 same, 0.8 probable, 0.0 different, 0.5 indeterminate
    reason: str                                  # short, goes into evidence
    a: str; b: str                               # canonical forms compared

def normalise_person(name: str | None) -> PersonName: ...
def match_person(a: str | None, b: str | None) -> NameMatch: ...
def match_company(a: str | None, b: str | None, *, allow_truncation: bool = True) -> NameMatch: ...
def entity_key(name: str | None, kind: Literal["person", "company", "auto"] = "auto") -> str: ...
    # lower-case, tokens sorted, joined by "_", characters [a-z0-9_] only ("" for empty). THE discriminator form (scoring doc 4.1)
def normalise_id(s: str | None) -> str: ...      # upper-case, spaces and hyphens removed
def same_identity(a_name: str | None, a_id: str | None, b_name: str | None, b_id: str | None, *, kind: Literal["person", "company"] = "person") -> IdentityMatch: ...
def mask_id(s: str) -> str: ...                  # "S8222222A" -> "S****222A" (first char, "****", last 4)
def pct_tol(*cells: Cell) -> Decimal: ...        # rounding-aware percent tolerance (4.3)
def union_region(boxes: Sequence[Region | None]) -> Region | None: ...   # bounding box on one page, label None
def cell_region(cell: Cell | None, row: Row | None = None) -> Region | None: ...   # cell.box, else row.box, else None

# fairness slice test (section 4.7)
class SlicePair(NamedTuple): group: str; a: str; b: str; same_person: bool; expect: tuple[NameVerdict, ...]
class SliceRow(BaseModel): group: str; same_pairs: int; false_flag_rate: float; probable_rate: float; indeterminate_rate: float; baseline_false_flag_rate: float; diff_pairs: int; false_accept_rate: float; baseline_false_accept_rate: float
def slice_pairs(seed: int = 4002) -> list[SlicePair]: ...
def run_slice_test(matcher: Callable[[str, str], NameVerdict] | None = None, seed: int = 4002) -> list[SliceRow]: ...   # default matcher: match_person().verdict

# backend/app/graph.py
APPLICANT_KEY = "@applicant"
class Entity(NamedTuple): key: str; display: str; kind: Literal["applicant", "company", "person", "unknown"]; uen: str | None
class Flow(NamedTuple):
    src: str; dst: str; direction: Literal["in", "out"]; date: date; amount: Decimal; currency: str
    document_id: str; row: int; counterparty_raw: str; c4: str | None; c5: str | None
class Hop(NamedTuple): credit: Flow; debit: Flow; gap_days: int; diff: Decimal
class PassThrough(NamedTuple): hops: list[Hop]; entities: list[str]; credit_total: Decimal; credits_total_all: Decimal; share: float; circular: list[str]
def counterparty_of(row: Row) -> str | None: ...        # 4.6
class EntityGraph:
    g: "networkx.MultiDiGraph"                           # nodes: Entity.key with attr "entity"; edges keyed, attr "kind" in flow|invoice|owns|director_of
    @classmethod
    def build(cls, ctx: CaseContext) -> "EntityGraph": ...
    def resolve(self, raw: str, uen: str | None = None, *, kind: str = "unknown") -> Entity: ...
    def flows(self, direction: Literal["in", "out"] | None = None) -> list[Flow]: ...   # sorted by (date, numeric document id, row)
    def pass_through(self, *, window_days: int, tol_pct: Decimal, min_amount: Decimal,
                     min_pairs: int, min_entities: int, tolerance_abs: Decimal) -> PassThrough | None: ...
    def summary(self) -> dict: ...                        # {"nodes": [...], "edges": [...]} plain JSON, amounts as decimal strings
```

Dependencies on other stages (assumed, not owned): `transactions` rows carry annotations `counterparty` (always), `c4` (credits), `c5` (all rows) from extraction/classification (data-model.md section 6.1). A row missing an annotation is treated as unknown; a rule that needs annotations and finds none raises `CheckSkipped`.

### 3.3 Flag examples

```json
{"id": "id_checksum.d7.id_number", "rule": "id_checksum", "stage": "rules", "severity": "high", "document_ids": ["d7"],
 "table": null, "field": "id_number", "row": null,
 "region": {"file_id": "f9", "page": 1, "x": 0.31, "y": 0.58, "w": 0.2, "h": 0.03, "label": "id_number"}, "extra_regions": [],
 "evidence": "ID S****222A: printed check letter A, computed G (prefix S, weighted digit sum 70, 70 mod 11 = 4)",
 "explanation": "The check letter on this NRIC does not match its digits (A printed, G expected). The ID number is not well-formed."}
```
```json
{"id": "x_ownership_vs_registry.d4.-~lim_siew_hoon", "rule": "x_ownership_vs_registry", "stage": "crossdoc", "severity": "high",
 "document_ids": ["d4"], "table": "holders", "field": null, "row": null,
 "region": {"file_id": "f4", "page": 1, "x": 0.08, "y": 0.30, "w": 0.84, "h": 0.22, "label": "holder missing here"}, "extra_regions": [],
 "evidence": "registry holds Lim Siew Hoon 30.00% (S****321B); not on ownership chart d4 (3 holders: 100.00% printed)",
 "explanation": "Lim Siew Hoon holds 30.00% per the registry but is missing from the ownership chart. The chart's other holders were scaled to 100%."}
```
(`make_flag` yields ids as `<rule>.<docs>.<locator>[~disc]`; the locator is `-` when the flag has neither a `field` nor a `row`, as for the table-level flag above.)

## 4. Behaviour spec

### 4.1 Conventions for every rule

- Documents in numeric id order (`d2` before `d10`); rows in printed order; output order = (document, row, discriminator). No dict-order dependence, no randomness.
- Cell values are read with `Cell.money()`, `date_()`, `int_()`, `text()`. `value is None` and `raw is None` = not read: skip that item, never flag it (an extraction gap is shown by extraction warnings and coverage). `value is None` and `raw` set on a **date** cell = unparseable or impossible date (data-model.md section 1); only `date_valid` treats that as a finding.
- A check raises `CheckSkipped(reason)` when nothing could be evaluated (no readable id, registry missing, currency mismatch, no annotations). Partial evaluation returns flags for what it could judge.
- `Cell.verified is False` on any value used in a flag adds `; value not confirmed in the text layer` to the evidence; the flag still stands.
- IDs in evidence are masked with `mask_id` (NRIC numbers are personal data; PDPA handling is [confirm], plan 7.1). The page highlight shows the real value. Names in evidence are as printed, passed through `_safe` (strip control characters, collapse whitespace, truncate to 80 chars; extracted text is untrusted, architecture.md section 11).
- Region: the offending `Cell.box`; else `Row.box`; else `union_region` of the table's row boxes; else `None`. Related places go to `extra_regions` (cap 6).
- Discriminators (scoring doc contract request): `make_flag(discriminator=entity_key(<name>))` for `kyc_shareholdings` (holder-level flags only), `x_ownership_vs_registry` (holder name), `x_directors` (director name), `x_remitter_vs_buyer` (remitter name). Others: none.
- First sentence of `explanation` stands alone, carries the key numbers, no file text beyond names; max 300 chars.
- Tolerances: money `ctx.settings.money_tolerance` (1.00). Percent: `pct_tol` (4.3). Dates exact.
- Currency: amounts compare only within one currency; otherwise `CheckSkipped("currency mismatch")`.
- All thresholds below are module constants named in capitals; they are **starting guesses** recalibrated from the eval (plan 4 intro, 9).

### 4.2 ID rules (`national_id`, `passport`)

**NRIC/FIN algorithm** (plan 1.4; implemented in `kyc.py` as `nric_check_letter(prefix, digits) -> str`). **python-stdnum has no NRIC module** (verified at R0: `stdnum.sg` contains only `uen`), so there is no library oracle; the tests pin the hand-computed vectors below, and the generator's independent implementation (`_gen/ids.py`) is pinned to the same vectors:

1. Normalise: `normalise_id` (upper-case, strip spaces/hyphens).
2. Weights `(2, 7, 6, 5, 4, 3, 2)` on the 7 digits; `total = sum(d_i * w_i)`.
3. Prefix offset: `+4` for `T` and `G`; `0` for `S` and `F`.
4. `r = total mod 11`.
5. Letter: `"JZIHGFEDCBA"[r]` for `S`/`T`; `"XWUTRQPNMLK"[r]` for `F`/`G`.
6. `M` prefix: **not verified** (table unconfirmed). Format passes; checksum is skipped for that ID.

Worked vectors (re-computed at R0, use verbatim in tests): `S1234567D` (total 106, r 7, `D`); `T0812345J` (106+4=110, r 0, `J`); `F1234567N` (106, r 7, `N`); `G1234567X` (110, r 0, `X`); `S8222222G` (70, r 4, `G`); `S0000001I` (2, r 2, `I`). `S8222222A` has the right shape and the wrong letter (expected `G`), and is also the "letter touching digits is not an amount" example (data-model.md section 2).

| Rule | Logic | Flags |
|---|---|---|
| `id_format` | For each `national_id` doc with `id_number.value` not None: must match `^[STFGM]\d{7}[A-Z]$` after normalisation | high, field `id_number`; evidence `ID 'S12345' does not have the form S/T/F/G/M + 7 digits + letter` (masked if long enough) |
| `id_checksum` | For each ID that passes `id_format` and has prefix in `STFG`: compare printed letter with the computed one | high, field `id_number`; evidence in 3.3. IDs failing the format are not re-flagged here. All IDs `M`-prefixed or unreadable: `CheckSkipped("no S/T/F/G id to verify")` |
| `date_valid` | See below | high |

`date_valid` (both `national_id` and `passport`), one flag per failed condition, field = the date field:
- **Real calendar date:** `date_of_birth`, `date_of_issue`, `expiry_date` (`date_of_expiry` on passports) with `value is None` and `raw` set: evidence `printed '31 Feb 1990' is not a real calendar date`.
- **Not in the future:** `date_of_birth > ctx.today`, `date_of_issue > ctx.today`.
- **Plausible age:** `age = whole years from DOB to ctx.today` must satisfy `MIN_AGE <= age <= MAX_AGE` (`MIN_AGE = 16`, `MAX_AGE = 100`; ids are issued from age 15 and directors must be 18, so 16 is the lenient floor).
- **Order:** `date_of_issue` strictly after `date_of_birth`; `expiry` not before `date_of_issue`.
- **NRIC prefix vs birth year (`national_id` only):** prefix `S` with birth year >= 2000 is flagged (S numbers are issued to people born before 2000). Prefix `T` with an older birth year is **not** flagged: the issuing rules for that combination were not confirmed (plan 13), so the conservative choice is silence, stated in gaps.
- An expired document is **not** flagged (a currency issue, not forgery); stated in gaps.

**`passport_mrz`** (TD3, ICAO 9303 part 4, 2 lines of 44 characters; fields `mrz_line1`, `mrz_line2`, whitespace stripped, nothing "corrected").

Check digit: character values `0-9 -> 0-9`, `A-Z -> 10-35`, `< -> 0`; weights `7, 3, 1` repeating from the first character; digit = `sum(value * weight) mod 10`.

| Line 2 (0-based) | Content | Check digit at |
|---|---|---|
| 0-8 | passport number (padded with `<`) | 9 |
| 10-12 | nationality (3 letters) | none |
| 13-18 | date of birth `YYMMDD` | 19 |
| 20 | sex `M`, `F` or `<` | none |
| 21-26 | expiry `YYMMDD` | 27 |
| 28-41 | personal number | 42 (may be `<` or `0` when the personal number is all `<`) |
| 43 | composite check over `line2[0:10] + line2[13:20] + line2[21:43]` | itself |

Line 1: `[0]` = `P`; `[1]` any filler/letter; `[2:5]` issuing state; `[5:44]` = `SURNAME<<GIVEN<NAMES` padded with `<`.

Steps and flags (all high, field `mrz_line1` or `mrz_line2`, discriminator = the element name; at most 8 flags per passport; if the length test fails, only that flag is emitted):
1. Each line is exactly 44 characters of `[A-Z0-9<]`; else one flag `MRZ line 2 has 43 characters, expected 44` (or `contains characters outside A-Z 0-9 <`).
2. Each of the five check digits (passport number, DOB, expiry, personal number, composite): mismatch -> flag `date of birth check digit: printed 7, computed 3`.
3. Visual zone vs MRZ (the main tamper: visual edited, MRZ left alone, or the reverse): `passport_number` vs `line2[0:9]` with trailing `<` removed; `date_of_birth` vs `line2[13:19]` (century: 20yy if that is not after `ctx.today`, else 19yy); `date_of_expiry` vs `line2[21:27]` (century 20yy); `sex` (`X` equals `<`); `nationality` and `issuing_country` vs `line2[10:13]` and `line1[2:5]`; surname and given names (`<<` splits surname from given names, `<` becomes a space) vs fields `surname`, `given_names` using `match_person` (diacritics folded; verdict `different` flags, `probable` does not, because the MRZ truncates at 39 characters). Evidence `visual date of birth 1985-03-12, MRZ 850318`.
4. `mrz_line1`/`mrz_line2` missing (both `None`): `CheckSkipped("no mrz extracted")` when no passport had them; a single passport without them is not flagged.

Vectors (computed): ICAO specimen `L898902C36UTO7408122F1204159ZE184226B<<<<<10` with `P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<` (check digits 6, 2, 9, 1, composite 0: all valid). Fictitious Singapore passport: line 1 `P<SGPTAN<<WEI<MING<<<<<<<<<<<<<<<<<<<<<<<<<<`, line 2 `K1234567D9SGP8503127M3106154<<<<<<<<<<<<<<<8` (digits 9, 7, 4, filler, composite 8). Tampered DOB `K1234567D9SGP8503187M3106154<<<<<<<<<<<<<<<8`: DOB digit printed 7, computed 3, and composite printed 8, computed 0 (two flags). Tampered number `K1234568D9SGP8503127M3106154<<<<<<<<<<<<<<<8`: number digit printed 9, computed 2.

### 4.3 `kyc_shareholdings`

Percent tolerance `pct_tol(*cells)` = sum over the cells of the half-unit of their last printed decimal place (from `Cell.raw`: `60.00` -> 0.005, `33.3` -> 0.05, `33%` -> 0.5; 0.005 when `raw` is None) plus 0.001. This lets `33.33 + 33.33 + 33.33 = 99.99` pass and `33.3 x 3` pass, while a missing 1% holder fails.

On the **register** (`shareholder_register.shareholders`), per document:
1. `S = sum(percent)` over rows with a percent; flag if `abs(S - 100) > pct_tol(all those cells)`. High, table `shareholders`, region `union_region` of the percent cells. Evidence `percentages sum to 95.00, expected 100.00 (tolerance 0.016)`.
2. `sum(shares)` equals `fields.total_shares` exactly (if both read); else high, field `total_shares`, evidence `rows total 480000 shares, register states 500000`.
3. For each row with `shares` and `total_shares`: `abs(percent - shares/total_shares*100) <= pct_tol(percent) + 0.005`; else high on that row (the arithmetic of one edited row).

On the **chart** (`ownership_chart.holders`), per document; `percent` of a row with `parent_name` is the percent **held in the parent**, `effective = parent_effective * percent / 100` (Contract requests):
4. Direct holders (`parent_name` empty): `sum(percent)` within `pct_tol` of 100, else high. For each intermediate company with children: children's percents sum to 100 within tolerance, else high. A `parent_name` that matches no holder (`entity_key` comparison): low, evidence `parent 'X' is not on the chart`; that subtree is not evaluated.
5. **UBO threshold:** `S = ctx.settings.ubo_threshold_percent` (`GLDB_UBO_THRESHOLD_PERCENT`, default `25.00`, **[confirm] against Notice 626**, plan 4.6/7.1); `T = min(chart.ubo_threshold_percent or S, S)` (a chart cannot raise the bar to hide an owner, so the stricter value wins). For every holder (register row, or chart holder using `effective`) with percent `>= T`:
   - **named:** `holder_name` non-empty and, case-insensitively, not one of `NAME_PLACEHOLDERS = {"", "-", "n/a", "na", "nominee", "nominees", "various", "others", "other", "tbc", "unknown", "undisclosed", "bearer", "confidential"}`; otherwise high, evidence `holder of 30.00% is not named ('Nominee')`.
   - a chart `individual` with `is_ubo == "false"`: high, `holder above threshold is not marked as UBO`.
   - a chart `company` holder `>= T` with no children listed: **medium** (severity override), `no natural person shown behind 'X Holdings' (40.00%)`. Honest quirk: listed companies and government entities are exempt from look-through under Notice 626 [confirm]; the reviewer dismisses with a reason.
Discriminator `entity_key(holder_name)` on flags 5 (named holders); none on the arithmetic flags. Fewer than 1 readable percent in a document: that document is skipped; no readable document: `CheckSkipped("no readable percentages")`.

### 4.4 `x_ownership_vs_registry`

Setup: `uen = ctx.applicant.uen`; none -> `CheckSkipped("applicant uen unknown")`. `ctx.registry is None` -> `CheckSkipped("registry unavailable")`. `entity = ctx.registry.lookup(uen)`; **absent** from a loaded registry -> **one** high flag (field `uen`, region the chart/register/profile `uen` cell if boxed, else none): `applicant UEN 2019xxxx not found in registry (as of 2026-09-30)`. This is the "applicant absent from the registry" finding that data-model.md section 8 points here for; `x_directors` then raises `CheckSkipped("entity absent from registry")` so the fact is not counted twice (Contract requests).

Compare each of `ownership_chart.holders` (direct rows), `shareholder_register.shareholders`, `company_profile.shareholders` with `entity.shareholders`, **independently per document**. Row pairing uses `same_identity(name, id, reg.name, reg.id_or_uen, kind=holder_type)`: ids equal -> same holder (names `different` -> pair kept, extra flag below); ids both present and different -> not the same holder; otherwise name verdict `same`/`probable` -> same holder (`unknown` -> unmatched, no flag). Pair greedily in registry order, each row used once. Flags (all high unless noted; discriminator `entity_key(holder name)`):
- **(a) missing from the document:** a registry holder with no counterpart. Region `union_region` of the document's holder rows, label `holder missing here`. Evidence `registry holds Lim Siew Hoon 30.00% (S****321B); not on ownership chart d4 (3 holders, 100.00% printed)`. The same holder missing on chart, register and profile yields three flags with the same discriminator, which the scorer groups into one finding (plan 8.4).
- **(b) not in the registry:** a document holder with no registry counterpart. Region = that row.
- **(c) percent differs:** paired rows with `abs(doc - reg) > pct_tol(doc_cell, reg_pct_as_cell)` (registry percents have 2 decimals). Evidence `chart 25.00%, registry 30.00%`.
- **(d) same id, different name:** paired by id with `match_person(...).verdict == "different"`: evidence `S****321B is 'Lim Siew Hoon' in the registry, 'Lee Ah Kow' here`.
- **(e) intermediate company look-through:** a chart company holder whose `id_or_uen` resolves in `ctx.registry` is compared the same way against that entity's shareholders using its children (depth cap 2). If the holder is not in the registry, the subtree is simply not compared (gap).
Chart only: rows with `parent_name` are not compared at the applicant level. Registry holders are never read for `status` (a struck-off applicant is not flagged by this rule; gap).

### 4.5 `x_directors`

Requires `company_profile` and a registry entity (`CheckSkipped("registry unavailable")`, `CheckSkipped("entity absent from registry")`). Definitions: *profile directors* = `company_profile.directors` rows; *registry directors* = `entity.officers` with `"director"` in `position.lower()` and `cessation_date is None or cessation_date > ctx.today`; *ID holders* = `national_id` (`holder_name`, `id_number`) and `passport` (`holder_name`, `passport_number`) documents.

- **(a) profile director not in registry** (the deck scenario): no registry director with `same_identity(...) in {"match", "match_name_conflict"}`. High, table `directors`, row = that row, discriminator `entity_key(director_name)`. Evidence `director Tan Mei Ling (S****678C) is on the profile but not among the 2 current registry directors`.
- **(b) registry director not on the profile:** high, region `union_region` of the directors table. Evidence `registry director Ravi Kumar (S****901A) is missing from the profile`.
- **(c) id conflicts:** a profile director paired by name (`same`/`probable`) with an ID document whose number differs from the profile's `id_number` (both present, masked suffixes compared): high, `id number on profile ends 678C, on ID document d9 ends 123A`. A pair with equal ids and a `different` name: high, `id number belongs to a different name`.
- **(d) info notes** (severity override `info`, weight 0): a profile director with no ID document in the bundle (`no ID document supplied for director X`); an ID document whose holder matches no director and no shareholder or chart individual (`ID of X is not linked to a director or owner (guarantor?)`).
Appointment dates, positions and nationalities are not compared (gap).

### 4.6 `x_declared_vs_activity` (C5)

Inputs: `company_profile.fields.principal_activity` and every `bank_statement.transactions` row with annotation `c5` whose `probability >= settings.classifier_min_prob` (0.70); `counterparty` annotation for names. Statements in different currencies: `CheckSkipped("currency mismatch")`. No row has `c5`: `CheckSkipped("no c5 annotations")`.

**Declared categories** `D = declared_categories(text)`: case-insensitive keyword match to C5 labels (a set, may hold several): `logistics`: logistic, freight, forward, shipping, warehous, transport, courier, cargo, supply chain; `trading`: trading, wholesale, import, export, distribut, merchant; `manufacturing`: manufactur, fabricat, assembl, production of; `professional_services`: consult, accounting, advisory, legal, engineering services, software, it services; `crypto_virtual_assets`: crypto, virtual asset, digital token, blockchain; `gambling`: gaming, gambling, casino, betting; `money_services`: remittance, money changer, payment services. No keyword: `CheckSkipped("declared activity not mappable")`.

**Observed:** per C5 label (excluding `other`): `rows`, `amount = sum(debit or credit)`, `share = amount / total turnover` (turnover = sum of all debits and credits of the statements).

Flags (one per label; document ids = statements involved; `row`/`region` = first matching row, `extra_regions` = up to 5 more):
- **Risky label** `L in {crypto_virtual_assets, gambling, money_services}`, `L not in D`, and (`rows >= DVA_MIN_ROWS` (2) or `share >= DVA_MIN_SHARE` (0.05)): **medium**. For `money_services` count **credit rows only** (staff-wage remittances out are normal). Evidence `declared: logistics; crypto_virtual_assets: 6 rows, 182400.00 (31.2% of turnover); top counterparties: COINHARBOUR PTE LTD x4, ...` (top 3, 40 chars each).
- **Dominant other activity:** the largest non-risky label with `share >= DVA_DOMINANT_SHARE` (0.50) not in `D`: **low** (severity override), `62.0% of turnover goes to/comes from 'trading' counterparties; declared: logistics`.
No flag when `D` already contains the label (a declared crypto business is not a mismatch).

### 4.7 Name matching

Goal: compare identity, not characters. "Wong Jessica" equals "Jessica Wong"; a near-spelled different person must not pass on similarity alone (plan 4.6). Pure functions in `names.py`, no randomness, no I/O.

**`normalise_person` pipeline** (in order):
1. `None`/blank -> `script="empty"`.
2. `unicodedata.normalize("NFKC")`, `casefold()`.
3. CJK detection (code points U+3400-4DBF, U+4E00-9FFF, U+F900-FAFF): all CJK -> `script="cjk"`, tokens = the characters with spaces removed (surname-first order is the norm, no reordering); CJK mixed with Latin letters -> `script="mixed"`.
4. Latin: `NFKD`, drop combining marks (Jose = Jose), apostrophes removed (`O'Brien` -> `obrien`, `Dato'` -> `dato`), other punctuation (`. , ; : _ - / ( )` except as below) -> space.
5. Bracketed text -> `optional` tokens (`Wong Mei Ling (Jessica)`); the compare uses both the core tokens and core+optional, best verdict wins.
6. **Patronymic markers** (matched before punctuation is removed, on token boundaries): `bin`, `binte`, `binti`, `bte`, `bt` (only before another token), `ibni`, `anak`, `s/o`, `d/o`, `a/l`, `a/p`, `son of`, `daughter of`. The first marker splits `given` (before) from `patronym` (after); the marker itself is dropped. `tokens = given + patronym`.
7. **Titles**, removed only while they are *leading* tokens (so surname `Tan` in `Tan Wei Ming` is safe, `Tan Sri Lim` loses `Tan Sri`): multi-word first (`tan sri`, `puan sri`, `dato sri`, `datuk seri`, `datin seri`, `assoc prof`), then `mr`, `mrs`, `ms`, `miss`, `mdm`, `madam`, `dr`, `prof`, `professor`, `dato`, `datuk`, `datin`, `tun`, `haji`, `hajjah`, `hj`, `hjh`, `encik`, `en`, `cik`, `puan`, `sir`, `rev`, `ir`, `er`. Trailing suffixes dropped: `jr`, `sr`, `ii`, `iii`, `iv`, `phd`.
8. **Aliases** (`TOKEN_ALIASES`, exact map applied per token): `mohd, mohamed, mohammad, mohamad, muhammad, muhamad, mohammed, muhd -> muhammad`; `abd, abdul, abdull -> abdul`; `nur, noor -> nur`; `siti, st -> siti`. Extend only with a test.

**`match_person(a, b)` decision** (first match wins; `ta`, `tb` token lists):
1. Either `empty` -> `indeterminate("empty name")`. Both `cjk`: equal characters -> `same`, else `different`. One `cjk`, other Latin, or any `mixed` -> `indeterminate("cannot compare scripts")`.
2. When **both** names carry a patronymic marker the given names and the patronym are compared apart (`Ahmad bin Ismail` and `Ismail bin Ahmad`, father and son, are `different`); with a marker on one side only, order stays free. Otherwise `sorted(ta) == sorted(tb)` -> **same** ("same tokens, order-insensitive"). This covers Chinese order (`Tan Wei Ming` = `Wei Ming Tan` = `Ming Wei Tan`), comma order, and patronymic marker present or absent.
3. **Joined given names:** generate variants of each list by merging adjacent tokens (both of length <= 6, in written order, at most 4 merges); any pair of variants with equal sorted lists -> **same** ("given names joined", `Tan Weiming` = `Tan Wei Ming`).
4. **Probable** (one of; reason names it):
   - exactly one token pair differs and it is an initial (one token has length 1 and the other is longer and starts with that letter: `J`/`Jessica`; `J`/`K` is not an initial) or edit distance 1 with both tokens of length >= 6 (`Jessika`/`Jessica`);
   - the shorter list is a subset of the longer and has >= 2 tokens (`Faris Abdullah` within `Muhammad Faris bin Abdullah`);
   - mononym: one side has 1 token, of length >= 5, equal to the **first** token of the other (`Suryadi` / `Suryadi Wibowo`; `Kumar` is not `Rajesh Kumar`).
   - a `probable` that rests on the one-letter edit alone is a *typo-level* match (`NameMatch.typo`): `same_identity` does not accept it on the name alone (see below).
5. Otherwise **different**, reason `only in a: [...]; only in b: [...]`. Edit distance 1 on tokens shorter than 6 (`Wong`/`Wang`, `Lim`/`Lin`) is **different** on purpose: character similarity alone passes near-spelled fakes.

**`match_company`:** `NFKC`, casefold, fold diacritics, `&` -> ` and `, punctuation -> space, leading `the` dropped, trailing legal tokens stripped repeatedly (`pte, ltd, limited, private, llp, lp, llc, inc, incorporated, corp, corporation, co, company, sdn, bhd, berhad, plc, pty`). `same`: equal token sequences or equal token sets. `probable`: one-token edit distance 1 (length >= 6); sets equal after removing generic words (`holdings, group, enterprise, enterprises, international, global, services, solutions, industries, resources`) with >= 1 core token left; with `allow_truncation` the shorter normalised string (>= 12 characters) is a prefix of the longer (bank descriptions are cut at a fixed width, `ACME TRADING PTE L`). Else `different`.

**`same_identity(a_name, a_id, b_name, b_id)`:** `normalise_id` both. Both ids present and neither masked: equal -> `match` (name verdict `different` -> `match_name_conflict`), unequal -> `mismatch` regardless of name. A masked id (`S****567D`) compares only the last 4 characters and counts as `match` only when the name verdict is `same`/`probable`, else `unknown`. No id on one side: name verdict `same`/`probable` -> `match`, `different` -> `mismatch`, `indeterminate` -> `unknown`. `unknown` never creates a flag and is noted in evidence when a flag fires elsewhere. `probable` (initial, subset, mononym) is a pass; a typo-level `probable` is not: without ids it is `mismatch`, with equal ids `match_name_conflict` (the near-spelled fake is flagged, plan 4.6).

**`entity_key`:** `person` -> tokens from `normalise_person`, sorted; `company` -> `match_company` tokens, sorted; `auto` -> company if any legal token was stripped, else person. Joined with `_`, `[a-z0-9_]` only, so it is safe inside a flag id after `~`.

**Fairness slice test (plan 7.2, FEAT).** `slice_pairs(seed)` builds fictitious pairs deterministically (`random.Random(seed)` over fixed name pools in `names.py`; no real names). At least 20 same-person pairs and 10 near-miss different-person pairs per group:

| Group | Same-person recipes (expect `same`, or `probable` where noted) | Different-person near misses (expect `different`; must never be `same`) |
|---|---|---|
| `western_order` | `Jessica Wong` / `Wong Jessica`; `Wong, Jessica`; case and spacing | different given name, same surname |
| `chinese_order` | `Tan Wei Ming` / `Wei Ming Tan` / `Tan Weiming` / `Tan Wei-Ming` / `Tan Wei Ming (Jason)` vs `Jason Tan` (`probable`) | `Tan Wei Ming` / `Tan Wei Jie`; `Tan Wei Ming` / `Tan Wei Ning` (edit 1, length 4: different) |
| `malay_patronymic` | `Muhammad Faris bin Abdullah` / `Mohd Faris Abdullah` / `Faris Abdullah` (`probable`) / `Siti Aminah binte Yusof` / `Siti Aminah bte Yusof` / `Aminah Yusof Siti` | same father, different given name; `Faris bin Abdullah` / `Farid bin Abdullah` (edit 1, length 5: different) |
| `indian_patronymic` | `Rajesh s/o Kumar` / `Rajesh Kumar` / `Kumar Rajesh`; `Priya d/o Subramaniam` / `Priya Subramaniam` | `Ravi s/o Kumar` / `Rani s/o Kumar` (edit 1, length 4: different). Note `Rajesh`/`Rakesh` is edit 1 at length 6, so it is `probable`, not `different`: a documented limit, excluded from the near-miss set |
| `titles` | `Mr Lim Boon Heng` / `Lim Boon Heng`; `Dr Chua Mei Ling` / `Chua Mei Ling`; `Dato' Sri Ahmad Zaki` / `Ahmad Zaki`; `Tan Sri Lim Kok` / `Lim Kok`; surname kept: `Tan Wei Ming` / `Mr Tan Wei Ming` | `Mr Lim Boon Heng` / `Mr Lim Boon Hong` (edit 1, length 4: different) |
| `mononym` | `Suryadi` / `Mr Suryadi`; `Suryadi` / `Suryadi Wibowo` (`probable`) | `Suryadi` / `Suryanto` (different) |
| `diacritics_initials` | `Jose Garcia` / `Jose Garcia` with accents; `J. Wong` / `Jessica Wong` (`probable`) | `J. Wong` / `K. Wong` (different) |
| `non_latin` | `陈伟明` / `陈伟明`; `陈伟明` / `Tan Wei Ming` (`indeterminate`, a documented limit) | `陈伟明` / `陈伟杰` (different) |

`run_slice_test` returns one `SliceRow` per group: `false_flag_rate` = share of same-person pairs with verdict `different` (what would cause a wrong flag); `probable_rate`; `indeterminate_rate`; `false_accept_rate` = share of different-person pairs with verdict `same`; and the same two rates for the **baseline** matcher `rapidfuzz.fuzz.ratio(a.casefold(), b.casefold()) >= 85` (so the table shows what token-set matching fixes, plan 4.6). Report as JSON list of `SliceRow`; the eval harness writes it to `docs/eval-results/name-slice.json` and the table into the validation report. **Build-time expectations on the designed set:** `false_flag_rate == 0` and `false_accept_rate == 0` for every group except `non_latin` (indeterminate by design). The acceptable *gap between groups* on real data is not invented here: it is set after the first baseline and reported even if missed (plan 6, 9).

### 4.8 `x_sof_support`

Inputs: each `source_of_funds_declaration` document (`declared_amount`, `currency`, `origin_category`, `counterparty_name`, `period_start`, `period_end`, `receiving_account`, `declaration_date`) and all `bank_statement` transactions. Each declaration is evaluated separately, in document id order.

1. Currency: statements whose `currency != declared currency` are excluded; none left -> `CheckSkipped("currency mismatch")`. `receiving_account` set: keep only statements with that normalised `account_number`; none (a statement whose account number could not be read counts as a candidate) -> a **medium** flag `declared ... into account ...; no statement for that account was supplied` (declaring an account that was not uploaded must not switch the check off).
2. Window `[start, end]`: `period_start`/`period_end` if read, else start = earliest statement `period_start`, end = `declaration_date`; credits dated after `declaration_date` are excluded.
3. **Supporting credit** (annotation `c4` counts only at `probability >= classifier_min_prob`):

| `origin_category` | A credit supports the claim when |
|---|---|
| `sales_proceeds` | `c4 in {trade_receipt, customer_payment}` and (no `counterparty_name` or `match_company(counterparty, counterparty_name)` is `same`/`probable`) |
| `loan` | `c4 == financing_proceeds` and the same counterparty condition |
| `asset_sale`, `investment_returns`, `inheritance`, `salary`, `other` | `counterparty_name` is given and matches (company or person matcher by whether it has a legal token), `c4 != refund`; with no `counterparty_name`: `CheckSkipped("origin not testable without a named counterparty")` |

4. `supported = sum(supporting credits)`; `unsupported = max(0, declared - supported)`; `tolerance = max(money_tolerance, SOF_TOL_PCT * declared)` (`SOF_TOL_PCT = 0.01`). Flag when `unsupported > tolerance`.
5. Severity: **high** when `unsupported >= SOF_HIGH_SHARE (0.25) * declared`, else **medium** (override). Region = the `declared_amount` cell; `extra_regions` = the amount cells of up to 5 largest supporting rows. Evidence: `declared 1000000.00 (sales_proceeds, 2026-01-01..2026-06-30); 5 supporting credits total 312400.00; unsupported 687600.00 (68.8%); 3 credits unclassified (18200.00)`. The unsupported part is always reported (plan 4.6).
6. Credits with no or low-probability `c4` are listed as `unclassified` in evidence and do not support the claim (the classifier never lowers risk, plan 5.3).

### 4.9 `x_remitter_vs_buyer`

Applicant required (`CheckSkipped("applicant unknown")`). Consider invoices **issued by the applicant** (`match_company(supplier_name, applicant.name)` is `same`/`probable` or `supplier_uen == applicant.uen`); other invoices are payables and out of scope here. For each, in (invoice date, id) order:
1. **Candidate credits:** credit rows of the statements, same currency, dated inside the window: if `paid_date` is set, `[paid_date - 5d, paid_date + 5d]`; else `[invoice_date, (due_date or invoice_date + 30d) + 30d]`. A candidate is (a) a single credit with `abs(credit - total) <= money_tolerance`, or, only when no single credit exists, (b) an **instalment set** (R0): the first set, in (date, row) order, of 2 to 4 unused window credits each larger than 1% of the total whose sum is within `money_tolerance` of `total` (search capped at the 40 largest window credits; deterministic). Each credit pays at most one invoice (greedy, closest date then lowest row). The AML demo pays INV-2026-0117 (total 1,000,000.00) in three credits of 333,333.33 / 333,333.33 / 333,333.34 from three other companies: that set is the candidate, and its remitters are compared below.
2. **Remitter** = `counterparty_of(row)` for each credit of the candidate (for a set, the flag fires when **no** credit of the set matches the buyer; evidence lists every remitter). Compare with `buyer_name` via `match_company(allow_truncation=True)`; if the description contains a UEN (`\b\d{8,9}[A-Z]\b`) and `buyer_uen` is set, the UENs decide. A candidate **matches** on `same`/`probable`/equal UEN.
3. If at least one candidate exists and **none** matches: high, `row` = the best candidate, region = its `description` cell, `extra_regions` = the invoice `buyer_name` cell. Discriminator `entity_key(remitter)`. Evidence `invoice INV-0042 to Alpha Trading Pte Ltd, 12500.00 due 2026-03-01; credit 2026-03-04 of 12500.00 from 'BETA HOLDINGS PTE LTD' (row 18, d3)`. If the remitter is the applicant itself: `paid by the applicant's own account`.
4. No candidate credit: no flag here (that is `x_invoice_vs_receipts`). Matching window and tolerance must agree with that rule (Contract requests).
Honest quirk: a group treasury or payment agent paying for the buyer produces a true mismatch flag; the analyst dismisses it with a reason.

### 4.10 In-case entity graph and `bs_pass_through`

**`counterparty_of(row)`:** the `counterparty` annotation label if present; else the `description` with a leading channel prefix removed (`^(FAST|GIRO|PAYNOW|IBG|TT|INWARD|OUTWARD)\b(\s+(PAYMENT|TRANSFER|CREDIT))?\s*(FROM|TO)?`), trimmed; `None` if empty.

**Graph definition** (`EntityGraph.build`, `networkx.MultiDiGraph`):
- *Nodes* are `Entity` records keyed by `entity_key`. The applicant is `APPLICANT_KEY` (from `ctx.applicant`). A counterparty resolves to an existing node when its UEN equals, or `match_company` is `same`, or it is a truncation prefix of an existing display name (`probable` by typo is **not** merged). A counterparty that matches the applicant (own-account transfer) maps to `APPLICANT_KEY` and its flows are dropped.
- *Edges*, attribute `kind`: `flow` (every bank row: credit = `counterparty -> applicant`, debit = `applicant -> counterparty`, with `Flow` fields), `invoice` (`supplier -> buyer`, total, document id), `owns` (`holder -> applicant`, percent, from the register, chart direct rows and registry), `director_of` (`person -> applicant`, from profile directors and registry officers). Only `flow` edges feed `bs_pass_through`; the others make `summary()` useful for the case pack and let a future rule ask "is this remitter also a director or shareholder?".
- `resolve()` is deterministic: entities are created in first-seen order over (documents by numeric id, rows by index).

**`bs_pass_through`** (medium, stage rules). Constants: `PT_WINDOW_DAYS = 5`, `PT_TOL_PCT = 0.03`, `PT_MIN_AMOUNT = Decimal("5000.00")`, `PT_MIN_PAIRS = 3`, `PT_MIN_ENTITIES = 3`, `PT_MIN_SHARE = 0.20`.
1. All `bank_statement` documents of one currency (mixed currencies: `CheckSkipped("currency mismatch")`); credits and debits from `flows()`, rows with no counterparty are ignored.
2. Process credits in (date, numeric document id, row) order, each with `amount >= PT_MIN_AMOUNT` and `c4` label not `refund` (a missing `c4` is allowed). Candidate debits: `date(credit) <= date(debit) <= date(credit) + PT_WINDOW_DAYS`; counterparty entity different from the credit's entity and not the applicant; `abs(credit - debit) <= max(money_tolerance, PT_TOL_PCT * credit)`; not already matched. Pick the candidate with the smallest `(gap_days, abs diff, document id, row)`. A matched pair is a `Hop`.
3. A pattern exists when `len(hops) >= PT_MIN_PAIRS`, `len({source and destination entities}) >= PT_MIN_ENTITIES` (applicant excluded) and `sum(hop credits) / sum(all credits) >= PT_MIN_SHARE`. Otherwise return no flag.
4. One flag: `row` = first hop's credit row (table `transactions`), region = that amount cell, `extra_regions` = up to 6 further hop cells, alternating credit/debit. `circular` entities (a destination that also appears as a source) are listed in evidence. Evidence `5 credits (480200.00, 61.2% of credits) each followed within 5 days by a near-equal debit to another entity; 6 entities; first: 2026-02-03 +100000.00 ALPHA TRADING -> 2026-02-04 -98500.00 BETA ENTERPRISE; circular: GAMMA PTE LTD`.
Honest quirk: a back-to-back trader forwarding customer receipts to suppliers at a margin below 3% inside 5 days is flagged (medium, dismissable); real trading margins are normally wider, which is why the tolerance is tight.

## 5. Files owned

| Path | Agent |
|---|---|
| `backend/app/checks/kyc.py`, `backend/app/checks/aml.py` | rules-kyc |
| `backend/app/names.py`, `backend/app/graph.py` | rules-kyc |
| `backend/tests/test_checks_kyc*.py`, `test_checks_aml*.py`, `test_names*.py`, `test_graph*.py` | rules-kyc |

No change to `core/**`, `pyproject.toml`, `uv.lock`. Libraries already installed: `python-stdnum` (UEN only; there is no NRIC module), `rapidfuzz` (edit distance and the baseline matcher), `networkx`, `python-dateutil`, `Decimal` from the standard library.

## 6. Test plan

Tests build documents with `make_doc` / `make_ctx(registry=...)`, `FIXED_TODAY = date(2026, 10, 27)`, and the registry example of data-model.md section 8 (Example Logistics Pte. Ltd., UEN `201912345R` (valid; the earlier `201912345K` fails `stdnum.sg.uen`), Tan Wei Ming `S1234567D` 60.00%, plus fictitious holders). No network, no clock.

| # | Case | Expected |
|---|---|---|
| 1 | **Clean KYC bundle**: profile, register, chart, 2 IDs, 1 passport, 3 statements all agreeing with the registry | all 8 KYC rules `ran`, 0 flags |
| 2 | NRIC vectors `S1234567D`, `T0812345J`, `F1234567N`, `G1234567X`, `S8222222G`, `S0000001I`, lower-case with spaces | pass format and checksum; `nric_check_letter` returns the letters of the pinned vectors (no library oracle exists) |
| 3 | `S8222222A`; `S12345678`; `X1234567D`; `M1234567K` | `id_checksum` flag (expected G); `id_format` flag; `id_format` flag; `M` passes format and is silently unverified (`id_checksum` still ran if another ID verifies) |
| 4 | Passport ICAO specimen; SG vector | 0 flags |
| 5 | SG vector with DOB `850318`; with number `K1234568D...`; line 2 of 43 chars; `mrz_line2` containing `a` | 2 flags (DOB digit, composite); 2 flags (number digit, composite); 1 flag (length); 1 flag (characters) |
| 6 | Visual DOB `1985-03-12` with MRZ `850318` and valid digits; visual surname `Tan` vs MRZ `LEE` | `passport_mrz` visual-vs-MRZ flags |
| 7 | `date_valid`: `31 Feb 1990` (value None, raw set); DOB 2027-01-01; DOB 2015-06-01 (age 11); DOB 1900-01-01; `S` id with DOB 2003; `T` id with DOB 1985; expiry before issue; expired passport | flags for 1,2,3,4,5,7; none for `T`/1985 and none for the expired passport |
| 8 | Register 60.00 + 25.00 + 10.00; 33.33 x 3; 33.3 x 3; shares sum 480000 vs `total_shares` 500000; one row percent edited | flag (95.00); pass; pass; flag; flag (row) |
| 9 | Chart with holder `Nominee` at 30.00%; company holder 40% with no children; `is_ubo=false` on a 26% individual; chart prints threshold 10 | high; medium; high; threshold 10 used (min with 25) |
| 10 | **KYC demo, naive forger**: chart omits Lim Siew Hoon (30%) and the remaining holders sum to 70 | `kyc_shareholdings` (chart sum) and `x_ownership_vs_registry` (a) |
| 11 | **KYC demo, careful forger**: chart percents rescaled to 100 | `kyc_shareholdings` silent; `x_ownership_vs_registry` (a) and (c) still fire, discriminator `lim_siew_hoon`; same holder missing on chart and register: two flags, one discriminator |
| 12a | Applicant UEN absent from a loaded registry | one high `x_ownership_vs_registry` flag; `x_directors` `CheckSkipped("entity absent from registry")` |
| 12b | `ctx.registry is None` | both `CheckSkipped("registry unavailable")`, no flag |
| 13 | Profile director not in registry; registry director missing from profile; profile id differs from ID doc; same id with a different name; director without an ID document | flags (a), (b), (c) x2, info (d) |
| 14 | `x_declared_vs_activity`: declared "Freight forwarding and logistics"; 6 rows `crypto_virtual_assets` = 31.2% of turnover; 1 row crypto at 1% turnover; `money_services` debits only; `money_services` credits x3 | medium; no flag (1 row, 1%); none; medium. Declared "crypto trading": no flag |
| 15 | `x_sof_support`: declared 1,000,000.00 `sales_proceeds`, supporting credits 312,400.00; supporting 995,000.00; supporting 1,200,000.00; credits unclassified | high (68.8%); no flag (unsupported 5,000 <= 1% tolerance 10,000); no flag; flag, credits listed as unclassified |
| 16 | `x_sof_support` with `receiving_account` not in any statement; origin `inheritance` without counterparty | medium flag; `CheckSkipped` |
| 17 | `x_remitter_vs_buyer`: invoice buyer `Alpha Trading Pte Ltd`, credit from `BETA HOLDINGS PTE LTD`; credit `ALPHA TRADING PTE L` (truncated); credit from the applicant itself; invoice 1,000,000.00 paid by three credits 333,333.33 / 333,333.33 / 333,333.34 from three other companies; invoice 10,900.00 paid by two instalments from the buyer | high; no flag; high with "own account"; high (instalment set, evidence lists three remitters); no flag |
| 18 | `bs_pass_through` positive: 3 pairs, credits 100000 / 80000 / 60000 from A / C / E, debits 98500 / 79000 / 58900 to B / D / F within 1-3 days | one medium flag, 6 entities, share computed |
| 19 | Same with a 10% margin; 2 pairs only; 3 pairs between one source and one destination (A -> B x3, only 2 entities); gap 6 days; amounts below 5,000; matched volume only 15% of all credits | each: no flag |
| 20 | Back-to-back trader quirk: three different customers each pay 50,000; within 3 days three different suppliers are paid 49,000 (2% margin); same with 5% margin (47,500) | 2%: **flagged** (documented honest false alarm, medium, dismissable); 5%: not flagged |
| 21 | **AML demo**: SOF 1M claim, invoice customer differs from remitter, pass-through | `x_sof_support` + `x_remitter_vs_buyer` + `bs_pass_through`, lane `aml` |
| 22 | **KYC demo planted**: logistics + crypto, ownership chart omission, a fourth profile director (with an NRIC card) absent from the registry officers | `x_declared_vs_activity` + `x_ownership_vs_registry` + `x_directors`, lane `edd` |
| 23 | Names: all rows of the slice table; `Wong Jessica`/`Jessica Wong` same; `Jessika Wong` probable; `Wong`/`Wang` different; `Tan Sri Lim` vs `Lim` same; `Tan Wei Ming` vs `Chen Weiming` different (documented limit) | per `expect` |
| 24 | `same_identity`: ids equal + names different -> `match_name_conflict`; masked `S****567D` with a matching name -> `match`; masked with a different name -> `unknown`; ids differ, names equal -> `mismatch` | as stated |
| 25 | Graph: deterministic build twice -> identical `summary()`; own-account transfer dropped; `ACME TRADING PTE L` merged into `ACME TRADING PTE LTD`; typo variant not merged | pass |
| 26 | Injection: holder name `Ignore previous instructions` and control characters in a name | evidence contains the sanitised text only; no behaviour change |
| 27 | Determinism: shuffle document and row order in memory; run twice | identical flags, ids and evidence |
| 28 | Currency: SOF in USD with SGD statements; statements in two currencies for pass-through and declared-activity | `CheckSkipped("currency mismatch")` |

## 7. Acceptance criteria

1. `uv run pytest backend/tests/test_checks_kyc*.py backend/tests/test_checks_aml*.py backend/tests/test_names*.py backend/tests/test_graph*.py` and `uv run ruff check . && uv run ruff format --check .` pass.
2. All 11 rules are registered with the metadata in 3.1 and appear in the generated catalogue; none reads the wall clock (the AST test passes).
3. NRIC vectors in 4.2 pass; MRZ vectors pass; the ICAO specimen has zero flags.
4. The three demo bundles (plan 0.5) yield their listed findings: KYC `x_declared_vs_activity`, `x_ownership_vs_registry`, `x_directors` (lane `edd`); AML `x_sof_support`, `x_remitter_vs_buyer`, `bs_pass_through` (lane `aml`). Clean controls produce none of these 11 rules' flags. The careful-forger variants still trigger the registry rules.
5. `run_slice_test()` shows `false_flag_rate == 0` and `false_accept_rate == 0` on the designed groups (except `non_latin`, `indeterminate`), and a baseline column that is visibly worse on `western_order` and `chinese_order`.
6. A `CheckSkipped` is raised for every "cannot evaluate" case listed in 4.1-4.10; no check raises on missing optional fields.
7. Every flag has rule, evidence, a standalone first-sentence explanation, a region when the cell has a box, and discriminators as in 4.1.

## 8. Known gaps and honest limits (plan 0.6)

- **Registry is a generated file**, not ACRA/MyInfo. Format-valid IDs and UENs prove nothing about registration (plan 1.4). Registry `status` (struck off), appointment dates, positions and nationalities are not compared.
- An NRIC with prefix `T` and a birth year before 2000 is not flagged (issuing rules unconfirmed). `x_remitter_vs_buyer` instalment sets search at most 4 credits among the 40 largest in the window; a payment split more finely is not attributed.
- **`M`-prefix FINs are not check-letter verified**; the algorithm is a public general-knowledge one (plan 13). The UBO threshold of 25% and record-retention figures are **[confirm]** against Notice 626 text.
- **Passports:** TD3 only; no verification of the document's physical security features; a matching MRZ proves only self-consistency. Expired IDs are not flagged.
- **Name matching is conservative, not complete.** Dialect versus pinyin surname pairs (`Tan`/`Chen`), nicknames, married names, transliterated Chinese characters, and an unknown surname order inside Latin names beyond token-set equality are not resolved (`different` or `indeterminate`). That is a fairness cost to report, not to hide. A typo-level fake (`Jessika`/`Jessica`) is `probable` and passes when no id exists on both sides. Ids override names.
- **Typo-level names are flagged, not passed.** A one-letter slip between the printed and the registry name (`Gregory`/`Gregury`) is a mismatch (or a name conflict when the ID is equal): a real typo costs a reviewer a look, a near-spelled fake no longer passes. Chinese-order names behave the same way. All 30 same-person surface forms of the six fairness groups stay silent (`tests/e2e/test_kyc_names.py`).
- **Chart semantics:** child percent is read as the percent of the parent; if the extractor prints effective percentages the sum checks will misfire (Contract requests).
- **`x_declared_vs_activity` depends on C5 labels** from the classifier; it is as good as the counterparty classification. Keyword mapping of the declared business is coarse. Money-service credits are an honest false-alarm source.
- **`x_sof_support`** tests amount and counterparty only, not that an *identifiable* real sale happened; it does not read invoices (that is `x_invoice_vs_receipts`).
- **`bs_pass_through`** uses fixed windows and tolerances (starting guesses). It sees only the supplied statements, one account: layering across other accounts is invisible. Back-to-back traders are a false-alarm source.
- **Entity graph** is in-case and name-based; there is no cross-case graph and no graph view in the UI (flags carry the evidence). Entity resolution merges exact and truncated names, not typos.
- **Not built (plan 0.4, listed "received, not checked"):** board resolution, proof of address, business licences, wealth evidence, RFI documents.
- Eval results are an upper bound because the generator is ours (plan 9.4). Every rule needs a row in the eval (caught or missed) before it counts as addressed.
- **W3 eval triage (T-integration-kyc-07).** One false alarm found and fixed: `x_sof_support` fired (medium) on clean AML companies 3 and 7 because the declaration printed `Company account` in the bank-name slot and the extractor read that value as a repeated `Company` label, taking the declaration sentence as the receiving account (`kyc_sof_declaration.py` company-label pattern now excludes `Company account`; regression test `test_extract_kyc_layouts.py`). After the fix the digital clean pool has 0 false alarms in 33 bundles. The fairness slice reports FAIL against our proposed criteria only on the wrong-match rate (3 of 15 negatives in the Malay and 6 of 15 in the Indian patronymic groups are not flagged: a one-letter slip with the same ID on both sides is read as a typo of the same person); false-flag rate is 0 in all six groups and disparity 0.0. This is the documented rule that IDs override names and is pinned by `test_kyc_names.py`; the criteria are ours and the group sizes are small.

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

**R0 outcomes:** registry-absent entity accepted (data-model.md 8 amended); chart child percent accepted (data-model.md 6.8 amended); discriminators confirmed as `names.entity_key` output for the holder, director and remitter families (the entity family uses `core.textnorm.company_disc`/`uen_disc`, checks-financial.md 3.4); window alignment: documented difference, and `x_remitter_vs_buyer` gained instalment sets (4.9); annotation dependencies confirmed; case pack graph summary is stretch; ID display: masked in flag evidence only until H-22; fairness table location accepted; UBO threshold is the setting `GLDB_UBO_THRESHOLD_PERCENT`.

- **Registry-absent entity (data-model.md section 8).** The sentence "raise `CheckSkipped` ... or the entity is absent" is replaced here for the *applicant*: `x_ownership_vs_registry` emits one high flag when a loaded registry lacks the applicant UEN, and `x_directors` skips. Please amend the sentence to match.
- **Chart child percent (data-model.md section 6.8).** State that `holders.percent` for a row with `parent_name` is the percent held *in the parent*. extract-kyc and gen-kyc must follow it.
- **Discriminator form (scoring-and-queue.md 4.1).** Confirm discriminators are `names.entity_key` output (`[a-z0-9_]`), which is what this doc emits.
- **Matching window alignment (checks-financial.md).** `x_invoice_vs_receipts` should use the same candidate-credit definition as 4.9 (amount tolerance, `paid_date +/- 5d`, else `[invoice_date, due_date + 30d]`, one credit per invoice) or document the difference.
- **Annotation dependencies (classification.md, ingest-and-extraction.md).** Extraction must write `counterparty` on every transaction row, `c4` on credits and `c5` on all rows, with probabilities; bank descriptions may be truncated, so the graph treats prefixes as the same entity.
- **Case pack (optional).** `pack.py` may include `EntityGraph.summary()` for the investigator; needs a `CaseContext` or a stored summary. Not required for acceptance.
- **ID display.** Flag evidence masks NRIC numbers (`S****222A`); confirm with compliance whether the case pack and UI should also mask (PDPA, plan 7.1 [confirm]).
- **Fairness table location.** The eval agent writes `run_slice_test()` output to `docs/eval-results/name-slice.json` and the table into `docs/validation-report.md`.
