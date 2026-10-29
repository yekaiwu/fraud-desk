"use client";

import { useEffect, useRef, useState } from "react";
import { addFiles } from "@/lib/api";
import { ErrorState } from "../StateViews";
import { FileDropzone } from "../upload/FileDropzone";
import { FileList, useFileSelection } from "../upload/useFileSelection";

/** Add documents to a case (new version, re-analysis). Same validation as Upload. */
export function AddDocuments({ caseId, onStarted }: { caseId: string; onStarted: (r: { job_id: string; version: number }) => void }) {
  const sel = useFileSelection();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) alertRef.current?.focus();
  }, [error]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (sel.blocking) return;
    setBusy(true);
    setError(null);
    try {
      const r = await addFiles(caseId, sel.files);
      sel.clear();
      onStarted(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-border bg-card p-4 shadow-sm" aria-label="Add documents">
      <h3 className="font-display text-lg font-semibold">Add documents</h3>
      <p className="text-sm text-muted">Creates the next version and re-runs the analysis. Earlier decisions are kept as history and do not carry over.</p>
      <FileDropzone onFiles={(f) => void sel.add(f)} label="Choose files to add" />
      <FileList entries={sel.entries} dups={sel.dups} onRemove={sel.remove} />
      {sel.countError ? <p className="text-sm font-medium text-sev-high">{sel.countError}</p> : null}
      {error ? (
        <div ref={alertRef} tabIndex={-1}>
          <ErrorState error={error} />
        </div>
      ) : null}
      <button type="submit" disabled={sel.blocking || busy} className="min-h-11 rounded bg-ink px-4 font-semibold text-white disabled:opacity-50">
        {busy ? `Uploading ${sel.files.length} files...` : "Add and re-run"}
      </button>
    </form>
  );
}
