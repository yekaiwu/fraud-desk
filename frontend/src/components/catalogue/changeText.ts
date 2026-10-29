import { severityLabel } from "@/lib/labels";
import type { RuleChange, RuleOverride, Severity } from "@/lib/types";

const QUOTE_MAX = 140;
const q = (t: string) => {
  const s = t.trim().replace(/\s+/g, " ");
  return `“${s.length > QUOTE_MAX ? `${s.slice(0, QUOTE_MAX - 1)}…` : s}”`;
};

const text = (v: string | null | undefined) => v?.trim() || null;

function wording(label: string, from: string | null, to: string | null): string | null {
  if (from === to) return null;
  if (to === null) return `${label} back to the default wording`;
  if (from === null) return `${label} set to ${q(to)}`;
  return `${label} changed from ${q(from)} to ${q(to)}`;
}

/**
 * What a log entry changed, as short plain sentences. An absent override means "all defaults", so a reset reads as the
 * values that were put back. `defaultSeverity` names the severity a null setting stands for, when the caller knows it.
 */
export function describeChange(c: Pick<RuleChange, "before" | "after">, defaultSeverity?: Severity): string[] {
  const b: Partial<RuleOverride> = c.before ?? {};
  const a: Partial<RuleOverride> = c.after ?? {};
  const out: string[] = [];
  const be = b.enabled ?? true;
  const ae = a.enabled ?? true;
  if (be !== ae) out.push(ae ? "Switched on" : "Switched off");
  const bs = b.severity ?? null;
  const as = a.severity ?? null;
  if (bs !== as) {
    const name = (s: Severity | null) => (s ? severityLabel(s) : defaultSeverity ? `${severityLabel(defaultSeverity)} (default)` : "the default");
    out.push(`Severity changed from ${name(bs)} to ${name(as)}`);
  }
  for (const line of [wording("Name", text(b.name), text(a.name)), wording("Description", text(b.description), text(a.description)), wording("Internal note", text(b.note), text(a.note))]) {
    if (line) out.push(line);
  }
  return out;
}
