"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { DocNamer } from "@/lib/docNames";
import { parseEvidence } from "@/lib/evidence";
import { ruleIdText } from "@/lib/labels";
import type { Flag } from "@/lib/types";
import { Button } from "../ui";
import { Disclosure } from "./EvidenceDisclosure";
import { Amounts } from "./FindingParts";

/** The facts behind one flag as label/value rows (or calm sentences), with document ids swapped for names. */
export function EvidenceRows({ evidence, names }: { evidence: string; names: DocNamer }) {
  const rows = useMemo(() => parseEvidence(evidence), [evidence]);
  if (rows.length === 0) return <p className="text-muted">No evidence text was recorded for this concern.</p>;
  return (
    <ul className="space-y-1.5">
      {rows.map((r, i) =>
        r.kind === "fact" ? (
          <li key={i} className="grid grid-cols-1 gap-x-4 sm:grid-cols-[minmax(7rem,9rem)_1fr]">
            <span className="text-sm text-muted">{r.label}</span>
            <span className="min-w-0 break-words text-ink">
              <Amounts text={names.text(r.value)} />
            </span>
          </li>
        ) : (
          <li key={i} className="break-words text-ink-2">
            <Amounts text={names.text(r.text)} />
          </li>
        ),
      )}
    </ul>
  );
}

/**
 * The numbers behind one concern, behind an "Evidence" disclosure. Facts the string allows are shown as
 * label/value rows, the rest as calm sentences, ids are swapped for document names. The check id is small
 * secondary text, and the untouched evidence is one click away with a copy button.
 */
export function FindingEvidence({ flag, names, defaultOpen = false }: { flag: Flag; names: DocNamer; defaultOpen?: boolean }) {
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rawId = useId();
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(flag.evidence);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Disclosure label="Evidence" defaultOpen={defaultOpen}>
      <div className="rounded-md border border-rule bg-paper-2/60 px-3.5 py-3 text-[0.9375rem]">
        <EvidenceRows evidence={flag.evidence} names={names} />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-rule pt-2">
          <span className="num text-xs text-muted">{ruleIdText(flag.rule)}</span>
          <button
            type="button"
            aria-expanded={raw}
            aria-controls={rawId}
            onClick={() => setRaw((v) => !v)}
            className="-mr-1 min-h-9 rounded-md px-1 text-xs font-semibold text-brand-2 underline-offset-2 hover:bg-brand-tint hover:underline pointer-coarse:min-h-11"
          >
            {raw ? "Hide raw evidence" : "Show raw evidence"}
          </button>
        </div>
        {raw ? (
          <div id={rawId} className="mt-2">
            <pre className="num max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border border-rule bg-surface px-3 py-2 text-xs leading-relaxed text-ink-2">{flag.evidence}</pre>
            <div className="mt-2 flex items-center gap-3">
              <Button size="sm" variant="secondary" onClick={() => void copy()} icon={copied ? <Check aria-hidden className="h-4 w-4" /> : <Copy aria-hidden className="h-4 w-4" />}>
                {copied ? "Copied" : "Copy raw evidence"}
              </Button>
              <span role="status" className="sr-only">
                {copied ? "Copied to the clipboard" : ""}
              </span>
            </div>
          </div>
        ) : null}
      </div>
    </Disclosure>
  );
}
