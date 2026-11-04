import { PencilRuler } from "lucide-react";
import { customCoverageText, customRunTally } from "@/lib/customRules";
import { plural } from "@/lib/labels";
import type { CaseDetail } from "@/lib/types";

/**
 * A quiet block for the analysis trail: the custom rules that ran on this case (triggered or not), who wrote each, and
 * that they sit outside coverage. Renders nothing when no custom rule ran. Names and wording are the owner's text,
 * shown as plain text.
 */
export function CustomRunsNote({ c }: { c: CaseDetail }) {
  const tally = customRunTally(c);
  if (tally.total === 0) return null;
  const runs = c.check_runs.filter((r) => r.custom);
  return (
    <div className="text-sm text-ink-2" data-testid="custom-runs-note">
      <p className="flex items-start gap-2">
        <PencilRuler aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
        <span className="tabular">
          {tally.total} custom {plural(tally.total, "rule")} {tally.total === 1 ? "was" : "were"} used, {tally.flags} {plural(tally.flags, "flag")} raised. {customCoverageText(tally)}
        </span>
      </p>
      <details className="mt-1 pl-6">
        <summary className="cursor-pointer font-medium text-brand-2 underline underline-offset-2">Which custom rules</summary>
        <ul className="mt-1.5 space-y-1">
          {runs.map((r) => (
            <li key={r.rule} className="break-words">
              <span className="font-medium text-ink">{r.custom?.name}</span> by {r.custom?.author}, version {r.custom?.version}
              {r.custom?.origin === "adopted" ? " (adopted)" : ""}: {r.status === "ran" ? (r.flags > 0 ? `${r.flags} ${plural(r.flags, "flag")}` : "no flags") : "did not apply"}
              <span className="block text-muted">{r.custom?.readback}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
