/** Highlight overlay maths (frontend.md section 6). Regions are fractions of the page, origin top-left. */
import type { Region } from "./types";

const warned = new Set<string>();

function warnOnce(r: Region): void {
  const key = `${r.file_id}:${r.page}:${r.x}:${r.y}:${r.w}:${r.h}`;
  if (warned.has(key)) return;
  warned.add(key);
  console.warn("Ignoring an invalid page region", r);
}

/** Returns the region clamped to the page, or null (and one console.warn) when it cannot be drawn. */
export function clampRegion(r: Region): Region | null {
  const { x, y, w, h } = r;
  if (![x, y, w, h].every((n) => typeof n === "number" && Number.isFinite(n)) || w <= 0 || h <= 0 || x < -0.0001 || y < -0.0001 || x >= 1 || y >= 1) {
    warnOnce(r);
    return null;
  }
  const cx = Math.min(1, Math.max(0, x));
  const cy = Math.min(1, Math.max(0, y));
  return { ...r, x: cx, y: cy, w: Math.min(w, 1 - cx), h: Math.min(h, 1 - cy) };
}

const pct = (n: number) => `${(n * 100).toFixed(3)}%`;

/** Percent positions inside the image wrapper: no measuring, so the box tracks every size and zoom. */
export function regionStyle(r: Region): { left: string; top: string; width: string; height: string } | null {
  const c = clampRegion(r);
  if (!c) return null;
  return { left: pct(c.x), top: pct(c.y), width: pct(c.w), height: pct(c.h) };
}

/** Multiple of 200 in [600, 2000], so the server's render cache is shared (api.md section 5). */
export function pickImageWidth(containerCssPx: number, dpr: number): number {
  const raw = Math.ceil((containerCssPx * dpr) / 200) * 200;
  return Math.min(2000, Math.max(600, Number.isFinite(raw) ? raw : 1000));
}
