# Name matching: fairness on a larger DEV/TEST set, real aliases and honest limits

**Sanctions screening is also a name-matching task.** Its false-positive rate by name group (fictitious customers, and random draws of real list entries) is in [verification-sources.md](verification-sources.md) section 2.5, with an offline regression test (`backend/tests/test_sanctions_fairness.py`); it uses a narrower matcher than the one described below.

Scope: `backend/app/names.py` (person-name matching and `same_identity`), the dev/test generator in `backend/scripts/_gen/names_data.py` (extended pools) and the scorer `backend/scripts/_eval/name_fairness.py` (CLI `backend/scripts/name_fairness.py`). The 270-pair pipeline slice (`eval.py --suite fairness`) is unchanged and still runs. All names are fictitious or public Wikidata labels; the generator is ours, so every synthetic number is an upper bound.

The bank, not this tool, defines what is fair and which attributes are protected. Everything below measures one thing we can define ourselves, how often the same person is not accepted and a different person is, by the groups our generator and the Wikidata export give us. Those groups are not a validated taxonomy and are not protected attributes. The thresholds in "Metric and thresholds" are our proposal for the bank to confirm or replace.

**Short version of the 2026-11 work.** Of 7,088 real same-person alias pairs in the TEST split, 30.9% were not accepted. The fixable part is small: about 83 pairs (1.2%) were names that a matcher could reasonably accept (the same words joined or split differently, a documented surname variant, an honorific). Two rules recover 61 pairs (30.9% to 30.0%), at no cost on the near-spelled and hard-negative sets and at a cost of 4 pairs among 115,801 pairs of different people who share a name word. The other 99% of the unaccepted pairs (2,104 of 2,128 on TEST) are not name-matchable: one-letter and other spelling variants that look exactly like a deliberate near-spelled substitution, a name part that exists on one side only (nicknames, married names, other given or family names, stage names), and initials with nothing to match. They need an ID number, a date of birth or a document. The gap between India and the other two countries is almost unchanged (35.2% against 20.7% and 17.0%); the tool does not close it, and cannot by changing the name matcher.

## What was wrong

A near-spelled different person (`Suresh s/o Raman` against `Sulesh s/o Raman`, same ID number) passed as the same person in 3 of 15 Malay and 6 of 15 Indian pairs. It was a bug, not a design limit: `NameMatch.typo` looked for the reason prefix `one letter differs`, and the comparison for names with a `bin/binte/s/o/d/o` marker threw the inner reason away (`given names probable, patronym same`). Unmarked, Chinese, western and mononym names kept the prefix and were flagged. The matcher-only column in `fairness.py` ignores `.typo`, so it overstates the problem for western names (10/15) and mononyms (11/15) and is misleading; the pipeline column is the real one.

## What changed in `names.py`

1. A slip inside either part of a marked name now keeps its reason (`one letter differs in the patronym: abdullah/abdulrah`). `NameMatch` has explicit `slip` and `slip_kind` fields (`double_letter`, `vowel`, `other`); `typo` is `probable and slip`.
2. `identity_status()` returns `(match, needs, name)`; `same_identity()` is its first field. `needs` is `"id"` (an NRIC/FIN is missing, or only its last 4 characters are known) or `"dob_or_document"` (the ID is equal but the name differs by one letter, which looks the same for a typo and a deliberate substitution). A masked ID with equal last four characters and a slip is now `match_name_conflict`; it used to be `unknown`, which every rule skips silently.
3. Normalisation: initials expansion for several initials (`H.L. Gokhale` for `Hemant Laxman Gokhale`, `P. U. Chinnappa` for `Pudukkottai Ulaganathan Pillai Chinnappa`; a shared name must anchor it), and the Muhammad spellings `mhd`, `muhamed`, `mohmmad`, `mohamod`, `mohammod`. These spellings are priors, not DEV-selected: measured one at a time, none of them changes a DEV outcome, and `muhamed` gains one TEST pair only. The multi-initial rows were measured only against negatives that contain no initials (see the table below), so "no cost" there means no cost on those negatives, not none. `ab` is not an alias: `AB` is also a pair of initials, so `Ab`/`Abdul` as the only difference is `probable` (reason `short form`), never `same`.
4. Long names written joined or split (2026-11). `MERGE_MAX_TOKEN_LEN` went from 6 to 12 letters: `Brajakishore Tripathy` is now `same` as `Braja Kishore Tripathy`, `Kommanapalli Ganapathi Rao` as `Kommanapalli Ganapathirao`. Only spacing changes; every letter must still agree, so it cannot make a one-letter-different name acceptable (tests in `tests/test_names_alias_rules.py`). The cap was a guess; gains on DEV were 36 at 8, 47 at 10, 49 at 12 and 49 at 16, so 12.
5. A table of surname variants, `SURNAME_VARIANTS` (2026-11): 14 groups of two spellings that are two or more letters apart, a formal and an anglicised form (`Banerjee`/`Bandyopadhyay`, `Chatterjee`/`Chattopadhyay`, `Mukherjee`/`Mukhopadhyay`, `Ganguly`/`Gangopadhyay`) or a regional respelling (`Kapoor`/`Kapur`, `Dixit`/`Dikshit`, `Saxena`/`Saksena`, `Laxman`/`Lakshman`, `Chakraborty`/`Chakravarty`, `Bhattacharya`/`Bhattacharjee`, `Pattanayak`/`Pattnaik`, `Satapathi`/`Satpathy`, `Srivastava`/`Shrivastav`, `Raghavaiah`/`Raghavayya`). Every group had at least one same-person pair on DEV. Rules, enforced by a test: every pair in a group is at least two edits apart (so the table never makes a one-letter-different name acceptable, which was an explicit constraint); no group has two members one edit apart (this is why `Banerji` and `Bandopadhyay` are left out); a spelling is in one group only; none of the common different surnames (`Lim`/`Lin`, `Tan`/`Chen`, `Lee`/`Li`) is in it. Remaining risk, the same as for `Mohd`/`Muhammad`: a deliberate swap to the other spelling with the same ID number reads as the same person. `Samsudin`/`Shamsuddin` was dropped: aliases are applied to words before they are joined, so it broke `Shams-ud-Din`/`Shamsuddin`.

## Results (TEST split unless stated)

Pipeline view with the same ID number on both sides, so only `match` passes; 120 near-spelled pairs per group; Wilson 95% intervals; "old" is the real previous `names.py` run on the same pairs.

| group | wrong match, old | wrong match, new | false flag, same person (surface forms, aliases) |
|---|---|---|---|
| malay_patronymic | 45/120 = 0.375 [0.29-0.46] | 0/120 [0.00-0.03] | 0/160 [0.00-0.02] |
| indian_patronymic | 53/120 = 0.442 [0.36-0.53] | 0/120 [0.00-0.03] | 0/120 [0.00-0.03] |
| western, chinese, mononym, titles | 0/120 each | 0/120 each | 0/120 each |

Hard negatives (shared surname or patronym, shared given name, father and son swapped; 40 per class and group) are 0/40 wrong matches in every group. The real pipeline slice (270 pairs, fake LLM, dev seed 4002) went from 3/15 and 6/15 wrong matches to 0/15 in both groups, 0 false flags, disparity 0.0; with 15 pairs per group the Wilson 95% upper bound of 0/15 is 0.20, and the Malay change alone (3 to 0, exact paired p = 0.25) is within noise, the Indian change alone (6 to 0, p = 0.03) is borderline, and the two together (9 to 0 of 30 pairs, p = 0.004) are not.

Real Wikidata aliases (same person, public export, 7,088 usable TEST pairs after the filter in `usable_positive`): not accepted 39.7% with the matcher before the 2026-10 bug fix, 30.9% with the frozen matcher of 2026-10-29 (`names_baseline.py`, India 36.4%, Malaysia 20.7%, Singapore 17.0%), and **30.0% with the matcher now (2,128 of 7,088, 95% interval 29.0 to 31.1; India 35.2%, Malaysia 20.7%, Singapore 17.0%)**. The change is 61 pairs gained and none lost (exact sign test p < 1e-18), 60 of them in India and one in Malaysia, so the gap between the highest and lowest group (max minus min) moves from 0.194 (bootstrap interval 0.160 to 0.230) to 0.181 (0.148 to 0.218): the intervals overlap, so this is not a closed gap. The group is the country of the Wikidata entry, not name style, and there is no Chinese-order breakdown. DEV, where the rules were chosen: 31.2% to 30.1% (2,197 to 2,120 of 7,038; 77 gained, none lost). What the unaccepted pairs are is in the next section. The 2026-10 split of the failures, measured for the earlier 2,188, was: 1,165 (16%) different or extra name parts, 431 (6%) respelled in two or more letters or in short tokens, 416 (6%) one-letter slips flagged by design, 176 (2.5%) initials that do not line up. Different people one or two letters apart (153 pairs): 0/153 accepted.

## What the unaccepted real aliases are

Each unaccepted pair is put in one category by the strings alone (`scripts/_eval/name_alias_categories.py`, using the frozen baseline normalisation, so the categories do not move when `names.py` does). The export's own `pair_type` is a heuristic and was not used. Two buckets:

* **(a) a name matcher could reasonably accept it:** the same words up to spacing or order, an honorific, a documented surname variant, or initials that line up. No letter disagrees.
* **(b) it needs other evidence** (an ID number, a date of birth, a document): either a letter-level disagreement (a spelling variant; a one-letter variant is the same string change as a deliberate near-spelled substitution, so accepting it would also accept the fakes), or a name part that is on one side only (a nickname, a married name, another given or family name, a stage name, an initial with nothing to match). Some (b) pairs are not the same person at all: Wikidata alias fields hold stage names, honorific forms, and sometimes another person's name (`Zoya Khan` / `Sana Khan`, `Arjun Kapoor` / `Arjun Khan`, `Saif Ali Khan` / `Sajid Ali Khan`). No matcher should accept those, and there is no label that says which they are; the categories are our reading of the strings.

Counts of unaccepted pairs, by country of the Wikidata item (matcher now; the frozen baseline in brackets). TEST is what to quote; DEV is where the rules were chosen.

| bucket | category | TEST India | TEST Malaysia | TEST Singapore | TEST all | DEV all |
|---|---|---|---|---|---|---|
| a | spacing or order (joined or split words) | 0 (33) | 0 (1) | 0 (0) | 0 (34) | 1 (48) |
| a | honorific only | 20 (20) | 3 (3) | 0 (0) | 23 (23) | 26 (26) |
| a | documented surname variant | 0 (25) | 0 (0) | 0 (0) | 0 (25) | 0 (25) |
| a | initials that line up | 0 (0) | 1 (1) | 0 (0) | 1 (1) | 1 (1) |
| b | one letter of a word differs | 548 (548) | 58 (58) | 10 (10) | 616 (616) | 548 (548) |
| b | spelling variant, two or more letters | 241 (241) | 17 (17) | 11 (11) | 269 (269) | 290 (290) |
| b | initials with nothing to match | 153 (153) | 16 (16) | 3 (3) | 172 (172) | 183 (183) |
| b | shares only the family name | 318 (318) | 104 (104) | 19 (19) | 441 (441) | 455 (455) |
| b | shares only the first name | 213 (214) | 36 (36) | 12 (12) | 261 (262) | 261 (263) |
| b | other different name parts | 168 (169) | 130 (130) | 47 (47) | 345 (346) | 355 (358) |
| | **not accepted, all** | **1,661 (1,721)** | **365 (366)** | **102 (102)** | **2,128 (2,189)** | **2,120 (2,197)** |
| | of which (a) | 20 (78) | 4 (5) | 0 (0) | 24 (83) | 28 (100) |
| | of which (b) | 1,641 (1,643) | 361 (361) | 102 (102) | 2,104 (2,106) | 2,092 (2,097) |

As a share of all usable pairs in the group (TEST): (a) 0.4% India, 0.2% Malaysia, 0.0% Singapore, from 1.7%, 0.3%, 0.0% before (pooled, 24 pairs against 83: 0.3% against 1.2%); (b) 34.7% India (95% interval 33.4 to 36.1), 20.4% Malaysia (18.6 to 22.4), 17.0% Singapore (14.2 to 20.2). The (b) gap between India and the others is the whole gap. **So: about 1.2% of the real aliases were fixable by a name matcher, we fixed about 0.9 of those points, and 29.7% of the pairs need other evidence.** A "one letter of a word differs" pair counts here whether the word is long enough for the matcher to call it a slip (flagged by design, 404 of the 616 on TEST) or short (read as a different name; flagged as well).

Rows the usable filter drops are (b) by construction: 1,891 in TEST (1,138 are `unrelated_token_set`: stage names, pinyin against English given names, descriptions). Counting every row of the split, not only the usable ones, 41.5% were not accepted (3,728 of 8,979, 95% interval 40.5 to 42.5; 42.3% with the baseline). The 30.0% above is the figure on the usable pairs, as before.

## Adopted and rejected rules, measured on DEV

Each candidate was measured on DEV only, against: the 1,400 synthetic different-person pairs (near-spelled, shared family, shared given, father and son, sibling written as initials), the 153 real different-person pairs one or two letters apart, and a stronger probe added for this work: **112,254 pairs of strings of two different Wikidata items that share a name word** ("natural collisions": no near-spelling is needed, any two Kumars are in it; the frozen baseline already accepts 656 of them, 0.58%, mostly through initials and shorter-name-inside-longer-name rules). A rule is rejected if it adds any wrong match on the synthetic or the 153 sets; the natural-collision count is reported for every rule and judged by what it buys.

| rule | DEV alias pairs gained | added wrong matches: synthetic (1,400) / real 1-2 letters (153) / natural collisions (112,254) | decision |
|---|---|---|---|
| join long words, `MERGE_MAX_TOKEN_LEN` 6 to 12 | +49 | 0 / 0 / 2 (`Nandamuri Taraka Rama Rao` against `Nandamuri Tarakaramarao Jr.` and back: identical letters, the `Jr.` is dropped by the baseline already) | adopted |
| `SURNAME_VARIANTS`, 14 groups | +28 | 0 / 0 / 1 (`Subhash Mukhopadhyay` against `Subhash Mukherjee`: two different people, one surname family) | adopted |
| further honorifics (`swami acharya justice maharaja maharani lady tunku tengku`), stripped while two words remain | +4 | 0 / 0 / 4 | rejected: too small to carry its cost |
| the same honorifics stripped to a single word | +9 | 0 / 0 / 10 (`Tengku Adnan` against `Adnan Sami`: a lone word matches any longer name that starts with it) | rejected |
| one extra initial: `S. K. Bakre` for `Sadanand Bakre` | +57 | 49 sibling-initials (`Wong B. L.` against `Wong Bee Hwee`) / 0 / 997 | rejected |
| the same, only when every written-out word is covered by an initial | +53 | 1 / 0 / 251 (`K S George` against `Susan George`) | rejected |
| two or more words shared, one differs | +288 | 488 (337 near-spelled) / 34 / 1,410 | rejected |
| same family name | +976 | 597 / 101 / 28,074 | rejected |
| common first three letters, at most three edits (`Kishor`/`Kishore`, `Narain`/`Narayan`) | +506 | 199 near-spelled / 19 / 27 | rejected |
| dialect against pinyin surnames, doubled letters, `y/i c/k z/s ph/f ee oo` or all vowels folded; `Lim`/`Lin`, `Lee`/`Li`, `Chua`/`Cai` | see the table below (2026-10) | wrong matches in every case | rejected, not repeated |

The last four rows are the trade the whole task is about: each recovers many real aliases and each accepts the fakes, because the real alias and the fake are the same string change. The adopted rules are the only ones found that do not.

## What was tried and not adopted in 2026-10 (DEV, 7,038 real alias pairs, 1,200 synthetic negatives)

| idea | alias pairs gained | cost |
|---|---|---|
| several initials | +518 (7.4 points) | none on the initial-free negatives; adopted. Not measured against different people who share only an initial |
| initials with extra written-out names | +70 | none on the initial-free negatives; adopted. `J. Wong` is `probable` against `James Peter Wong` and any other longer name with a given name starting with J (accepted on the name alone, `needs == "id"`) |
| `ab` as a plain alias | +4 (from `ab` alone) | `AB Kumar` became `same` as `Abdul Kumar`; replaced by a `probable` `short form` rule |
| extra Muhammad spellings | 0 on DEV (+1 on TEST, `muhamed`) | none measured; kept as known spellings |
| Tan/Chen dialect-to-pinyin | -1 | none |
| Lim/Lin | +1 | +2 wrong matches (a real dialect pair drawn as a "different person") |
| Lee/Li, Chua/Cai | +2, 0 | +1 false flag |
| fold doubled letters and `h` after a vowel | +117 | would accept every `Kamarudin`/`Kamaruddin` style fake by construction; not measured by our generator, which does not draw them |
| fold `y/i, c/k, z/s, ph/f, ee, oo` | +63 | +85 wrong matches |
| fold all vowels | +208 | +252 wrong matches, +2 on Wikidata negatives |

Phonetic folding is therefore not an accepted equivalence. Spelling variants stay `probable` with `slip=True` (flagged) and carry `slip_kind` so a rule can grade them.

## Pairs that name alone cannot decide

- One-letter differences with the same ID number. A typo and a deliberate substitution are the same string. The new matcher flags 100% of them (0 of 720 TEST near-spelled fakes pass; all 541 TEST same-person typos are flagged too). Any genuine document with a one-letter slip is flagged, so the real false-flag rate roughly equals the slip prevalence. On Wikidata Singapore/Malaysia/India aliases, 5.7% of usable TEST pairs (404 of 7,088; 416 before the 2026-11 rules) are one-letter variants of the same person (`Zaleha Kamarudin`/`Zaleha Kamaruddin`, `Mani Ratnam`/`Mani Rathnam`). The benchmark's different-person pairs share an NRIC, which a real different person would not.
- Grading by slip kind would not separate them. Of 416 real one-letter variants, 220 are vowel and 46 double-letter (64%); of the 250 one-letter fakes our generator draws, 114 are vowel swaps (46%) and none is a double letter, because its substitution table swaps vowels. Lowering severity for those kinds would cut the review load of genuine slips by about 64% and also lower the severity of 46% of the generator's fakes. This is a policy decision, not a measured improvement.
- Probable matches without an ID (`J. Wong`/`Jessica Wong`, dropped prefix `Muhammad`, mononym with surname) are accepted on the name alone; `identity_status().needs == "id"` records it.
- Dialect against pinyin surnames (`Tan`/`Chen`), nicknames, married names, transliterated Chinese characters and stage names: `different` or `indeterminate`, a fairness cost that stays.
- A deliberate substitution that keeps the NRIC and uses a true alias spelling (`Mohd` for `Muhammad`, or the other member of a `SURNAME_VARIANTS` group such as `Kapur` for `Kapoor`) is the same person to the matcher. The same goes for two real people who share a first name and a surname family: among the 115,801 pairs of strings of different Wikidata items in TEST that share a name word, 669 are accepted as one person (665 with the 2026-10-29 matcher).
- Chinese and Indian names whose registry form is the other one's English or native-script rendering cannot be compared at all.

## Not done (outside the files this task owned)

- `checks/kyc.py` still treats `match_name_conflict` as a match in `x_directors` checks (a) and (b), and `unknown` is skipped silently everywhere. That includes every different name that shares an ID number (not only one-letter slips), so a profile director whose name is wrong against the registry, with the same ID, raises nothing in (a) or (b); only (c), against an ID document, catches it. Fixing that adds flags and needs its own measurement. `slip_kind` is read nowhere. `needs` is read in one place only (next section).
- No date of birth is compared: `RegistryOfficer` has no DOB field, so "equal ID, equal DOB, one-letter slip" cannot score low and a conflicting DOB cannot score high.
- `docs/checks-kyc-aml.md` (lines 392 and 400) and the comments in `tests/e2e/test_fairness_smoke.py` still call the old miss "the documented rule that IDs override names"; it was a bug.
- `kyc_fairness.near_spell` can draw a true alias (`Muhammad` to `Mohammad`) and label it a different person; the new scorer redraws those, the 270-pair slice does not.

## Routing `identity_status().needs` into `kyc.py`

Decided on the DEV split only (6 groups; the TEST tables above were not used to decide).

What `needs` can change. With the same ID number on both sides, only `match` passes, so `needs` cannot move a pair across the flag line: `dob_or_document` is set only on pairs that are already `match_name_conflict` (flagged), and `match` carries no `needs`. With an ID missing or masked, `needs == "id"` is set on `match` verdicts that are only probable (accepted on the name alone) and on slips with no ID.

Wired, as text only (`SLIP_NEEDS_NOTE` in `checks/kyc.py`). The existing "name differs" flag of `x_ownership_vs_registry`, and the "belongs to a different name" flag of `x_directors` (c), say in the explanation: "One letter apart on the same ID: a typo and a different person look alike, so check the date of birth or the ID document." Severity, evidence, grouping and the number of flags are unchanged. Nothing is decided and no flag is lowered, because lowering would also lower the severity of a deliberate substitution, which is the same string.

DEV, matcher view with equal ID (what the pipeline does), before and after the change (identical by construction; the 270-pair pipeline slice also gives the same per-group numbers before and after, 0 false flags, 0 wrong matches, disparity 0.0):

| group | same person, surface + alias, false flag | near-spelled different person, wrong match | near-spelled pairs that carry the note | same-person typos that carry the note |
|---|---|---|---|---|
| western_order | 0/120 | 0/120 | 66/120 | 75/87 |
| chinese_order | 0/120 | 0/120 | 0/120 | 0/98 |
| malay_patronymic | 0/160 | 0/120 | 45/120 | 44/82 |
| indian_patronymic | 0/120 | 0/120 | 60/120 | 65/88 |
| mononym | 0/120 | 0/120 | 88/120 | 84/84 |
| titles | 0/120 | 0/120 | 0/120 | 0/99 |

The note appears only where the matcher sees a one-letter slip inside a longer name. In Chinese-order and titled names a one-letter change reads as a different name, not a slip, so those flags are as before and carry no note. The wording is the same for a typo and a fake on purpose, and the coverage differs by group, so the note is a hint to the reviewer, not a measure. The flag is still raised for every typo (100% of same-person typos, by design).

Not wired: an info or low flag for `needs == "id"`. Same-person surface forms with the ID missing or masked reach it only in `malay_patronymic` (6/120 = 0.050, all of them the form with the first given name `Mohd` or `Muhammad` dropped) and in no other group (0/120 in the other five). Counted as a flag that is above the 0.02 line for one group and a 0.05 disparity between groups, for a pair the name accepts (verdict probable). As an info note it would not count in the project's false-flag metric, which ignores info, but it would load Malay names with reviewer notes that the other groups do not get. Slips with no ID are already flagged as a missing holder (`mismatch`), so a note there only reworded an existing flag. This was not measured on real aliases or in the pipeline with missing IDs, because the fairness bundle gives both sides the same ID.

Limits. DOB is still not compared anywhere (no DOB field on `RegistryOfficer`), so the note asks for a check, it does not perform one. `x_directors` (a) and (b) still pass a same-ID slip silently (see Not done).

## Metric and thresholds (our proposal for the bank to confirm)

**Definitions.** All with the same ID number on both sides, as the pipeline sees a name check, so only an identity of `match` passes.

* *Group:* the six synthetic name groups of `kyc_fairness.GROUPS` (western order, Chinese order, Malay patronymic, Indian patronymic, mononym, titles), and for real aliases the country of the Wikidata item (India, Malaysia, Singapore). Neither is a validated taxonomy or a protected attribute. The bank defines the protected attributes and the groups.
* *Wrong match* (different people accepted as one person): the share of different-person pairs accepted. Classes: near-spelled, shared family name, shared given name, father and son, sibling written as initials, real people one or two letters apart, and (as a probe) real people who share a name word.
* *False flag* (the same person not accepted): the share of same-person pairs that are not `match`. Surface forms and aliases (synthetic, from the generator) and real aliases (Wikidata), the latter split into (a) and (b) above. A one-letter typo of the same person is reported separately and is flagged by design.
* *Disparity:* the highest group false-flag rate minus the lowest, with a 95% bootstrap interval (1,000 resamples, fixed seed; for real aliases the unit that is resampled is the person, since one person has several aliases, so the interval is not narrower than the data allow). Per-group intervals are Wilson 95%. When every group has no failure the bootstrap interval is zero by construction, and the Wilson upper bound of the worst group (3.1% at 120 pairs) is the honest bound.

**Proposed thresholds.** Enforced on a small fixed fixture by `tests/test_name_fairness_regression.py` (offline, 0.5 s) and reported in full by `scripts/name_fairness.py`:

1. Wrong matches on the near-spelled and hard-negative sets: 0 in every group. A hard gate. With 120 pairs a group cannot show a rate below 3.1% (Wilson upper bound), so "0" is a gate on our sets, not a claim about the world.
2. Same-person false flags on surface forms and aliases: at most 2% in every group, and max minus min at most 0.05 (present value: 0 in every group on DEV and TEST).
3. Real aliases: no group's count of unaccepted pairs may rise, the (a) count may not rise, and the max-minus-min disparity may not rise, on the fixture and on the full export. We propose no numeric target for (b): it is mostly not name-matchable (see above), so a target would reward accepting fakes.
4. Probes that are reported, not gated, because the frozen baseline already fails them: a sibling written as initials (baseline accepts 1 of 40 in the Indian-patronymic group, none in the others) and different people who share a name word (baseline accepts 656 of 112,254 on DEV, 665 of 115,801 on TEST). The count of additional pairs the current matcher accepts must not rise above the recorded 3 (DEV) and 4 (TEST); the fixture holds every one of the DEV pairs.

**What the bank has to decide** (we cannot): which groups matter and which attributes are protected; whether max minus min of the false-flag rate is the right disparity measure and what size is tolerable (0.05 is ours); whether a false flag on a real alias of the same person costs more than a missed near-spelled fake (the choice between accepting and flagging a one-letter difference is that trade, and it is the bank's); and what other evidence (ID, date of birth, document) a reviewer may use to clear a (b) pair.

**The fixture** (`backend/tests/data/name_fairness_fixture.json`, 100 KB) holds 350 fictitious pairs from the generator (6 groups) and, from the public Wikidata export (CC0), 180 aliases (60 per country), 60 different-person pairs and 103 shared-word pairs, all from DEV. It records what the frozen baseline did with each pair and the numbers today (`expect`); the test fails if a number gets worse. Rebuild it with `--write-fixture` and review the diff. It is a regression anchor, not a sample: it has 60 aliases per country, so its rates are not the population rates.

## Reproduce

```
# the full run (needs the Wikidata export, which is not in the repository)
uv run python -I backend/scripts/name_fairness.py --out <dir> \
  --wikidata <external-data>/name-pairs/wikidata_alias_pairs.csv

# rebuild the offline regression fixture (DEV pairs only), then review the diff
uv run python -I backend/scripts/name_fairness.py \
  --wikidata <external-data>/name-pairs/wikidata_alias_pairs.csv \
  --write-fixture backend/tests/data/name_fairness_fixture.json

# the fast gate (offline, under a second)
uv run pytest backend/tests/test_name_fairness_regression.py -q
```

The Wikidata export is `wikidata_alias_pairs.csv` (17,990 pairs, 12,954 people; CC0; split by a hash of the QID, so a person is whole in one split). `--legacy-names` is no longer needed: the "baseline" column is the frozen copy of the 2026-10-29 matcher in `backend/scripts/_eval/names_baseline.py`, and it also decides which real pairs are usable and which synthetic fakes are redrawn as aliases, so the pair sets do not depend on the matcher under test (a test pins them to the sets every earlier table used). The older "old" column (the emulated pre-fix matcher) is still in `name_fairness.json` and no longer printed.

Tune on DEV only. The TEST tables below are the ones to quote.

**Last run: 3 November 2026**, code as committed. Rules were chosen and measured on DEV only (the DEV tables of the previous two sections). The full run that also printed TEST was made after the rules were fixed, and neither a rule nor a threshold was changed after seeing it. The baseline TEST figures are the ones documented before (2,189 of 7,088). A DEV copy of the Wikidata results follows the TEST tables.

### Synthetic TEST (2701 pairs)

#### Near-spelled different people (wrong_match)

| group | baseline | new | name only |
|---|---|---|---|
| western_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 73/120 = 0.608 [0.519-0.691] |
| chinese_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 1/120 = 0.008 [0.001-0.046] |
| malay_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 45/120 = 0.375 [0.294-0.464] |
| indian_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 53/120 = 0.442 [0.356-0.531] |
| mononym | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 77/120 = 0.642 [0.553-0.722] |
| titles | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 1/120 = 0.008 [0.001-0.046] |

#### All different-person classes pooled (wrong_match)

| group | baseline | new | name only |
|---|---|---|---|
| western_order | 0/200 = 0.000 [0.000-0.019] | 0/200 = 0.000 [0.000-0.019] | 73/200 = 0.365 [0.301-0.434] |
| chinese_order | 0/200 = 0.000 [0.000-0.019] | 0/200 = 0.000 [0.000-0.019] | 1/200 = 0.005 [0.001-0.028] |
| malay_patronymic | 0/240 = 0.000 [0.000-0.016] | 0/240 = 0.000 [0.000-0.016] | 45/240 = 0.188 [0.143-0.242] |
| indian_patronymic | 0/240 = 0.000 [0.000-0.016] | 0/240 = 0.000 [0.000-0.016] | 53/240 = 0.221 [0.173-0.277] |
| mononym | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 77/120 = 0.642 [0.553-0.722] |
| titles | 0/200 = 0.000 [0.000-0.019] | 0/200 = 0.000 [0.000-0.019] | 1/200 = 0.005 [0.001-0.028] |

#### Same person, surface forms and aliases (false_flag)

| group | baseline | new | name only |
|---|---|---|---|
| western_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| chinese_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| malay_patronymic | 0/160 = 0.000 [0.000-0.023] | 0/160 = 0.000 [0.000-0.023] | 0/160 = 0.000 [0.000-0.023] |
| indian_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| mononym | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| titles | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |

#### Same person with a one-letter typo (flagged by design) (false_flag)

| group | baseline | new | name only |
|---|---|---|---|
| western_order | 78/78 = 1.000 [0.953-1.000] | 78/78 = 1.000 [0.953-1.000] | 13/78 = 0.167 [0.100-0.265] |
| chinese_order | 102/102 = 1.000 [0.964-1.000] | 102/102 = 1.000 [0.964-1.000] | 102/102 = 1.000 [0.964-1.000] |
| malay_patronymic | 88/88 = 1.000 [0.958-1.000] | 88/88 = 1.000 [0.958-1.000] | 46/88 = 0.523 [0.420-0.624] |
| indian_patronymic | 77/77 = 1.000 [0.952-1.000] | 77/77 = 1.000 [0.952-1.000] | 23/77 = 0.299 [0.208-0.408] |
| mononym | 90/90 = 1.000 [0.959-1.000] | 90/90 = 1.000 [0.959-1.000] | 0/90 = 0.000 [0.000-0.041] |
| titles | 106/106 = 1.000 [0.965-1.000] | 106/106 = 1.000 [0.965-1.000] | 103/106 = 0.972 [0.920-0.990] |

#### Disparity of the same-person false-flag rate over the six groups (surface forms and aliases)

Max minus min of the group rates, with a 95% bootstrap interval (1,000 resamples, fixed seed).

| matcher | max - min [95% interval] | highest group | lowest group | worst group, Wilson upper bound |
|---|---|---|---|---|
| baseline | 0.000 [0.000-0.000] | western_order | western_order | 0.031 |
| new | 0.000 [0.000-0.000] | western_order | western_order | 0.031 |
| name_only | 0.000 [0.000-0.000] | western_order | western_order | 0.031 |

#### Hard-negative classes, new matcher (wrong_match)

`initials_sibling` is a sibling written as initials (`R. K. Nair` for `Ravi Kumar Nair`); it is not in the pooled rates above. The baseline matcher already accepts a few of them: an initial that lines up with another person's first name is `probable` by design.

| group | near_spelling | shared_family | shared_given | father_son | initials_sibling | initials_sibling, baseline |
|---|---|---|---|---|---|---|
| western_order | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | n/a | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] |
| chinese_order | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | n/a | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] |
| malay_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] |
| indian_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 1/40 = 0.025 [0.004-0.129] | 1/40 = 0.025 [0.004-0.129] |
| mononym | 0/120 = 0.000 [0.000-0.031] | n/a | n/a | n/a | n/a | n/a |
| titles | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | n/a | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] |

### Real Wikidata pairs (same person aliases; different people one or two letters apart)

The group is the country of the Wikidata item, not a name style. Several aliases of one person are one cluster in the bootstrap.

#### TEST: 7088 usable alias pairs of 8979, 153 different-person pairs

| matcher | false_flag (same person) | wrong_match (different people) |
|---|---|---|
| baseline | 2189/7088 = 0.309 [0.298-0.320] | 0/153 = 0.000 [0.000-0.024] |
| new | 2128/7088 = 0.300 [0.290-0.311] | 0/153 = 0.000 [0.000-0.024] |
| name_only | 1724/7088 = 0.243 [0.233-0.253] | 6/153 = 0.039 [0.018-0.083] |

One-letter slips among the real alias pairs (flagged by design): 404/7088 = 0.057 [0.052-0.063]

Not accepted by group (country of the item), and split into (a) a name matcher could reasonably accept and (b) needs other evidence:

| group | n | baseline not accepted | new not accepted | new (a) | new (b) |
|---|---|---|---|---|---|
| India | 4723 | 1721/4723 = 0.364 [0.351-0.378] | 1661/4723 = 0.352 [0.338-0.365] | 20/4723 = 0.004 [0.003-0.007] | 1641/4723 = 0.347 [0.334-0.361] |
| Malaysia | 1766 | 366/1766 = 0.207 [0.189-0.227] | 365/1766 = 0.207 [0.188-0.226] | 4/1766 = 0.002 [0.001-0.006] | 361/1766 = 0.204 [0.186-0.224] |
| Singapore | 599 | 102/599 = 0.170 [0.142-0.202] | 102/599 = 0.170 [0.142-0.202] | 0/599 = 0.000 [0.000-0.006] | 102/599 = 0.170 [0.142-0.202] |

Max minus min of the group rates, 95% cluster-bootstrap interval: baseline 0.194 [0.160-0.230], new 0.181 [0.148-0.218]

Categories of the pairs that are not accepted (count per group, new matcher; baseline in brackets):

| bucket | category | India | Malaysia | Singapore | all |
|---|---|---|---|---|---|
| a | spacing_or_order | 0 (33) | 0 (1) | 0 (0) | 0 (34) |
| a | honorific_only | 20 (20) | 3 (3) | 0 (0) | 23 (23) |
| a | documented_variant | 0 (25) | 0 (0) | 0 (0) | 0 (25) |
| a | initials_aligned | 0 (0) | 1 (1) | 0 (0) | 1 (1) |
| b | one_letter_variant | 548 (548) | 58 (58) | 10 (10) | 616 (616) |
| b | spelling_variant | 241 (241) | 17 (17) | 11 (11) | 269 (269) |
| b | initials_unaligned | 153 (153) | 16 (16) | 3 (3) | 172 (172) |
| b | dropped_name_part | 0 (0) | 0 (0) | 0 (0) | 0 (0) |
| b | shares_family_name_only | 318 (318) | 104 (104) | 19 (19) | 441 (441) |
| b | shares_first_name_only | 213 (214) | 36 (36) | 12 (12) | 261 (262) |
| b | other_name_parts | 168 (169) | 130 (130) | 47 (47) | 345 (346) |

Rows the usable filter drops: 1891 (by pair_type {'unrelated_token_set': 1138, 'token_added_or_dropped': 483, 'partial_overlap': 64, 'spelling_variant': 192, 'case_punct_diacritic_only': 7, 'initials_or_abbrev': 7}); of them not accepted: baseline 1611, new 1600. Over all 8979 rows of the split, not accepted: baseline 3800/8979 = 0.423 [0.413-0.433], new 3728/8979 = 0.415 [0.405-0.425].

Different people who share a name word (115801 pairs of strings of distinct Wikidata items): accepted as one person by baseline 665, by new 669; newly accepted by new: 4 (Teji Harivansh Rai Srivastava Bachchan / Harivansh Rai Shrivastav; Suman Chattopadhyay / Suman Chatterjee; Krushna Chandra Tripathy / Krushnachandra Tripathy; Krushnachandra Tripathy / Krushna Chandra Tripathy).



#### DEV: 7038 usable alias pairs of 9011, 153 different-person pairs

| matcher | false_flag (same person) | wrong_match (different people) |
|---|---|---|
| baseline | 2197/7038 = 0.312 [0.301-0.323] | 0/153 = 0.000 [0.000-0.024] |
| new | 2120/7038 = 0.301 [0.291-0.312] | 0/153 = 0.000 [0.000-0.024] |
| name_only | 1751/7038 = 0.249 [0.239-0.259] | 4/153 = 0.026 [0.010-0.065] |

One-letter slips among the real alias pairs (flagged by design): 369/7038 = 0.052 [0.047-0.058]

Not accepted by group (country of the item), and split into (a) a name matcher could reasonably accept and (b) needs other evidence:

| group | n | baseline not accepted | new not accepted | new (a) | new (b) |
|---|---|---|---|---|---|
| India | 4579 | 1681/4579 = 0.367 [0.353-0.381] | 1605/4579 = 0.351 [0.337-0.364] | 20/4579 = 0.004 [0.003-0.007] | 1585/4579 = 0.346 [0.333-0.360] |
| Malaysia | 1855 | 402/1855 = 0.217 [0.199-0.236] | 401/1855 = 0.216 [0.198-0.235] | 7/1855 = 0.004 [0.002-0.008] | 394/1855 = 0.212 [0.194-0.232] |
| Singapore | 604 | 114/604 = 0.189 [0.160-0.222] | 114/604 = 0.189 [0.160-0.222] | 1/604 = 0.002 [0.000-0.009] | 113/604 = 0.187 [0.158-0.220] |

Max minus min of the group rates, 95% cluster-bootstrap interval: baseline 0.178 [0.147-0.215], new 0.162 [0.132-0.199]

Categories of the pairs that are not accepted (count per group, new matcher; baseline in brackets):

| bucket | category | India | Malaysia | Singapore | all |
|---|---|---|---|---|---|
| a | spacing_or_order | 0 (46) | 1 (2) | 0 (0) | 1 (48) |
| a | honorific_only | 20 (20) | 5 (5) | 1 (1) | 26 (26) |
| a | documented_variant | 0 (25) | 0 (0) | 0 (0) | 0 (25) |
| a | initials_aligned | 0 (0) | 1 (1) | 0 (0) | 1 (1) |
| b | one_letter_variant | 470 (470) | 63 (63) | 15 (15) | 548 (548) |
| b | spelling_variant | 248 (248) | 31 (31) | 11 (11) | 290 (290) |
| b | initials_unaligned | 155 (155) | 16 (16) | 12 (12) | 183 (183) |
| b | dropped_name_part | 0 (0) | 0 (0) | 0 (0) | 0 (0) |
| b | shares_family_name_only | 341 (341) | 91 (91) | 23 (23) | 455 (455) |
| b | shares_first_name_only | 202 (204) | 47 (47) | 12 (12) | 261 (263) |
| b | other_name_parts | 169 (172) | 146 (146) | 40 (40) | 355 (358) |

Rows the usable filter drops: 1973 (by pair_type {'unrelated_token_set': 1146, 'partial_overlap': 76, 'token_added_or_dropped': 517, 'spelling_variant': 216, 'initials_or_abbrev': 9, 'case_punct_diacritic_only': 9}); of them not accepted: baseline 1635, new 1622. Over all 9011 rows of the split, not accepted: baseline 3832/9011 = 0.425 [0.415-0.435], new 3742/9011 = 0.415 [0.405-0.425].

Different people who share a name word (112254 pairs of strings of distinct Wikidata items): accepted as one person by baseline 656, by new 659; newly accepted by new: 3 (Subhash Mukhopadhyay / Subhash Mukherjee; Nandamuri Taraka Rama Rao / Nandamuri Tarakaramarao Jr.; Nandamuri Tarakaramarao / Nandamuri Taraka Rama Rao).

