"use client";

import clsx from "clsx";
import { ChevronRight, CircleSlash, Crosshair, Flag as FlagIcon, MapPin, ZapOff } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { makeDocNamer } from "@/lib/docNames";
import {
  dispositionSummary,
  findingHeadline,
  flagWhere,
  groupBySeverity,
  humanisePartialReason,
  isHighFlag,
  scoreImpactText,
  seenOnText,
  type NumberedFlag,
  type NumberedFlags,
} from "@/lib/flags";
import { CONFIRM_LABEL, DISMISS_LABEL, HARD_STOP_LABEL, coverageText, plural, severityLabel } from "@/lib/labels";
import { NO_FLAGS_NOTE } from "@/lib/wording";
import type { CaseDetail, Coverage, Document, FileRef, Finding, Region, Severity } from "@/lib/types";
import { ExplainBlocksFastLane } from "../explain/NextStepExplainer";
import { ExplainFinding } from "../explain/FindingExplainer";
import { Badge, Button, ConcernBadge, Notice, Skeleton, Stagger } from "../ui";
import { Disclosure, Reveal } from "./EvidenceDisclosure";
import { FindingEvidence } from "./FindingEvidence";
import { FindingMenu } from "./FindingMenu";
import { Amounts, DispositionChip, Marker } from "./FindingParts";
import { SEVERITY_COLOR } from "./RegionOverlay";

export { DispositionChip } from "./FindingParts";
export type DecideTarget = { decision: "confirm" | "dismiss"; flagIds?: string[]; findingId?: string };
export type SelectOpts = { reveal?: boolean; to?: { fileId: string; page: number } };

const MAX_BATCH = 50;
const EMPTY_DOCS: Document[] = [];
const EMPTY_FILES: FileRef[] = [];

// ---- what the officer has opened, remembered per case for as long as the page stays loaded (memory only) ----
type Memory = { groups: Partial<Record<Severity, boolean>>; cards: Record<string, boolean> };
const memory = new Map<string, Memory>();
const memoryOf = (key: string): Memory => {
  let m = memory.get(key);
  if (!m) memory.set(key, (m = { groups: {}, cards: {} }));
  return m;
};

const otherPageRegions = (nf: NumberedFlag): Region[] => {
  const r = nf.flag.region;
  return nf.flag.extra_regions.filter((x) => !r || x.file_id !== r.file_id || x.page !== r.page);
};

type Names = ReturnType<typeof makeDocNamer>;
type Common = {
  names: Names;
  selectedFlagId: string | null;
  checked: Set<string>;
  canDecide: boolean;
  onSelect: (id: string, opts?: SelectOpts) => void;
  onCheck: (id: string, on: boolean) => void;
  onDecide: (t: DecideTarget) => void;
  /** The whole case, for the "?" explainers (they need its score, coverage and documents). Without it no "?" is shown. */
  explainCase?: CaseDetail;
};

/** The pair of decisions for one flag. The sr-only number tells identical buttons apart. */
function DecisionButtons({ nf, onDecide }: { nf: NumberedFlag; onDecide: (t: DecideTarget) => void }) {
  return (
    <>
      <Button size="sm" variant="secondary" icon={<FlagIcon aria-hidden className="h-4 w-4" />} onClick={() => onDecide({ decision: "confirm", flagIds: [nf.flag.id] })}>
        {CONFIRM_LABEL}
        {" "}
        <span className="sr-only">(flag {nf.n})</span>
      </Button>
      <Button size="sm" variant="secondary" icon={<CircleSlash aria-hidden className="h-4 w-4" />} onClick={() => onDecide({ decision: "dismiss", flagIds: [nf.flag.id] })}>
        {DISMISS_LABEL}...
        {" "}
        <span className="sr-only">(flag {nf.n})</span>
      </Button>
    </>
  );
}

/** Actions and detail for one flag: where it is, jump to it, decide, evidence. Shared by single-flag cards and instances. */
function FlagBody({ nf, c, showWhere = true }: { nf: NumberedFlag; c: Common; showWhere?: boolean }) {
  const { flag, n } = nf;
  const decidable = c.canDecide && flag.disposition !== "pending_second_approval";
  const others = otherPageRegions(nf);
  const otherLink = (r: Region, i: number) => (
    <button
      key={i}
      type="button"
      onClick={() => c.onSelect(flag.id, { reveal: true, to: { fileId: r.file_id, page: r.page } })}
      className="min-h-9 rounded text-left text-brand-2 underline decoration-dotted underline-offset-2 hover:text-brand pointer-coarse:min-h-11"
    >
      Also on {c.names.page(r.file_id, r.page)}
      {r.label ? ` (${r.label})` : ""}
    </button>
  );
  return (
    <div className="space-y-2.5">
      {showWhere ? (
        <p className="flex items-start gap-1.5 text-sm text-ink-2" title={flag.document_ids.map((d) => c.names.docTitle(d)).join(", ") || undefined}>
          <MapPin aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
          <span className="min-w-0 break-words">{flagWhere(flag, c.names)}</span>
        </p>
      ) : null}
      {others.length > 0 ? (
        others.length <= 2 ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-2">{others.map(otherLink)}</div>
        ) : (
          <Disclosure label={`Also on ${others.length} other pages`} buttonClassName="font-medium text-ink-2">
            <div className="flex flex-col items-start gap-0.5 text-sm">{others.map(otherLink)}</div>
          </Disclosure>
        )
      ) : null}
      {flag.disposition === "pending_second_approval" ? <p className="text-sm font-medium text-brass-deep">A dismissal is waiting for a second reviewer.</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" className="-ml-2" icon={<Crosshair aria-hidden className="h-4 w-4" />} onClick={() => c.onSelect(flag.id, { reveal: true })}>
          Show on document
          {" "}
          <span className="sr-only">(flag {n})</span>
        </Button>
        {decidable ? (
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
            <DecisionButtons nf={nf} onDecide={c.onDecide} />
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <FindingEvidence flag={flag} names={c.names} />
        {c.canDecide ? (
          <label className="-mr-1 inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-1 text-sm text-muted hover:text-ink pointer-coarse:min-h-11">
            <input type="checkbox" checked={c.checked.has(flag.id)} onChange={(e) => c.onCheck(flag.id, e.target.checked)} className="h-4 w-4 accent-[var(--brand-2)]" />
            Select<span className="sr-only"> flag {n}</span>
          </label>
        ) : null}
      </div>
    </div>
  );
}

/** One instance inside a finding that fired more than once. */
export function FlagRow({ nf, c, headline }: { nf: NumberedFlag; c: Common; headline: string }) {
  const { flag } = nf;
  const selected = flag.id === c.selectedFlagId;
  const own = flag.explanation.trim();
  return (
    <li
      id={`flag-${flag.id}`}
      data-selected={selected}
      className={clsx("rounded-md border p-3.5", selected ? "border-brand-2 bg-brand-tint/50 ring-1 ring-brand-2" : "border-rule bg-paper/70")}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5">
          <Marker n={nf.n} severity={flag.severity} />
        </span>
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            {headline ? null : <ConcernBadge severity={flag.severity} />}
            <DispositionChip d={flag.disposition} />
          </div>
          {own && own !== headline ? (
            <p className="text-[0.9375rem] text-ink">
              <Amounts text={c.names.text(own)} mono={false} />
            </p>
          ) : null}
          <FlagBody nf={nf} c={c} />
        </div>
      </div>
    </li>
  );
}

function SeenOn({ ids, names, onShowDocument }: { ids: string[]; names: Names; onShowDocument: (docId: string) => void }) {
  if (ids.length === 0) return null;
  const button = (d: string) => (
    <button
      key={d}
      type="button"
      title={names.docTitle(d)}
      onClick={() => onShowDocument(d)}
      className="min-h-9 rounded-md border border-rule bg-surface px-2.5 text-left text-sm text-ink-2 hover:border-brand-2 hover:bg-brand-tint pointer-coarse:min-h-11"
    >
      {names.doc(d)}
    </button>
  );
  if (ids.length === 1) {
    return (
      <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
        {seenOnText(1)}
        {button(ids[0])}
      </p>
    );
  }
  return (
    <Disclosure label={seenOnText(ids.length)} buttonClassName="font-medium text-ink-2">
      <div className="flex flex-wrap gap-2">{ids.map(button)}</div>
    </Disclosure>
  );
}

export function FindingCard({
  finding,
  flags,
  expanded,
  onToggle,
  common,
  onShowDocument,
}: {
  finding: Finding;
  flags: NumberedFlag[];
  expanded: boolean;
  onToggle: (open: boolean) => void;
  common: Common;
  onShowDocument: (docId: string) => void;
}) {
  const { names, canDecide, onDecide, onSelect } = common;
  const state = dispositionSummary(flags.map((f) => f.flag));
  const headline = findingHeadline(finding, flags.map((f) => f.flag));
  const multi = flags.length > 1;
  const single = flags.length === 1 ? flags[0] : null;
  const selected = flags.some((f) => f.flag.id === common.selectedFlagId);
  const bulkable = canDecide && multi && flags.some((f) => f.flag.disposition !== "pending_second_approval");
  const first = flags[0];
  return (
    <li
      id={single ? `flag-${single.flag.id}` : undefined}
      data-finding={finding.id}
      data-selected={selected}
      className={clsx("relative overflow-hidden rounded-lg border bg-surface shadow-paper", selected ? "border-brand-2 ring-1 ring-brand-2" : "border-rule")}
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: SEVERITY_COLOR[finding.severity] }} />
      <div className="space-y-3 p-4 pl-5 sm:p-5 sm:pl-6">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <ConcernBadge severity={finding.severity} />
          {finding.hard_stop ? (
            <>
              <Badge variant="status" icon={ZapOff} title="Not eligible for the fast lane">
                {HARD_STOP_LABEL}
              </Badge>
              {common.explainCase ? <ExplainBlocksFastLane c={common.explainCase} finding={finding} /> : null}
            </>
          ) : null}
          {single ? <DispositionChip d={single.flag.disposition} /> : null}
          <span className="inline-flex items-center gap-1.5 sm:ml-auto">
            <span className="tabular text-xs text-muted">{scoreImpactText(finding.weight)}</span>
            {common.explainCase ? <ExplainFinding c={common.explainCase} finding={finding} flags={flags.map((f) => f.flag)} /> : null}
          </span>
        </div>

        <h4 className="flex items-start gap-2.5 text-[1.0625rem] font-semibold leading-snug text-ink">
          {single ? (
            <span className="mt-px">
              <Marker n={single.n} severity={single.flag.severity} />
            </span>
          ) : null}
          <span className="min-w-0 break-words">
            <Amounts text={names.text(headline)} mono={false} />
          </span>
        </h4>

        <SeenOn ids={finding.document_ids} names={names} onShowDocument={onShowDocument} />

        {single ? (
          <FlagBody nf={single} c={common} />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {first ? (
                <Button size="sm" variant="ghost" className="-ml-2" icon={<Crosshair aria-hidden className="h-4 w-4" />} onClick={() => onSelect(first.flag.id, { reveal: true })}>
                  Show on document
                  {" "}
                  <span className="sr-only">(flag {first.n})</span>
                </Button>
              ) : null}
              {bulkable ? (
                <FindingMenu
                  className="sm:ml-auto"
                  label={`Apply to all ${flags.length}`}
                  items={[
                    { label: `Mark all ${flags.length} as a real concern`, icon: <FlagIcon aria-hidden className="h-4 w-4 text-ink-2" />, onSelect: () => onDecide({ decision: "confirm", findingId: finding.id }) },
                    { label: `Mark all ${flags.length} as not a concern...`, icon: <CircleSlash aria-hidden className="h-4 w-4 text-ink-2" />, onSelect: () => onDecide({ decision: "dismiss", findingId: finding.id }) },
                  ]}
                />
              ) : null}
            </div>
            <Disclosure
              label={`${expanded ? "Hide" : "Review"} the ${flags.length} ${plural(flags.length, "instance")}`}
              meta={state ? `(${state})` : undefined}
              open={expanded}
              onOpenChange={onToggle}
            >
              <ul className="space-y-2.5" aria-label={`${flags.length} instances`}>
                {flags.map((nf) => (
                  <FlagRow key={nf.flag.id} nf={nf} c={common} headline={headline} />
                ))}
              </ul>
            </Disclosure>
          </>
        )}
      </div>
    </li>
  );
}

const GROUP_ICON_COLOR: Record<Severity, string> = { high: "bg-sev-high", medium: "bg-sev-medium", low: "bg-sev-low", info: "bg-sev-info" };

function FindingsSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true">
      <p role="status" className="sr-only">
        Loading findings...
      </p>
      {[0, 1, 2].map((i) => (
        <div key={i} className="space-y-3 rounded-lg border border-rule bg-surface p-5 shadow-paper">
          <Skeleton className="h-5 w-28" />
          <Skeleton className="h-5 w-4/5" />
          <Skeleton className="h-9 w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function FindingsPanel({
  findings,
  numbered,
  selectedFlagId,
  onSelectFlag,
  onShowDocument,
  onDecide,
  canDecide,
  partial,
  partialReasons,
  coverage,
  documents = EMPTY_DOCS,
  files = EMPTY_FILES,
  caseId,
  explainCase,
  loading = false,
}: {
  findings: Finding[];
  numbered: NumberedFlags;
  selectedFlagId: string | null;
  onSelectFlag: (id: string, opts?: SelectOpts) => void;
  onShowDocument: (docId: string) => void;
  onDecide: (t: DecideTarget) => void;
  canDecide: boolean;
  partial: boolean;
  partialReasons: string[];
  coverage: Coverage | null;
  /** Optional: the case's documents and files, so ids read as "Bank statement, Nov 2025". Without them names fall back to "Document 8". */
  documents?: Document[];
  files?: FileRef[];
  /** Optional: keeps which groups are open apart between cases. */
  caseId?: string;
  /** The whole case, so each finding can carry a "Why was this flagged?" explainer. */
  explainCase?: CaseDetail;
  loading?: boolean;
}) {
  const names = useMemo(() => makeDocNamer(documents, files), [documents, files]);
  const groups = useMemo(() => groupBySeverity(findings), [findings]);
  const flagsOf = (f: Finding) => f.flag_ids.map((id) => numbered.byId.get(id)).filter((x): x is NumberedFlag => !!x);

  const memKey = `${caseId ?? ""}|${findings.map((f) => `${f.id}:${f.flag_ids.join(",")}`).join("|")}`;
  const [prevKey, setPrevKey] = useState(memKey);
  const [groupOpen, setGroupOpen] = useState<Partial<Record<Severity, boolean>>>(() => ({ ...memoryOf(memKey).groups }));
  const [cardOpen, setCardOpen] = useState<Record<string, boolean>>(() => ({ ...memoryOf(memKey).cards }));
  const [checked, setChecked] = useState<Set<string>>(new Set());
  if (memKey !== prevKey) {
    // another case (or a re-run) starts from what was remembered for it
    setPrevKey(memKey);
    setGroupOpen({ ...memoryOf(memKey).groups });
    setCardOpen({ ...memoryOf(memKey).cards });
    setChecked(new Set());
  }
  const isGroupOpen = (s: Severity) => groupOpen[s] ?? s === "high";
  const setGroup = (s: Severity, open: boolean) => {
    memoryOf(memKey).groups[s] = open;
    setGroupOpen((g) => ({ ...g, [s]: open }));
  };
  const setCard = (id: string, open: boolean) => {
    memoryOf(memKey).cards[id] = open;
    setCardOpen((c) => ({ ...c, [id]: open }));
  };

  // selecting a flag (overlay, shortcut, URL) opens the group and the finding that own it
  const ownerFlag = selectedFlagId ? numbered.byId.get(selectedFlagId) : undefined;
  const owner = ownerFlag?.findingId ?? null;
  const [prevOwner, setPrevOwner] = useState<string | null>(null);
  if (owner !== prevOwner) {
    // adjust state while rendering: a new selection opens what holds it
    setPrevOwner(owner);
    const f = owner ? findings.find((x) => x.id === owner) : undefined;
    if (f) {
      if (!isGroupOpen(f.severity)) setGroup(f.severity, true);
      if (!cardOpen[f.id]) setCard(f.id, true);
    }
  }
  useEffect(() => {
    if (selectedFlagId) document.getElementById(`flag-${selectedFlagId}`)?.scrollIntoView?.({ block: "nearest" });
  }, [selectedFlagId, owner]);

  const onCheck = (id: string, on: boolean) =>
    setChecked((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  const selectedNums = useMemo(() => [...checked].map((id) => numbered.byId.get(id)).filter((x): x is NumberedFlag => !!x), [checked, numbered]);
  const anyHigh = selectedNums.some((x) => isHighFlag(x.flag));
  const baseId = useId();

  if (loading) return <FindingsSkeleton />;

  const didNotRun = coverage ? Math.max(0, coverage.applicable - coverage.ran) : 0;
  if (findings.length === 0 && numbered.other.length === 0) {
    return partial ? (
      <Notice variant="warning" title="No concerns so far, but the analysis is incomplete.">
        <p>Some checks or pages did not run, so this is not a clearance.</p>
        <ul className="mt-1 list-disc pl-5">
          {partialReasons.map((r) => (
            <li key={r}>{humanisePartialReason(r)}</li>
          ))}
        </ul>
      </Notice>
    ) : (
      <div className="rounded-lg border border-rule bg-surface p-5 shadow-paper">
        {didNotRun > 0 ? (
          <>
            <p className="font-display text-lg font-semibold text-ink">No concerns were found.</p>
            <p className="mt-1 text-[0.9375rem] text-ink-2">
              This is not a clearance: {didNotRun} {plural(didNotRun, "check")} did not run.
            </p>
          </>
        ) : (
          <>
            <p className="font-display text-lg font-semibold text-ink">No concerns were found.</p>
            {coverage ? <p className="mt-1 text-[0.9375rem] text-ink-2">{coverageText(coverage.ran, coverage.applicable)}.</p> : null}
          </>
        )}
        <p className="mt-1 text-sm text-muted">{NO_FLAGS_NOTE}</p>
      </div>
    );
  }

  const common: Common = { names, selectedFlagId, checked, canDecide, onSelect: onSelectFlag, onCheck, onDecide, explainCase };
  const noMain = !findings.some((f) => f.severity !== "info");

  return (
    <div className="space-y-4">
      {selectedNums.length > 0 ? (
        <div role="region" aria-label="Selected flags" className="sticky top-2 z-10 rounded-lg border border-brand-2 bg-surface p-3 shadow-lift">
          <p className="tabular text-sm font-semibold text-ink">{selectedNums.length} flags selected</p>
          {anyHigh ? <p className="mt-1 text-sm text-brass-deep">A second reviewer must approve a dismissal that includes a high concern; the whole entry will be pending.</p> : null}
          {selectedNums.length > MAX_BATCH ? <p className="mt-1 text-sm text-sev-high">At most {MAX_BATCH} flags per decision.</p> : null}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" disabled={selectedNums.length > MAX_BATCH} onClick={() => onDecide({ decision: "confirm", flagIds: [...checked] })}>
              {CONFIRM_LABEL}: {selectedNums.length}
            </Button>
            <Button size="sm" variant="secondary" disabled={selectedNums.length > MAX_BATCH} onClick={() => onDecide({ decision: "dismiss", flagIds: [...checked] })}>
              {DISMISS_LABEL}: {selectedNums.length}...
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setChecked(new Set())}>
              Clear selection
            </Button>
          </div>
        </div>
      ) : null}

      {noMain ? (
        <Notice variant="info" title="No high, medium or low concerns were found.">
          {didNotRun > 0 ? `This is not a clearance: ${didNotRun} ${plural(didNotRun, "check")} did not run. ` : ""}
          {NO_FLAGS_NOTE}
        </Notice>
      ) : (
        <p className="text-sm text-muted">Highest concern first. The numbers match the markers on the document.</p>
      )}

      <Stagger className="space-y-5">
        {groups.map(({ severity, findings: list }) => {
          const open = isGroupOpen(severity);
          const bodyId = `${baseId}-${severity}`;
          const flagCount = list.reduce((n, f) => n + flagsOf(f).length, 0);
          return (
            <section key={severity} aria-labelledby={`${bodyId}-h`}>
              <h3 id={`${bodyId}-h`} className="rule-statement rule-statement-soft !pb-2.5">
                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={bodyId}
                  onClick={() => setGroup(severity, !open)}
                  className="-mx-2 flex min-h-11 w-[calc(100%+1rem)] items-center gap-2.5 rounded-md px-2 text-left hover:bg-brand-tint/60"
                >
                  <ChevronRight aria-hidden className={clsx("h-4 w-4 shrink-0 text-muted transition-transform duration-150", open && "rotate-90")} />
                  <span aria-hidden className={clsx("h-2.5 w-2.5 shrink-0 rounded-full", GROUP_ICON_COLOR[severity])} />
                  <span className="text-[1.1875rem]">{severityLabel(severity)}</span>
                  <span className="tabular rounded-full border border-rule bg-paper-2 px-2 py-px font-sans text-xs font-semibold text-ink-2">
                    {list.length}
                    <span className="sr-only"> {plural(list.length, "finding")}</span>
                  </span>
                  <span className="tabular ml-auto hidden font-sans text-xs font-normal text-muted sm:inline">
                    {flagCount} {plural(flagCount, "flag")}
                  </span>
                </button>
              </h3>
              {open ? (
                <Reveal id={bodyId} className="mt-3">
                  <ol className="space-y-3" aria-label={severityLabel(severity)}>
                    {list.map((f) => {
                      const fl = flagsOf(f);
                      return (
                        <FindingCard
                          key={f.id}
                          finding={f}
                          flags={fl}
                          expanded={!!cardOpen[f.id]}
                          onToggle={(o) => setCard(f.id, o)}
                          common={common}
                          onShowDocument={onShowDocument}
                        />
                      );
                    })}
                  </ol>
                </Reveal>
              ) : null}
            </section>
          );
        })}

        {numbered.other.length > 0 ? (
          <section aria-label="Other flags">
            <h3 className="rule-statement rule-statement-soft !pb-2.5 text-[1.1875rem]">Other flags</h3>
            <ul className="mt-3 space-y-3">
              {numbered.other.map((nf) => (
                <FlagRow key={nf.flag.id} nf={nf} c={common} headline="" />
              ))}
            </ul>
          </section>
        ) : null}
      </Stagger>
    </div>
  );
}
