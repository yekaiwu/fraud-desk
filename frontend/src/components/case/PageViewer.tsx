"use client";

import clsx from "clsx";
import { ChevronLeft, ChevronRight, ImageOff } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { pageUrl } from "@/lib/api";
import { makeDocNamer } from "@/lib/docNames";
import { flaggedOnPage, type NumberedFlag } from "@/lib/flags";
import { severityLabel } from "@/lib/labels";
import { pickImageWidth } from "@/lib/regions";
import type { Document, FileRef, Severity } from "@/lib/types";
import { usePrefersReducedMotion } from "@/lib/useMedia";
import { Button, EmptyState, Notice, inputClasses } from "../ui";
import { RegionOverlay, SEVERITY_COLOR } from "./RegionOverlay";

type Zoom = "fit" | 150 | 200;
const ZOOMS: { v: Zoom; l: string }[] = [
  { v: "fit", l: "Fit" },
  { v: 150, l: "150%" },
  { v: 200, l: "200%" },
];
const EMPTY_DOCS: Document[] = [];

/** Outline style per severity, as on the page itself, so the key and the boxes agree. */
const KEY_STYLE: Record<Severity, { style: "solid" | "dashed" | "dotted" | "double"; width: number }> = {
  high: { style: "solid", width: 2 },
  medium: { style: "dashed", width: 2 },
  low: { style: "dotted", width: 3 },
  info: { style: "double", width: 4 },
};

export function PageViewer({
  caseId,
  files,
  flags,
  selectedFlagId,
  onSelectFlag,
  current,
  onNavigate,
  documents = EMPTY_DOCS,
}: {
  caseId: string;
  files: FileRef[];
  flags: NumberedFlag[];
  selectedFlagId: string | null;
  onSelectFlag: (flagId: string) => void;
  current: { fileId: string; page: number };
  onNavigate: (to: { fileId: string; page: number }) => void;
  /** Optional: the case's documents, so pages read as "Bank statement, Nov 2025, page 1" instead of file names. */
  documents?: Document[];
}) {
  const reduced = usePrefersReducedMotion();
  const names = useMemo(() => makeDocNamer(documents, files), [documents, files]);
  const file = files.find((f) => f.id === current.fileId) ?? files[0];
  const pageInfo = file?.pages.find((p) => p.page === current.page);
  const [zoom, setZoom] = useState<Zoom>("fit");
  const [showChips, setShowChips] = useState(true);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [nonce, setNonce] = useState(0);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [cw, setCw] = useState(700);
  const scroller = useRef<HTMLDivElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => el.clientWidth > 0 && setCw(el.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [file?.id]);

  const flagFlags = useMemo(() => flags.map((x) => x.flag), [flags]);
  const withFlags = useMemo(() => {
    const seen = new Map<string, { fileId: string; page: number; count: number }>();
    for (const { flag } of flags) {
      const r = flag.region;
      if (!r) continue;
      const k = `${r.file_id}:${r.page}`;
      seen.set(k, { fileId: r.file_id, page: r.page, count: flaggedOnPage(flagFlags, r.file_id, r.page) });
    }
    return [...seen.values()];
  }, [flags, flagFlags]);

  const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  const zoomFactor = zoom === "fit" ? 1 : zoom / 100;
  const width = pickImageWidth(cw * zoomFactor, dpr);
  const src = file ? pageUrl(caseId, file.id, current.page, width) : "";

  const imgKey = `${src}#${nonce}`;
  const [prevKey, setPrevKey] = useState(imgKey);
  if (imgKey !== prevKey) {
    // a different image starts out loading
    setPrevKey(imgKey);
    setState("loading");
    setNatural(null);
  }

  // bring the selected flag's box into view (no smooth scrolling when reduced motion is requested)
  useEffect(() => {
    if (!selectedFlagId || !wrapper.current) return;
    const el = Array.from(wrapper.current.querySelectorAll<HTMLElement>("[data-flag-id]")).find((e) => e.dataset.flagId === selectedFlagId);
    el?.scrollIntoView?.({ block: "center", behavior: reduced ? "auto" : "smooth" });
  }, [selectedFlagId, current.fileId, current.page, reduced]);

  if (!file) {
    return <EmptyState compact icon={<ImageOff aria-hidden className="h-6 w-6" />} title="No pages to show" text="This case has no page images to display." />;
  }
  const aspect = natural ? natural.w / natural.h : pageInfo ? pageInfo.width_pt / pageInfo.height_pt : 595.28 / 841.89;
  const pageCount = file.pages.length;
  const onPage = flags.filter(({ flag }) => flag.region?.file_id === file.id && flag.region.page === current.page);
  const here = onPage.length;
  const severitiesHere = (["high", "medium", "low", "info"] as const).filter((s) => onPage.some((x) => x.flag.severity === s));
  const hasExtras = flags.some(({ flag }) => flag.extra_regions.some((x) => x.file_id === file.id && x.page === current.page));
  const go = (page: number) => onNavigate({ fileId: file.id, page: Math.min(pageCount, Math.max(1, page)) });
  const pageName = names.page(file.id, current.page);

  return (
    <section aria-label="Page viewer" className="overflow-hidden rounded-lg border border-rule bg-surface shadow-paper">
      <div className="space-y-2.5 p-3">
        <div>
          <label htmlFor="viewer-page" className="sr-only">
            Document and page
          </label>
          <select
            id="viewer-page"
            value={`${file.id}:${current.page}`}
            onChange={(e) => {
              const [fileId, page] = e.target.value.split(":");
              onNavigate({ fileId, page: Number(page) });
            }}
            title={`${file.name}, page ${current.page}`}
            className={clsx(inputClasses, "truncate pr-8 font-medium")}
          >
            {files.flatMap((f) =>
              f.pages.map((p) => {
                const n = flaggedOnPage(flagFlags, f.id, p.page);
                return (
                  <option key={`${f.id}:${p.page}`} value={`${f.id}:${p.page}`}>
                    {names.page(f.id, p.page)}
                    {n > 0 ? ` (${n} flagged)` : ""}
                  </option>
                );
              }),
            )}
          </select>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <div className="flex items-center gap-1">
            <Button size="sm" variant="secondary" className="min-w-9! px-2! pointer-coarse:min-w-11!" onClick={() => go(current.page - 1)} disabled={current.page <= 1} aria-label="Previous page" icon={<ChevronLeft aria-hidden className="h-4 w-4" />} />
            <span className="tabular min-w-[6.5rem] text-center text-sm font-medium text-ink" aria-live="polite">
              Page {current.page} of {pageCount}
            </span>
            <Button size="sm" variant="secondary" className="min-w-9! px-2! pointer-coarse:min-w-11!" onClick={() => go(current.page + 1)} disabled={current.page >= pageCount} aria-label="Next page" icon={<ChevronRight aria-hidden className="h-4 w-4" />} />
          </div>
          <button
            type="button"
            aria-pressed={showChips}
            onClick={() => setShowChips((v) => !v)}
            title="Show or hide the flag numbers drawn beside each figure"
            className={clsx("min-h-9 rounded-md border border-rule-strong px-2.5 text-sm font-semibold pointer-coarse:min-h-11", showChips ? "bg-brand-tint text-brand" : "bg-surface text-ink-2 hover:bg-brand-tint")}
          >
            Numbers
          </button>
          <div role="group" aria-label="Zoom" className="flex overflow-hidden rounded-md border border-rule-strong bg-surface">
            {ZOOMS.map((z) => (
              <button
                key={String(z.v)}
                type="button"
                aria-pressed={zoom === z.v}
                onClick={() => setZoom(z.v)}
                className={clsx("min-h-9 min-w-11 px-2.5 text-sm font-semibold pointer-coarse:min-h-11", zoom === z.v ? "bg-brand text-on-brand" : "text-brand hover:bg-brand-tint")}
              >
                {z.l}
              </button>
            ))}
          </div>
        </div>
      </div>

      {withFlags.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-rule bg-paper/60 px-3 py-2">
          <span className="label-caps mr-1">Pages with flags</span>
          {withFlags.map((p) => {
            const on = p.fileId === file.id && p.page === current.page;
            const full = names.page(p.fileId, p.page);
            return (
              <button
                key={`${p.fileId}:${p.page}`}
                type="button"
                aria-pressed={on}
                title={full}
                onClick={() => onNavigate({ fileId: p.fileId, page: p.page })}
                className={clsx(
                  "inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-md border px-2.5 text-xs font-semibold pointer-coarse:min-h-11",
                  on ? "border-brand bg-brand text-on-brand" : "border-rule-strong bg-surface text-ink-2 hover:border-brand-2 hover:bg-brand-tint",
                )}
              >
                <span className="truncate">{full}</span>
                <span className={clsx("tabular rounded px-1.5 py-px", on ? "bg-on-brand/20" : "bg-paper-2")}>{p.count}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      {pageInfo?.error ? (
        <div className="border-t border-rule p-3">
          <Notice variant="warning" title="This page could not be read" role="status">
            {pageInfo.error}
          </Notice>
        </div>
      ) : null}

      <div ref={scroller} role="region" aria-label="Page image, scrolls when zoomed" tabIndex={0} className="max-h-[80dvh] overflow-auto border-t border-rule bg-paper-2 p-3 shadow-[inset_0_3px_10px_rgb(20_32_27/0.08)] sm:p-5">
        {state === "error" ? (
          <div role="alert" className="mx-auto flex max-w-sm flex-col items-center gap-2 rounded-lg border border-rule bg-surface p-8 text-center shadow-paper">
            <ImageOff aria-hidden className="h-8 w-8 text-muted" />
            <p className="font-semibold text-ink">Page image unavailable</p>
            <p className="text-sm text-ink-2">The picture of this page did not load. The findings are unaffected.</p>
            <Button size="sm" variant="secondary" onClick={() => setNonce((n) => n + 1)}>
              Retry
            </Button>
          </div>
        ) : (
          <div
            ref={wrapper}
            className="relative bg-surface shadow-lift ring-1 ring-ink/10"
            style={{ width: zoom === "fit" ? "100%" : `${zoom}%`, aspectRatio: `${aspect}` }}
          >
            {state === "loading" ? <div aria-hidden className="skeleton absolute inset-0 rounded-none" /> : null}
            {/* eslint-disable-next-line @next/next/no-img-element -- page renders come from the API and are sized by the server */}
            <img
              key={`${src}#${nonce}`}
              src={nonce ? `${src}${src.includes("?") ? "&" : "?"}r=${nonce}` : src}
              alt={`Page ${current.page} of ${file.name}, ${here} flagged ${here === 1 ? "region" : "regions"}`}
              title={pageName}
              draggable={false}
              loading="lazy"
              className="block h-full w-full select-none"
              onLoad={(e) => {
                const img = e.currentTarget;
                if (img.naturalWidth > 0 && img.naturalHeight > 0) setNatural({ w: img.naturalWidth, h: img.naturalHeight });
                setState("ok");
              }}
              onError={() => setState("error")}
            />
            <RegionOverlay flags={flags} current={{ fileId: file.id, page: current.page }} selectedFlagId={selectedFlagId} onSelect={onSelectFlag} showChips={showChips} />
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-rule bg-paper/60 px-3 py-2 text-xs text-ink-2">
        <span className="font-medium text-ink">{pageName}</span>
        {severitiesHere.length > 0 ? (
          <>
            <span>The numbers match the findings list.</span>
            <ul className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Key to the outlines">
              {severitiesHere.map((s) => (
                <li key={s} className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="inline-block h-3 w-5 rounded-[2px]" style={{ border: `${KEY_STYLE[s].width}px ${KEY_STYLE[s].style} ${SEVERITY_COLOR[s]}`, background: `color-mix(in srgb, ${SEVERITY_COLOR[s]} 14%, transparent)` }} />
                  {severityLabel(s)}
                </li>
              ))}
              {hasExtras ? (
                <li className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="inline-block h-3 w-5 rounded-[2px] border border-dashed border-ink-2 bg-ink-2/10" />
                  Related evidence
                </li>
              ) : null}
            </ul>
          </>
        ) : (
          <span>Nothing is marked on this page.</span>
        )}
      </div>
    </section>
  );
}
