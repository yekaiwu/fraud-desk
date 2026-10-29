/**
 * In-case entity graph derived in the browser from CaseDetail.documents (frontend.md 9.2, plan 0.4 theme 4).
 * Names are matched exactly after normalising; this is not the backend's fuzzy matcher.
 */
import { fmtCents, toCents } from "./decimal";
import type { Cell, CaseDetail, Document, Flag, Row, Table } from "./types";

export type NodeKind = "applicant" | "person" | "company" | "counterparty";
export type NodeRole = "owner" | "director" | "payer" | "payee" | "supplier" | "buyer";
export type GNode = { id: string; label: string; kind: NodeKind; roles: NodeRole[]; flagged: boolean };
export type EdgeKind = "owns" | "director" | "paid" | "paid_out" | "invoiced";
export type GEdge = {
  id: string; source: string; target: string; kind: EdgeKind; label: string;
  amountCents?: bigint; count?: number; docIds: string[]; rows: { docId: string; row: number }[]; flagged: boolean;
};
export type EntityGraph = { nodes: GNode[]; edges: GEdge[]; truncated: number };

const COMPANY_TOKENS = new Set(["pte", "ltd", "limited", "llp", "inc", "co"]);
export const MAX_PER_SIDE = 8;
export const MAX_NODES = 40;
const HEURISTIC_RULES = new Set(["x_directors", "x_ownership_vs_registry", "kyc_shareholdings", "x_entity", "bs_pass_through"]);

/** NFKC, lower-case, & -> and, strip . , ' " ( ), drop company tokens, sort tokens, join with a space. */
export function norm(name: string): string {
  return normInfo(name).key;
}

function normInfo(name: string): { key: string; hadCompanyToken: boolean } {
  const cleaned = name.normalize("NFKC").toLowerCase().replace(/&/g, " and ").replace(/[.,'"()]/g, "");
  const tokens = cleaned.split(/\s+/).filter(Boolean);
  const kept = tokens.filter((t) => !COMPANY_TOKENS.has(t));
  return { key: [...kept].sort().join(" "), hadCompanyToken: kept.length !== tokens.length };
}

const val = (c: Cell | undefined): string => (c?.value ?? "").trim();
function pick(row: Row, keys: string[]): string {
  for (const k of keys) {
    const v = val(row.cells[k]);
    if (v) return v;
  }
  return "";
}
const table = (d: Document, name: string): Table | undefined => d.tables[name];
const field = (d: Document, keys: string[]): string => {
  for (const k of keys) {
    const v = val(d.fields[k]);
    if (v) return v;
  }
  return "";
};
const pctLabel = (s: string): string => (s ? (s.endsWith("%") ? s : `${s}%`) : "");

type Acc = { amount: bigint; hasAmount: boolean; count: number };

export function deriveEntityGraph(c: CaseDetail): EntityGraph {
  const nodes = new Map<string, GNode>();
  const edges = new Map<string, GEdge>();
  const chartPct = new Map<string, string>();
  const regPct = new Map<string, string>();
  const sums = new Map<string, Acc>();

  const profile = c.documents.find((d) => d.doc_type === "company_profile");
  const applicantName = (c.applicant.name ?? "").trim() || (profile ? field(profile, ["company_name", "name"]) : "") || "Applicant";
  const APP = "applicant";
  nodes.set(APP, { id: APP, label: applicantName, kind: "applicant", roles: [], flagged: false });
  const appKey = norm(applicantName);

  function node(label: string, kind: NodeKind, role?: NodeRole): GNode | null {
    const { key, hadCompanyToken } = normInfo(label);
    if (!key) return null;
    if (key === appKey) {
      const a = nodes.get(APP)!;
      if (role && !a.roles.includes(role)) a.roles.push(role);
      return a;
    }
    const id = `n:${key}`;
    let n = nodes.get(id);
    if (!n) {
      n = { id, label: label.trim(), kind: kind === "person" && hadCompanyToken ? "company" : kind, roles: [], flagged: false };
      nodes.set(id, n);
    } else if (n.kind === "counterparty" && kind !== "counterparty") {
      n.kind = kind === "person" && hadCompanyToken ? "company" : kind;
    }
    if (role && !n.roles.includes(role)) n.roles.push(role);
    return n;
  }

  function edge(kind: EdgeKind, source: string, target: string, docId: string, row?: number, amount?: string): GEdge {
    const id = `${kind}:${source}>${target}`;
    let e = edges.get(id);
    if (!e) {
      e = { id, source, target, kind, label: "", docIds: [], rows: [], flagged: false };
      edges.set(id, e);
    }
    if (!e.docIds.includes(docId)) e.docIds.push(docId);
    if (row !== undefined) {
      e.rows.push({ docId, row });
      e.count = (e.count ?? 0) + 1;
    }
    if (amount !== undefined) {
      const acc = sums.get(id) ?? { amount: 0n, hasAmount: false, count: 0 };
      try {
        acc.amount += toCents(amount);
        acc.hasAmount = true;
      } catch {
        /* an amount we cannot read exactly is left out of the sum */
      }
      acc.count += 1;
      sums.set(id, acc);
    }
    return e;
  }

  for (const d of c.documents) {
    // directors
    const dirs = d.doc_type === "company_profile" ? table(d, "directors") : undefined;
    for (const row of dirs?.rows ?? []) {
      const name = pick(row, ["director_name", "name"]);
      const n = name && node(name, "person", "director");
      if (n && n.id !== APP) edge("director", n.id, APP, d.id, undefined);
    }
    // holders
    const holderTables: [Document, string, Map<string, string>][] = [];
    if (d.doc_type === "ownership_chart") holderTables.push([d, "holders", chartPct]);
    if (d.doc_type === "shareholder_register") holderTables.push([d, "shareholders", regPct]);
    if (d.doc_type === "company_profile") holderTables.push([d, "shareholders", regPct]);
    for (const [doc, tname, pcts] of holderTables) {
      for (const row of table(doc, tname)?.rows ?? []) {
        const name = pick(row, ["holder_name", "shareholder_name", "name", "holder"]);
        if (!name) continue;
        const type = pick(row, ["holder_type", "type"]).toLowerCase();
        const n = node(name, type === "company" ? "company" : "person", "owner");
        if (!n || n.id === APP) continue;
        if (type === "company") n.kind = "company";
        edge("owns", n.id, APP, doc.id);
        const p = pctLabel(pick(row, ["percent", "percentage", "shareholding_percent", "pct", "ownership_percent"]));
        if (p && !pcts.has(n.id)) pcts.set(n.id, p);
      }
    }
    // transactions
    if (d.doc_type === "bank_statement") {
      for (const row of table(d, "transactions")?.rows ?? []) {
        const label = row.annotations.counterparty?.label?.trim();
        if (!label) continue;
        const credit = val(row.cells.credit);
        const debit = val(row.cells.debit);
        if (credit) {
          const n = node(label, "counterparty", "payer");
          if (n && n.id !== APP) edge("paid", n.id, APP, d.id, row.index, credit);
        }
        if (debit) {
          const n = node(label, "counterparty", "payee");
          if (n && n.id !== APP) edge("paid_out", APP, n.id, d.id, row.index, debit);
        }
      }
    }
    // invoices
    if (d.doc_type === "invoice") {
      const sup = field(d, ["supplier_name"]);
      const buy = field(d, ["buyer_name"]);
      const s = sup ? node(sup, "counterparty", "supplier") : null;
      const b = buy ? node(buy, "counterparty", "buyer") : null;
      if (s && b && s.id !== b.id) edge("invoiced", s.id, b.id, d.id, undefined, field(d, ["total"]) || undefined);
    }
  }

  // labels and amounts
  for (const [id, e] of edges) {
    const acc = sums.get(id);
    if (acc?.hasAmount) e.amountCents = acc.amount;
    if (e.kind === "paid" || e.kind === "paid_out" || e.kind === "invoiced") e.count = e.kind === "invoiced" ? acc?.count ?? 0 : e.count;
    if (e.kind === "owns") e.label = chartPct.get(e.source) ?? regPct.get(e.source) ?? "owner";
    else if (e.kind === "director") e.label = "director";
    else e.label = e.amountCents !== undefined ? fmtCents(e.amountCents) : "";
  }

  // caps: 8 counterparties per side (by amount), 40 nodes total
  let truncated = 0;
  const amountOf = (n: GNode, kind: EdgeKind) => {
    let t = 0n;
    for (const e of edges.values()) if (e.kind === kind && (e.source === n.id || e.target === n.id)) t += e.amountCents ?? 0n;
    return t;
  };
  for (const [side, kind, moreId, role] of [
    ["in", "paid", "n:+more-in", "payer"],
    ["out", "paid_out", "n:+more-out", "payee"],
  ] as const) {
    void side;
    const cps = [...nodes.values()].filter((n) => n.kind === "counterparty" && n.roles.includes(role as NodeRole));
    // a counterparty that is also on the other side stays drawn on its first side only for the cap
    cps.sort((a, b) => (amountOf(b, kind) > amountOf(a, kind) ? 1 : amountOf(b, kind) < amountOf(a, kind) ? -1 : a.label.localeCompare(b.label)));
    const dropped = cps.slice(MAX_PER_SIDE).filter((n) => !edgesOfKindTouch(edges, n.id, kind === "paid" ? "paid_out" : "paid"));
    if (!dropped.length) continue;
    const more: GNode = { id: moreId, label: `+${dropped.length} more`, kind: "counterparty", roles: [role as NodeRole], flagged: false };
    nodes.set(moreId, more);
    const moreEdgeId = kind === "paid" ? `paid:${moreId}>${APP}` : `paid_out:${APP}>${moreId}`;
    const merged: GEdge = {
      id: moreEdgeId, kind, source: kind === "paid" ? moreId : APP, target: kind === "paid" ? APP : moreId,
      label: "", amountCents: 0n, count: 0, docIds: [], rows: [], flagged: false,
    };
    for (const n of dropped) {
      for (const [id, e] of [...edges]) {
        if (e.kind !== kind || (e.source !== n.id && e.target !== n.id)) continue;
        merged.amountCents = (merged.amountCents ?? 0n) + (e.amountCents ?? 0n);
        merged.count = (merged.count ?? 0) + (e.count ?? 0);
        for (const d of e.docIds) if (!merged.docIds.includes(d)) merged.docIds.push(d);
        merged.rows.push(...e.rows);
        edges.delete(id);
      }
      for (const [id, e] of [...edges]) if (e.source === n.id || e.target === n.id) edges.delete(id);
      nodes.delete(n.id);
    }
    merged.label = fmtCents(merged.amountCents ?? 0n);
    edges.set(moreEdgeId, merged);
    truncated += dropped.length;
  }
  if (nodes.size > MAX_NODES) {
    const keep = new Set<string>([APP, "n:+more-in", "n:+more-out"]);
    for (const n of nodes.values()) if (keep.size < MAX_NODES) keep.add(n.id);
    for (const id of [...nodes.keys()]) {
      if (keep.has(id)) continue;
      nodes.delete(id);
      truncated += 1;
    }
    for (const [id, e] of [...edges]) if (!nodes.has(e.source) || !nodes.has(e.target)) edges.delete(id);
  }

  // flags
  const txFlags = c.flags.filter((f) => f.table === "transactions" && typeof f.row === "number");
  for (const e of edges.values()) {
    e.flagged = e.rows.some((r) => txFlags.some((f) => f.row === r.row && f.document_ids.includes(r.docId)));
  }
  const heur = c.flags.filter((f) => HEURISTIC_RULES.has(f.rule));
  for (const n of nodes.values()) {
    const name = n.label.toLowerCase();
    n.flagged =
      [...edges.values()].some((e) => e.flagged && (e.source === n.id || e.target === n.id)) ||
      (n.id !== "n:+more-in" && n.id !== "n:+more-out" && heur.some((f) => textHas(f, name)));
  }

  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated };
}

function edgesOfKindTouch(edges: Map<string, GEdge>, nodeId: string, kind: EdgeKind): boolean {
  for (const e of edges.values()) if (e.kind === kind && (e.source === nodeId || e.target === nodeId)) return true;
  return false;
}

function textHas(f: Flag, lowerName: string): boolean {
  return lowerName.length > 0 && (f.evidence.toLowerCase().includes(lowerName) || f.explanation.toLowerCase().includes(lowerName));
}

/** The graph is shown for kyc and aml cases, and whenever one of the pass-through style flags exists. */
export function graphApplies(c: Pick<CaseDetail, "use_case" | "flags">): boolean {
  return c.use_case === "kyc" || c.use_case === "aml" || c.flags.some((f) => ["bs_pass_through", "x_remitter_vs_buyer", "x_sof_support"].includes(f.rule));
}

/** Both a payer and a payee. */
export const isInAndOut = (n: GNode): boolean => n.roles.includes("payer") && n.roles.includes("payee");
