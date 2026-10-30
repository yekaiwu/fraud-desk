/** Flag numbering, dispositions and small helpers shared by the case page (frontend.md 9.1, 7.3, 9.3). */
import { CLASSIFIER_LOW_PROB } from "./constants";
import { docTypeLabel, type DocNamer } from "./docNames";
import { plural } from "./labels";
import type { CaseDetail, Disposition, Finding, Flag, Severity, UncheckedPage, UncheckedReason } from "./types";

/**
 * A "high flag" for review CONTROLS (approve-block, second approver for a dismissal): high by the severity in force or
 * by the code's own severity, so lowering a check in the catalogue never relaxes a control. Badges and points keep
 * using `flag.severity`.
 */
export const isHighFlag = (f: Pick<Flag, "severity" | "code_severity">): boolean => f.severity === "high" || f.code_severity === "high";

export type NumberedFlag = { flag: Flag; n: number; findingId: string | null };
export type NumberedFlags = { all: NumberedFlag[]; byId: Map<string, NumberedFlag>; other: NumberedFlag[] };

/** Number flags 1..n: findings in API order, then each finding's flag_ids order. Unlisted flags go last. */
export function numberFlags(findings: Finding[], flags: Flag[]): NumberedFlags {
  const byFlag = new Map(flags.map((f) => [f.id, f]));
  const all: NumberedFlag[] = [];
  const byId = new Map<string, NumberedFlag>();
  for (const fnd of findings) {
    for (const id of fnd.flag_ids) {
      const flag = byFlag.get(id);
      if (!flag || byId.has(id)) continue;
      const nf = { flag, n: all.length + 1, findingId: fnd.id };
      all.push(nf);
      byId.set(id, nf);
    }
  }
  const other: NumberedFlag[] = [];
  for (const flag of flags) {
    if (byId.has(flag.id)) continue;
    const nf = { flag, n: all.length + 1, findingId: null };
    all.push(nf);
    other.push(nf);
    byId.set(flag.id, nf);
  }
  return { all, byId, other };
}

const DISP_ORDER: Disposition[] = ["open", "pending_second_approval", "confirmed", "dismissed"];
export const DISPOSITION_TEXT: Record<Disposition, string> = {
  open: "open",
  confirmed: "confirmed",
  dismissed: "dismissed",
  pending_second_approval: "needs 2nd approval",
};

/** "2 open, 1 confirmed" */
export function dispositionSummary(flags: Pick<Flag, "disposition">[]): string {
  const counts = new Map<Disposition, number>();
  for (const f of flags) counts.set(f.disposition, (counts.get(f.disposition) ?? 0) + 1);
  return DISP_ORDER.filter((d) => counts.has(d))
    .map((d) => `${counts.get(d)} ${DISPOSITION_TEXT[d]}`)
    .join(", ");
}

export const SEVERITY_ORDER: Severity[] = ["high", "medium", "low", "info"];
export const SEVERITY_LABEL: Record<Severity, string> = { high: "High", medium: "Medium", low: "Low", info: "Info" };
export const SEVERITY_Z: Record<Severity, number> = { info: 1, low: 2, medium: 3, high: 4 };

/** "d3 · transactions row 18 · f1 p2" (row shown 1-based). */
export function flagLocation(flag: Flag): string {
  const parts: string[] = [];
  if (flag.document_ids.length) parts.push(flag.document_ids.join(", "));
  if (flag.table) parts.push(typeof flag.row === "number" ? `${flag.table} row ${flag.row + 1}` : flag.table);
  else if (flag.field) parts.push(flag.field);
  parts.push(flag.region ? `${flag.region.file_id} p${flag.region.page}` : "No location on a page");
  return parts.join(" · ");
}

/** Humanise `partial_reasons` strings (frontend.md 7.3). Unknown strings come back verbatim. */
export function humanisePartialReason(reason: string): string {
  const stage = /^stage_failed:(.+)$/.exec(reason);
  if (stage) return `The ${stage[1]} stage failed`;
  const check = /^check_error:(.+)$/.exec(reason);
  if (check) return `Check ${check[1]} errored`;
  const doc = /^document_failed:(.+)$/.exec(reason);
  if (doc) return `Document ${doc[1]} could not be read`;
  const split = /^statement_incomplete:(\d+)$/.exec(reason);
  if (split) return `${split[1]} scanned ${Number(split[1]) === 1 ? "statement" : "statements"} could not be put back together`;
  return reason;
}

/**
 * The pages no check looked at, with the reason. Uses the backend's list; for a case analysed before the list existed
 * it applies the same rules to the case itself (the first reason that applies wins, as in backend/app/queue.py).
 */
export function uncheckedPageList(c: CaseDetail): UncheckedPage[] {
  // An older analysis is served with an empty list although its count says otherwise: derive the list then.
  const sent = c.coverage?.unchecked_pages;
  if (sent && sent.length === (c.coverage?.pages_unchecked ?? 0)) return sent;
  const out: UncheckedPage[] = [];
  for (const f of c.files) {
    for (const p of f.pages) {
      const d = c.documents.find((x) => x.file_id === f.id && x.page_start <= p.page && p.page <= x.page_end);
      const cls = p.classification;
      let reason: UncheckedReason | null = null;
      if (!d) reason = "no_document";
      else if (p.error) reason = "unreadable";
      else if (d.status === "received_not_checked") reason = "not_checked";
      else if (d.status === "failed") reason = "failed";
      else if (d.doc_type === "boilerplate" || d.doc_type === "other" || cls?.label === "boilerplate" || cls?.label === "other") reason = "unrecognised_type";
      else if (cls && cls.probability < CLASSIFIER_LOW_PROB) reason = "low_confidence";
      if (reason) out.push({ file_id: f.id, page: p.page, reason });
    }
  }
  return out;
}

export function pageCounts(c: CaseDetail): { unchecked: number; total: number } {
  if (c.coverage && typeof c.coverage.pages_total === "number" && c.coverage.pages_total > 0) {
    return { unchecked: c.coverage.pages_unchecked ?? 0, total: c.coverage.pages_total };
  }
  // Fallback (frontend.md 9.3), only for fixtures that omit the API fields.
  const total = c.files.reduce((n, f) => n + f.pages.length, 0);
  const bad = new Set<string>();
  for (const d of c.documents) {
    const lowOrNone = d.classification.probability < CLASSIFIER_LOW_PROB || d.doc_type === "boilerplate" || d.doc_type === "other";
    if (d.status === "received_not_checked" || d.status === "failed" || lowOrNone) {
      for (let p = d.page_start; p <= d.page_end; p++) bad.add(`${d.file_id}:${p}`);
    }
  }
  for (const f of c.files) for (const p of f.pages) if (p.error) bad.add(`${f.id}:${p.page}`);
  return { unchecked: bad.size, total };
}

/** First page of a document, for "view page" links. */
export function documentFirstPage(c: Pick<CaseDetail, "documents">, docId: string): { fileId: string; page: number } | null {
  const d = c.documents.find((x) => x.id === docId);
  return d ? { fileId: d.file_id, page: d.page_start } : null;
}

/** "f1:1-3" style location from not_checked_documents.pages. */
export function parsePagesLabel(s: string): { fileId: string; page: number } | null {
  const m = /^(f\w+):(\d+)/.exec(s);
  return m ? { fileId: m[1], page: Number(m[2]) } : null;
}

export function flaggedOnPage(flags: Flag[], fileId: string, page: number): number {
  return flags.filter((f) => f.region?.file_id === fileId && f.region.page === page).length;
}

// ---- plain-language helpers for the findings, viewer, documents and coverage panels ----

/** Findings grouped by severity, highest first, empty groups dropped. API order is kept inside a group. */
export function groupBySeverity(findings: Finding[]): { severity: Severity; findings: Finding[] }[] {
  return SEVERITY_ORDER.map((severity) => ({ severity, findings: findings.filter((f) => f.severity === severity) })).filter((g) => g.findings.length > 0);
}

/** The backend appends "(seen on 3 documents)" or "(+3 similar)" to a title; the panel shows those counts itself. */
export function stripTitleSuffix(title: string): string {
  let t = title.trim();
  for (;;) {
    const next = t.replace(/\s*\((?:seen on \d+ documents?|\+\d+ similar)\)\s*$/i, "");
    if (next === t) return t;
    t = next;
  }
}

/** One plain sentence saying what is wrong: the first flag's full explanation, else the cleaned title. */
export function findingHeadline(finding: Finding, flags: Pick<Flag, "explanation">[]): string {
  const e = flags.find((f) => f.explanation.trim())?.explanation.trim();
  return e ?? stripTitleSuffix(finding.title);
}

/** "Adds 30 to the risk score". A weight of zero changes nothing, and says so. */
export function scoreImpactText(weight: number): string {
  return weight > 0 ? `Adds ${weight} to the risk score` : "Does not change the risk score";
}

/** "Seen on 3 documents". */
export function seenOnText(n: number): string {
  return `Seen on ${n} ${plural(n, "document")}`;
}

const RULE_PREFIX: Record<string, string> = {
  bs: "Bank statement",
  fs: "Financial statement",
  inv: "Invoice",
  x: "Across documents",
  kyc: "Ownership",
  doc: "Document",
  pdf: "PDF",
};

/** "fs_subtotals" -> "Financial statement: subtotals". Only for lists of checks; the id stays in the title attribute. */
export function ruleName(id: string): string {
  const [head, ...rest] = id.split("_");
  const prefix = rest.length > 0 ? RULE_PREFIX[head] : undefined;
  if (prefix) return `${prefix}: ${rest.join(" ")}`;
  const all = id.replace(/_/g, " ");
  return all ? all[0].toUpperCase() + all.slice(1) : id;
}

/** Where a concern is, in words: "Bank statement, Nov 2025, page 1 · transactions row 20". */
export function flagWhere(flag: Flag, names: DocNamer): string {
  const parts: string[] = [];
  if (flag.region) parts.push(names.page(flag.region.file_id, flag.region.page));
  else if (flag.document_ids.length) parts.push(flag.document_ids.slice(0, 2).map((d) => names.doc(d)).join(", ") + (flag.document_ids.length > 2 ? ` and ${flag.document_ids.length - 2} more` : ""));
  if (flag.table) parts.push(typeof flag.row === "number" ? `${flag.table.replace(/_/g, " ")} row ${flag.row + 1}` : flag.table.replace(/_/g, " "));
  else if (flag.field) parts.push(flag.field.replace(/_/g, " "));
  if (!flag.region) parts.push("No location on a page");
  return parts.join(" \u00b7 ");
}

/** "f15:2" -> "page 2"; "f1:1-3" -> "pages 1 to 3". */
export function pagesLabelText(s: string): string {
  const m = /^f\w+:(\d+)(?:-(\d+))?$/.exec(s.trim());
  if (!m) return s;
  return m[2] && m[2] !== m[1] ? `pages ${m[1]} to ${m[2]}` : `page ${m[1]}`;
}

/** Why a page was received but not checked, in plain English. Unknown text is shown as it came. */
export function plainNotCheckedReason(reason: string, docType: string): string {
  const m = /^classified as (\w+): no checks apply$/i.exec(reason.trim());
  const type = m?.[1] ?? docType;
  if (m) {
    if (type === "boilerplate") return "This looks like standard wording (for example terms and conditions), so none of our checks apply to it. Read it yourself if it matters.";
    if (type === "other") return "We could not tell what kind of document this is, so none of our checks were run on it.";
    return `It was recognised as ${docTypeLabel(type).toLowerCase()}, but no check applies to that kind of page.`;
  }
  if (/failed|error/i.test(reason)) return `We could not read this page (${reason}).`;
  return reason;
}
