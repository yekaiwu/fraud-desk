"use client";

import useSWR from "swr";
import { getQueue } from "@/lib/api";
import type { QueueItem } from "@/lib/types";
import type { CaseNav } from "./CaseHeader";

/**
 * Previous and next case in the worklist, derived from the first page of the default (priority) worklist.
 * Closed cases are skipped when picking a neighbour. Returns null when the case is not on that page: no navigation is better than a wrong one.
 */
export function neighbours(items: QueueItem[], id: string, total: number = items.length): CaseNav | null {
  const i = items.findIndex((x) => x.case_id === id);
  if (i < 0) return null;
  const open = (x: QueueItem) => x.status !== "closed";
  let prev: string | null = null;
  for (let k = i - 1; k >= 0; k--) if (open(items[k])) { prev = items[k].case_id; break; }
  let next: string | null = null;
  for (let k = i + 1; k < items.length; k++) if (open(items[k])) { next = items[k].case_id; break; }
  if (!prev && !next) return null;
  return { prev, next, position: i + 1, total };
}

export function useCaseNeighbours(id: string): CaseNav | null {
  const { data } = useSWR(["case-neighbours"], () => getQueue({}), { revalidateOnFocus: false, shouldRetryOnError: false });
  return data ? neighbours(data.items, id, data.total) : null;
}
