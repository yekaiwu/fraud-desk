"use client";

import { AlertTriangle, CheckCircle2, Globe, PencilRuler } from "lucide-react";
import Link from "next/link";
import { LOW_COVERAGE_RATIO } from "@/lib/constants";
import { pageCounts, ruleName } from "@/lib/flags";
import { checkName } from "@/lib/checkCatalogue";
import { customCoverageText, customRunTally } from "@/lib/customRules";
import { coverageText, plural, unreviewedPagesText } from "@/lib/labels";
import { groupSkippedChecks } from "@/lib/skipGroups";
import { useCheckName } from "@/lib/useCheckNames";
import type { CaseDetail } from "@/lib/types";
import { NO_AI_HINT, NO_SIGNAL } from "@/lib/wording";
import { ExplainChecks } from "../explain/CoverageExplainer";
import { MeterBar, Panel } from "../ui";
import { Disclosure } from "./EvidenceDisclosure";

export { groupSkippedChecks, plainSkipReason, type SkipGroup } from "@/lib/skipGroups";

export function CoveragePanel({ c, defaultOpen = true, id = "coverage" }: { c: CaseDetail; defaultOpen?: boolean; id?: string }) {
  const nameOf = useCheckName(ruleName);
  const lookupName = useCheckName(checkName);
  const cov = c.coverage;
  if (!cov) return null;
  const { unchecked, total } = pageCounts(c);
  // checks that looked something up outside the documents say what they asked and what came back, flags or not
  const lookups = c.check_runs.filter((r) => r.status === "ran" && !r.custom && (r.note ?? "").trim() !== "");
  const custom = customRunTally(c);
  const noSignal = c.check_runs.filter((r) => r.stage === "forensics" && r.status === "ran" && r.flags === 0);
  const groups = groupSkippedChecks(cov.skipped);
  const low = cov.ratio < LOW_COVERAGE_RATIO;
  const didNotRun = cov.skipped.length;
  return (
    <div id={id} className="scroll-mt-4">
      <Panel title="Coverage" eyebrow="What was checked" headingLevel={3} actions={<ExplainChecks c={c} />}>
        <div className="space-y-3">
          <MeterBar label="Checks completed" value={cov.ran} max={cov.applicable} valueText={coverageText(cov.ran, cov.applicable)} marker={LOW_COVERAGE_RATIO} tone={low ? "medium" : "brand"} />
          {low ? <p className="text-sm font-medium text-sev-medium">Low coverage: a low score here means less than it would with every check done.</p> : null}
          {unchecked > 0 ? (
            <p className="tabular flex items-start gap-2 text-[0.9375rem] font-medium text-brass-deep">
              <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                {unreviewedPagesText(unchecked)} (of {total})
              </span>
            </p>
          ) : (
            <p className="flex items-start gap-2 text-[0.9375rem] text-ink-2">
              <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-sev-low" />
              <span className="tabular">All {total} {plural(total, "page")} were reviewed.</span>
            </p>
          )}
          {lookups.length > 0 ? (
            <div role="region" aria-label="Looked up outside the documents" className="rounded-md border border-rule bg-surface p-3.5">
              <h4 className="label-caps mb-1.5 flex items-center gap-1.5">
                <Globe aria-hidden className="h-3.5 w-3.5" />
                Looked up outside the documents
              </h4>
              <ul className="space-y-1.5 text-[0.9375rem]">
                {lookups.map((r) => (
                  <li key={r.rule} title={r.rule} className="break-words">
                    <span className="font-semibold text-ink">{lookupName(r.rule)}</span>
                    <span className="text-ink-2">: {r.note}</span>
                    {r.flags > 0 ? <span className="ml-1 font-semibold text-brass-deep">({r.flags} {plural(r.flags, "flag")})</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {custom.total > 0 ? (
            <p data-testid="custom-coverage" className="tabular flex items-start gap-2 text-[0.9375rem] text-ink-2">
              <PencilRuler aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brass-deep" />
              <span>{customCoverageText(custom)}</span>
            </p>
          ) : null}
          {didNotRun > 0 ? <p className="text-sm text-ink-2">A check that did not run is not a clearance.</p> : null}
          <Disclosure label="Coverage details" meta={didNotRun > 0 ? `(${didNotRun} ${plural(didNotRun, "check")} did not run)` : undefined} defaultOpen={defaultOpen}>
            <div className="space-y-4 rounded-md border border-rule bg-paper-2/50 p-3.5">
              {groups.length > 0 ? (
                <div role="region" aria-label="Checks that did not run" className="space-y-4">
                  <h4 className="label-caps">Checks that did not run</h4>
                  {groups.map((g) => (
                    <section key={g.key} aria-label={g.title}>
                      <p className="text-[0.9375rem] font-semibold text-ink">
                        {g.title} <span className="tabular font-normal text-muted">({g.checks.length})</span>
                      </p>
                      <p className="mt-0.5 text-sm text-ink-2">{g.why}</p>
                      {g.key === "switched_off" ? (
                        <ul className="mt-0.5 space-y-0.5 text-sm text-ink-2">
                          {g.checks.filter((x) => x.reason.trim() && x.reason.trim().toLowerCase() !== "switched_off").map((x) => (
                            <li key={x.rule} className="break-words">
                              <span className="font-medium text-ink">{nameOf(x.rule)}</span>: {x.reason}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {g.errored ? <p className="mt-0.5 text-sm text-sev-high">{g.checks.map((x) => x.reason).filter((v, i, a) => a.indexOf(v) === i).join("; ")}</p> : null}
                      <ul className="mt-1.5 flex flex-wrap gap-1.5">
                        {g.checks.map((x) => (
                          <li key={x.rule} title={x.rule} className="rounded-md border border-rule bg-surface px-2 py-0.5 text-sm text-ink-2">
                            {nameOf(x.rule)}
                          </li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-ink-2">Every applicable check ran.</p>
              )}
              {noSignal.length > 0 ? (
                <div>
                  <h4 className="label-caps mb-1">Forensic checks with no flags</h4>
                  <ul className="space-y-1 text-sm text-ink-2">
                    {noSignal.map((r) => (
                      <li key={r.rule} title={r.rule}>
                        <span className="font-medium text-ink">{nameOf(r.rule)}</span>: {r.rule === "ai_provenance_hint" ? NO_AI_HINT : NO_SIGNAL}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="text-sm">
                <Link href="/rules" className="font-medium text-brand-2 underline underline-offset-2">
                  See what each check looks for
                </Link>
              </p>
            </div>
          </Disclosure>
        </div>
      </Panel>
    </div>
  );
}
