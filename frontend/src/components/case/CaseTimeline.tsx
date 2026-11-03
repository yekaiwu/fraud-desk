"use client";

import clsx from "clsx";
import { ChevronDown, ChevronRight, Download } from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";
import { checkName } from "@/lib/checkCatalogue";
import { docTypeLabel, makeDocNamer } from "@/lib/docNames";
import { formatBytes, formatSeconds, formatUtc } from "@/lib/format";
import { plural } from "@/lib/labels";
import { buildTimeline, offsetText, tallyText, timelineExport, type TimelineStep } from "@/lib/timeline";
import { useCheckName } from "@/lib/useCheckNames";
import type { CaseDetail, Flag } from "@/lib/types";
import { TeamMembers } from "../explain/ReviewTeam";
import { Badge, ConcernBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";

function download(name: string, text: string) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    /* no Blob URLs in this environment */
  }
}

const usd = (n: number | null) => (n === null || n === 0 ? "" : `$${n.toFixed(n < 0.01 ? 4 : 3)}`);

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h4 className="label-caps">{title}</h4>
      {children}
    </div>
  );
}

function Table({ head, children, caption }: { head: string[]; children: ReactNode; caption: string }) {
  return (
    <div className="overflow-x-auto rounded-md border border-rule">
      <table className="w-full min-w-[32rem] border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-paper-2 text-left">
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="label-caps px-3 py-2 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-rule bg-surface">{children}</tbody>
      </table>
    </div>
  );
}

const cell = "px-3 py-1.5 align-top";

function FlagRows({ flags, onShowFlag, names }: { flags: Flag[]; onShowFlag: (id: string) => void; names: { text(s: string): string } }) {
  if (flags.length === 0) return <p className="text-sm text-ink-2">This step raised no flags.</p>;
  return (
    <ul className="space-y-1.5">
      {flags.map((f) => (
        <li key={f.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 rounded-md border border-rule bg-surface px-3 py-2">
          <ConcernBadge severity={f.severity} />
          <span className="min-w-0 flex-1 break-words text-[0.9375rem] text-ink">
            {names.text(f.explanation || f.rule)}
            <span className="num ml-2 text-xs text-muted">{f.rule}</span>
          </span>
          <Button size="sm" variant="ghost" onClick={() => onShowFlag(f.id)}>
            Show on document
          </Button>
        </li>
      ))}
    </ul>
  );
}

function StepBody({ c, step, onShowFlag }: { c: CaseDetail; step: TimelineStep; onShowFlag: (id: string) => void }) {
  const nameOf = useCheckName(checkName);
  const names = useMemo(() => makeDocNamer(c.documents, c.files), [c.documents, c.files]);
  const ran = step.checks.filter((r) => r.status === "ran");
  const notRan = step.checks.filter((r) => r.status !== "ran");
  const sortedChecks = [...step.checks].sort((a, b) => b.flags - a.flags || Number(a.status !== "ran") - Number(b.status !== "ran") || a.rule.localeCompare(b.rule));

  return (
    <div className="space-y-4 text-[0.9375rem]">
      {step.error ? <p className="font-medium text-sev-high">Error: {step.error}</p> : null}
      {step.warnings.length > 0 ? (
        <Section title="Warnings">
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-ink-2">
            {step.warnings.map((w, i) => (
              <li key={i} className="break-words">
                {w}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {step.facts.length > 0 ? (
        <Section title="What the step recorded">
          <dl className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-x-6 gap-y-1.5 text-sm">
            {step.facts.map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted">{k}</dt>
                <dd className="num break-words text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        </Section>
      ) : null}

      {step.kind === "ingest" ? (
        <Section title="Files">
          <Table head={["File", "Pages", "Text layer", "Scanned", "Problems"]} caption="Files read">
            {c.files.map((f) => (
              <tr key={f.id}>
                <td className={cell}>
                  <span className="break-all">{f.name}</span>
                  <span className="ml-2 text-xs text-muted">{formatBytes(f.size_bytes)}</span>
                </td>
                <td className={cell}>{f.pages.length}</td>
                <td className={cell}>{f.pages.filter((p) => p.has_text_layer).length}</td>
                <td className={cell}>{f.pages.filter((p) => p.is_scan).length}</td>
                <td className={cell}>{f.pages.filter((p) => p.error).map((p) => `p${p.page}: ${p.error}`).join("; ") || "none"}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}

      {step.kind === "classify" ? (
        <Section title="What each document was recognised as">
          <Table head={["Document", "Recognised as", "Confidence", "By", "Why"]} caption="Document classification">
            {c.documents.map((d) => (
              <tr key={d.id} className={d.classification.probability < 0.7 ? "bg-brass-tint/30" : undefined}>
                <td className={cell}>{names.doc(d.id)}</td>
                <td className={cell}>{docTypeLabel(d.doc_type)}</td>
                <td className={clsx(cell, "tabular")}>{Math.round(d.classification.probability * 100)}%</td>
                <td className={cell}>
                  {d.classification.classifier} {d.classification.version}
                </td>
                <td className={cell}>{(d.classification.reasons ?? []).slice(0, 2).join("; ") || d.classification.rationale || ""}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}

      {step.kind === "extract" ? (
        <Section title="What was read from each document">
          <Table head={["Document", "Status", "Read with", "Fields", "Table rows", "Warnings"]} caption="Extraction per document">
            {c.documents.map((d) => (
              <tr key={d.id}>
                <td className={cell}>{names.doc(d.id)}</td>
                <td className={cell}>{d.status.replace(/_/g, " ")}</td>
                <td className={cell}>{d.extractor ?? "text layer"}</td>
                <td className={clsx(cell, "tabular")}>{Object.values(d.fields).filter((x) => x.value !== null && x.value !== "").length}</td>
                <td className={clsx(cell, "tabular")}>{Object.values(d.tables).reduce((n, t) => n + t.rows.length, 0)}</td>
                <td className={cell}>{d.warnings.length ? d.warnings.join("; ") : d.error ?? ""}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}

      {step.checks.length > 0 ? (
        <Section title={`Checks (${ran.length} ran, ${notRan.length} did not)`}>
          <Table head={["Check", "Result", "Flags", "What it did or why not"]} caption="Checks of this step">
            {sortedChecks.map((r) => (
              <tr key={r.rule} className={r.flags > 0 ? "bg-brand-tint/40" : undefined}>
                <td className={cell}>
                  <span className="font-medium text-ink">{nameOf(r.rule)}</span>
                  <span className="num ml-2 text-xs text-muted">{r.rule}</span>
                </td>
                <td className={cell}>{r.status === "ran" ? "ran" : r.status === "error" ? "failed" : "skipped"}</td>
                <td className={clsx(cell, "tabular")}>{r.flags || "-"}</td>
                <td className={cell}>{r.note ?? r.reason ?? ""}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}

      {step.kind === "team" ? (
        <Section title="Each member, what it saw, what it ruled">
          <TeamMembers agents={step.agents} onShowFlag={onShowFlag} />
        </Section>
      ) : null}

      {step.kind === "score" ? (
        <Section title="How the findings added up">
          <Table head={["#", "Finding", "Severity", "Adds", "Blocks fast lane"]} caption="Findings and their weights">
            {c.findings.map((f, i) => (
              <tr key={f.id}>
                <td className={clsx(cell, "tabular")}>{i + 1}</td>
                <td className={cell}>{f.title}</td>
                <td className={cell}>{f.severity}</td>
                <td className={clsx(cell, "tabular")}>{f.weight}</td>
                <td className={cell}>{f.hard_stop ? "yes" : "no"}</td>
              </tr>
            ))}
          </Table>
          {c.coverage ? (
            <p className="text-sm text-ink-2">
              Coverage: {c.coverage.ran} of {c.coverage.applicable} checks ran; {c.coverage.pages_unchecked} {plural(c.coverage.pages_unchecked, "page")} nobody reviewed.
              {c.partial ? ` The case is partial: ${c.partial_reasons.join("; ")}.` : ""}
            </p>
          ) : null}
        </Section>
      ) : null}

      {step.kind === "review" ? (
        <Section title="Decisions, oldest first">
          <ul className="space-y-1.5">
            {step.entries.map((e) => (
              <li key={e.id} className="rounded-md border border-rule bg-surface px-3 py-2 text-sm">
                <span className="font-semibold text-ink">{e.reviewer}</span> <span className="text-ink-2">({e.kind.replace(/_/g, " ")})</span>: {e.decision.replace(/_/g, " ")}
                {e.status !== "effective" ? <Badge variant="attention" className="ml-2">{e.status.replace(/_/g, " ")}</Badge> : null}
                <span className="block text-ink-2">{e.reason}</span>
                <span className="text-xs text-muted">{formatUtc(e.created_at)}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {step.kind !== "received" && step.kind !== "review" && step.kind !== "team" ? (
        <Section title={`Flags this step raised (${step.flags.length})`}>
          <FlagRows flags={step.flags} onShowFlag={onShowFlag} names={names} />
        </Section>
      ) : null}

      {step.calls.length > 0 ? (
        <Section title={`Model calls (${step.calls.length})`}>
          <Table head={["Purpose", "Model", "Tokens in / out", "Cost", "Time", "Cached"]} caption="Model calls of this step">
            {step.calls.map((x, i) => (
              <tr key={i}>
                <td className={cell}>{x.purpose}</td>
                <td className={cell}>{x.model}</td>
                <td className={clsx(cell, "tabular")}>
                  {x.prompt_tokens} / {x.completion_tokens}
                </td>
                <td className={clsx(cell, "tabular")}>${Number(x.usd).toFixed(4)}</td>
                <td className={clsx(cell, "tabular")}>{formatSeconds(x.seconds)}</td>
                <td className={cell}>{x.cache_hit ? "yes" : "no"}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
    </div>
  );
}

function StepCard({ c, step, n, open, onToggle, onShowFlag }: { c: CaseDetail; step: TimelineStep; n: number; open: boolean; onToggle: () => void; onShowFlag: (id: string) => void }) {
  const id = useId();
  const raisedHere = step.flags.length;
  return (
    <li className="relative pl-11">
      <span aria-hidden className="absolute left-0 top-3 grid h-8 w-8 place-items-center rounded-full bg-brand text-sm font-semibold text-on-brand">
        {n}
      </span>
      <span aria-hidden className="absolute bottom-[-0.75rem] left-[15px] top-12 w-px bg-rule" />
      <div className={clsx("rounded-lg border bg-surface shadow-paper", step.status === "failed" ? "border-sev-high" : "border-rule")}>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={onToggle}
          className="flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left hover:bg-brand-tint/40"
        >
          {open ? <ChevronDown aria-hidden className="mt-1 h-4 w-4 shrink-0 text-muted" /> : <ChevronRight aria-hidden className="mt-1 h-4 w-4 shrink-0 text-muted" />}
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="text-[1.0625rem] font-semibold text-ink">{step.title}</span>
              <span className="tabular text-xs text-muted">
                {[offsetText(step), step.seconds !== null && step.seconds > 0 ? formatSeconds(step.seconds) : "", usd(step.costUsd)].filter(Boolean).join(" · ")}
              </span>
              {step.status === "failed" ? <Badge variant="high">Failed</Badge> : null}
              {step.status === "skipped" ? <Badge variant="neutral" icon={null}>Skipped</Badge> : null}
            </span>
            <span className="mt-0.5 block text-[0.9375rem] text-ink-2">{step.headline}</span>
            {raisedHere > 0 || step.kind === "forensics" || step.kind === "rules" || step.kind === "crossdoc" ? (
              <span className="mt-1 block text-xs text-muted">
                Raised here: {raisedHere}. Flags so far: {tallyText(step.cumulative)}.
              </span>
            ) : null}
          </span>
        </button>
        {open ? (
          <div id={id} className="border-t border-rule px-4 py-3.5">
            <StepBody c={c} step={step} onShowFlag={onShowFlag} />
          </div>
        ) : null}
      </div>
    </li>
  );
}

/**
 * Every step a case went through, in order, each opening to what that step produced: the figures it recorded, each
 * check and what it did, the flags it raised (with Show on document), its model calls and their cost, and for the
 * review team every member's report and the challenger's rulings. "Flags so far" is the running tally, so you can see
 * which step added what. The whole thing downloads as JSON for analysis elsewhere.
 */
export function CaseTimeline({ c, onShowFlag }: { c: CaseDetail; onShowFlag: (flagId: string) => void }) {
  const steps = useMemo(() => buildTimeline(c), [c]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(steps.filter((s) => s.kind === "team" || s.flags.length > 0).map((s) => s.key)));
  const toggle = (key: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  const totalSeconds = steps.reduce((n, s) => n + (s.seconds ?? 0), 0);
  const totalUsd = steps.reduce((n, s) => n + (s.costUsd ?? 0), 0);
  const calls = c.costs.length;
  const allOpen = open.size === steps.length;

  return (
    <Panel
      title="Timeline"
      eyebrow="Every step this case went through"
      headingLevel={3}
      actions={
        <span className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => setOpen(allOpen ? new Set() : new Set(steps.map((s) => s.key)))}>
            {allOpen ? "Collapse all" : "Expand all"}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<Download aria-hidden className="h-4 w-4" />}
            onClick={() => download(`timeline-${c.id}.json`, JSON.stringify(timelineExport(c, steps), null, 2))}
          >
            Download as JSON
          </Button>
        </span>
      }
    >
      <p className="mb-4 text-[0.9375rem] text-ink-2">
        {steps.length} steps, {formatSeconds(totalSeconds)} of work, {calls} model {plural(calls, "call")}
        {totalUsd > 0 ? `, ${usd(totalUsd)}` : ""}. Open a step to see what it produced; &ldquo;Flags so far&rdquo; shows which step added what.
      </p>
      <ol className="space-y-3" aria-label="Steps of this case, in order">
        {steps.map((s, i) => (
          <StepCard key={s.key} c={c} step={s} n={i + 1} open={open.has(s.key)} onToggle={() => toggle(s.key)} onShowFlag={onShowFlag} />
        ))}
      </ol>
    </Panel>
  );
}
