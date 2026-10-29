"use client";

import clsx from "clsx";
import { Check, ChevronLeft, ChevronRight, Copy } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { VersionInfo } from "@/lib/api";
import { formatAge, formatRelative, formatUtc } from "@/lib/format";
import { useCaseLabel as caseTypeLabel } from "@/lib/labels";
import type { CaseDetail } from "@/lib/types";
import { Badge, StatusBadge } from "../ui/Badge";
import { inputClasses } from "../ui/Field";

export type CaseNav = { prev: string | null; next: string | null; position: number; total: number };

export function VersionSelect({ versions, value, onChange }: { versions: VersionInfo[]; value: number; onChange: (v: number) => void }) {
  if (versions.length < 2) return null;
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="version-select" className="label-caps">
        Version
      </label>
      <select id="version-select" value={value} onChange={(e) => onChange(Number(e.target.value))} className={clsx(inputClasses, "w-auto max-w-[min(100%,26rem)] py-1.5 text-[0.9375rem]")}>
        {versions.map((v) => (
          <option key={v.version} value={v.version}>
            v{v.version}{v.current ? " (current)" : ""} · {v.reason.replace("_", " ")}{v.score !== null ? ` · score ${v.score}` : ""} · {formatUtc(v.created_at)}
          </option>
        ))}
      </select>
    </div>
  );
}

/** Copies text to the clipboard and says so. A blocked clipboard (insecure origin, permissions) reports it instead of failing silently. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  async function copy() {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        ok = false;
      }
    }
    setState(ok ? "copied" : "failed");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  }
  return (
    <>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={label}
        className="inline-flex h-11 w-11 items-center justify-center rounded-md text-ink-2 hover:bg-brand-tint hover:text-brand sm:-my-2.5 sm:h-9 sm:w-9 pointer-coarse:h-11 pointer-coarse:w-11"
      >
        {state === "copied" ? <Check aria-hidden className="h-4 w-4 text-sev-low" /> : <Copy aria-hidden className="h-4 w-4" />}
      </button>
      <span role="status" className={clsx("text-xs font-medium", state === "failed" ? "text-sev-high" : "text-sev-low", state === "idle" && "sr-only")}>
        {state === "copied" ? "Copied" : state === "failed" ? "Could not copy" : ""}
      </span>
    </>
  );
}

/** "2 h" since an ISO time (used where "ago" would read oddly: "Waiting 2 h"). */
function ageSince(iso: string): string {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? formatAge((Date.now() - t) / 1000) : "";
}

const navLink = "inline-flex min-h-11 items-center gap-1 rounded-md px-2 text-sm font-semibold text-brand-2 hover:bg-brand-tint";

/** Applicant, how to identify the case, and the way back. The score and what to do live in the recommendation hero below. */
export function CaseHeader({ c, versions, onVersion, nav }: {
  c: CaseDetail; isCurrent?: boolean; versions: VersionInfo[]; onVersion: (v: number) => void; nav?: CaseNav | null;
}) {
  const reference = c.reference ?? c.id;
  const since =
    c.status === "awaiting_documents"
      ? `Waiting ${ageSince(c.updated_at)}`
      : `Received ${formatRelative(c.created_at)}`;
  return (
    <header className="space-y-3">
      <div className="-mx-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <Link href="/" className={navLink}>
          <ChevronLeft aria-hidden className="h-4 w-4" />
          Back to worklist
        </Link>
        {nav ? (
          <nav aria-label="Other cases in the worklist" className="flex items-center gap-1">
            {nav.prev ? (
              <Link href={`/cases/${encodeURIComponent(nav.prev)}`} className={navLink}>
                <ChevronLeft aria-hidden className="h-4 w-4" />
                <span>Previous case</span>
              </Link>
            ) : null}
            <span className="num px-1 text-xs text-muted">
              {nav.position} of {nav.total}
            </span>
            {nav.next ? (
              <Link href={`/cases/${encodeURIComponent(nav.next)}`} className={navLink}>
                <span>Next case</span>
                <ChevronRight aria-hidden className="h-4 w-4" />
              </Link>
            ) : null}
          </nav>
        ) : null}
      </div>

      <div className="rule-statement flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pb-3">
        <div className="min-w-0">
          <h1 className="break-words text-[1.75rem] leading-tight sm:text-[2.25rem]">{c.applicant.name ?? <span className="text-muted">Unnamed applicant</span>}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
            {c.applicant.uen ? (
              <span className="text-sm text-ink-2">
                <span className="label-caps mr-1.5">UEN</span>
                <span className="num text-[0.9375rem] text-ink">{c.applicant.uen}</span>
              </span>
            ) : null}
            <Badge variant="neutral" icon={null}>
              {caseTypeLabel(c.use_case)}
            </Badge>
            <StatusBadge status={c.status} />
            {c.version > 1 ? (
              <Badge variant="info" icon={null} title="This case has been re-run; earlier versions are kept as history">
                Version {c.version}
              </Badge>
            ) : null}
          </div>
        </div>
        <VersionSelect versions={versions} value={c.version} onChange={onVersion} />
      </div>

      <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
        <span className="label-caps">Reference</span>
        <span className="num text-[0.8125rem] text-ink-2">{reference}</span>
        <CopyButton text={reference} label={`Copy reference ${reference}`} />
        <span aria-hidden className="hidden sm:inline">·</span>
        <span>{since}</span>
      </p>
    </header>
  );
}
