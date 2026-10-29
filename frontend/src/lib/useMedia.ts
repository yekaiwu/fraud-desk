"use client";

import { useSyncExternalStore } from "react";

/** matchMedia as an external store. Where matchMedia is missing (jsdom) the answer is `fallback`. */
export function useMedia(query: string, fallback = true): boolean {
  return useSyncExternalStore(
    (cb) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
      const m = window.matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : fallback),
    () => fallback,
  );
}

export const useIsMd = () => useMedia("(min-width: 768px)");
export const useIsLg = () => useMedia("(min-width: 1024px)");
export const useIsSm = () => useMedia("(min-width: 640px)");
export const usePrefersReducedMotion = () => useMedia("(prefers-reduced-motion: reduce)", false);
