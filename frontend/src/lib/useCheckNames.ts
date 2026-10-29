"use client";

import { checkName, effectiveName } from "./checkCatalogue";
import { useRuleCatalogue } from "./useExplain";

/**
 * Name of a check as the officer set it in the catalogue, else the derived plain name (or the caller's own `fallback`).
 * Reads the catalogue cache the Check catalogue page shares; until it has loaded, or when it cannot be reached, the
 * derived name is used, so a list never shows a blank.
 */
export function useCheckName(fallback: (id: string) => string = checkName): (rule: string) => string {
  const { byId } = useRuleCatalogue();
  return (rule: string) => effectiveName(rule, byId.get(rule)?.override, fallback);
}

/** "What it checks" for a check: the officer's wording when set, else `fallback` (the description the caller already has). */
export function useCheckDescription(): (rule: string, fallback: string) => string {
  const { byId } = useRuleCatalogue();
  return (rule, fallback) => byId.get(rule)?.override?.description?.trim() || fallback;
}
