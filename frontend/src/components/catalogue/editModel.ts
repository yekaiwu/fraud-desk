import { checkName, DESCRIPTION_MAX, defaultSeverity, documentTypeLabel, effectiveSeverity, isEnabled, NAME_MAX, NOTE_MAX, SEVERITY_ORDER, stageLabel } from "@/lib/checkCatalogue";
import { severityLabel } from "@/lib/labels";
import type { RuleInfo, RuleOverrideRequest, Severity } from "@/lib/types";

/** What the officer has typed in the editor. Empty wording means "use the default". */
export type Draft = { enabled: boolean; severity: Severity; name: string; description: string; note: string };

export function draftFrom(r: RuleInfo): Draft {
  return {
    enabled: isEnabled(r),
    severity: effectiveSeverity(r),
    name: r.override?.name ?? "",
    description: r.override?.description ?? "",
    note: r.override?.note ?? "",
  };
}

export type Patch = Partial<Pick<RuleOverrideRequest, "enabled" | "severity" | "name" | "description" | "note">>;

const norm = (v: string | null | undefined) => (v ?? "").trim();

/** Only the settings that differ from what is saved now. Wording equal to the default counts as "use the default". */
export function buildPatch(r: RuleInfo, d: Draft): Patch {
  const patch: Patch = {};
  if (d.enabled !== isEnabled(r)) patch.enabled = d.enabled;
  const sev = d.severity === defaultSeverity(r) ? null : d.severity;
  if (sev !== (r.override?.severity ?? null)) patch.severity = sev;
  const wording: [keyof Draft & ("name" | "description" | "note"), string | null][] = [
    ["name", checkName(r.rule)],
    ["description", r.description],
    ["note", null],
  ];
  for (const [k, def] of wording) {
    let next: string | null = norm(d[k]) || null;
    if (next !== null && next === def) next = null;
    const saved = norm(r.override?.[k]) || null;
    if (next !== saved) patch[k] = next;
  }
  return patch;
}

export const hasChanges = (p: Patch): boolean => Object.keys(p).length > 0;

/** The catalogue's reason limit (the review forms allow more). */
export const CATALOGUE_REASON_MAX = 500;

export type WordingErrors = { name?: string; description?: string; note?: string };

export function validateWording(d: Draft): WordingErrors {
  const e: WordingErrors = {};
  const check = (k: "name" | "description" | "note", max: number, label: string) => {
    const n = norm(d[k]).length;
    if (n > max) e[k] = `${label} can be at most ${max} characters (now ${n}).`;
  };
  check("name", NAME_MAX, "The name");
  check("description", DESCRIPTION_MAX, "The description");
  check("note", NOTE_MAX, "The note");
  return e;
}

export type Effect = { id: string; tone: "info" | "warning"; text: string };

export type EffectContext = {
  /** Points a finding adds, by severity (GET /explain/method). Absent when that could not be loaded. */
  weights?: Record<Severity, number>;
  /** Checks that block the fast lane (GET /explain/method). */
  hardStops?: string[];
  /** The whole catalogue, to tell whether a check is the only one of its kind. */
  catalogue: RuleInfo[];
};

/** Types of document that nothing else switched on would check, and the stage if this is its last switched-on check. */
export function soleCoverage(r: RuleInfo, catalogue: RuleInfo[]): { docTypes: string[]; stageAlone: boolean } {
  const others = catalogue.filter((x) => x.rule !== r.rule && isEnabled(x));
  const anyFile = (x: RuleInfo) => x.applies_to.some((t) => documentTypeLabel(t) === "Any file");
  const docTypes = r.applies_to
    .filter((t) => documentTypeLabel(t) !== "Any file")
    .filter((t) => !others.some((x) => anyFile(x) || x.applies_to.includes(t)));
  return { docTypes, stageAlone: !others.some((x) => x.stage === r.stage) };
}

const SAVE_TIMING = "These settings apply to analyses run after you save. Cases already analysed keep their results until they are re-run.";

/** The plain-language consequences of the pending change, computed from the change and nothing else. */
export function describeEffects(r: RuleInfo, patch: Patch, ctx: EffectContext): Effect[] {
  const out: Effect[] = [];
  const stronger = !!ctx.hardStops?.includes(r.rule);
  const points = (from: Severity, to: Severity) => (ctx.weights ? ` Each finding from it adds ${ctx.weights[to]} instead of ${ctx.weights[from]} points.` : "");

  if (patch.enabled === false) {
    out.push({
      id: "off",
      tone: "warning",
      text: "This check will not run. Cases will show it as not completed, so their coverage drops. A check that did not run is never a clearance.",
    });
    if (stronger) {
      out.push({
        id: "stronger-off",
        tone: "warning",
        text: "This is one of the stronger checks: a problem it finds blocks the fast lane. With it switched off, the fast lane is not offered at all for new analyses.",
      });
    }
    const sole = soleCoverage(r, ctx.catalogue);
    if (sole.docTypes.length > 0) {
      out.push({
        id: "sole-doc",
        tone: "warning",
        text: `It is the only switched-on check for ${sole.docTypes.map((t) => documentTypeLabel(t).toLowerCase()).join(", ")}. Nothing else would look at ${sole.docTypes.length === 1 ? "that kind of document" : "those kinds of document"}.`,
      });
    }
    if (sole.stageAlone) {
      out.push({ id: "sole-stage", tone: "warning", text: `It is the only switched-on check in the group "${stageLabel(r.stage)}". That whole group would stop running.` });
    }
  } else if (patch.enabled === true) {
    out.push({ id: "on", tone: "info", text: "This check will run again. Cases analysed from now on will show it as completed." });
  }

  if (patch.severity !== undefined) {
    const from = effectiveSeverity(r);
    const to = patch.severity ?? defaultSeverity(r);
    if (SEVERITY_ORDER[to] > SEVERITY_ORDER[from]) {
      out.push({
        id: "lower",
        tone: "info",
        text: `Lower concern (${severityLabel(from)} to ${severityLabel(to)}): findings add fewer points to the score.${points(from, to)} Points are all it changes. It does not open the fast lane (a case the default settings would have blocked stays blocked) and it never relaxes a review control: the block on approving and the second approver for high flags stay as they are.`,
      });
      if (stronger) {
        out.push({ id: "stronger-lower", tone: "warning", text: "This is one of the stronger checks. Lowering its severity does not remove its block on the fast lane, but its findings will weigh less in the score." });
      }
    } else {
      out.push({
        id: "higher",
        tone: "info",
        text: `Higher concern (${severityLabel(from)} to ${severityLabel(to)}): findings add more points to the score, so cases may score higher.${points(from, to)}`,
      });
    }
  }

  if (patch.name !== undefined || patch.description !== undefined || patch.note !== undefined) {
    out.push({ id: "wording", tone: "info", text: "Wording only changes how the check is described on screen. It does not change what the check does or how a case is scored." });
  }

  if (hasChanges(patch)) out.push({ id: "timing", tone: "info", text: SAVE_TIMING });
  return out;
}

export const NOTHING_CHANGED = "Nothing has been changed yet. Change a setting above and the effect is explained here.";
export { SAVE_TIMING };
