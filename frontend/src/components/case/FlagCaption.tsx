"use client";

import clsx from "clsx";
import { FileText, Info } from "lucide-react";
import type { DocNamer } from "@/lib/docNames";
import { locusOf, locusPhrase, placesOf, type DocSpan, type Place } from "@/lib/flagLocus";
import type { NumberedFlag } from "@/lib/flags";
import { severityLabel } from "@/lib/labels";
import { SEVERITY_COLOR } from "./RegionOverlay";
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
  onFocusPlace,
  current,
}: {
  selected: NumberedFlag;
  documents: DocSpan[];
  names: DocNamer;
  onNavigate: (to: { fileId: string; page: number }) => void;
  /** Go to one of the flag's places: open its page if needed and bring it into view. */
  onFocusPlace?: (place: Place) => void;
  /** The page on show, so related evidence on another page is offered as a link and the rest is just listed. */
  current?: { fileId: string; page: number };
}) {
  const { flag, n } = selected;
  const locus = locusOf(flag, documents);
  const color = SEVERITY_COLOR[flag.severity];
  const places = placesOf(flag);
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
      {places.length > 1 ? (
        <div>
          <p className="label-caps mb-1" data-testid="places-heading">
            Marked in {places.length} places
          </p>
          <ul className="space-y-1">
            {places.map((pl) => {
              const here = !current || (pl.region.file_id === current.fileId && pl.region.page === current.page);
              return (
                <li key={pl.key} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ink-2">
                  {pl.letter === null ? (
                    <span aria-hidden className="num inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-xs font-bold text-white" style={{ background: color }}>
                      {n}
                    </span>
                  ) : (
                    <span aria-hidden className="num inline-flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-dashed bg-surface px-1 text-xs font-bold" style={{ borderColor: color, color }}>
                      {pl.letter}
                    </span>
                  )}
                  <span className="text-ink">{pl.label}</span>
                  <span className="text-xs text-muted">{here ? "on this page" : names.page(pl.region.file_id, pl.region.page)}</span>
                  <button
                    type="button"
                    onClick={() => (onFocusPlace ? onFocusPlace(pl) : onNavigate({ fileId: pl.region.file_id, page: pl.region.page }))}
                    className="text-xs font-semibold text-brand underline underline-offset-2"
                    aria-label={`Go to ${pl.label}${pl.letter ? ` (${pl.letter})` : ""}`}
                  >
                    Go to
                  </button>
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
