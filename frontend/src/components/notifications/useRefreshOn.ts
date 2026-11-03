"use client";

import { useEffect, useRef } from "react";

/** Run `refresh` whenever `tick` changes (not on mount): one section asks the others to reload after an action. */
export function useRefreshOn(tick: number, refresh: () => unknown): void {
  const last = useRef(tick);
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  });
  useEffect(() => {
    if (last.current === tick) return;
    last.current = tick;
    void latest.current();
  }, [tick]);
}
