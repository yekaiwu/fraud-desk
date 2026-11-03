"use client";

import type { CSSProperties } from "react";
import { SEVERITY_Z, type NumberedFlag } from "@/lib/flags";
import { locusOf, locusPhrase, placesOf, touchesPage, type DocSpan, type Locus } from "@/lib/flagLocus";
import { paddedRegionStyle } from "@/lib/regions";
import type { Severity } from "@/lib/types";

export const SEVERITY_COLOR: Record<Severity, string> = {
  high: "var(--sev-high)",
  medium: "var(--sev-medium)",
  low: "var(--sev-low)",
  info: "var(--sev-info)",
};

/** Chip height in CSS px (1.25rem), plus the gap kept between two chips in the same margin. */
const CHIP_PX = 20;
const CHIP_GAP = 3;

export type Chip = { id: string; side: "left" | "right"; centre: number };

/**
 * Where each numbered chip goes. Chips live in the page margin (left or right, whichever is nearer the box), never
 * on the figure and never beside it where another figure sits, so no number can hide an amount. When the page height
 * is known, chips in one margin are pushed apart so two neighbouring rows do not stack on top of each other.
 * Returns the CSS `top` of each chip by flag id.
 */
export function placeChips(chips: Chip[], pageHeightPx: number | null): Map<string, string> {
  const out = new Map<string, string>();
  for (const side of ["left", "right"] as const) {
    const col = chips.filter((c) => c.side === side).sort((a, b) => a.centre - b.centre);
    if (pageHeightPx === null) {
      for (const c of col) out.set(c.id, `${(c.centre * 100).toFixed(3)}%`);
      continue;
    }
    let floor = -Infinity;
    for (const c of col) {
      const y = Math.min(Math.max(c.centre * pageHeightPx, floor, CHIP_PX / 2), Math.max(CHIP_PX / 2, pageHeightPx - CHIP_PX / 2));
      out.set(c.id, `${y.toFixed(1)}px`);
      floor = y + CHIP_PX + CHIP_GAP;
    }
  }
  return out;
}

/**
 * Boxes are absolutely positioned children of the image wrapper, in percent of it (frontend.md section 6),
 * so they track every display size and zoom without measuring. DOM order is flag-number order (tab order);
 * stacking is by severity with the selected flag on top. A flag that is not tied to one spot (a whole page, or
 * the whole file) gets a frame round the page when it is selected and a small numbered pill in the top corner
 * otherwise, so it can be found and chosen from the page itself.
 */
export function RegionOverlay({
  flags,
  current,
  selectedFlagId,
  onSelect,
  showChips = true,
  documents = [],
  pageHeightPx = null,
}: {
  showChips?: boolean;
  flags: NumberedFlag[];
  current: { fileId: string; page: number };
  selectedFlagId: string | null;
  onSelect: (flagId: string) => void;
  documents?: DocSpan[];
  pageHeightPx?: number | null;
}) {
  const chips: Chip[] = [];
  const wide: { nf: NumberedFlag; locus: Locus }[] = [];
  const boxes = flags.map((nf) => {
    const { flag } = nf;
    const locus = locusOf(flag, documents);
    const here = locus.kind === "box" && locus.region.file_id === current.fileId && locus.region.page === current.page ? locus.region : null;
    const style = here ? paddedRegionStyle(here) : null;
    const side: "left" | "right" = here && here.x + here.w / 2 < 0.5 ? "left" : "right";
    if (here && style && showChips) chips.push({ id: flag.id, side, centre: here.y + here.h / 2 });
    if ((locus.kind === "page" || locus.kind === "document") && touchesPage(flag, documents, current.fileId, current.page)) wide.push({ nf, locus });
    return { nf, here, style, side };
  });
  // Related evidence of the selected flag: dashed boxes with a letter in the margin. Their names are listed in the
  // caption; no text is ever drawn on the page, because a label under a figure hides the figure next to it.
  const selectedNf = flags.find((x) => x.flag.id === selectedFlagId) ?? null;
  const extras = selectedNf
    ? placesOf(selectedNf.flag)
        .filter((pl) => pl.letter !== null && pl.region.file_id === current.fileId && pl.region.page === current.page)
        .map((pl) => ({ x: pl.region, letter: pl.letter as string, label: pl.label, style: paddedRegionStyle(pl.region) }))
        .filter((e) => e.style)
    : [];
  if (showChips) for (const e of extras) chips.push({ id: `extra:${e.letter}`, side: e.x.x + e.x.w / 2 < 0.5 ? "left" : "right", centre: e.x.y + e.x.h / 2 });
  const chipTop = placeChips(chips, pageHeightPx);
  const framed = wide.find((w) => w.nf.flag.id === selectedFlagId) ?? null;

  return (
    <>
      {framed ? (
        <span
          className="region-frame"
          data-flag-id={framed.nf.flag.id}
          data-kind={framed.locus.kind}
          data-testid="region-frame"
          style={{ "--region-color": SEVERITY_COLOR[framed.nf.flag.severity], zIndex: 25 } as CSSProperties}
        >
          <span className="region-frame-tag">
            <span className="num">{framed.nf.n}</span>
            {locusPhrase(framed.locus)}
          </span>
        </span>
      ) : null}
      {wide.length > 0 ? (
        <span className="region-pills" role="group" aria-label="Flags that apply to this whole page or file">
          {wide.map(({ nf }) => (
            <button
              key={nf.flag.id}
              type="button"
              className="region-pill num"
              data-flag-id={nf.flag.id}
              aria-pressed={nf.flag.id === selectedFlagId}
              aria-label={`Flag ${nf.n}, ${nf.flag.rule}, ${nf.flag.severity} severity, whole ${nf.flag.region ? "page" : "file"}`}
              onClick={() => onSelect(nf.flag.id)}
              style={{ "--region-color": SEVERITY_COLOR[nf.flag.severity] } as CSSProperties}
            >
              {nf.n}
            </button>
          ))}
        </span>
      ) : null}
      {extras.map(({ x, letter, label, style }) => (
        <span key={`extra:${letter}`} className="contents">
          <span
            className="region-extra"
            style={{ ...style, zIndex: 2, "--region-color": SEVERITY_COLOR[selectedNf!.flag.severity] } as CSSProperties}
            title={label}
            data-place={letter}
            data-extra-for={selectedFlagId ?? undefined}
          />
          {showChips && chipTop.has(`extra:${letter}`) ? (
            <span
              className="region-margin-chip region-margin-chip-extra num"
              aria-hidden
              title={label}
              data-extra-letter={letter}
              style={{ "--region-color": SEVERITY_COLOR[selectedNf!.flag.severity], top: chipTop.get(`extra:${letter}`), [x.x + x.w / 2 < 0.5 ? "left" : "right"]: "0.6%", zIndex: 34 } as CSSProperties}
            >
              {letter}
            </span>
          ) : null}
        </span>
      ))}
      {boxes.map(({ nf, here, style, side }) => {
        const { flag, n } = nf;
        const selected = flag.id === selectedFlagId;
        const color = { "--region-color": SEVERITY_COLOR[flag.severity] } as CSSProperties;
        return (
          <span key={flag.id} className="contents">
            {style && here ? (
              <button
                type="button"
                className="region-box"
                data-flag-id={flag.id}
                data-place="main"
                data-severity={flag.severity}
                aria-pressed={selected}
                aria-label={`Flag ${n}, ${flag.rule}, ${flag.severity} severity, page ${here.page}`}
                onClick={() => onSelect(flag.id)}
                style={{ ...style, zIndex: selected ? 30 : SEVERITY_Z[flag.severity], ...color } as CSSProperties}
              />
            ) : null}
            {here && style && showChips && chipTop.has(flag.id) ? (
              <span
                className="region-margin-chip num"
                aria-hidden
                data-selected={selected ? "true" : undefined}
                data-chip-for={flag.id}
                style={{ ...color, top: chipTop.get(flag.id), [side]: "0.6%", zIndex: selected ? 40 : 35 } as CSSProperties}
              >
                {n}
              </span>
            ) : null}
          </span>
        );
      })}
    </>
  );
}
