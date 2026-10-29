/**
 * Money handling (frontend.md section 3 and 9.2). Amounts are decimal strings; they are never
 * passed through Number(). Sums use BigInt cents.
 */
const CENTS_RE = /^-?\d+(\.\d{1,2})?$/;
const DECIMAL_RE = /^(-?)(\d+)(\.\d+)?$/;

function group(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "12345.67" -> 1234567n. Throws on anything that is not -?digits(.1-2 digits). */
export function toCents(s: string): bigint {
  const t = s.trim();
  if (!CENTS_RE.test(t)) throw new Error(`Not a decimal amount with at most 2 decimals: ${JSON.stringify(s)}`);
  const neg = t.startsWith("-");
  const [int, frac = ""] = (neg ? t.slice(1) : t).split(".");
  const cents = BigInt(int) * 100n + BigInt(frac.padEnd(2, "0"));
  return neg ? -cents : cents;
}

/** 1234567n -> "12,345.67". */
export function fmtCents(c: bigint): string {
  const neg = c < 0n;
  const abs = neg ? -c : c;
  const int = abs / 100n;
  const frac = (abs % 100n).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${group(int.toString())}.${frac}`;
}

/** "1234567.8" -> "1,234,567.8": string-based grouping, every decimal kept. Non-decimals come back unchanged. */
export function formatDecimalString(s: string): string {
  const m = DECIMAL_RE.exec(s.trim());
  if (!m) return s;
  return `${m[1]}${group(m[2])}${m[3] ?? ""}`;
}
