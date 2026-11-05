# The review team

A case is looked at by six named members, in this order, after the cross-document checks and before
scoring. The trail of what each one saw, handed on and changed is stored on the case (`Case.agents`)
and shown in the analysis trail under "The review team" and, with every ruling of the challenger, as a step of the case's Timeline tab (frontend.md 5.3).

| Member | What it is | Model call | Can it change a flag? |
|---|---|---|---|
| Documents specialist | the forensic checks and file-reuse checks | no | no |
| Finance specialist | statement, invoice and financial-statement arithmetic, figures across documents, transaction labels | no | no |
| Identity specialist | ID numbers, passports, company numbers, directors, owners | no | no |
| Relationships specialist | payers, sources of funds, stated business against activity, model-based fit checks | no | no |
| Challenger | argues the innocent reading of each *judgement* flag | one call per case, only if there is such a flag | lowers a flag to `info`, only with a verified quote |
| Case officer | writes a short summary and up to three next steps | one call per case, only if a flag at medium or above stands | no, words only |

The specialists are the existing checks grouped by what they examine; each rule has exactly one owner
(`app/agents/roster.py`). They report how many checks ran and which flags they raised, and say how many
flags they pass to the challenger. They make no model call and cost nothing.

## The challenger

It sees the flags that are a matter of judgement (who or what a document refers to, whether
declarations fit): the name and identity rules, the cross-document party rules and the model-based
checks. It never sees arithmetic flags (a total that does not add up is not a matter of opinion),
forensic flags, duplicate-invoice flags, file re-use or the pass-through pattern.

It is asymmetric by design. It answers `upheld` or `doubtful` for each flag. A `doubtful` answer lowers
the flag to `info` only if the challenger names a document and copies a passage that really appears in
that document's extracted text (compared after folding case and whitespace, at least five characters).
No verified quote, an unknown flag id, a malformed reply or a failed call leave the flag as it was. A
lowered flag stays on the case as `info` with the challenger's reason in front of its explanation, so a
reviewer still sees it; it stops adding to the score and the queue, the same way a flag the second
reading of a scan did not reproduce does.

## The case officer

It reads the specialists' summaries, the flags still standing and the challenger's reasons, and writes a
summary and next steps. Step citations are checked against real flag ids. A step ends with the flag ids in square brackets; those trailing brackets are removed and replaced by the rule names, while other bracketed text in the sentence (for example a period such as "[March 2024]") is kept. The text never changes a flag,
the score or the lane, and the screen labels it as generated.

## Cost and switches

`GLDB_AGENTS` (empty = on under the anthropic backend, off otherwise; `false` turns it off). A case with
no flag at medium or above makes no extra model call. Both calls go through the same per-case and
per-day cost caps as every other call; if either fails the flags stand and the stage records a
`agents_unavailable` warning. The team is switched off inside the second-reading shadow run.

## What it does not do

The team does not read page images, does not look anything up, and does not decide a case. The
specialists are not autonomous: which checks run is fixed by the case's documents, and the only choice
the team makes is the challenger's ruling. Whether the challenger lowers false alarms without lowering
recall is a measurement, not a design property; see "Measured" below.

## Measured

One run on a fresh synthetic seed (9103), code frozen at commit 74fc3fe, 30 clean and 32 tampered scanned bundles (T01 4, T10 4, T58 10, T59 14; high-quality scans, Tesseract plus Haiku 5.5), team on. Flags before the team are reconstructed from the flags it lowered.

| | Before the team | After the team |
|---|---|---|
| Clean bundles with a rule-based false alarm | 2 / 30 (6.7%, 95% interval 1.8% to 21%) | 2 / 30 (same) |
| Clean bundles with any flag at medium or above | 3 / 30 (10%, 3.5% to 26%) | 3 / 30 (same) |
| Tampered bundles whose expected flag stands | 29 / 32 (90.6%, 75.8% to 96.8%) | 29 / 32 (same) |

- **The challenger lowered nothing.** It was asked about 24 cases with a judgement flag and found no passage in the documents that explains one, so it changed neither false alarms nor recall. It upheld both clean-bundle false alarms it was shown (`x_directors`, `passport_mrz`): no passage in the case explains them. We do not claim it helps.
- **The case officer's summaries are unmeasured.** 32 cases got one; whether reviewers find them useful needs a reviewer, not a rate.
- **Cost and time of the team:** challenger 24 calls, mean $0.0013 and 2.8 s; officer 32 calls, mean $0.0004 and 3.3 s; $0.044 in total over 62 cases. A whole case averaged $0.115 ($0.091 clean, $0.137 tampered) and 285 s clean, 408 s tampered with three cases running at once.
- **Reliability.** 4 of 56 model calls (3 officer, 1 challenger) were rejected because the model sent null or left out an optional field; the flags stood and the trail said so. The schemas now accept both (commit after 74fc3fe). The re-run was not repeated.
- **A finding outside the team.** The three tampered misses are T59 cases where `x_remitter_vs_buyer` was raised by the first reading and set aside as `info` because the second reading did not reproduce it (T59 11 / 14, interval 52% to 92%). On seed 9101 T59 was 4 / 4. This is the second reading costing recall, and it was not tuned on this seed.
- The seed is synthetic and single-run, and the data and the checks share an author. Every figure is an upper bound.
