# The review team

A case is looked at by six named members, in this order, after the cross-document checks and before
scoring. The trail of what each one saw, handed on and changed is stored on the case (`Case.agents`)
and shown in the analysis trail under "The review team".

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
summary and next steps. Step citations are checked against real flag ids. The text never changes a flag,
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

Not yet measured.
