"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { createCase } from "@/lib/api";
import { MAX_FILES_PER_CASE, MAX_UPLOAD_MB } from "@/lib/constants";
import { buildCaseForm } from "@/lib/uploadCheck";
import { recogniseFiles } from "@/lib/uploadChecklist";
import type { UseCase } from "@/lib/types";
import { ErrorState } from "../StateViews";
import { Field } from "../ui/Field";
import { Panel } from "../ui/Panel";
import { CaseTypePicker } from "./CaseTypePicker";
import { DocumentChecklist } from "./DocumentChecklist";
import { FileDropzone } from "./FileDropzone";
import { SubmitSummary, type SubmitPhase } from "./SubmitSummary";
import { FileList, useFileSelection } from "./useFileSelection";

/** The three steps: kind of case, documents, review and start. Behaviour (limits, checks, errors, redirect) is unchanged. */
export function UploadForm() {
  const router = useRouter();
  const sel = useFileSelection();
  const [useCase, setUseCase] = useState<UseCase>("loan");
  const [reference, setReference] = useState("");
  const [name, setName] = useState("");
  const [uen, setUen] = useState("");
  const [phase, setPhase] = useState<SubmitPhase>({ kind: "idle" });
  const [error, setError] = useState<unknown>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const busy = phase.kind !== "idle";

  useEffect(() => {
    if (error) alertRef.current?.focus();
  }, [error]);

  // File names and types only: a guess to help the officer, never a gate.
  const recognition = useMemo(() => recogniseFiles(useCase, sel.entries.filter((e) => !e.problem).map((e) => ({ id: e.id, name: e.file.name, type: e.file.type }))), [useCase, sel.entries]);
  const totalBytes = sel.entries.reduce((s, e) => s + e.file.size, 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (sel.blocking || busy) return;
    const count = sel.files.length;
    setPhase({ kind: "uploading", count });
    setError(null);
    try {
      const res = await createCase(buildCaseForm({ use_case: useCase, files: sel.files, reference, applicant_name: name, applicant_uen: uen }));
      setPhase({ kind: "created", caseId: res.case_id, jobId: res.job_id, count });
      router.push(`/cases/${res.case_id}?job=${res.job_id}`);
    } catch (err) {
      // all-or-nothing: the form keeps its content for a retry
      setError(err);
      setPhase({ kind: "idle" });
    }
  }

  const blockMsg =
    sel.entries.length === 0
      ? "Add at least one file to continue."
      : sel.countError
        ? sel.countError
        : sel.badCount > 0
          ? `Remove ${sel.badCount} ${sel.badCount === 1 ? "file" : "files"} with problems to continue.`
          : null;

  return (
    <form onSubmit={submit} noValidate className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
      <div className="min-w-0 space-y-6">
        {error ? (
          <div ref={alertRef} tabIndex={-1} className="focus:outline-none">
            <ErrorState error={error} />
          </div>
        ) : null}

        <Panel eyebrow="Step 1 of 3" title="What kind of case is this?">
          <CaseTypePicker value={useCase} onChange={setUseCase} />
          <div className="mt-6">
            <h3 className="font-sans text-[0.9375rem] font-semibold">About the customer <span className="font-normal text-muted">(all optional)</span></h3>
            <p className="mt-0.5 text-sm text-muted">Helps you find the case later. The documents are read either way.</p>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <Field label="Reference (optional)" hint="Your own case or application number." value={reference} maxLength={80} onChange={(e) => setReference(e.target.value)} />
              <Field label="Applicant UEN (optional)" hint="An invalid UEN is accepted and checked later." value={uen} maxLength={20} onChange={(e) => setUen(e.target.value)} />
              <Field label="Applicant name (optional)" hint="The company, as you know it." value={name} maxLength={120} onChange={(e) => setName(e.target.value)} className="sm:col-span-2" />
            </div>
          </div>
        </Panel>

        <Panel eyebrow="Step 2 of 3" title="Add the documents">
          <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
            <div className="space-y-3">
              <FileDropzone onFiles={(f) => void sel.add(f)} disabled={busy} />
              <p className="text-sm text-muted">
                PDF, JPEG or PNG; 1 to {MAX_FILES_PER_CASE} files; at most {MAX_UPLOAD_MB} MB each. The server makes the final file check. Use fictitious documents only in demos.
              </p>
            </div>
            <DocumentChecklist useCase={useCase} recognition={recognition} fileCount={sel.entries.length} />
            <div className="xl:col-span-2">
              <FileList entries={sel.entries} dups={sel.dups} onRemove={sel.remove} onClear={sel.clear} looksLike={recognition.byFile} />
            </div>
          </div>
        </Panel>
      </div>

      <div className="min-w-0 lg:sticky lg:top-4">
        <SubmitSummary useCase={useCase} fileCount={sel.entries.length} totalBytes={totalBytes} recognition={recognition} blockMessage={blockMsg} phase={phase} />
      </div>
    </form>
  );
}
