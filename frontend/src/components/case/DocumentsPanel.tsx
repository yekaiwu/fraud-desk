"use client";

import { AlertOctagon, AlertTriangle, CheckCircle2, EyeOff, FileText, type LucideIcon } from "lucide-react";
import { useMemo } from "react";
import { CLASSIFIER_LOW_PROB } from "@/lib/constants";
import { docTypeLabel, makeDocNamer } from "@/lib/docNames";
import { pagesLabelText, parsePagesLabel, plainNotCheckedReason, SEVERITY_ORDER } from "@/lib/flags";
import { UNCHECKED_PAGES_LABEL, plural } from "@/lib/labels";
import { openRowLink, ROW_LINK_ATTR } from "@/lib/rowLink";
import type { CaseDetail, DocStatus, Document, FileRef, Severity } from "@/lib/types";
import { NOT_EXAMINED } from "@/lib/wording";
import { ExplainPages } from "../explain/CoverageExplainer";
import { Badge, Button, EmptyState, Panel, type BadgeVariant } from "../ui";

const EMPTY_DOCS: Document[] = [];
const EMPTY_FILES: FileRef[] = [];

/** How far each document was read, in words and an icon (never colour alone). */
const RESULT: Record<DocStatus, { label: string; variant: BadgeVariant; icon: LucideIcon }> = {
  ok: { label: "Read OK", variant: "low", icon: CheckCircle2 },
  partial: { label: "Partly read", variant: "medium", icon: AlertTriangle },
  failed: { label: "Could not read", variant: "high", icon: AlertOctagon },
  received_not_checked: { label: "Not reviewed", variant: "attention", icon: EyeOff },
};

export function docPages(d: Document): string {
  return `${d.file_id}:${d.page_start}${d.page_end !== d.page_start ? `-${d.page_end}` : ""}`;
}

export function DocumentsPanel({ c, onShowDocument, onShowPage }: { c: CaseDetail; onShowDocument: (docId: string) => void; onShowPage: (fileId: string, page: number) => void }) {
  const names = useMemo(() => makeDocNamer(c.documents, c.files), [c.documents, c.files]);
  const fileName = (id: string) => c.files.find((f) => f.id === id)?.name;
  const concerns = useMemo(() => {
    const m = new Map<string, { count: number; top: Severity }>();
    for (const f of c.flags) {
      for (const id of f.document_ids) {
        const cur = m.get(id);
        const top = !cur || SEVERITY_ORDER.indexOf(f.severity) < SEVERITY_ORDER.indexOf(cur.top) ? f.severity : cur.top;
        m.set(id, { count: (cur?.count ?? 0) + 1, top });
      }
    }
    return m;
  }, [c.flags]);

  return (
    <div className="space-y-5">
      <Panel title="Documents" eyebrow={`${c.documents.length} in this case`} padding="none" headingLevel={3}>
        {c.documents.length === 0 ? (
          <div className="p-4">
            <EmptyState compact icon={<FileText aria-hidden className="h-6 w-6" />} title="No documents were recognised" text="Nothing in the upload could be identified as a document we know how to read." />
          </div>
        ) : (
          <div role="region" aria-label="Documents table" tabIndex={0} className="overflow-x-auto">
            <table className="w-full min-w-[19rem] text-left text-[0.9375rem]">
              <caption className="sr-only">Documents in this case. Choose one to open its first page in the viewer.</caption>
              <thead className="bg-paper-2 text-sm text-muted">
                <tr>
                  <th scope="col" className="px-4 py-2.5 font-semibold">Document</th>
                  <th scope="col" className="hidden px-3 py-2.5 font-semibold md:table-cell">Type</th>
                  <th scope="col" className="hidden px-3 py-2.5 font-semibold md:table-cell">Pages</th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">Result</th>
                  <th scope="col" className="px-4 py-2.5 text-right font-semibold">Concerns</th>
                </tr>
              </thead>
              <tbody>
                {c.documents.map((d) => {
                  const low = d.classification.probability < CLASSIFIER_LOW_PROB;
                  const res = RESULT[d.status];
                  const n = concerns.get(d.id);
                  const pages = pagesLabelText(docPages(d));
                  return (
                    <tr key={d.id} onClick={openRowLink} className="cursor-pointer border-t border-rule align-top hover:bg-brand-tint/40 focus-within:bg-brand-tint/60">
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          {...{ [ROW_LINK_ATTR]: "" }}
                          onClick={() => onShowDocument(d.id)}
                          title={names.docTitle(d.id)}
                          className="text-left font-semibold text-ink hover:text-brand-2"
                        >
                          {names.doc(d.id)}
                          <span className="sr-only">: open in the viewer</span>
                        </button>
                        <span className="num mt-0.5 block break-all text-xs text-muted">{fileName(d.file_id)}</span>
                        <span className="mt-0.5 block text-sm text-muted md:hidden">
                          {docTypeLabel(d.doc_type)} &middot; {pages}
                        </span>
                        {low ? <span className="mt-1 block text-sm text-brass-deep">Low confidence about what kind of document this is</span> : null}
                        {d.error ? <span className="mt-1 block text-sm text-sev-high">Error: {d.error}</span> : null}
                        {d.warnings.length > 0 ? <span className="mt-1 block text-sm text-ink-2">Warnings: {d.warnings.join("; ")}</span> : null}
                        <details className="mt-1 text-sm text-muted">
                          <summary className="inline-flex min-h-9 cursor-pointer items-center rounded px-1 -mx-1 font-medium text-brand-2 hover:bg-brand-tint pointer-coarse:min-h-11">How it was read</summary>
                          <p className="tabular mt-1">
                            Recognised as {d.classification.label.replace(/_/g, " ")} ({d.classification.probability.toFixed(2)}, {d.classification.classifier} v{d.classification.version})
                            {d.issuer ? ` · issuer ${d.issuer.label}` : ""}
                            {d.extractor ? ` · read by ${d.extractor}` : ""}
                          </p>
                        </details>
                      </td>
                      <td className="hidden px-3 py-3 text-ink-2 md:table-cell">{docTypeLabel(d.doc_type)}</td>
                      <td className="tabular hidden whitespace-nowrap px-3 py-3 text-ink-2 md:table-cell" title={docPages(d)}>
                        {pages}
                      </td>
                      <td className="px-3 py-3">
                        <Badge variant={res.variant} icon={res.icon}>
                          {res.label}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {n ? (
                          <Badge variant={n.top}>
                            {n.count} {plural(n.count, "concern")}
                          </Badge>
                        ) : (
                          <span className="text-muted">None</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <NotCheckedList items={c.not_checked_documents} onView={onShowPage} documents={c.documents} files={c.files} />
    </div>
  );
}

/**
 * "Pages we could not review": always visible when there is anything in it. Silence is not safety, so each
 * page says which document it belongs to, why no check looked at it, and offers to open it.
 * `documents` and `files` are optional; without them names fall back to the document type and page.
 */
export function NotCheckedList({
  items,
  onView,
  documents = EMPTY_DOCS,
  files = EMPTY_FILES,
  explainCase,
}: {
  items: CaseDetail["not_checked_documents"];
  onView: (fileId: string, page: number) => void;
  documents?: Document[];
  files?: FileRef[];
  /** The whole case, for the "Why pages are not reviewed" explainer. */
  explainCase?: CaseDetail;
}) {
  const names = useMemo(() => makeDocNamer(documents, files), [documents, files]);
  if (items.length === 0) return null;
  return (
    <Panel
      tone="attention"
      title={`${UNCHECKED_PAGES_LABEL} (${items.length})`}
      eyebrow="Not cleared"
      headingLevel={3}
      actions={explainCase ? <ExplainPages c={explainCase} /> : undefined}
      className="relative"
    >
      <p className="text-[0.9375rem] text-ink-2">
        {NOT_EXAMINED} They are not cleared: read them yourself if they matter to your decision.
      </p>
      <ul className="mt-3 divide-y divide-rule rounded-md border border-rule bg-paper/60">
        {items.map((n) => {
          const loc = parsePagesLabel(n.pages);
          const label = `${names.known ? names.doc(n.document_id) : docTypeLabel(n.doc_type)}, ${pagesLabelText(n.pages)}`;
          return (
            <li key={`${n.document_id}-${n.pages}`} className="flex flex-col gap-2 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
              <div className="min-w-0">
                <p className="break-words font-semibold text-ink" title={`${n.document_id} ${n.pages}`}>
                  {label}
                </p>
                <p className="mt-0.5 text-sm text-ink-2">{plainNotCheckedReason(n.reason, n.doc_type)}</p>
              </div>
              {loc ? (
                <Button size="sm" variant="secondary" className="shrink-0 self-start sm:self-center" onClick={() => onView(loc.fileId, loc.page)}>
                  View page
                  <span className="sr-only">: {label}</span>
                </Button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
