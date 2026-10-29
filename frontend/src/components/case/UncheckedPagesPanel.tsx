"use client";

import { useMemo } from "react";
import { docTypeLabel } from "@/lib/docNames";
import { uncheckedPageList } from "@/lib/flags";
import { UNCHECKED_PAGES_LABEL, UNCHECKED_REASON_TEXT } from "@/lib/labels";
import type { CaseDetail } from "@/lib/types";
import { NOT_EXAMINED } from "@/lib/wording";
import { ExplainPages } from "../explain/CoverageExplainer";
import { Button, Panel } from "../ui";

/**
 * The pages behind "3 pages we could not review": each one by file and page number, why no check looked at it and a
 * button to open it. These pages are not cleared, so a person has to read them.
 */
export function UncheckedPagesPanel({ c, onView, id = "unreviewed-pages" }: { c: CaseDetail; onView: (fileId: string, page: number) => void; id?: string }) {
  const rows = useMemo(() => {
    return uncheckedPageList(c).map((u) => {
      const file = c.files.find((f) => f.id === u.file_id);
      const doc = c.documents.find((d) => d.file_id === u.file_id && d.page_start <= u.page && u.page <= d.page_end);
      const kind = doc && doc.doc_type !== "other" && doc.doc_type !== "boilerplate" ? `${docTypeLabel(doc.doc_type)}, page` : "Page";
      return { key: `${u.file_id}:${u.page}`, fileId: u.file_id, page: u.page, label: `${kind} ${u.page} of ${file?.name ?? "a file"}`, why: UNCHECKED_REASON_TEXT[u.reason] ?? u.reason };
    });
  }, [c]);
  if (rows.length === 0) return null;
  return (
    <div id={id} className="scroll-mt-4">
      <Panel tone="attention" title={`${UNCHECKED_PAGES_LABEL} (${rows.length})`} eyebrow="Not cleared" headingLevel={3} actions={<ExplainPages c={c} />} className="relative">
        <p className="text-[0.9375rem] text-ink-2">{NOT_EXAMINED} They are not cleared: read them yourself if they matter to your decision.</p>
        <ul className="mt-3 divide-y divide-rule rounded-md border border-rule bg-paper/60">
          {rows.map((r) => (
            <li key={r.key} className="flex flex-col gap-2 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
              <div className="min-w-0">
                <p className="break-words font-semibold text-ink">{r.label}</p>
                <p className="mt-0.5 text-sm text-ink-2">{r.why}</p>
              </div>
              <Button size="sm" variant="secondary" className="shrink-0 self-start sm:self-center" onClick={() => onView(r.fileId, r.page)}>
                View page
                <span className="sr-only">: {r.label}</span>
              </Button>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
