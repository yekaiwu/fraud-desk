"use client";

import { useEffect, useRef } from "react";
import useSWR, { useSWRConfig } from "swr";
import { getNotificationSummary } from "./api";
import type { NotificationSummary } from "./monitoring";

export const SUMMARY_KEY = "notification-summary";
/** About once a minute, plus on navigation and when the tab regains focus. */
export const SUMMARY_REFRESH_MS = 60_000;

/**
 * The number on the Notifications tab: open alerts nobody acknowledged plus unread notifications (an open alert and
 * its own "raised" notification count once). A failed or slow answer just shows no badge; the page itself says why.
 */
export function useNotificationBadge(pathname: string): number {
  const { data, mutate } = useSWR<NotificationSummary>(SUMMARY_KEY, getNotificationSummary, {
    refreshInterval: SUMMARY_REFRESH_MS,
    revalidateOnFocus: true,
    shouldRetryOnError: false,
  });
  const first = useRef(true);
  useEffect(() => {
    // SWR fetches on mount; only a later change of page asks again.
    if (first.current) {
      first.current = false;
      return;
    }
    void mutate();
  }, [pathname, mutate]);
  return data && Number.isFinite(data.badge) ? Math.max(0, data.badge) : 0;
}

/** For the page: refresh the badge right after something was read or acknowledged. */
export function useRefreshBadge(): () => void {
  const { mutate } = useSWRConfig();
  return () => void mutate(SUMMARY_KEY);
}
