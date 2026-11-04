"use client";

import useSWR from "swr";
import { getMe, type Me } from "./api";
import { USE_MOCK } from "./constants";

/** Who is signed in and whether they may see everybody's spending. Any failure just means "not an administrator". */
export function useMe(enabled = true): Me | null {
  const { data } = useSWR<Me>(USE_MOCK || !enabled ? null : "me", getMe, { shouldRetryOnError: false, revalidateOnFocus: false });
  return data ?? null;
}
