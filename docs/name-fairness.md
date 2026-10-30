# Name matching: fairness on a larger DEV/TEST set, real aliases and honest limits

Scope: `backend/app/names.py` (person-name matching and `same_identity`), the dev/test generator in `backend/scripts/_gen/names_data.py` (extended pools) and the scorer `backend/scripts/_eval/name_fairness.py` (CLI `backend/scripts/name_fairness.py`). The 270-pair pipeline slice (`eval.py --suite fairness`) is unchanged and still runs. All names are fictitious or public Wikidata labels; the generator is ours, so every synthetic number is an upper bound.

## What was wrong

A near-spelled different person (`Suresh s/o Raman` against `Sulesh s/o Raman`, same ID number) passed as the same person in 3 of 15 Malay and 6 of 15 Indian pairs. It was a bug, not a design limit: `NameMatch.typo` looked for the reason prefix `one letter differs`, and the comparison for names with a `bin/binte/s/o/d/o` marker threw the inner reason away (`given names probable, patronym same`). Unmarked, Chinese, western and mononym names kept the prefix and were flagged. The matcher-only column in `fairness.py` ignores `.typo`, so it overstates the problem for western names (10/15) and mononyms (11/15) and is misleading; the pipeline column is the real one.

## What changed in `names.py`

1. A slip inside either part of a marked name now keeps its reason (`one letter differs in the patronym: abdullah/abdulrah`). `NameMatch` has explicit `slip` and `slip_kind` fields (`double_letter`, `vowel`, `other`); `typo` is `probable and slip`.
2. `identity_status()` returns `(match, needs, name)`; `same_identity()` is its first field. `needs` is `"id"` (an NRIC/FIN is missing, or only its last 4 characters are known) or `"dob_or_document"` (the ID is equal but the name differs by one letter, which looks the same for a typo and a deliberate substitution). A masked ID with equal last four characters and a slip is now `match_name_conflict`; it used to be `unknown`, which every rule skips silently.
3. Normalisation: initials expansion for several initials (`H.L. Gokhale` for `Hemant Laxman Gokhale`, `P. U. Chinnappa` for `Pudukkottai Ulaganathan Pillai Chinnappa`; a shared name must anchor it), and the Muhammad spellings `mhd`, `muhamed`, `mohmmad`, `mohamod`, `mohammod`. These spellings are priors, not DEV-selected: measured one at a time, none of them changes a DEV outcome, and `muhamed` gains one TEST pair only. The multi-initial rows were measured only against negatives that contain no initials (see the table below), so "no cost" there means no cost on those negatives, not none. `ab` is not an alias: `AB` is also a pair of initials, so `Ab`/`Abdul` as the only difference is `probable` (reason `short form`), never `same`.

## Results (TEST split unless stated)

Pipeline view with the same ID number on both sides, so only `match` passes; 120 near-spelled pairs per group; Wilson 95% intervals; "old" is the real previous `names.py` run on the same pairs.

| group | wrong match, old | wrong match, new | false flag, same person (surface forms, aliases) |
|---|---|---|---|
| malay_patronymic | 45/120 = 0.375 [0.29-0.46] | 0/120 [0.00-0.03] | 0/160 [0.00-0.02] |
| indian_patronymic | 53/120 = 0.442 [0.36-0.53] | 0/120 [0.00-0.03] | 0/120 [0.00-0.03] |
| western, chinese, mononym, titles | 0/120 each | 0/120 each | 0/120 each |

Hard negatives (shared surname or patronym, shared given name, father and son swapped; 40 per class and group) are 0/40 wrong matches in every group. The real pipeline slice (270 pairs, fake LLM, dev seed 4002) went from 3/15 and 6/15 wrong matches to 0/15 in both groups, 0 false flags, disparity 0.0; with 15 pairs per group the Wilson 95% upper bound of 0/15 is 0.20, and the Malay change alone (3 to 0, exact paired p = 0.25) is within noise, the Indian change alone (6 to 0, p = 0.03) is borderline, and the two together (9 to 0 of 30 pairs, p = 0.004) are not.

Real Wikidata aliases (same person, public export, 7,088 usable TEST pairs after the filter in `usable_positive`): not accepted 39.7% with the old matcher, 30.9% with the new one (India 36.4%, Malaysia 20.7%, Singapore 17.0%). This is the final-matcher re-run of 29 October 2026 (see Reproduce); the count moved by one pair, 2,188 to 2,189 of 7,088, and every percentage here is unchanged to the first decimal. This is the honest fairness cost and it is large: Aliases from India are not accepted about 1.8 times as often as those from Malaysia (36.4% against 20.7%) and 2.1 times as often as those from Singapore (17.0%); the split is by the country of the Wikidata entry, not by name style, and there is no Chinese-order breakdown. Where the TEST pairs fail (split measured for the earlier 2,188; not re-derived after the extra pair, which is in one of the four classes): 1,165 (16%) have different or extra name parts (nicknames, titles, a second name, spelling noise inside Wikidata itself), 431 (6%) are respelled in two or more letters or in short tokens, 416 (6%) are one-letter slips that are flagged by design, 176 (2.5%) have initials that do not line up. The one-letter-slip count (416) was re-measured and is the same. Different people one or two letters apart (153 pairs): 0/153 accepted.

## What was tried and not adopted (DEV, 7,038 real alias pairs, 1,200 synthetic negatives)

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

- One-letter differences with the same ID number. A typo and a deliberate substitution are the same string. The new matcher flags 100% of them (0 of 720 TEST near-spelled fakes pass; all 541 TEST same-person typos are flagged too). Any genuine document with a one-letter slip is flagged, so the real false-flag rate roughly equals the slip prevalence. On Wikidata Singapore/Malaysia/India aliases, 5.9% of usable pairs are one-letter variants of the same person (`Zaleha Kamarudin`/`Zaleha Kamaruddin`, `Mani Ratnam`/`Mani Rathnam`). The benchmark's different-person pairs share an NRIC, which a real different person would not.
- Grading by slip kind would not separate them. Of 416 real one-letter variants, 220 are vowel and 46 double-letter (64%); of the 250 one-letter fakes our generator draws, 114 are vowel swaps (46%) and none is a double letter, because its substitution table swaps vowels. Lowering severity for those kinds would cut the review load of genuine slips by about 64% and also lower the severity of 46% of the generator's fakes. This is a policy decision, not a measured improvement.
- Probable matches without an ID (`J. Wong`/`Jessica Wong`, dropped prefix `Muhammad`, mononym with surname) are accepted on the name alone; `identity_status().needs == "id"` records it.
- Dialect against pinyin surnames (`Tan`/`Chen`), nicknames, married names, transliterated Chinese characters and stage names: `different` or `indeterminate`, a fairness cost that stays.
- A deliberate substitution that keeps the NRIC and uses a true alias spelling (`Mohd` for `Muhammad`) is the same person to the matcher.
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

## Reproduce

```
uv run python -I backend/scripts/name_fairness.py --out <dir> \
  --wikidata <external-data>/name-pairs/wikidata_alias_pairs.csv \
  --legacy-names <old names.py from git history>
```

Tune on DEV only. TEST tables below are the ones to quote.

Last run: 29 October 2026, final matcher (after the review removed the alias `ab` and changed the slip tie-break), TEST split once, legacy column from the committed `names.py`. Nothing was changed after seeing the result. Against the numbers documented before the review, every synthetic TEST cell is identical. Only the Wikidata counts moved, by one pair in each case: TEST not accepted by the new matcher 2,188 to 2,189 (0.309, unchanged to three decimals) and by name only 1,772 to 1,773; DEV 2,196 to 2,197 and 1,824 to 1,825. The pair was not identified (the pre-review matcher is not in git), so the cause is not proven; the `ab` removal is the likely one. The tables below show the new values.

### Synthetic TEST (2501 pairs)

### Near-spelled different people (wrong_match)

| group | old matcher | new matcher | name only |
|---|---|---|---|
| western_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 73/120 = 0.608 [0.519-0.691] |
| chinese_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 1/120 = 0.008 [0.001-0.046] |
| malay_patronymic | 45/120 = 0.375 [0.294-0.464] | 0/120 = 0.000 [0.000-0.031] | 45/120 = 0.375 [0.294-0.464] |
| indian_patronymic | 53/120 = 0.442 [0.356-0.531] | 0/120 = 0.000 [0.000-0.031] | 53/120 = 0.442 [0.356-0.531] |
| mononym | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 77/120 = 0.642 [0.553-0.722] |
| titles | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 1/120 = 0.008 [0.001-0.046] |

### All different-person classes pooled (wrong_match)

| group | old matcher | new matcher | name only |
|---|---|---|---|
| western_order | 0/200 = 0.000 [0.000-0.019] | 0/200 = 0.000 [0.000-0.019] | 73/200 = 0.365 [0.301-0.434] |
| chinese_order | 0/200 = 0.000 [0.000-0.019] | 0/200 = 0.000 [0.000-0.019] | 1/200 = 0.005 [0.001-0.028] |
| malay_patronymic | 45/240 = 0.188 [0.143-0.242] | 0/240 = 0.000 [0.000-0.016] | 45/240 = 0.188 [0.143-0.242] |
| indian_patronymic | 53/240 = 0.221 [0.173-0.277] | 0/240 = 0.000 [0.000-0.016] | 53/240 = 0.221 [0.173-0.277] |
| mononym | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 77/120 = 0.642 [0.553-0.722] |
| titles | 0/200 = 0.000 [0.000-0.019] | 0/200 = 0.000 [0.000-0.019] | 1/200 = 0.005 [0.001-0.028] |

### Same person, surface forms and aliases (false_flag)

| group | old matcher | new matcher | name only |
|---|---|---|---|
| western_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| chinese_order | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| malay_patronymic | 0/160 = 0.000 [0.000-0.023] | 0/160 = 0.000 [0.000-0.023] | 0/160 = 0.000 [0.000-0.023] |
| indian_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| mononym | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |
| titles | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] | 0/120 = 0.000 [0.000-0.031] |

### Same person with a one-letter typo (flagged by design) (false_flag)

| group | old matcher | new matcher | name only |
|---|---|---|---|
| western_order | 78/78 = 1.000 [0.953-1.000] | 78/78 = 1.000 [0.953-1.000] | 13/78 = 0.167 [0.100-0.265] |
| chinese_order | 102/102 = 1.000 [0.964-1.000] | 102/102 = 1.000 [0.964-1.000] | 102/102 = 1.000 [0.964-1.000] |
| malay_patronymic | 46/88 = 0.523 [0.420-0.624] | 88/88 = 1.000 [0.958-1.000] | 46/88 = 0.523 [0.420-0.624] |
| indian_patronymic | 23/77 = 0.299 [0.208-0.408] | 77/77 = 1.000 [0.952-1.000] | 23/77 = 0.299 [0.208-0.408] |
| mononym | 90/90 = 1.000 [0.959-1.000] | 90/90 = 1.000 [0.959-1.000] | 0/90 = 0.000 [0.000-0.041] |
| titles | 106/106 = 1.000 [0.965-1.000] | 106/106 = 1.000 [0.965-1.000] | 103/106 = 0.972 [0.920-0.990] |

### Hard-negative classes, new matcher (wrong_match)

| group | near_spelling | shared_family | shared_given | father_son |
|---|---|---|---|---|
| western_order | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | n/a |
| chinese_order | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | n/a |
| malay_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] |
| indian_patronymic | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] |
| mononym | 0/120 = 0.000 [0.000-0.031] | n/a | n/a | n/a |
| titles | 0/120 = 0.000 [0.000-0.031] | 0/40 = 0.000 [0.000-0.088] | 0/40 = 0.000 [0.000-0.088] | n/a |

### Real Wikidata pairs (same person aliases; different people one or two letters apart)

### DEV: 7038 usable alias pairs of 9011, 153 different-person pairs

| matcher | false_flag (same person) | wrong_match (different people) |
|---|---|---|
| old | 2787/7038 = 0.396 [0.385-0.407] | 0/153 = 0.000 [0.000-0.024] |
| new | 2197/7038 = 0.312 [0.301-0.323] | 0/153 = 0.000 [0.000-0.024] |
| name_only | 1825/7038 = 0.259 [0.249-0.270] | 4/153 = 0.026 [0.010-0.065] |

One-letter slips among the real alias pairs (flagged by design): 372/7038 = 0.053 [0.048-0.058]

### TEST: 7088 usable alias pairs of 8979, 153 different-person pairs

| matcher | false_flag (same person) | wrong_match (different people) |
|---|---|---|
| old | 2814/7088 = 0.397 [0.386-0.408] | 0/153 = 0.000 [0.000-0.024] |
| new | 2189/7088 = 0.309 [0.298-0.320] | 0/153 = 0.000 [0.000-0.024] |
| name_only | 1773/7088 = 0.250 [0.240-0.260] | 6/153 = 0.039 [0.018-0.083] |

One-letter slips among the real alias pairs (flagged by design): 416/7088 = 0.059 [0.053-0.064]

