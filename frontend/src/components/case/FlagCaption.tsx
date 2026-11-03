"use client";

import clsx from "clsx";
import { FileText, Info } from "lucide-react";
import type { DocNamer } from "@/lib/docNames";
import { locusOf, locusPhrase, type DocSpan } from "@/lib/flagLocus";
import type { NumberedFlag } from "@/lib/flags";
import { severityLabel } from "@/lib/labels";
import { extraLetter, SEVERITY_COLOR } from "./RegionOverlay";
import { EvidenceRows } from "./FindingEvidence";

const MAX_DOC_LINKS = 6;

/**
 * What the selected flag says, beside the page it points at: its number, severity, the explanation, and the facts
 * behind it. For a flag that is not tied to one spot it says so in words and lists the documents it concerns as
 * links, so the audience is never left looking at an unmarked page. Everything shown comes from the flag itself.
 */
export function FlagCaption({
  selected,
  documents,
  names,
  onNavigate,
  current,
}: {
  selected: NumberedFlag;
  documents: DocSpan[];
  names: DocNamer;
  onNavigate: (to: { fileId: string; page: number }) => void;
  /** The page on show, so related evidence on another page is offered as a link and the rest is just listed. */
  current?: { fileId: string; page: number };
}) {
  const { flag, n } = selected;
  const locus = locusOf(flag, documents);
  const color = SEVERITY_COLOR[flag.severity];
  return (
    <div role="status" aria-live="polite" data-testid="flag-caption" className="space-y-2 border-t border-rule bg-surface px-3 py-3 text-sm">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="num inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1.5 text-xs font-bold text-white" style={{ background: color }}>
          {n}
        </span>
        <span className="font-semibold text-ink">{severityLabel(flag.severity)}</span>
        <span className="text-ink-2">· {locusPhrase(locus)}</span>
      </p>
      {flag.explanation ? <p className="text-[0.9375rem] leading-snug text-ink">{names.text(flag.explanation)}</p> : null}
      {locus.kind === "document" || locus.kind === "case" ? (
        <p className={clsx("flex items-start gap-1.5 rounded-md border border-rule bg-paper-2 px-2.5 py-2 text-ink-2")}>
          <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
          <span>
            {locus.kind === "document"
              ? "This is about the file itself (its properties, or how it compares with other documents), so there is no figure to circle. The page is framed to show which file it is."
              : "This concerns the case as a whole, so there is no page to show."}
          </span>
        </p>
      ) : null}
      {flag.evidence ? (
        <div>
          <p className="label-caps mb-1">What was found</p>
          <EvidenceRows evidence={flag.evidence} names={names} />
        </div>
      ) : null}
      {flag.extra_regions.length > 0 ? (
        <div>
          <p className="label-caps mb-1">Related evidence (dashed boxes)</p>
          <ul className="space-y-1">
            {flag.extra_regions.map((x, i) => {
              const here = !current || (x.file_id === current.fileId && x.page === current.page);
              return (
                <li key={i} className="flex items-center gap-2 text-ink-2">
                  <span aria-hidden className="num inline-flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-dashed border-ink-2 bg-surface px-1 text-xs font-bold">
                    {extraLetter(i)}
                  </span>
                  <span>{x.label ?? "related figure"}</span>
                  {here ? null : (
                    <button type="button" onClick={() => onNavigate({ fileId: x.file_id, page: x.page })} className="text-xs font-semibold text-brand underline underline-offset-2">
                      on {names.page(x.file_id, x.page)}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {locus.kind === "document" && locus.pages.length > 1 ? (
        <div>
          <p className="label-caps mb-1">Documents this applies to</p>
          <ul className="flex flex-wrap gap-1.5">
            {locus.pages.slice(0, MAX_DOC_LINKS).map((p) => (
              <li key={p.docId}>
                <button
                  type="button"
                  onClick={() => onNavigate({ fileId: p.fileId, page: p.page })}
                  className="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-rule-strong bg-surface px-2.5 text-xs font-semibold text-ink-2 hover:border-brand-2 hover:bg-brand-tint pointer-coarse:min-h-11"
                >
                  <FileText aria-hidden className="h-3.5 w-3.5" />
                  {names.doc(p.docId)}
                </button>
              </li>
            ))}
            {locus.pages.length > MAX_DOC_LINKS ? <li className="self-center text-xs text-muted">and {locus.pages.length - MAX_DOC_LINKS} more</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
