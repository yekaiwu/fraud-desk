/**
 * Readable evidence. The API sends one string per flag ("gross_profit: printed 281920.00; revenue ...").
 * We split it into label/value rows where its shape allows and otherwise keep calm sentences.
 * Nothing is ever rounded, converted or re-ordered: amounts only gain thousands separators, which is
 * lossless, and the untouched string is always available as the raw evidence.
 */

export type EvidenceRow = { kind: "fact"; label: string; value: string } | { kind: "note"; text: string };

/** "1234567.89" -> "1,234,567.89". Only plain decimals (optional sign, no leading zero) are touched. */
export function groupThousands(num: string): string {
  const m = /^([-+]?)(\d+)(\.\d+)?$/.exec(num);
  if (!m) return num;
  const [, sign, int, frac = ""] = m;
  if (int.length < 4 || (int.length > 1 && int.startsWith("0"))) return num;
  return `${sign}${int.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${frac}`;
}

/** Money-shaped numbers: digits, a dot and exactly two decimals, standing alone (not inside a date, id or longer number). */
const AMOUNT = /(?<![\w.,-])[-+]?\d+\.\d{2}(?![\w.])/g;

export type TextPart = { text: string; amount: boolean };

/** Splits free text into plain parts and amount parts (with separators added). Concatenating the originals gives the input back. */
export function splitAmounts(s: string): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const m of s.matchAll(AMOUNT)) {
    const at = m.index ?? 0;
    if (at > last) parts.push({ text: s.slice(last, at), amount: false });
    parts.push({ text: groupThousands(m[0]), amount: true });
    last = at + m[0].length;
  }
  if (last < s.length) parts.push({ text: s.slice(last), amount: false });
  return parts;
}

/** "gross_profit" -> "Gross profit"; short upper-case labels such as "MAD" stay as they are. */
export function humanLabel(raw: string): string {
  const s = raw.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  if (!s) return s;
  return /^[a-z]/.test(s) ? s[0].toUpperCase() + s.slice(1) : s;
}

const KEY_VALUE = /^([A-Za-z][A-Za-z0-9_ ./]{0,38}?):\s+(\S.*)$/;
const LABEL_NUMBER = /^([A-Za-z][A-Za-z_ ]{0,28}?)\s+(-?\d[\d.,]*%?)$/;

/** Splits an evidence string on semicolons into label/value rows and plain notes. Order is preserved. */
export function parseEvidence(raw: string): EvidenceRow[] {
  const segments = raw
    .split(/;\s+|\n+/)
    .map((x) => x.trim())
    .filter(Boolean);
  return segments.map((seg): EvidenceRow => {
    const kv = KEY_VALUE.exec(seg);
    if (kv) return { kind: "fact", label: humanLabel(kv[1]), value: kv[2] };
    const ln = LABEL_NUMBER.exec(seg);
    if (ln) return { kind: "fact", label: humanLabel(ln[1]), value: ln[2] };
    return { kind: "note", text: seg };
  });
}
