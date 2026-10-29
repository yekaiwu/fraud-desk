import { isSwitchedOffReason, SWITCHED_OFF_REASON, SWITCHED_OFF_TEXT } from "./checkCatalogue";
import { docTypeLabel } from "./docNames";
import type { CoverageSkip } from "./types";

/** Plain-language reason for a skipped check. Unknown text is shown verbatim. */
export function plainSkipReason(reason: string): string {
  if (isSwitchedOffReason(reason)) return SWITCHED_OFF_TEXT;
  const m = /^requires\s+(.+)$/i.exec(reason.trim());
  return m ? `Needs a ${m[1].replace(/_/g, " ")} document, and none was found.` : reason;
}

export type SkipGroup = { key: string; title: string; why: string; errored: boolean; checks: { rule: string; reason: string }[] };

const NEEDS = /^(?:requires\s+|no usable document of type\s+)(.+)$/i;
const IMAGE = /raster|jpeg|png|image/i;

/** Groups the checks that did not run by why they did not, each with a plain title and why it matters. Errors come first. */
export function groupSkippedChecks(skipped: CoverageSkip[]): SkipGroup[] {
  const groups = new Map<string, SkipGroup>();
  for (const s of skipped) {
    const reason = s.reason.trim();
    let key: string;
    let title: string;
    let why: string;
    const needs = NEEDS.exec(reason);
    if (s.status === "error") {
      key = `error:${reason}`;
      title = "A check stopped with an error";
      why = "It did not finish, so the area it covers has not been looked at. Re-running the analysis may fix this.";
    } else if (s.code === SWITCHED_OFF_REASON || isSwitchedOffReason(reason)) {
      key = "switched_off";
      title = SWITCHED_OFF_TEXT;
      why = "A person turned these checks off, so they did not run. What they cover has not been looked at, and that is never a clearance.";
    } else if (needs) {
      const type = needs[1].trim().toLowerCase().replace(/\s+/g, "_");
      const label = docTypeLabel(type).toLowerCase();
      key = `needs:${type}`;
      title = `No usable ${label} was found`;
      why = `These checks test the figures in a ${label} against the rest of the case. Without one nothing has been tested against it.`;
    } else if (IMAGE.test(reason)) {
      key = "image";
      title = "No photo or scan to examine";
      why = "These checks look for signs of editing inside photographed or scanned pages. None were suitable, so that kind of editing was not looked for.";
    } else {
      key = `other:${reason}`;
      title = plainSkipReason(reason).replace(/^./, (ch) => ch.toUpperCase());
      why = "The check did not run, so what it covers is unchecked rather than clear.";
    }
    const g = groups.get(key) ?? { key, title, why, errored: s.status === "error", checks: [] };
    g.checks.push({ rule: s.rule, reason });
    groups.set(key, g);
  }
  const rank = (g: SkipGroup) => (g.errored ? 0 : g.key === "switched_off" ? 1 : g.key.startsWith("needs:") ? 2 : g.key === "image" ? 3 : 4);
  return [...groups.values()].sort((a, b) => rank(a) - rank(b));
}

