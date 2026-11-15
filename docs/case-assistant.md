# Ask this case

An officer can ask a question about the case on screen and about the cases still on the worklist. The answer is a few short sentences, shown as prose. Every case fact is still checked against a cell, a flag, the review team, or a worklist line before it is shown. The check is not printed as a label in the sentence. The assistant does not decide, and it does not change a flag, a score, a lane or a decision.

It is off unless `GLDB_CASE_ASSISTANT=on`. It needs a personal sign-in. A shared login gets `403 account_required`. The case view shows the Ask tab only when `/me` reports `case_assistant: true`.

## Invariants

1. **Cites or abstains.** The model must quote a cell it was shown (`file_id`, page, field or table row, and the quote), a flag it was shown (`flag_id`, and a quote copied from that flag's evidence or explanation), or a worklist line (`worklist_id`, and a quote copied from that case's reference, applicant, score, or top finding). `_verify_citation` in `backend/app/semantic/claims.py` checks a cell. A flag quote is checked against the flag's own evidence and explanation. A worklist quote is checked against the line built from the queue. A cell must sit on a line the model was given: a row past the 150-row limit, or a cell cut off when a block is too long, is not citable. A quote taken from part of a flag, a top finding, the review team or the service note is at least twelve characters and starts and ends on a word boundary. A worklist quote of a reference, an applicant, a score, a band or a lane is the whole value, not a label such as `on_screen`. A flag quote is checked against the evidence and explanation as the model saw them, trimmed. A sentence that names a rule id or a flag id from this case (`x_remitter_vs_buyer`) is dropped: the model sees the ids so it can cite, but the officer reads prose. The review-team block names each challenger verdict by the finding's explanation, not its rule id, and the plain answer about what the challenger lowered leaves out a reason that names one. A sentence with no verified citation is dropped. If none remain, the reply is "I can't find that in this case or on the worklist."
2. **Never decides.** A question that asks for a verdict ("should I approve?", "is this fraud?") gets a fixed first sentence: "That decision is yours. Here is the evidence that bears on it:" Then cited facts, and links to the open flags that sit on those documents. The words approve, reject, escalate and clear are not used as a recommendation. Whatever the question, a sentence that recommends a verdict ("you should reject", "approve the case") is dropped. The one exception is relaying the case officer: a sentence that names the case officer and cites the review team may repeat a recommending phrase when that phrase is in the officer's recorded summary or next steps ("The case officer's next step is to escalate the case to compliance"). "Is this fraudulent?" is a decision question too.
3. **Read-only.** The route does not write the case. Flags, the score, the lane and the decision are unchanged. The question is remembered only so the next turn can see the last four, and the audit log stores a length and a sha256, not the text.

## What the model sees

Cells already stored on the case on screen, every flag on that case, and a short line for each case still on the worklist (at most 40, in worklist order: reference, applicant, score, band, lane, top finding). Other cases' documents are not sent. Nothing is re-extracted and no flag is rewritten. Scope `document` limits the documents to the file on screen. The flags and the worklist are included either way. Scope `case` adds the other documents of this case, cut to about 24,000 characters: pages with an open flag first, then the page on screen and its neighbours. Each block is wrapped with `wrap_untrusted`, and so is the question. Instructions live only in the system prompt (`assistant:ask`), which is registered for provenance. The model has no tools. The reply is schema-forced JSON.

The last four turns of this officer on this case version are sent with the next question. A new version starts with an empty memory. The question text is sealed when encryption at rest is on. Deleting the case deletes those turns. They are not kept in the audit log.

A call still counts toward the case cap when the model answers in a shape that cannot be used. The next question then stops if the cap is spent. A question refused before any call (a full budget) is not stored; the audit row records it. A question whose call failed is stored as a count only, without its words, so it is not read as an earlier turn and does not push an answered turn out of memory.

## Cost

One cheap-model call per question (purpose `assistant:ask`). It is reserved through the same meter as the rest of the tool, so it counts against the per-case call and dollar caps, the per-day cap and the per-account cap (`docs/cost-efficiency.md`). A full budget returns 429 and does not call the model. The dollars land on the Spending page through the daily ledger. The call is listed on the Timeline under "Ask this case". The stage stored on the cost row is `score`, because a cost's stage is one of the seven pipeline stages; the timeline takes these calls out of that step and shows them on their own.

Private mode uses the local model. A hosted model is refused at startup, and the socket guard still blocks anything else.

A live measurement of ten questions on demo 4 with Claude Haiku was not run for this change. The offline tests use `FakeLLMClient`, which records the call and prices it at zero. To measure: process demo 4 with `GLDB_CASE_ASSISTANT=on` and the Haiku backend, ask the three questions in the demo script plus seven more, and read `prompt_tokens`, `completion_tokens`, `usd` and `seconds` from the timeline or from `assistant_turns`.

## Limits

- It can quote cells the extractors stored, the flags on this case, and the worklist lines. It cannot open another case's documents from here. A figure that was never extracted and is not in a flag or a worklist line cannot be cited, and the reply abstains.
- It does not see the page image. A box is drawn only when the cited cell has one.
- It can be wrong about what a true quote means. The citation shows the cell; it does not make the sentence true.
- Four turns of memory. Older questions on that version lose their text and answer. A turn that made a model call keeps its call count and dollars, so the case cap and the timeline still see every call. They are not in the audit log.
- It shares the review rate limit, so a burst of questions is `429 rate_limited`.
- Switched off, the route is `404 feature_off`. The checks and flags are unaffected either way.
