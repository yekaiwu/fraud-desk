/** Display formatting (frontend.md section 4). The UI wall clock is allowed here; checks never use it. */

/** 45 -> "45 s", 720 -> "12 min", 10800 -> "3 h", 172800 -> "2 d". */
export function formatAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

/** "2026-10-27T10:02:11Z" -> "2026-10-27 10:02 UTC". Unparseable input is returned as is. */
export function formatUtc(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]} UTC` : iso;
}

/** "2 h ago" style text relative to `nowMs` (defaults to the browser clock). */
export function formatRelative(iso: string, nowMs: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const diff = (nowMs - t) / 1000;
  return diff >= 0 ? `${formatAge(diff)} ago` : `in ${formatAge(-diff)}`;
}

/** 0.857 -> "86%". */
export function formatRatio(r: number): string {
  return `${Math.round(r * 100)}%`;
}

/** 540 -> "9 min", 45 -> "45 s", null -> "-". */
export function formatSeconds(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return "-";
  return formatAge(s);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
