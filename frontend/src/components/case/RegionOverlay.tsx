"use client";

import type { CSSProperties } from "react";
import { SEVERITY_Z, type NumberedFlag } from "@/lib/flags";
import { regionStyle } from "@/lib/regions";
import type { Severity } from "@/lib/types";

export const SEVERITY_COLOR: Record<Severity, string> = {
  high: "var(--sev-high)",
  medium: "var(--sev-medium)",
  low: "var(--sev-low)",
  info: "var(--sev-info)",
};

/**
 * Boxes are absolutely positioned children of the image wrapper, in percent of it (frontend.md section 6),
 * so they track every display size and zoom without measuring. DOM order is flag-number order (tab order);
 * stacking is by severity with the selected flag on top.
 */
export function RegionOverlay({
  flags,
  current,
  selectedFlagId,
  onSelect,
  showChips = true,
}: {
  showChips?: boolean;
  flags: NumberedFlag[];
  current: { fileId: string; page: number };
  selectedFlagId: string | null;
  onSelect: (flagId: string) => void;
}) {
  return (
    <>
      {flags.map(({ flag, n }) => {
        const r = flag.region;
        const selected = flag.id === selectedFlagId;
        const extras = flag.extra_regions.filter((x) => x.file_id === current.fileId && x.page === current.page);
        const here = r && r.file_id === current.fileId && r.page === current.page ? r : null;
        const style = here ? regionStyle(here) : null;
        return (
          <span key={flag.id} className="contents">
            {style && here ? (
              <button
                type="button"
                className="region-box"
                data-flag-id={flag.id}
                data-severity={flag.severity}
                aria-pressed={selected}
                aria-label={`Flag ${n}, ${flag.rule}, ${flag.severity} severity, page ${here.page}`}
                onClick={() => onSelect(flag.id)}
                style={{ ...style, zIndex: selected ? 30 : SEVERITY_Z[flag.severity], "--region-color": SEVERITY_COLOR[flag.severity] } as CSSProperties}
              >
                {showChips ? (
                  // Beside the figure, never on it: left of the box, or right of it at the page edge.
                  <span className="region-chip num" aria-hidden style={here.x < 0.045 ? { left: "100%", marginLeft: 3 } : { right: "100%", marginRight: 3 }}>
                    {n}
                  </span>
                ) : null}
              </button>
            ) : null}
            {extras.map((x, i) => {
              const s = regionStyle(x);
              return s ? (
                <span
                  key={i}
                  className="region-extra"
                  style={{ ...s, zIndex: 1, outline: "1.5px dashed var(--ink-2)", background: "color-mix(in srgb, var(--ink-2) 9%, transparent)" }}
                  title={x.label ?? undefined}
                  data-extra-for={flag.id}
                >
                  {x.label ? <span className="absolute left-0 top-full mt-0.5 whitespace-nowrap rounded bg-surface/95 px-1 text-[11px] text-ink-2">{x.label}</span> : null}
                </span>
              ) : null;
            })}
          </span>
        );
      })}
    </>
  );
}
