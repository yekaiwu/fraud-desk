"use client";

import { useMemo } from "react";
import { effectiveDescription, effectiveName, stageLabel } from "@/lib/checkCatalogue";
import { makeDocNamer } from "@/lib/docNames";
import { flagWhere, SEVERITY_ORDER, stripTitleSuffix } from "@/lib/flags";
import { HARD_STOP_LABEL, plural, severityLabel } from "@/lib/labels";
import { useExplainMethod, useRuleCatalogue } from "@/lib/useExplain";
import type { CaseDetail, Finding, Flag, RuleInfo } from "@/lib/types";
import { EvidenceRows } from "../case/FindingEvidence";
import { Explain } from "./Explain";
import { ExplainFacts, ExplainLead, ExplainNote, ExplainSection, TechnicalDetail } from "./parts";

export const FINDING_LABEL = "Why was this flagged?";

function highestDefault(rules: RuleInfo[]) {
  return [...rules].sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity))[0]?.severity ?? null;
}

/** Why one finding exists: the check, what it found, where, and what it adds to the score. */
export function FindingExplainer({ c, finding, flags }: { c: CaseDetail; finding: Finding; flags: Flag[] }) {
  const { byId, rules, loading } = useRuleCatalogue();
  const { method } = useExplainMethod();
  const names = useMemo(() => makeDocNamer(c.documents, c.files), [c.documents, c.files]);
  const infos = finding.rules.map((r) => byId.get(r)).filter((r): r is RuleInfo => !!r);
  const first = flags[0];
  const usual = highestDefault(infos);
  const more = flags.length - 1;
  return (
    <>
      <ExplainLead>
        {stripTitleSuffix(finding.title)}.{" "}
        {infos.length > 0
          ? "This is what the check that raised it looks for."
          : loading
            ? "Loading the description of the check..."
            : rules
              ? "The description of this check is not in the catalogue."
              : "The check descriptions are not available right now."}
      </ExplainLead>

      {infos.length > 0 ? (
        <ul className="space-y-2.5">
          {infos.map((r) => (
            <li key={r.rule}>
              <p className="font-semibold text-ink" title={r.rule}>
                {effectiveName(r.rule, r.override)}
                {r.override?.name ? <span className="num ml-2 text-xs font-normal text-muted">{r.rule}</span> : null}
              </p>
              <p className="break-words text-sm">{effectiveDescription(r)}</p>
              {r.tamper ? <p className="text-sm text-muted">Looks for: {r.tamper}.</p> : null}
            </li>
          ))}
        </ul>
      ) : null}

      <ExplainSection title="What was found">
        {first ? (
          <>
            <EvidenceRows evidence={first.evidence} names={names} />
            {more > 0 ? (
              <p className="text-sm text-muted">
                {more} more {plural(more, "instance")} of the same finding; open the finding to see {more === 1 ? "it" : "them"}.
              </p>
            ) : null}
          </>
        ) : (
          <p>No evidence is recorded for this finding.</p>
        )}
      </ExplainSection>

      <ExplainSection title="Where">
        {first ? <p>{flagWhere(first, names)}</p> : null}
        {finding.document_ids.length > 0 ? <p className="text-sm">Documents: {finding.document_ids.map((d) => names.doc(d)).join("; ")}.</p> : null}
      </ExplainSection>

      <ExplainSection title="What it adds to the score">
        <p className="tabular">
          {finding.weight > 0 ? (
            <>
              Adds <strong className="text-ink">{finding.weight}</strong> to the risk score, because it is rated <strong className="text-ink">{severityLabel(finding.severity).toLowerCase()}</strong>.
            </>
          ) : (
            <>It is for information only, so it does not change the risk score.</>
          )}
          {usual && usual !== finding.severity ? ` The check is usually rated ${severityLabel(usual).toLowerCase()}; this case was rated differently.` : usual ? ` That is the usual rating for this check.` : ""}
          {flags.length > 1 ? ` It counts once, although ${flags.length} flags sit behind it.` : ""}
        </p>
        {finding.hard_stop ? (
          <p>
            It also carries the label <strong className="text-ink">{HARD_STOP_LABEL}</strong>: this case cannot go to the fast lane. That does not change the score.
          </p>
        ) : null}
      </ExplainSection>

      <ExplainNote>A finding is something to check, not proof of fraud.</ExplainNote>

      <TechnicalDetail>
        <ExplainFacts
          items={[
            { label: "Finding", value: <span className="num text-xs">{finding.id}</span> },
            { label: "Checks", value: <span className="num break-words text-xs">{finding.rules.join(", ")}</span> },
            { label: "Version", value: <span className="num text-xs">{infos.map((r) => `${r.rule} v${r.version}`).join(", ") || "not available"}</span> },
            { label: "Stage", value: infos.length > 0 ? infos.map((r) => stageLabel(r.stage)).join(", ") : first ? stageLabel(first.stage) : "not available" },
            { label: "Group", value: <span className="num break-words text-xs">{finding.group_key}</span> },
            { label: "Flags", value: <span className="num break-words text-xs">{finding.flag_ids.join(", ")}</span> },
            { label: "Ruleset", value: <span className="num text-xs">{method?.ruleset_version ?? c.provenance?.ruleset_version ?? "not recorded"}</span> },
          ]}
        />
      </TechnicalDetail>
    </>
  );
}

export function ExplainFinding({ c, finding, flags, className }: { c: CaseDetail; finding: Finding; flags: Flag[]; className?: string }) {
  return (
    <Explain label={FINDING_LABEL} title={FINDING_LABEL} className={className}>
      <FindingExplainer c={c} finding={finding} flags={flags} />
    </Explain>
  );
}
