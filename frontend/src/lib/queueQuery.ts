import { PAGE_SIZE } from "./constants";
import type { Band, CaseStatus, Lane, QueueQuery, UseCase } from "./types";

/** Statuses shown when the URL carries no `status` (api.md section 4). Not serialised when untouched. */
export const DEFAULT_QUEUE_STATUS: CaseStatus[] = ["processing", "open", "awaiting_documents"];

const SORTS = ["priority", "score", "age", "coverage", "created"] as const;
const ORDERS = ["asc", "desc"] as const;

function csv<T extends string>(p: URLSearchParams, key: string, allowed: readonly T[]): T[] | undefined {
  const raw = p.get(key);
  if (!raw) return undefined;
  const out = raw.split(",").filter((x): x is T => (allowed as readonly string[]).includes(x));
  return out.length ? out : undefined;
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x));
}

export function queueQueryToSearchParams(q: QueueQuery): URLSearchParams {
  const p = new URLSearchParams();
  if (q.use_case?.length) p.set("use_case", q.use_case.join(","));
  if (q.status?.length && !sameSet(q.status, DEFAULT_QUEUE_STATUS)) p.set("status", q.status.join(","));
  if (q.lane?.length) p.set("lane", q.lane.join(","));
  if (q.band?.length) p.set("band", q.band.join(","));
  if (q.min_score !== undefined) p.set("min_score", String(q.min_score));
  if (q.q) p.set("q", q.q);
  if (q.partial !== undefined) p.set("partial", String(q.partial));
  if (q.sort && q.sort !== "priority") p.set("sort", q.sort);
  if (q.order) p.set("order", q.order);
  if (q.page !== undefined && q.page > 1) p.set("page", String(q.page));
  if (q.page_size !== undefined && q.page_size !== PAGE_SIZE) p.set("page_size", String(q.page_size));
  return p;
}

export function searchParamsToQueueQuery(p: URLSearchParams): QueueQuery {
  const q: QueueQuery = {};
  const use_case = csv<UseCase>(p, "use_case", ["kyc", "aml", "loan"]);
  if (use_case) q.use_case = use_case;
  const status = csv<CaseStatus>(p, "status", ["processing", "open", "awaiting_documents", "closed", "error"]);
  if (status) q.status = status;
  const lane = csv<Lane>(p, "lane", ["edd", "aml", "loan", "fast"]);
  if (lane) q.lane = lane;
  const band = csv<Band>(p, "band", ["low", "medium", "high"]);
  if (band) q.band = band;
  const ms = p.get("min_score");
  if (ms !== null && /^\d+$/.test(ms)) q.min_score = Math.min(100, Number(ms));
  const text = p.get("q");
  if (text) q.q = text;
  const partial = p.get("partial");
  if (partial === "true") q.partial = true;
  else if (partial === "false") q.partial = false;
  const sort = p.get("sort");
  if (sort && (SORTS as readonly string[]).includes(sort)) q.sort = sort as QueueQuery["sort"];
  const order = p.get("order");
  if (order && (ORDERS as readonly string[]).includes(order)) q.order = order as QueueQuery["order"];
  const page = p.get("page");
  if (page && /^\d+$/.test(page) && Number(page) >= 1) q.page = Number(page);
  const ps = p.get("page_size");
  if (ps && /^\d+$/.test(ps) && Number(ps) >= 1) q.page_size = Math.min(100, Number(ps));
  return q;
}

/** Page size of the "scope" request the worklist uses for lane and band counts, the summary sentence and "Open next case". */
export const SCOPE_PAGE_SIZE = 100;

/**
 * The query behind the counts: the same cases the officer is looking at (use case, status, search) but not
 * narrowed by lane or band, always the first page in priority order, so each tab can show how many cases
 * it would hold and "next case" is the highest ranked one.
 */
export function scopeQuery(q: QueueQuery): QueueQuery {
  const scope: QueueQuery = { page_size: SCOPE_PAGE_SIZE };
  if (q.use_case?.length) scope.use_case = q.use_case;
  if (q.status?.length) scope.status = q.status;
  if (q.q) scope.q = q.q;
  if (q.min_score !== undefined) scope.min_score = q.min_score;
  if (q.partial !== undefined) scope.partial = q.partial;
  return scope;
}
