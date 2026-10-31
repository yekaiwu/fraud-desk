# What this tool cannot do, and what we have not proven

This page is for anyone deciding how far to trust the tool. It lists the things the tool cannot detect, the things we measured only in a simulation, and the places where an earlier claim was wrong or too strong. It is written in plain language. Every claim points to the file that backs it.

The tool never approves or rejects anyone. It raises flags and ranks cases for a human reviewer. All data used here is fictitious or public sample data.

## How to read the evidence links

- The numbers come from the evaluation runs in `docs/eval-results/`. That folder is not committed to the repository (see `.gitignore`), except `baseline.json` and `timed-test.csv`. It is written on your machine when you run the evaluation, so a link into it works only after you have run it, or if you were given a copy.
- The main file is `docs/eval-results/before-after.md`. It compares five runs and was checked against the raw per-case records in `docs/eval-results/audit.md` (the audit found no arithmetic errors; it found stale or overstated wording, which was corrected). The held-out runs are summarised in `docs/eval-results/heldout/summary.md`.
- The runs are: DEV (seed 4002), BEFORE the changes and AFTER them, and HELD-OUT (seed 9001) for the original code, the original code with the evaluation fix, and the final code. How to run the evaluation: `docs/test-data-and-eval.md`, section 3.1 (`uv run python backend/scripts/eval.py`, with `--seed` choosing the data set).
- The timed analyst test (`timed-test.csv`) was not re-run. Run times in these reports differ between runs by up to seven times for the same code, and we did not find out why, so read no speed claim into them.
- Outside tests (public data sets, simulated scans, real OCR) are described in `docs/eval-results/external/README.md`. Their scripts are in `backend/scripts/ext_eval/` (see its `README.md`).
- A number written `k/n [lo-hi]` is a count out of a total, with a 95% range. Wide ranges mean the sample is small. Read the range, not just the count.

## 1. A forger who recomputes every figure is invisible

If someone edits one amount in a bank statement and then recomputes every total, running balance and closing balance that depends on it, the statement adds up. The arithmetic checks have nothing to complain about. The same holds for dates that stay in order and in the right period, and for plausible-looking values.

What we measured:

- In our main evaluation, the tamper called T02c ("every dependent number recomputed") was caught 0 out of 5 times, in all five runs (dev before and after, and all three held-out runs). `docs/eval-results/before-after.md`, section "Cannot be established, or only partly".
- In a separate local test modelled on the published description of FraudRAG (we re-implemented it; we did not obtain the original data), edits made consistently were not caught from one statement: opening balance shifted 0/24, one amount changed with everything recomputed 0/40, rows inserted with everything recomputed 0/30, a date moved but kept in order 0/24. The same kinds of edit made without recomputing were caught (for example 24/24 for dates moved outside the period, 30/30 for rows inserted without updating balances). `docs/eval-results/external/fraudrag-local.md`.
- A consistent edit can be caught only with an outside reference. In the same local test, a consistent edit to one month sitting between two untouched months of the same account was caught 20/20 by the check that compares neighbouring months. That works only if the neighbouring months are uploaded and untouched.
- Differences of 1.00 or less in money are inside the tolerance and are not flagged (0/16 for small digit changes). Same file.

What this means: a good forger working from the statement alone is not stopped by these checks. Treat "no flags" as "nothing in the document contradicts itself", not as "genuine".

## 2. AI inpainting and pixel forensics

The image checks look for signs of editing in the pixels: a patch saved at a different JPEG quality (`ela_region`), a repeated area (`copy_move`) and a patch with different noise (`noise_mismatch`). When an AI model redraws a word or a region, it writes the area to match its surroundings, so these signs mostly are not there. `docs/eval-results/external/README.md` has the explanation.

What we measured:

- On RealText-V2 (a public set of generic text images such as agendas and posters, not financial documents; 115 images, 70 forged and 45 genuine), after resizing and re-saving as JPEG: the checks raised a flag on 16 of 45 genuine images (0.36 [0.23-0.50]) and on 30 of 70 forged images (0.43 [0.32-0.55]). That is almost the same rate on both groups, so the flag carries no useful signal. `docs/eval-results/external/forensics-realtext.md`.
- Of the 30 forged images that were flagged, only 3 had a flagged region on the edited pixels. The edits below 0.5% of the page were flagged 10 of 25 times. Same file and `forensics-realtext-breakdown.md`.
- On our own generated scans, three image tampers are mostly missed. T41 (photo swapped on an ID), T42 (a region cloned) and T43 (an amount retyped over a white box) were each caught 0 or 1 times out of 2 or 3 on every scan and photo render. In most of those cases the same check was already firing on the untouched page, so the catch cannot be credited to the check. `docs/eval-results/before-after.md`, section "Cannot be established, or only partly"; `docs/forensics.md`, last section.
- The thresholds in the image checks were starting guesses and were not retuned, because retuning on three clean scans and our own edits would only fit the generator (`docs/forensics.md`).

What this means: the image checks are low-severity hints that need corroboration. They do not detect modern AI regeneration of text.

## 3. Scans beyond the simulation

Most scan numbers in `before-after.md` come from an "oracle": the evaluation hands the tool the true text of each page instead of making it read the image. That is an upper bound. When the tool reads the pixels itself with a real OCR engine, the picture is much worse.

What we measured (Apple Vision OCR plus the repo's rule-based readers, no vision model, three clean bundles per render, so every range is wide):

- Clean bundles still raise false alarms on real OCR. On the final code, every render that could be read at all raised a medium-or-high flag on 3 of 3 clean bundles: `scan_hq` 51 flags, `scan_pdf` 58, simulated light scan 59, simulated medium scan 79. With the oracle, the same bundles raised 0 flags. The false alarms come from reading errors. `docs/eval-results/before-after.md`, section "Real OCR on the final code"; `docs/eval-results/real-ocr-tamper/real-ocr.md`; `docs/eval-results/real-ocr/real-ocr.md` (clean bundles, an earlier code state).
- Tamper recall drops. On `scan_hq`, the oracle caught 13/13 test tampers; real OCR caught 1/13 (0.08 [0.01-0.33]). Simulated light scan 7/13, medium 6/13, heavy 0/13. Because the clean base already carries OCR flags, even these catches can coincide with noise. Same section.
- Reading quality fell between two runs and the reason is unknown. On `scan_hq`, statement rows read went from 575/604 to 424/604 and valid balance chains from 13/15 to 0/15 between the pre-review run and the run on the final code. Field accuracy on `scan_pdf` went the other way (600/783 to 716/783). The cause was not diagnosed, nothing was tuned, and it may be a regression. Same section.
- Heavy degradation is not read at all. In the simulated heavy render, 6 of 63 documents were found and 0 of 604 statement rows were read. Its "0 false alarms" in the earlier run means "unread", not "clean". `real-ocr.md` (above).
- The real-OCR numbers depend on the OCR engine of the machine that ran them. The same code and the same page images read at 575/604 statement rows in one run and 424/604 in a later run. Re-running the original code from before our changes, on identical images, gave the lower number again, so the drop is not caused by our code. Apple Vision on the machine used for the later runs adds stray characters to some amounts (for example `57,038.7źîŘ`) and sometimes swaps a comma for a full stop, so those rows cannot be parsed.
- Real OCR was not run on the final code for the `photo` and `scan_lq` renders. For those, the only real-OCR numbers are from an earlier code state (`before-after.md`, section "Real OCR (Apple Vision) on clean bundles"): 3/3 clean bundles flagged in both.
- Page-by-page JPEG saving costs accuracy. In the real-OCR tamper run, the same simulated scans saved as JPEG pages caught fewer tampers than as PDF scans: light 3/13 against 7/13, medium 2/13 against 6/13. Documents found at medium: 25/63 against 40/63. `real-ocr-tamper/real-ocr.md`.
- The tool does not read scans with a vision model in any of these runs. A live check of the paid model path is described in section 9.

Anything whose evidence lived inside the PDF (metadata, fonts, hidden text) is gone once a document is scanned. Real OCR errors also create false alarms in the arithmetic rules, so the fast lane is closed on scanned cases (`docs/eval-results/external/README.md`, "What this tool cannot detect"). A page dropped from a page-by-page upload cannot be told apart from a page never uploaded, so it is reported as "pages not reviewed" rather than as tampering (`before-after.md`, "Cannot be established").

## 4. A genuine document with false content

The tool checks that a document is consistent with itself and with the other documents in the case. It cannot check that what is printed is true. A real, unaltered statement from a real bank, or a real certificate, can carry content that is false or misleading: a genuine company with a different real person's identity details, an account that is real but not the applicant's, income that is real but irrelevant. Nothing is edited, so nothing is detected.

We have not measured this case, because there is nothing for the checks to detect: no tamper in our test set represents a true document that states something untrue. Treat it as a gap, not a pass. The related statement is in `docs/eval-results/external/README.md`, section "What this tool cannot detect": consistent forgeries "need an outside reference (the bank, neighbouring months, a ledger)". Where there is an outside reference (the company registry in `data-model.md`, other documents in the case), the cross-document checks use it, and those are the only defence here.

## 5. Synthetic-only evidence

Every document, every rule, and every tamper in the main evaluation came from this project. The same people wrote the generator that makes the documents, the rules that check them, and the tampers that test them. A tamper is easy to catch when the rule was written to catch exactly that shape.

- Our digital recall is high (dev after: 464/465 on the core tampers, 503/520 across all measured rows; held-out final code: 465/465 and 505/520). `docs/eval-results/before-after.md`. Part of the gain (for example the tampers T08, T35, T59 and T09, whose rules were written for the generator's shapes) is on tamper types outside the core set. Treat all of these as upper bounds on how the tool would do on real forgeries.
- The dev seed 4002 was looked at during scan and rule work. Its "after" numbers are therefore not an out-of-sample estimate, and we call it DEV. The held-out seed 9001 was not used to tune anything. One earlier run of the original code on it only measured. No file can prove that seed 9001 was never looked at before its first recorded run; this is a statement of how the runs were made (`heldout/summary.md`).
- Both seeds come from the same generator. Held-out numbers do not show that the tool generalises to real documents, only that it did not just memorise one seed.
- Most gains on the scan renders are within noise. On held-out, scan recall moved from 62/89 to 66/89 (p = 0.125 by an exact paired test), which is not distinguishable from chance. The same original code scored 53/89 on dev and 62/89 on held-out, a spread larger than that gain. `before-after.md`, section "Is the change larger than the noise?".
- Clean digital bundles raised no medium or high flags in any run: 0/33 (0.00 [0.00-0.10]). That bounds the false-alarm rate on our own clean documents at about 10% at most, not at zero, and says nothing about real statements. Real bank layouts and real PDF producers are unknown to the PDF checks, and a statement printed through a browser would be flagged as a false alarm (`docs/forensics.md`, limits).
- The fairness test (270 name pairs) passes in the final code, using pass criteria we proposed ourselves (`before-after.md`, "What changed in the measurement itself"). The names come from our own generator.
- No model cost or accuracy was measured in the main runs: they use a fake client (cost 0.000 per bundle).

## 6. Name matching: what is still undecided

The name matcher was changed so that a one-letter difference between two spellings is no longer accepted as "same person" on the name alone. That fixed a real bug: a near-spelled different person used to pass, 6 of 15 Indian-patronymic and 3 of 15 Malay pairs on dev (`name-fairness.md`, top). On the pipeline test, wrong matches are now 0/15 in all six groups on both seeds.

What is left:

- Real aliases of the same person are often not accepted. On the test split of a public alias list (Wikidata, India, Malaysia, Singapore), 2,188 of 7,088 same-person alias pairs are not accepted, so about 69% are accepted and about 31% are not. It was 2,814 of 7,088 before. India is the worst: 1,721 of 4,723 not accepted. `before-after.md`, section "Name matcher on real aliases"; `docs/name-fairness.md`. (That document reports 2,189 for the very last re-run; the file for that run was not found, so this page uses the 2,188 that can be checked.)
- A typo and a deliberate substitution cannot be told apart by the name alone. "Zaleha Kamarudin" against "Zaleha Kamaruddin" is the same string change as a person who swapped one letter on purpose. The matcher flags all of them: 0 of 720 near-spelled fakes pass, and all 541 same-person typos in the test split are flagged too. So a genuine document with a one-letter slip is flagged. `docs/name-fairness.md`, section "Pairs that name alone cannot decide".
- The tool does not decide these cases. It adds a sentence to the flag telling the reviewer that "a typo and a different person look alike, so check the date of birth or the ID document". The severity, the evidence and the number of flags are unchanged (`docs/name-fairness.md`, "Routing `identity_status().needs`"). No date of birth is compared by the tool, because the registry record has no date of birth field.
- The note appears only on one-letter slips inside longer names. In Chinese-order and titled names, a one-letter change reads as a different name, so those flags carry no note. Dialect against pinyin surnames (Tan and Chen), nicknames, married names and transliterated names are not resolved at all (`docs/checks-kyc-aml.md`, limits).
- A deliberate substitution that uses a true alias spelling (for example "Mohd" for "Muhammad") keeps the same ID number and is accepted as the same person.

## 7. Simulated scans are not physical scans

The "scan" and "photo" renders in our evaluation are software: the generator degrades a PDF with blur, noise, rotation, perspective and shadow. The `scan_sim_*` renders are a separate software model of print-and-scan at three strengths. We did not print any document and scan it with a scanner or photograph it with a phone (`docs/eval-results/real-ocr-tamper/real-ocr.md`, limits section; `backend/scripts/ext_eval/README.md`).

What that leaves unproven:

- Real print, paper, toner, folds, staples, stamps, handwriting, shadows from real light, phone-camera compression and multi-column real layouts.
- Image tampers on the simulated renders. They carry no PDF or phone metadata and no pixel-level tamper, so those tamper kinds are not planned on them (same limits section).
- Dropped pages on scans. The standard evaluation has no scan row for a removed page (T06). A hand-off report said 0/9 were caught; the raw file was not preserved and a final-code run was never finished, so that number cannot be reproduced and is not relied on here (`before-after.md`, "Cannot be established"). By design, a dropped page on a page-by-page upload is now shown as "not reviewed" rather than flagged.
- Scan acceptance as a pass/fail test is worse now than before: 2/9 bundles passed before and 0/9 after (dev and held-out), because the final code marks a scan case as partial when it cannot assemble a statement. This is a stated design choice, not a measured improvement (`before-after.md`, same section).

## 8. Public data sets we used, and what they do not show

We looked for public data that matches our documents and did not find any for bank statements or payslips.

- RealText-V2 is generic text (agendas, forms, posters); see section 2. It is not financial data, and its licence is non-commercial (`forensics-realtext.md`).
- DocSplit is used only for page grouping: it has no page images and no statements, so we replaced each multi-page document by a synthetic scanned statement with the same page count and arranged the pages in the packet's real order. The statement-assembly code made no wrong grouping or ordering in any of the 8 conditions tried (6,287 multi-page statements; with noisy cues 5,500 of 6,287 recovered, 787 not reviewed). That shows how the assembler copes with page order and gaps. It says nothing about the real documents in that data set. `docs/eval-results/external/docsplit-topology.md`, `external/README.md`.
- The FraudRAG-style test is our own re-implementation of the published idea, on our own statements. The original data set was not obtainable; its results are not comparable to any published score (`fraudrag-local.md`).
- Not obtained, so no result and no claim: the La Rochelle payslip data set (the host had an expired certificate), the Find-It-Again receipts data set (same), AIForge-Doc v2 (gated; AI-made forgeries, the case this tool is least able to detect), SIDTD (no data link; European IDs only) and the original FraudRAG-2400 data (the maintainer says it is missing) (`external/README.md`).
- We are not claiming that anything measured on these sets transfers to real bank statements, payslips, IDs or receipts.

## 9. The live model check is a sanity check, not an evaluation

One short check was run against the paid Claude Haiku model path (`docs/anthropic-haiku.md`): 3 pages, from one clean sample bundle, in one photo-style render, one run each, in two modes. The model read 33 of 35 fields correctly (94.3%, range about 81% to 98%), and both misses were differences in what counts as the right label rather than misreads. Live spend was about $0.008.

This does not show how accurate the model is. Three clean, typeset pages from our own generator say little about real scans. No tampered documents, bank statements (long tables), multi-page documents, handwriting or heavy degradation were sent. It checked that the path works end to end and gave the first measured cost per page. `docs/eval-results/live-model-check.md` has the details. The script that ran it was a one-off and is not in the repository, so this check cannot be re-run from the repo as it stands.

The main numbers in this document all use a fake client or the oracle, or Apple Vision OCR. None uses a live model.

## 10. The scan false-alarm story, told correctly

An earlier summary said clean scans raised "380 false alarms per 10 bundles". That was the baseline recorded in `docs/eval-results/baseline.json` from the original code. The figure was misleading in three ways:

- It counts flags, not bundles. 114 flags over 3 clean bundles is 38 per bundle, shown as 380 per 10. The comparable count is bundles with at least one flag: 3 of 3. Evidence is three bundles, so the range on any such rate is wide (0/3 has an upper bound of 0.56). `before-after.md`, "What changed in the measurement itself".
- The three renders (`scan_hq`, `scan_lq`, `photo`) used the same 3 bundles, so there are not three independent confirmations.
- Most of it came from uploading a statement as one image per page. The tool did not put the pages back together, so each page was treated as a whole statement and the printed-total and page-chain rules fired on fragments. The same bundles uploaded as one scanned PDF per document gave 0 flags (`scan_pdf`: 0 on both seeds, in all five runs). `docs/ingest-and-extraction.md`, section 4.11 and its limits; `before-after.md`.

How the count moved on held-out seed 9001: 117 flags with the original code; 71 once the evaluation oracle's own defect was fixed; 0 with the final code (which reassembles page images into statements). About 39% (46 of 117) of the original number came from the oracle defect, not from the tool; the remaining 71 went to zero with the final code. `before-after.md`, "Is the change larger than the noise?"; `audit.md`, section 6.

What is not proven:

- No run isolates reassembly from the other code changes in the final version, and no run of the final code with the defective oracle exists, so the share due to reassembly alone is not measured. "Mainly reassembly" is the best-supported reading (the file-per-document control is silent), not a measured fraction. `audit.md`, section 6, limit (ii).
- The 0 is from oracle extraction. With real OCR on the final code, clean `scan_hq` and `scan_pdf` bundles flag 3 of 3 (51 and 58 flags); see section 3. A zero from the oracle is an upper bound on how well the tool does on real scans. The false-alarm problem on real OCR is not solved; only the reassembly cause was removed.
- This is 3 clean bundles per render, and the dev seed 4002 was looked at during this work.

## 11. Semantic signals: what a model adds, and what it cannot be trusted with

Three checks (five rules) let a language model read meaning that the rules cannot: what a bank-statement line is (`sem_txn_labels`), whether two differently written names are one entity (`sem_entity_link`), and whether what is declared fits what the documents show (`sem_goods_fit`, `sem_income_fit`, `sem_coherence_fit`). They are on whenever a model is configured and can be turned off with `GLDB_SEMANTIC=off`; with them off nothing changes: the checks are not even counted, so no existing number or coverage ratio moves. The design is in `docs/semantic-signals.md`. The held-out results are in `docs/eval-results/semantic/summary.md` (a local file; see "How to read the evidence links" above) with the numbers in `docs/eval-results/semantic/metrics.json`.

What they add, measured on a held-out set (seed 7001) with a local 27B model, one run of frozen prompts, three repeats:

- Labels for statement lines: 369/384 correct (0.96 [0.94-0.98]), 274/288 on misspelt, abbreviated or romanised descriptions (0.95 [0.92-0.97]). No false risky label on 355 control lines.
- Entity names: the model recognised 180/192 true aliases (0.94 [0.89-0.96]) and merged 0/192 different pairs, where the existing string matcher recognised 82/192 and merged 35/192 different pairs (0.18).
- Declared-against-shown fit: 59/72 planted tampers flagged (0.82 [0.72-0.89]); 58/72 cited the planted evidence under a strict match (the one other case cited four of the five planted rows but left the column key empty, so the strict match counts it as a miss). We took the rules to flag none of these. That is an assumption: the base cases were tested against the loan and AML rule sets only, not the KYC rule set, where the rule `x_declared_vs_activity` flags crypto and gambling counterparties by code. The "baseline of 0" is therefore not a measured baseline.

What they cannot do, and what is not proven:

- The data is hand-written by us. The project team wrote the catalogues, templates, name pools, perturbation functions and answer keys; no model wrote or labelled a row. The held-out set differs from the dev set in brand words, vendors, founders and industries, but shares the generator code, templates, perturbation functions, generic words and the vocabulary the prompt glossaries cover (the glossaries were written after reading dev errors). So the held-out numbers show that the prompts did not memorise dev tokens, not that they work on text written by someone else. The Malay, romanised Chinese, abbreviation and dialect cases are the most inflated. Every number is an upper bound for real customer text. We have no result on real bank statements, real company registers or real applicants.
- The dev tables for transaction labels and entity names cannot be reproduced from the dev files in the repository. Those two prompts were tuned on copies of the dev data made before the generator was revised (336 label rows; about a third of the 384 entity pairs differ). The dev files were then regenerated (384 label rows, a different set of rows), and the held-out run used the regenerated generator. The freeze records for these two features hold the older dev hashes; the fit records match. We found no sign of leakage into the held-out set (no shared entity names; a few fixed label lines in common), but that is a spot check. Details: `docs/semantic-signals.md`, section 9.
- The adoption thresholds are written in the held-out summary only. No timestamped copy from before the run exists, so we cannot show they were fixed in advance. What is verifiable: the prompt, module and held-out data hashes match the freeze records, and those records are dated before the first held-out run. The three harness scripts were also edited 54 seconds after the first run started (an output-naming fix); no earlier copy exists, but the row-level files reproduce every stored score.
- A small local model is not a strong model. The same prompts were not tried on a second model: the paid Claude Haiku spot check was not run (no key was available), so whether the prompts carry over to another model is unknown. The cross-model question is open, not answered.
- They miss things. Labels recall only 23/29 (0.79) of gambling and crypto lines, and the six misses are abbreviations such as `CSNO`. Fit misses 13 of 72 tampers outright and is weakest on the hard ones (14/24 with evidence). Entity misses 12/192 true aliases, mostly Chinese dialect spellings and typos. Run to run, overall label accuracy moved by 0.8 points (369, 372, 372 of 384) and entity recall by 2.1 points (180, 179, 183 of 192). Recall on the risky label classes moved by 6.9 points (23, 23, 25 of 29), which is two rows. 16 of 384 lines and 15 of 384 pairs changed answer in at least one repeat. The fit repeats were identical, but each case is one independent call, so that shows the server is deterministic, not that the model is stable.
- They also raise false alarms. Fit flagged 3 of 120 ordinary or unusual-but-legitimate controls at medium (2.5% [0.9-7.1%]), and each is a wrong flag a reviewer would see. Entity added 33 low flags (20 distinct) to 10 of 33 clean bundles and took 3 of 15 fast-lane clean bundles out of the fast lane.
- Fairness is not settled. The entity check raised the difference in low-flag rates between name groups from 0.00 to 0.33 in the name-pair probe (low flags per group of 45 pairs: 15, 11, 9, 0, 14 and 6; the design says the difference must not rise). The spread seems to follow how many near-spelled pairs each naming style produces, not a different attitude of the model per group, but that was not tested separately. The label and fit checks could not be exercised by the 270-bundle fairness slice (it has no statements or invoices), so their fairness rests on small probes of 300 and 96 questions; in the 96-question fit probe (16 per group) a flag rate up to about 19% per group is not excluded.
- A model can be talked to. The checks never trust it: every claim must quote the extracted data and the quote is verified, severity is capped at medium, a model flag can only add attention (never lower a score or open the fast lane), and a failed, partial or empty reply counts as "not run" (the case is marked partial), never as a pass. The injection test on the bundles proved little: on the 15 canary bundles every semantic check that applied was skipped, so no injected text reached the model. (The label check applies to the KYC use case only and the canary bundles are loan and EDD, so it does not even appear in 10 of them.) The canary test also looks only for a semantic flag at `high`, which is the top of the severity scale. A separate probe (64 attempts, dev data, one model) found no effect, which is a small sample and not a guarantee.
- A model flag can duplicate a rule. On the declared-activity tampers, the fit flags came on top of a rule that already flags all five by code.
- Cost and speed. The local model costs no money but is slow: about 58 seconds and 14,000 prompt tokens per clean case with all three on, about 32 minutes for the 33 clean bundles with no response cache. The paid model was not run; the estimate for it is about USD 0.003 per case at the repository price table, which is an estimate, not a measurement.
- Possible bias. A language model carries the biases of its training data about names, places, trades and languages. We probed only name groups, and only with small samples. We did not test other attributes (nationality, age, religion, line of business), and a false-alarm rate of 3 of 120 (2.5%) may not fall evenly across the applicants.

Verdicts, in the words of the held-out summary: the label check met its criteria (adopted behind the switch, with no benefit shown on bundles); the fit checks met theirs (adopted behind the switch); the entity check met its accuracy criteria but fails the fairness rule, so it is not adopted. "Adopted" means only that the thresholds we set were met on our own data (see above: they were not recorded in advance in a way we can prove). It does not mean the switch should be turned on for real applicants.

After the held-out run, a review found three places where a failed reply could read as a pass or a re-run could read the wrong cache; they were fixed in code without touching a prompt (`docs/semantic-signals.md`). A partly answered label reply is now "not run"; an entity batch with no verified answer is now "not run"; and the evaluation result cache now keys on the semantic settings, so a run with the switch on can no longer be answered from a run with it off. Two limits remain. The fit check still treats a "does not fit" answer without a verifiable quote as a completed check with no flag (the dropped claim is counted in the check's tally, not shown). And the prompts were not re-run after these fixes; the held-out raw files show no partly answered label reply and no entity case with every claim dropped, so the recorded numbers should not move, but the features have not been re-validated end to end.

Known open items for the semantic checks. These are stated here so that nobody reads the section above as "all fixed".

- Fixed after the held-out run, found on a real pipeline run (2026-10-31): the three fit prompts showed example citations with evaluation-style ids (`invoice:INV-0001`), but in the product every document id is `d<N>`. The model copied the example form (it cited `invoice:INV-0011` for `d11`), the verifier dropped the claim as `document_not_provided`, and the check reported "ran, no flag". The evaluation never saw this because its documents carried keyed ids. The examples now use the product form (`d3`, `d5`, `d7`; `backend/app/semantic/fit.py`). This changes the text of the three fit prompts after the held-out run, so the recorded fit numbers describe the old text and have not been re-run. Checked once afterwards on four variants of one AML bundle (a clean control and one edit per family): each edit raised its fit flag, the control raised none. That is a smoke test, not an evaluation.

- Review findings that are confirmed and not fixed:
  - The provenance `prompt_hash` depends on process history. Semantic prompts are registered on first use, so with the switch on the hash covers only the prompts used so far in that process. Two runs of the same case can record different hashes.
  - `/health`, `/explain` and the rule-effectiveness table ignore `GLDB_SEMANTIC`. They list the rules as if the switch were off, so the semantic checks do not appear there (and the ruleset version `/explain` shows can differ from the one stamped on a case run with the switch on).
  - A flag that rests only on model labels carries the ordinary rule id `x_declared_vs_activity`, not a `sem_` id. It therefore escapes the `sem_` severity cap (a catalogue override can raise it above medium) and the fast-lane guard for semantic-only findings. It can only add score; it cannot remove any.
  - The evidence verifier accepts a quote of three characters, anywhere in the cited cell, as evidence. A short quote proves very little.
- Fixed after the held-out run: switching the fit checks on used to lower the displayed coverage ratio of 22 of the 33 clean bundles (and raise it in 11), because a skipped fit check counted as applicable but not run. A semantic check now enters the coverage ratio only when it fails, so a run or a skip leaves coverage as it is with the feature off. This is covered by unit tests; the 33-bundle comparison was not re-run, so the coverage figures measured in the held-out summary are from before the fix.
- The entity check is not adopted. The difference in low-flag rate between name groups rose from 0.00 to 0.33 (design invariant 6: it must not rise). It stays behind the switch and should not be turned on for real applicants until that is understood and fixed.
- The prompts were not re-run end to end after the review fixes, and the second-model check (Claude Haiku) was not run.

## Summary table

| Area | What we know | What we do not know |
|---|---|---|
| Consistent forgery | Not caught from one document (0/5, 0/24 to 0/40) | Whether outside references would be present in practice |
| AI inpainting | Pixel checks do not separate forged from genuine (16/45 against 30/70) | Anything on financial documents |
| Real scans | OCR errors make clean bundles flag (3/3), recall drops (1/13 against 13/13 on `scan_hq`) | Physical scans; a better OCR engine (Apple Vision on the test Mac adds stray characters to some amounts) |
| True document, false content | Not detectable by design | Nothing measured |
| Synthetic evidence | High recall on our own tampers; held-out agrees | Real forgeries; real layouts |
| Names | Near-spelled fakes now flagged | Typo against substitution; about 31% of real aliases not accepted |
| Public data | Only generic or partial data used | No bank or payslip data; payslip set not obtained |
| Live model | Path works, 33/35 fields on 3 pages | Accuracy, tampers, long statements |
| Scan false alarms | Down to 0 with oracle text | Real OCR still flags 3/3; share due to reassembly alone |
| Semantic signals | Model-read labels, aliases and declared-against-shown fit beat the rules on our own data (0.96, 0.94, 0.82); the entity check is not adopted (fairness gap 0.00 to 0.33) | Real customer text; a second model; fairness beyond small probes; whether the fit baseline of 0 holds under the KYC rules; dev tables for labels and entities that cannot be reproduced from the shipped dev files; open review findings (section 11) |
