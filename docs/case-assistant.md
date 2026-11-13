# Ask this case

An officer can ask a question about the documents already on a case. The answer is a few short sentences. Every sentence cites the cell it rests on. A sentence that cannot be checked against that cell is not shown. The assistant does not decide, and it does not change a flag, a score, a lane or a decision.

It is off unless `GLDB_CASE_ASSISTANT=on`. It needs a personal sign-in. A shared login gets `403 account_required`.

## Invariants

1. **Cites or abstains.** The model must quote a cell it was shown (`file_id`, page, field or table row, and the quote), or a flag it was shown (`flag_id`, and a quote copied from that flag's evidence or explanation). `_verify_citation` in `backend/app/semantic/claims.py` checks a cell: the document was shown, the field or row exists, and the quote is in that cell after the same normalisation the semantic checks use. A flag quote is checked the same way against the flag's own evidence and explanation. A sentence with no verified citation is dropped. If none remain, the reply is "I can't find that in this case's documents."
2. **Never decides.** A question that asks for a verdict ("should I approve?", "is this fraud?") gets a fixed first sentence: "That decision is yours. Here is the evidence that bears on it:" Then cited facts, and links to the open flags that sit on those documents. The words approve, reject, escalate and clear are not used as a recommendation.
3. **Read-only.** The route does not write the case. Flags, the score, the lane and the decision are unchanged. The question is remembered only so the next turn can see the last four, and the audit log stores a length and a sha256, not the text.

## What the model sees

Cells already stored on the case, and every flag already on it. Nothing is re-extracted and no flag is rewritten. Scope `document` limits the documents to the file on screen. The flags are included either way, because an officer asks about a finding while looking at another file. Scope `case` adds the other documents, cut to about 24,000 characters: pages with an open flag first, then the page on screen and its neighbours. Each block is wrapped with `wrap_untrusted`, and so is the question. Instructions live only in the system prompt (`assistant:ask`), which is registered for provenance. The model has no tools. The reply is schema-forced JSON.

The last four turns of this officer on this case version are sent with the next question. A new version starts with an empty memory. The question text is sealed when encryption at rest is on. Deleting the case deletes those turns. They are not kept in the audit log.

A call still counts toward the case cap when the model answers in a shape that cannot be used. The next question then stops if the cap is spent.

## Cost

One cheap-model call per question (purpose `assistant:ask`). It is reserved through the same meter as the rest of the tool, so it counts against the per-case call and dollar caps, the per-day cap and the per-account cap (`docs/cost-efficiency.md`). A full budget returns 429 and does not call the model. The dollars land on the Spending page through the daily ledger. The call is listed on the Timeline under "Ask this case". The stage stored on the cost row is `score`, because a cost's stage is one of the seven pipeline stages; the timeline takes these calls out of that step and shows them on their own.

Private mode uses the local model. A hosted model is refused at startup, and the socket guard still blocks anything else.

A live measurement of ten questions on demo 4 with Claude Haiku was not run for this change. The offline tests use `FakeLLMClient`, which records the call and prices it at zero. To measure: process demo 4 with `GLDB_CASE_ASSISTANT=on` and the Haiku backend, ask the three questions in the demo script plus seven more, and read `prompt_tokens`, `completion_tokens`, `usd` and `seconds` from the timeline or from `assistant_turns`.

## Limits

- It can only quote cells the extractors stored. A figure that was never extracted cannot be cited, and the reply abstains.
- It does not see the page image. A box is drawn only when the cited cell has one.
- It can be wrong about what a true quote means. The citation shows the cell; it does not make the sentence true.
- Four turns of memory. Older questions on that version are dropped. They are not in the audit log.
- It shares the review rate limit, so a burst of questions is `429 rate_limited`.
- Switched off, the route is `404 feature_off`. The checks and flags are unaffected either way.
