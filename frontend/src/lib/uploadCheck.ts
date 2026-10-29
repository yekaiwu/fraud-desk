/** Advisory client-side upload checks (frontend.md section 5.2). The API stays authoritative. */
import { MAX_FILES_PER_CASE, MAX_UPLOAD_MB } from "./constants";

export const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

const MAGIC: { name: string; bytes: number[] }[] = [
  { name: "PDF", bytes: [0x25, 0x50, 0x44, 0x46, 0x2d] }, // %PDF-
  { name: "JPEG", bytes: [0xff, 0xd8, 0xff] },
  { name: "PNG", bytes: [0x89, 0x50, 0x4e, 0x47] },
];

export function matchesMagic(head: Uint8Array): boolean {
  return MAGIC.some((m) => m.bytes.every((b, i) => head[i] === b));
}

async function readHead(file: Blob): Promise<Uint8Array> {
  const slice = file.slice(0, 8);
  if (typeof slice.arrayBuffer === "function") return new Uint8Array(await slice.arrayBuffer());
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(new Uint8Array(fr.result as ArrayBuffer));
    fr.onerror = () => reject(fr.error);
    fr.readAsArrayBuffer(slice);
  });
}

/** Reads the first 8 bytes; true for %PDF-, FFD8FF or 89504E47. */
export async function sniffMagic(file: Blob): Promise<boolean> {
  try {
    return matchesMagic(await readHead(file));
  } catch {
    return false;
  }
}

/** Reason a single file cannot be uploaded, or null. */
export function fileProblem(file: { name: string; size: number }, magicOk: boolean): string | null {
  if (file.size === 0) return "The file is empty (0 bytes).";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `Larger than ${MAX_UPLOAD_MB} MB. Split or compress it first.`;
  if (!magicOk) return "Not a PDF, JPEG or PNG, whatever the name says. Save or export it again as a PDF.";
  return null;
}

export function countProblem(n: number): string | null {
  if (n < 1) return "Add at least one file.";
  if (n > MAX_FILES_PER_CASE) return `Too many files: ${n}. The limit is ${MAX_FILES_PER_CASE}.`;
  return null;
}

export const dupKey = (f: { name: string; size: number }) => `${f.name}\u0000${f.size}`;

/** Keys that appear more than once (same name and size): a warning only, duplicates are a check not a gate. */
export function duplicateKeys(files: { name: string; size: number }[]): Set<string> {
  const seen = new Set<string>();
  const dup = new Set<string>();
  for (const f of files) {
    const k = dupKey(f);
    if (seen.has(k)) dup.add(k);
    seen.add(k);
  }
  return dup;
}

/** FormData exactly as the API expects (api.md section 3): repeated `files`, optional text parts. */
export function buildCaseForm(input: {
  use_case: string;
  files: File[];
  reference?: string;
  applicant_name?: string;
  applicant_uen?: string;
}): FormData {
  const form = new FormData();
  form.set("use_case", input.use_case);
  for (const f of input.files) form.append("files", f, f.name);
  for (const k of ["reference", "applicant_name", "applicant_uen"] as const) {
    const v = input[k]?.trim();
    if (v) form.set(k, v);
  }
  return form;
}
