"use client";

import useSWR from "swr";
import { getMe, type Me } from "./api";
import { USE_MOCK } from "./constants";
import { ApiErrorException } from "./errors";

/** Retry a failed /me a few times, then wait for the next focus or reconnect. A 401 or 403 is an answer, not a blip. */
export const ME_RETRY_COUNT = 3;
export const ME_RETRY_MS = 2000;

/**
 * Who is signed in and whether they may see everybody's spending. Until a /me has succeeded this
 * answers null, so the administrator and custom check screens never show before the server has said so.
 * A failed /me is retried and tried again when the tab regains focus or the network returns.
 */
export function useMe(enabled = true): Me | null {
  const { data } = useSWR<Me>(USE_MOCK || !enabled ? null : "me", getMe, {
    errorRetryCount: ME_RETRY_COUNT,
    errorRetryInterval: ME_RETRY_MS,
    revalidateOnFocus: true,
    revalidateOnReconnect: true,
    onErrorRetry: (error, _key, _config, revalidate, { retryCount }) => {
      if (error instanceof ApiErrorException && (error.status === 401 || error.status === 403)) return;
      // retryCount is 1 for the first retry: wait 2, 4, 8 s
      if (retryCount > ME_RETRY_COUNT) return;
      setTimeout(() => void revalidate({ retryCount }), ME_RETRY_MS * 2 ** (retryCount - 1));
    },
  });
  return data ?? null;
}
