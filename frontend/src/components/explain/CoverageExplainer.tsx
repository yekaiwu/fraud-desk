"use client";

import { useMemo } from "react";
import { docTypeLabel, makeDocNamer } from "@/lib/docNames";
import { effectiveCoverage } from "@/lib/explain";
import { pageCounts, pagesLabelText, plainNotCheckedReason } from "@/lib/flags";
import { formatRatio } from "@/lib/format";
import { coverageText, plural, UNCHECKED_PAGES_LABEL } from "@/lib/labels";
import { groupSkippedChecks } from "@/lib/skipGroups";
import { useExplainMethod } from "@/lib/useExplain";
import type { CaseDetail, CoverageSkip } from "@/lib/types";
import { Explain } from "./Explain";
import { ExplainFacts, ExplainLead, ExplainNote, ExplainSection, TechnicalDetail } from "./parts";

export const CHECKS_LABEL = "What checks completed means";
const MAX_GROUPS = 3;

/** Why checks did not run, grouped, most important first. Shared by the explainer and the analysis trail. */
export function NotRunGroups({ skipped, limit = MAX_GROUPS }: { skipped: CoverageSkip[]; limit?: number }) {
  const groups = groupSkippedChecks(skipped);
  const shown = groups.slice(0, limit);
  return (
    <ul className="space-y-2">
      {shown.map((g) => (
        <li key={g.key}>
          <p className="font-semibold text-ink">
            {g.title} <span className="tabular font-normal text-muted">({g.checks.length})</span>
          </p>
          <p className="text-sm">{g.why}</p>
        </li>
      ))}
      {groups.length > shown.length ? <li className="text-sm text-muted">and {groups.length - shown.length} more {plural(groups.length - shown.length, "reason")}, listed in the Coverage panel.</li> : null}
    </ul>
  );
}

/** Checks completed, with this case's numbers. */
export function CoverageExplainer({ c }: { c: CaseDetail }) {
  const { method } = useExplainMethod();
  const cov = c.coverage;
  const pages = pageCounts(c);
  const eff = effectiveCoverage(cov);
  const states = useMemo(() => {
    const runs = c.check_runs;
    return { ran: runs.filter((r) => r.status === "ran").length, skipped: runs.filter((r) => r.status === "skipped").length, error: runs.filter((r) => r.status === "error").length };
  }, [c.check_runs]);
  return (
    <>
      <ExplainLead>{method?.coverage.definition ?? "Checks completed shows how many of the checks that apply to this case were able to run."}</ExplainLead>
      <ExplainSection title="This case">
        {cov ? (
          <>
            <p className="tabular">
              <strong className="text-ink">{coverageText(cov.ran, cov.applicable)}</strong> ({formatRatio(cov.ratio)}).
              {method && cov.ratio < method.coverage.low_coverage_ratio ? ` That is below ${formatRatio(method.coverage.low_coverage_ratio)}, so a low score here means less than it would with every check done.` : ""}
            </p>
            {cov.skipped.length > 0 ? (
              <>
                <p className="label-caps">Why {cov.skipped.length} {plural(cov.skipped.length, "check")} did not run</p>
                <NotRunGroups skipped={cov.skipped} />
              </>
            ) : (
              <p>Every applicable check ran.</p>
            )}
            <p className="tabular">
              {pages.unchecked > 0
                ? `${pages.unchecked} of ${pages.total} ${plural(pages.total, "page")} ${pages.unchecked === 1 ? "was" : "were"} not reviewed by any check.`
                : `All ${pages.total} ${plural(pages.total, "page")} were reviewed.`}
              {method && eff !== null && pages.unchecked > 0 ? ` Counting those pages, the figure used for the fast lane is ${formatRatio(eff)}.` : ""}
            </p>
          </>
        ) : (
          <p>No checks have been counted yet.</p>
        )}
      </ExplainSection>
      <ExplainNote>A check that did not run is not a clearance.</ExplainNote>
      {cov ? (
        <TechnicalDetail>
          <ExplainFacts
            items={[
              { label: "Applicable", value: <span className="num text-xs">{cov.applicable}</span> },
              { label: "Ran", value: <span className="num text-xs">{states.ran || cov.ran}</span> },
              { label: "Skipped", value: <span className="num text-xs">{states.skipped}</span> },
              { label: "Errors", value: <span className="num text-xs">{states.error}</span> },
              { label: "Ratio", value: <span className="num text-xs">{cov.ratio}</span> },
              ...(method ? [{ label: "Fast-lane minimum", value: <span className="num text-xs">{method.coverage.fast_lane_min_coverage}</span> }] : []),
            ]}
          />
        </TechnicalDetail>
      ) : null}
    </>
  );
}

export function ExplainChecks({ c, className }: { c: CaseDetail; className?: string }) {
  return (
    <Explain label={CHECKS_LABEL} title="Checks completed" className={className}>
      <CoverageExplainer c={c} />
    </Explain>
  );
}

// ---------------------------------------------------------------- pages we could not review

export const PAGES_LABEL = "Why pages are not reviewed";

/** Why a page ends up in "Pages we could not review", from this case's own reasons. */
export function PagesExplainer({ c }: { c: CaseDetail }) {
  const { method } = useExplainMethod();
  const names = useMemo(() => makeDocNamer(c.documents, c.files), [c.documents, c.files]);
  const pages = pageCounts(c);
  const eff = effectiveCoverage(c.coverage);
  const groups = useMemo(() => {
    const m = new Map<string, { text: string; items: string[] }>();
    for (const n of c.not_checked_documents) {
      const text = plainNotCheckedReason(n.reason, n.doc_type);
      const g = m.get(text) ?? { text, items: [] };
      g.items.push(`${names.known ? names.doc(n.document_id) : docTypeLabel(n.doc_type)}, ${pagesLabelText(n.pages)}`);
      m.set(text, g);
    }
    return [...m.values()];
  }, [c.not_checked_documents, names]);
  return (
    <>
      <ExplainLead>A page is listed here when no check was able to look at it, so nothing has been tested on it. Each entry says why.</ExplainLead>
      <ExplainSection title="This case">
        {groups.length === 0 ? (
          <p>No pages are listed.</p>
        ) : (
          <ul className="space-y-2.5">
            {groups.map((g) => (
              <li key={g.text}>
                <p className="text-ink">{g.text}</p>
                <p className="text-sm">
                  {g.items.length} {plural(g.items.length, "entry", "entries")}: {g.items.join("; ")}
                </p>
              </li>
            ))}
          </ul>
        )}
        <p className="tabular">
          {pages.unchecked} of {pages.total} {plural(pages.total, "page")} not reviewed.
          {method && eff !== null && pages.unchecked > 0 ? ` They lower the figure used for the fast lane to ${formatRatio(eff)}.` : ""}
        </p>
      </ExplainSection>
      <ExplainNote>{UNCHECKED_PAGES_LABEL} are not cleared: read them yourself if they matter to your decision.</ExplainNote>
      {c.not_checked_documents.length > 0 ? (
        <TechnicalDetail>
          <ul className="space-y-0.5">
            {c.not_checked_documents.map((n) => (
              <li key={`${n.document_id}-${n.pages}`} className="num break-words text-xs">
                {n.document_id} · {n.doc_type} · {n.pages} · {n.reason}
              </li>
            ))}
          </ul>
        </TechnicalDetail>
      ) : null}
    </>
  );
}

export function ExplainPages({ c, className }: { c: CaseDetail; className?: string }) {
  return (
    <Explain label={PAGES_LABEL} title={UNCHECKED_PAGES_LABEL} className={className}>
      <PagesExplainer c={c} />
    </Explain>
  );
}
