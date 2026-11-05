"use client";

import clsx from "clsx";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePrefersReducedMotion } from "@/lib/useMedia";

export type FlaggedPage = { fileId: string; page: number; count: number };

/**
 * The pages that carry a flag, as chips in ONE row. A case can have dozens of flagged pages; a wrapping
 * row would grow with them and push the page image off the screen, so the row keeps its height and
 * scrolls sideways instead. Arrow buttons appear only on the side that has more, and the chip of the page
 * that is open is kept in view. (The page picker above the strip still lists every page.)
 */
export function FlaggedPagesStrip({
  pages,
  current,
  nameOf,
  onNavigate,
}: {
  pages: FlaggedPage[];
  current: { fileId: string; page: number };
  nameOf: (fileId: string, page: number) => string;
  onNavigate: (to: { fileId: string; page: number }) => void;
}) {
  const reduced = usePrefersReducedMotion();
  const scroller = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState({ before: false, after: false });

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const before = el.scrollLeft > 1;
    const after = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setMore((m) => (m.before === before && m.after === after ? m : { before, after }));
  }, []);

  useLayoutEffect(() => {
    measure();
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, pages.length]);

  // keep the open page's chip inside the visible part of the row (only this row moves, never the page)
  useEffect(() => {
    const el = scroller.current;
    const chip = el?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!el || !chip) return;
    const pad = 8;
    let left: number | null = null;
    if (chip.offsetLeft < el.scrollLeft) left = Math.max(0, chip.offsetLeft - pad);
    else if (chip.offsetLeft + chip.offsetWidth > el.scrollLeft + el.clientWidth) left = chip.offsetLeft + chip.offsetWidth - el.clientWidth + pad;
    if (left === null) return;
    if (typeof el.scrollTo === "function") el.scrollTo({ left, behavior: reduced ? "auto" : "smooth" });
    else el.scrollLeft = left;
  }, [current.fileId, current.page, pages.length, reduced]);

  const slide = (dir: -1 | 1) => {
    const el = scroller.current;
    if (!el) return;
    const left = el.scrollLeft + dir * Math.max(120, el.clientWidth * 0.7);
    if (typeof el.scrollTo === "function") el.scrollTo({ left, behavior: reduced ? "auto" : "smooth" });
    else el.scrollLeft = left;
  };

  if (pages.length === 0) return null;
  // laid over the ends of the row, so showing or hiding one never changes how wide the row is
  const arrow = "absolute top-1/2 z-10 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md border border-rule-strong bg-surface text-ink-2 shadow-paper hover:border-brand-2 hover:bg-brand-tint pointer-coarse:h-11 pointer-coarse:w-11";
  return (
    <div className="flex flex-col gap-1 border-t border-rule bg-paper/60 px-3 py-2 sm:flex-row sm:items-center sm:gap-2">
      <span className="label-caps shrink-0">
        Pages with flags <span className="tabular font-normal">({pages.length})</span>
      </span>
      <div className="relative min-w-0 flex-1">
        {more.before ? (
          <button type="button" className={clsx(arrow, "left-0")} aria-label="Show earlier flagged pages" onClick={() => slide(-1)}>
            <ChevronLeft aria-hidden className="h-4 w-4" />
          </button>
        ) : null}
        <div
          ref={scroller}
          role="group"
          aria-label="Pages with flags"
          onScroll={measure}
          className="flex flex-nowrap items-center gap-1.5 overflow-x-auto overscroll-x-contain py-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {pages.map((p) => {
            const on = p.fileId === current.fileId && p.page === current.page;
            const full = nameOf(p.fileId, p.page);
            return (
              <button
                key={`${p.fileId}:${p.page}`}
                type="button"
                aria-pressed={on}
                title={full}
                onClick={() => onNavigate({ fileId: p.fileId, page: p.page })}
                className={clsx(
                  "inline-flex min-h-8 max-w-[16rem] shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-xs font-semibold pointer-coarse:min-h-11",
                  on ? "border-brand bg-brand text-on-brand" : "border-rule-strong bg-surface text-ink-2 hover:border-brand-2 hover:bg-brand-tint",
                )}
              >
                <span className="truncate">{full}</span>
                <span className={clsx("tabular rounded px-1.5 py-px", on ? "bg-on-brand/20" : "bg-paper-2")}>{p.count}</span>
              </button>
            );
          })}
        </div>
        {more.after ? (
          <button type="button" className={clsx(arrow, "right-0")} aria-label="Show later flagged pages" onClick={() => slide(1)}>
            <ChevronRight aria-hidden className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    </div>
  );
}
