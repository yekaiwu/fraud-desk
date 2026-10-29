"use client";

import useSWR from "swr";
import { getExplainMethod, getRules } from "./api";
import type { ExplainMethod, RuleInfo } from "./types";

/**
 * GET /explain/method, fetched the first time an explainer opens and kept for the whole session (the method does not
 * change while the app is open). `unavailable` is true when the backend predates the endpoint or cannot be reached;
 * explainers then still show the numbers they can work out from the case.
 */
export function useExplainMethod(): { method: ExplainMethod | null; loading: boolean; unavailable: boolean } {
  const { data, error, isLoading } = useSWR("explain/method", () => getExplainMethod(), {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    revalidateIfStale: false,
    shouldRetryOnError: false,
    dedupingInterval: 3_600_000,
  });
  return { method: data ?? null, loading: isLoading && !data, unavailable: !data && !!error };
}

/** The check catalogue (GET /rules), shared with the Check catalogue page's cache entry. */
export function useRuleCatalogue(): { rules: RuleInfo[] | null; byId: Map<string, RuleInfo>; loading: boolean } {
  const { data, isLoading } = useSWR("rules", () => getRules(), { revalidateOnFocus: false, shouldRetryOnError: false });
  const rules = data?.rules ?? null;
  return { rules, byId: new Map((rules ?? []).map((r) => [r.rule, r])), loading: isLoading && !data };
}
