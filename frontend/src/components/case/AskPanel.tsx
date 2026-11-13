"use client";

import clsx from "clsx";
import { useId, useState } from "react";
import { askCase } from "@/lib/api";
import { makeDocNamer } from "@/lib/docNames";
import { explainError, isApiError } from "@/lib/errors";
import type { AskCitation, AskRequest, AskResponse, AskScope, CaseDetail } from "@/lib/types";
import { GENERATED_NOTE } from "../explain/AnalysisTrail";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";

const QUESTION_MAX = 500;

/** Three questions built from what this case actually contains. */
export function suggestionQuestions(c: CaseDetail): string[] {
  const chips: string[] = [];
  const seen = new Set<string>();
  const add = (q: string) => {
    const text = q.trim();
    if (!text || seen.has(text) || chips.length >= 3) return;
    seen.add(text);
    chips.push(text);
  };
  for (const d of c.documents) {
    if (d.doc_type !== "invoice") continue;
    const number = d.fields.invoice_number?.value?.trim();
    if (number) add(`Who paid invoice ${number}?`);
  }
  const sof = c.documents.find((d) => d.doc_type === "source_of_funds_declaration");
  if (sof) {
    const amount = sof.fields.declared_amount?.raw?.trim() || sof.fields.declared_amount?.value?.trim();
    add(amount ? `Which credits support the ${amount} declared?` : "Which credits support the declared source of funds?");
  }
  if (c.documents.length > 0) add("Summarise this document");
  if (c.flags.some((f) => f.disposition === "open")) add("What do the open flags on this case cite?");
  return chips.slice(0, 3);
}

function askFailure(e: unknown): { title: string; detail: string } {
  if (isApiError(e)) {
    if (e.error.code === "feature_off") {
      return { title: "Ask this case is switched off", detail: "This deployment has not turned it on. The checks and flags are unaffected." };
    }
    if (e.error.code === "assistant_unavailable" || e.status >= 500) {
      return { title: "The assistant is unavailable", detail: "The assistant is unavailable; the checks and flags are unaffected." };
    }
    if (e.error.code === "user_budget_used" || e.error.code === "site_budget_used" || e.error.code === "case_budget_used") {
      return { title: "The model budget is used up", detail: e.error.message };
    }
    if (e.error.code === "account_required") {
      return { title: "Sign in with your own account", detail: "A shared login cannot ask a question. The checks and flags are unaffected." };
    }
    const explained = explainError(e.error);
    return { title: explained.title, detail: explained.detail };
  }
  return { title: "The assistant is unavailable", detail: "The assistant is unavailable; the checks and flags are unaffected." };
}

/**
 * Ask a question about the documents on screen. Every sentence cites a cell; a click opens that page.
 * Generated text, in the same dashed box as the case officer's summary. It never changes a flag or the score.
 */
export function AskPanel({
  c,
  fileId,
  page,
  onCite,
  onOpenFlag,
  ask = askCase,
}: {
  c: CaseDetail;
  fileId: string;
  page: number;
  onCite: (citation: AskCitation) => void;
  onOpenFlag?: (id: string) => void;
  ask?: (id: string, body: AskRequest) => Promise<AskResponse>;
}) {
  const titleId = useId();
  const questionId = useId();
  const names = makeDocNamer(c.documents, c.files);
  const [scope, setScope] = useState<AskScope>(fileId ? "document" : "case");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ title: string; detail: string } | null>(null);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const chips = suggestionQuestions(c);

  async function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setQuestion(trimmed);
    setBusy(true);
    setError(null);
    try {
      const body = await ask(c.id, {
        question: trimmed.slice(0, QUESTION_MAX),
        scope: fileId ? scope : "case",
        file_id: fileId || null,
        page,
        case_version: c.version,
      });
      setAnswer(body);
    } catch (e) {
      setAnswer(null);
      setError(askFailure(e));
    } finally {
      setBusy(false);
    }
  }

  const chipLabel = (citation: AskCitation) => {
    if (citation.rule) return `${citation.rule} · p.${citation.page}`;
    const name = citation.document_id ? names.doc(citation.document_id) : names.file(citation.file_id);
    return `${name} · p.${citation.page}`;
  };

  return (
    <section aria-labelledby={titleId} className="space-y-4">
      <div>
        <h2 id={titleId} className="text-xl">Ask this case</h2>
        <p className="mt-1 text-sm text-ink-2">Questions are answered from the flags on this case and the documents in view. A sentence without a citation is not shown.</p>
      </div>

      <fieldset className="space-y-2">
        <legend className="label-caps">Look in</legend>
        <div role="radiogroup" aria-label="Look in" className="flex flex-wrap gap-2">
          {(["document", "case"] as const).map((id) => {
            const label = id === "document" ? "This document" : "Whole case";
            const disabled = id === "document" && !fileId;
            return (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={scope === id}
                disabled={disabled}
                onClick={() => setScope(id)}
                className={clsx(
                  "min-h-11 rounded-md border px-3 text-sm font-semibold",
                  scope === id ? "border-brand-2 bg-brand-tint text-brand" : "border-rule-strong bg-surface text-ink-2 hover:bg-brand-tint",
                  disabled && "cursor-not-allowed opacity-50",
                )}
              >
                {label}
              </button>
            );
          })}
        </div>
      </fieldset>

      {chips.length > 0 ? (
        <div>
          <p id="ask-suggestions" className="label-caps">Suggestions</p>
          <ul aria-labelledby="ask-suggestions" className="mt-2 flex flex-wrap gap-2">
            {chips.map((chip) => (
              <li key={chip}>
                <button
                  type="button"
                  onClick={() => void submit(chip)}
                  disabled={busy}
                  className="min-h-11 max-w-full rounded-md border border-rule-strong bg-surface px-3 text-left text-sm font-medium text-ink hover:bg-brand-tint disabled:opacity-50"
                >
                  {chip}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <form
        className="space-y-2"
        onSubmit={(ev) => {
          ev.preventDefault();
          void submit(question);
        }}
      >
        <label htmlFor={questionId} className="label-caps">Question</label>
        <textarea
          id={questionId}
          value={question}
          maxLength={QUESTION_MAX}
          rows={3}
          onChange={(ev) => setQuestion(ev.target.value)}
          placeholder="Ask about the documents on screen"
          className="w-full rounded-md border border-rule-strong bg-surface px-3 py-2 text-base text-ink shadow-paper placeholder:text-muted focus:outline-2 focus:outline-offset-2 focus:outline-brand-2"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="tabular text-xs text-muted">{question.length} / {QUESTION_MAX}</p>
          <Button type="submit" disabled={busy || question.trim().length === 0}>Ask</Button>
        </div>
      </form>

      {busy ? (
        <p role="status" className="text-sm text-ink-2">Reading the documents…</p>
      ) : null}

      {error ? (
        <Notice variant="warning" title={error.title}>{error.detail}</Notice>
      ) : null}

      {answer ? (
        <div className="rounded-lg border border-dashed border-rule-strong bg-paper/70 px-4 py-3">
          <p className="label-caps">Answer</p>
          {answer.preamble ? <p className="mt-1 text-[0.9375rem] leading-normal text-ink">{answer.preamble}</p> : null}
          {answer.sentences.map((sentence, i) => (
            <p key={i} className="mt-2 text-[0.9375rem] leading-normal text-ink">
              {sentence.text}{" "}
              {sentence.citations.map((citation, n) => (
                <button
                  key={`${citation.file_id}-${citation.page}-${n}`}
                  type="button"
                  onClick={() => onCite(citation)}
                  title={citation.quote}
                  className={clsx(
                    "mx-0.5 inline-flex min-h-11 items-center rounded-md border border-rule-strong bg-surface px-2 align-middle text-sm font-medium text-brand-2 hover:bg-brand-tint",
                    citation.rule && "num text-xs",
                  )}
                >
                  {chipLabel(citation)}
                </button>
              ))}
            </p>
          ))}
          {answer.message ? <p className="mt-2 text-[0.9375rem] leading-normal text-ink">{answer.message}</p> : null}
          {answer.flags.length > 0 ? (
            <ul className="mt-2 space-y-1">
              {answer.flags.map((flag) => (
                <li key={flag.id}>
                  <button
                    type="button"
                    onClick={() => onOpenFlag?.(flag.id)}
                    className="inline-flex min-h-11 items-center gap-2 rounded-md px-1 text-left text-sm font-semibold text-brand-2 hover:bg-brand-tint"
                  >
                    <span className="num text-xs">{flag.rule}</span>
                    <span className="font-normal text-ink-2">{flag.explanation}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-2 text-xs text-muted">{GENERATED_NOTE}</p>
        </div>
      ) : null}
    </section>
  );
}
