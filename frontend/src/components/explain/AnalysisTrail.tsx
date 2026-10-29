"use client";

import clsx from "clsx";
import { Printer, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CLASSIFIER_LOW_PROB } from "@/lib/constants";
import { docTypeLabel, makeDocNamer } from "@/lib/docNames";
import {
  alternatives,
  checksAreReproducible,
  costSummaryText,
  decider,
  describeExtractor,
  readSummary,
  secondReadings,
  secondsText,
  shortHash,
  unreadableNotes,
} from "@/lib/explain";
import { formatRatio } from "@/lib/format";
import { coverageText, plural } from "@/lib/labels";
import { useIsSm, usePrefersReducedMotion } from "@/lib/useMedia";
import type { CaseDetail, CoverageSkip, Document } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { CopyButton } from "../case/CaseHeader";
import { STAGE_LABEL, STAGE_ORDER } from "../case/ProcessingStepper";
import { CustomCatalogueNote } from "../catalogue/CustomCatalogueNote";
import { NotRunGroups } from "./CoverageExplainer";
import { NextStepExplainer } from "./NextStepExplainer";
import { ExplainFacts, ExplainNote } from "./parts";
import { ScoreExplainer } from "./ScoreExplainer";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The sentence under every model-written reason. */
export const GENERATED_NOTE = "Generated text, shown as is; it never changes the score.";

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  const id = useId();
  return (
    <li aria-labelledby={id} className="relative space-y-3 pb-6 pl-10 last:pb-0">
      <span aria-hidden className="absolute left-0 top-0 grid h-7 w-7 place-items-center rounded-full bg-brand text-sm font-semibold text-on-brand">
        {n}
      </span>
      <span aria-hidden className="absolute bottom-0 left-[13px] top-8 w-px bg-rule last:hidden" />
      <h3 id={id} className="text-[1.1875rem] leading-snug">
        {title}
      </h3>
      <div className="space-y-3 text-[0.9375rem] leading-relaxed text-ink-2">{children}</div>
    </li>
  );
}

function ConfidenceBar({ p }: { p: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, p)) * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className="block h-1.5 w-20 overflow-hidden rounded-full border border-rule bg-paper-2">
        <span className={clsx("block h-full rounded-full", p < CLASSIFIER_LOW_PROB ? "bg-sev-medium" : "bg-brand-2")} style={{ width: `${pct}%` }} />
      </span>
      <span className="tabular font-semibold text-ink">{pct}%</span>
      <span className="sr-only">confidence</span>
      {p < CLASSIFIER_LOW_PROB ? <Badge variant="medium" icon={null}>Low confidence</Badge> : null}
    </span>
  );
}

/** A model's own reason: always labelled as generated, plain text only, and never treated as evidence. */
export function ModelReason({ text }: { text: string }) {
  return (
    <figure className="rounded-md border border-dashed border-rule-strong bg-paper/70 px-3 py-2">
      <figcaption className="label-caps">Model&apos;s stated reason</figcaption>
      <blockquote className="mt-1 whitespace-pre-wrap break-words text-ink">{text}</blockquote>
      <p className="mt-1 text-xs text-muted">{GENERATED_NOTE}</p>
    </figure>
  );
}

function DocumentIdentified({ d, name }: { d: Document; name: string }) {
  const cl = d.classification;
  const who = decider(cl);
  const alts = alternatives(cl);
  const reasons = (cl.reasons ?? []).filter((r) => r.trim());
  const rationale = cl.rationale?.trim() ?? "";
  return (
    <li className="space-y-2 rounded-lg border border-rule bg-surface px-3.5 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 break-words font-semibold text-ink">{name}</p>
        <p className="text-sm">
          Type chosen: <strong className="text-ink">{docTypeLabel(cl.label)}</strong>
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <ConfidenceBar p={cl.probability} />
        <span>
          Decided by <span className="text-ink">{who.text}</span>
        </span>
      </div>
      {reasons.length > 0 ? (
        <div>
          <p className="label-caps">Why</p>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-5 text-sm">
            {reasons.map((r, i) => (
              <li key={i} className="break-words">
                {r}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {rationale ? <ModelReason text={rationale} /> : null}
      {reasons.length === 0 && !rationale ? <p className="text-sm text-muted">No reason was recorded for this choice.</p> : null}
      {alts.length > 0 ? (
        <details className="group text-sm">
          <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-1.5 font-semibold text-brand-2 pointer-coarse:min-h-11 [&::-webkit-details-marker]:hidden">
            <span aria-hidden className="inline-block w-3 text-xs transition-transform duration-150 group-open:rotate-90">
              ›
            </span>
            Other labels considered
          </summary>
          <ul className="mt-1 space-y-0.5 pl-4">
            {alts.map((a) => (
              <li key={a.label} className="tabular flex justify-between gap-3">
                <span>{docTypeLabel(a.label)}</span>
                <span>{Math.round(a.probability * 100)}%</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </li>
  );
}

function FiguresRead({ d, name }: { d: Document; name: string }) {
  const info = describeExtractor(d.extractor);
  const second = secondReadings(d);
  const notes = unreadableNotes(d);
  const notRead = d.status === "received_not_checked";
  return (
    <li className="space-y-1 rounded-lg border border-rule bg-surface px-3.5 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 break-words font-semibold text-ink">{name}</p>
        {d.status === "failed" ? <Badge variant="high">Could not be read</Badge> : d.status === "partial" ? <Badge variant="medium">Partly read</Badge> : notRead ? <Badge variant="neutral" icon={null}>Not read</Badge> : null}
      </div>
      <p className="text-sm">{notRead && !d.extractor ? "No figures were read: no check applies to this kind of document." : info.text}</p>
      {second.length > 0 ? (
        <p className="text-sm">
          A second reading was used on {second.length === 1 ? "page" : "pages"} {second.map((s) => s.page).join(", ")} because the first reading did not hold together.
        </p>
      ) : null}
      {notes.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm">
          {notes.map((n, i) => (
            <li key={i} className="break-words">
              {n}
            </li>
          ))}
        </ul>
      ) : null}
      {d.error && notes.length === 0 ? <p className="break-words text-sm">{d.error}</p> : null}
    </li>
  );
}

/** The six steps, in order, and the reproducibility footer. Read-only. */
export function AnalysisTrailBody({ c, onGoCoverage }: { c: CaseDetail; onGoCoverage: () => void }) {
  const names = useMemo(() => makeDocNamer(c.documents, c.files), [c.documents, c.files]);
  const read = readSummary(c);
  const cov = c.coverage;
  const notRun = useMemo<CoverageSkip[]>(
    () => c.check_runs.filter((r) => r.status !== "ran").map((r) => ({ rule: r.rule, status: r.status === "error" ? "error" : "skipped", reason: r.reason ?? "" })),
    [c.check_runs],
  );
  const flagged = new Set(c.check_runs.filter((r) => r.status === "ran" && r.flags > 0).map((r) => r.rule)).size;
  const pageProblems = c.files.flatMap((f) => f.pages.filter((p) => p.error).map((p) => ({ f, p })));
  const p = c.provenance;
  const models = p ? Object.entries(p.models) : [];
  const totalSeconds = c.stages.reduce((n, s) => n + s.seconds, 0);
  return (
    <>
      <ol className="space-y-0">
        <Step n={1} title="Read the documents">
          <p className="tabular">
            {read.files} {plural(read.files, "file")}, {read.pages} {plural(read.pages, "page")}. {read.textPages} had a text layer
            {read.scanPages > 0 ? `; ${read.scanPages} ${read.scanPages === 1 ? "was a scanned image" : "were scanned images"}` : ""}
            {read.ocrCalls ? `, read with ${read.ocrCalls} text-recognition ${plural(read.ocrCalls, "call")}` : ""}.{" "}
            {read.unreadable > 0 ? `${read.unreadable} ${plural(read.unreadable, "page")} could not be read.` : "Every page could be read."}
            {read.seconds !== null ? ` Took ${secondsText(read.seconds)}.` : ""}
          </p>
          {pageProblems.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {pageProblems.slice(0, 6).map(({ f, p: pg }) => (
                <li key={`${f.id}-${pg.page}`} className="break-words">
                  {names.page(f.id, pg.page)}: {pg.error}
                </li>
              ))}
            </ul>
          ) : null}
        </Step>

        <Step n={2} title="Identified each document">
          {c.documents.length === 0 ? (
            <p>No documents were identified.</p>
          ) : (
            <ul className="space-y-2.5">
              {c.documents.map((d) => (
                <DocumentIdentified key={d.id} d={d} name={names.doc(d.id)} />
              ))}
            </ul>
          )}
        </Step>

        <Step n={3} title="Read the figures">
          {c.documents.length === 0 ? (
            <p>No figures were read.</p>
          ) : (
            <ul className="space-y-2.5">
              {c.documents.map((d) => (
                <FiguresRead key={d.id} d={d} name={names.doc(d.id)} />
              ))}
            </ul>
          )}
        </Step>

        <Step n={4} title="Ran the checks">
          {cov ? (
            <>
              <p className="tabular">
                <strong className="text-ink">{coverageText(cov.ran, cov.applicable)}</strong> ({formatRatio(cov.ratio)}). {flagged} of them raised at least one flag.
              </p>
              {notRun.length > 0 ? (
                <>
                  <p className="label-caps">{notRun.length} did not run, and why</p>
                  <NotRunGroups skipped={notRun} limit={8} />
                </>
              ) : (
                <p>Every applicable check ran.</p>
              )}
              <p>
                <button type="button" onClick={onGoCoverage} className="font-semibold text-brand-2 underline underline-offset-2">
                  See the Coverage panel
                </button>
              </p>
              <ExplainNote>A check that did not run is not a clearance.</ExplainNote>
            </>
          ) : (
            <p>No checks have been counted yet.</p>
          )}
          <CustomCatalogueNote catalogue={p?.catalogue} />
        </Step>

        <Step n={5} title="Scored">
          <ScoreExplainer c={c} />
        </Step>

        <Step n={6} title="Suggested a next step">
          <NextStepExplainer c={c} />
        </Step>
      </ol>

      <section aria-labelledby="trail-repro" className="mt-6 space-y-3 rounded-lg border border-rule bg-paper-2/60 p-4">
        <h3 id="trail-repro" className="label-caps font-sans">
          Reproducibility
        </h3>
        {p ? (
          <ExplainFacts
            items={[
              { label: "Ruleset", value: <span className="num text-xs">{p.ruleset_version}</span> },
              { label: "Models used", value: models.length > 0 ? <span className="break-words">{models.map(([role, m]) => `${m} (${role})`).join(", ")}</span> : "None" },
              {
                label: "Prompt hash",
                value: p.prompt_hash ? (
                  <span className="inline-flex flex-wrap items-center gap-1">
                    <span className="num text-xs" title={p.prompt_hash}>
                      {shortHash(p.prompt_hash)}
                    </span>
                    <CopyButton text={p.prompt_hash} label="Copy the full prompt hash" />
                  </span>
                ) : (
                  "Not recorded"
                ),
              },
              { label: "Cost", value: costSummaryText(c) },
            ]}
          />
        ) : (
          <p className="text-sm">No reproducibility record was saved for this case.</p>
        )}
        <CustomCatalogueNote catalogue={p?.catalogue} />
        {c.stages.length > 0 ? (
          <div>
            <p className="label-caps">Time per stage</p>
            <ul className="tabular mt-1 grid gap-x-6 gap-y-0.5 text-sm sm:grid-cols-2">
              {STAGE_ORDER.map((s) => c.stages.find((x) => x.stage === s)).filter((x) => !!x).map((s) => (
                <li key={s.stage} className="flex justify-between gap-3">
                  <span>{STAGE_LABEL[s.stage]}</span>
                  <span className="text-ink">{secondsText(s.seconds)}</span>
                </li>
              ))}
              <li className="flex justify-between gap-3 border-t border-rule pt-0.5 font-semibold text-ink sm:col-span-2">
                <span>Total</span>
                <span>{secondsText(totalSeconds)}</span>
              </li>
            </ul>
          </div>
        ) : null}
        {checksAreReproducible(c) ? <p className="text-ink">Running the same documents again gives the same checks and the same flags.</p> : null}
      </section>
    </>
  );
}

const subscribeNone = () => () => {};

/**
 * "How this was analysed": a native <dialog> sliding in from the right (a full-screen sheet on phones), like the
 * Record decision sheet. Everything in it is read-only. Its content is only built once it has been opened, so the
 * method is fetched lazily. It is portalled to the body so it can be printed on its own.
 */
export function AnalysisTrailSheet({ open, onClose, c, onGoCoverage }: { open: boolean; onClose: () => void; c: CaseDetail; onGoCoverage: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const isSm = useIsSm();
  const reduced = usePrefersReducedMotion();
  const onCloseRef = useRef(onClose);
  const [everOpened, setEverOpened] = useState(false);
  const mounted = useSyncExternalStore(subscribeNone, () => true, () => false);
  if (open && !everOpened) setEverOpened(true);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (typeof dlg.showModal === "function") dlg.showModal();
      else dlg.setAttribute("open", "");
      (dlg.querySelector<HTMLElement>("[data-autofocus]") ?? dlg.querySelector<HTMLElement>(FOCUSABLE))?.focus();
      if (!reduced && isSm && typeof dlg.animate === "function") {
        dlg.animate([{ transform: "translateX(2.5rem)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 240, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" });
      }
    } else if (!open && dlg.open) {
      if (typeof dlg.close === "function") dlg.close();
      else dlg.removeAttribute("open");
      opener.current?.focus();
    }
  }, [open, reduced, isSm, mounted]);

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    const handleClose = () => {
      opener.current?.focus();
      onCloseRef.current();
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && typeof dlg.showModal !== "function") handleClose();
    };
    dlg.addEventListener("close", handleClose);
    dlg.addEventListener("keydown", handleKey);
    return () => {
      dlg.removeEventListener("close", handleClose);
      dlg.removeEventListener("keydown", handleKey);
    };
  }, [mounted]);

  if (!mounted) return null;
  return createPortal(
    <dialog
      ref={ref}
      className="sheet trail"
      aria-labelledby={titleId}
      style={isSm ? { margin: "0 0 0 auto", height: "100dvh", maxHeight: "100dvh", borderRadius: "14px 0 0 14px", maxWidth: "min(40rem, 100vw)", borderRightWidth: 0 } : undefined}
    >
      {open || everOpened ? (
        <div className="flex h-dvh max-h-dvh flex-col print:block print:h-auto print:max-h-none">
          <div className="band-dark relative bg-brand px-4 py-3.5 sm:px-6 print:bg-transparent print:text-ink">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-[0.09em] text-on-brand-muted print:text-muted">How this was analysed</p>
                <h2 id={titleId} data-autofocus tabIndex={-1} className="mt-0.5 break-words text-[1.375rem] font-semibold leading-tight text-on-brand focus:outline-none print:text-ink">
                  {c.applicant.name ?? "Unnamed applicant"}
                </h2>
              </div>
              <div className="-mr-2 flex shrink-0 items-center print:hidden">
                <button
                  type="button"
                  onClick={() => window.print()}
                  aria-label="Print this page"
                  className="inline-flex h-11 w-11 items-center justify-center rounded-md text-on-brand hover:bg-on-brand/10"
                >
                  <Printer aria-hidden className="h-5 w-5" />
                </button>
                <button type="button" onClick={onClose} aria-label="Close" className="inline-flex h-11 w-11 items-center justify-center rounded-md text-on-brand hover:bg-on-brand/10">
                  <X aria-hidden className="h-5 w-5" />
                </button>
              </div>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 print:overflow-visible">
            <p className="mb-5 max-w-prose text-[0.9375rem] text-ink-2">
              The steps the tool took, in order, with this case&apos;s own numbers. Nothing here can be changed. A suggestion is not a decision, and a finding is not proof of fraud.
            </p>
            <AnalysisTrailBody
              c={c}
              onGoCoverage={() => {
                onClose();
                onGoCoverage();
              }}
            />
          </div>
          <div className="shrink-0 border-t border-rule px-4 py-3 sm:px-6 print:hidden">
            <Button variant="secondary" fullWidth className="sm:w-auto" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      ) : null}
    </dialog>,
    document.body,
  );
}
