/**
 * Friendly names for documents and pages. The API only gives ids ("d8", "f8 p1"); an officer thinks in
 * "Bank statement, Nov 2025, page 1". Names come from the case's own documents (period, invoice number,
 * contract number ...); when nothing better exists the type and file name are used. The raw ids stay
 * available through `docTitle` for title attributes.
 */
import type { DocType, Document, FileRef } from "./types";

const TYPE_LABEL: Record<DocType, string> = {
  bank_statement: "Bank statement",
  invoice: "Invoice",
  financial_statement: "Financial statement",
  contract: "Contract",
  purchase_order: "Purchase order",
  company_profile: "Company profile",
  certificate_of_incorporation: "Certificate of incorporation",
  ownership_chart: "Ownership chart",
  shareholder_register: "Shareholder register",
  source_of_funds_declaration: "Source of funds declaration",
  national_id: "National ID",
  passport: "Passport",
  boilerplate: "Boilerplate page",
  other: "Unrecognised document",
};

/** "bank_statement" -> "Bank statement". Unknown types are humanised rather than shown raw. */
export function docTypeLabel(type: string): string {
  const known = TYPE_LABEL[type as DocType];
  if (known) return known;
  const s = type.replace(/_/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : "Document";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2025-07-01" -> "Jul 2025". Parsed from the string, never through Date (no timezone drift). */
export function monthYear(iso: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})/.exec((iso ?? "").trim());
  if (!m) return null;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${month} ${m[1]}` : null;
}

/** "bank_statement_2025-07.pdf" -> "Bank statement 2025-07". */
export function prettyFileName(name: string): string {
  const s = name.replace(/\.[A-Za-z0-9]{2,5}$/, "").replace(/[_]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : name;
}

const titleCase = (s: string) => s.toLowerCase().replace(/(^|[\s'-])([a-z])/g, (_, a: string, b: string) => a + b.toUpperCase());

function fieldValue(d: Document, keys: string[]): string {
  for (const k of keys) {
    const v = (d.fields?.[k]?.value ?? "").trim();
    if (v) return v;
  }
  return "";
}

/** Name of one document without page or file qualifiers, or null when the document has no distinguishing detail. */
function qualifier(d: Document): string | null {
  switch (d.doc_type) {
    case "bank_statement":
      return monthYear(fieldValue(d, ["period_start", "period_end"]));
    case "invoice": {
      const n = fieldValue(d, ["invoice_number"]);
      return n || null;
    }
    case "contract": {
      const n = fieldValue(d, ["contract_number"]);
      return n || null;
    }
    case "purchase_order": {
      const n = fieldValue(d, ["po_number"]);
      return n || null;
    }
    case "financial_statement": {
      const y = /^(\d{4})/.exec(fieldValue(d, ["period_end", "period_start"]));
      return y ? `FY${y[1]}` : null;
    }
    case "national_id":
    case "passport": {
      const n = fieldValue(d, ["holder_name"]);
      return n ? titleCase(n) : null;
    }
    default:
      return null;
  }
}

function baseName(d: Document): { name: string; specific: boolean } {
  const type = docTypeLabel(d.doc_type);
  const q = qualifier(d);
  if (!q) return { name: type, specific: false };
  // invoices, contracts and purchase orders read "Invoice INV-2025-0104"; statements read "Bank statement, Jul 2025"
  const joiner = d.doc_type === "invoice" || d.doc_type === "contract" || d.doc_type === "purchase_order" ? " " : d.doc_type === "national_id" || d.doc_type === "passport" ? ": " : ", ";
  return { name: `${type}${joiner}${q}`, specific: true };
}

export type DocNamer = {
  /** True when the namer was built from real documents (otherwise every name is a plain fallback). */
  readonly known: boolean;
  /** "Bank statement, Nov 2025". Unknown ids read "Document 8". */
  doc(id: string): string;
  /** "Bank statement, Nov 2025, page 1" for a page of a file. */
  page(fileId: string, page: number): string;
  /** Name of the first document in a file, or its tidied file name. */
  file(fileId: string): string;
  /** Id and file name, for a title attribute: "d8 (bank_statement_2025-11.pdf)". */
  docTitle(id: string): string;
  /** Rewrites known ids inside free text: "d8" and "f8 p1" become names. Unknown ids are left alone. */
  text(s: string): string;
};

export function makeDocNamer(documents: Document[] = [], files: FileRef[] = []): DocNamer {
  const fileById = new Map(files.map((f) => [f.id, f]));
  const docById = new Map(documents.map((d) => [d.id, d]));
  const names = new Map<string, string>();
  const bases = documents.map((d) => ({ d, ...baseName(d) }));
  const counts = new Map<string, number>();
  for (const b of bases) counts.set(b.name, (counts.get(b.name) ?? 0) + 1);
  const isVague = (b: { d: Document; specific: boolean }) => !b.specific && (b.d.doc_type === "boilerplate" || b.d.doc_type === "other");
  for (const b of bases) {
    if (isVague(b)) continue;
    const file = fileById.get(b.d.file_id);
    // Identical names (two statements for one month) are told apart by their file name.
    names.set(b.d.id, file && (counts.get(b.name) ?? 0) > 1 ? `${b.name} (${file.name})` : b.name);
  }
  for (const b of bases) {
    if (!isVague(b)) continue;
    // A page we could not classify is named after what shares its file, else by its file name.
    const sibling = bases.find((o) => o.d.file_id === b.d.file_id && !isVague(o));
    const file = fileById.get(b.d.file_id);
    names.set(b.d.id, sibling ? `${b.name} in ${names.get(sibling.d.id)}` : file ? `${b.name} (${file.name})` : b.name);
  }

  const fallbackDoc = (id: string) => `Document ${id.replace(/^d(?=\d)/, "")}`;
  const doc = (id: string) => names.get(id) ?? fallbackDoc(id);
  const file = (fileId: string) => {
    const first = documents.find((d) => d.file_id === fileId);
    if (first) return doc(first.id);
    const f = fileById.get(fileId);
    return f ? prettyFileName(f.name) : `File ${fileId.replace(/^f(?=\d)/, "")}`;
  };
  const page = (fileId: string, p: number) => {
    const d = documents.find((x) => x.file_id === fileId && p >= x.page_start && p <= x.page_end);
    return `${d ? doc(d.id) : file(fileId)}, page ${p}`;
  };
  const docTitle = (id: string) => {
    const d = docById.get(id);
    const f = d ? fileById.get(d.file_id) : undefined;
    return f ? `${id} (${f.name})` : id;
  };
  const text = (s: string) =>
    s
      .replace(/\bf(\d+)(?:\s+p|:)(\d+)\b/g, (m, fid: string, p: string) => (fileById.has(`f${fid}`) ? page(`f${fid}`, Number(p)) : m))
      .replace(/\bd(\d+)\b/g, (m) => (docById.has(m) ? doc(m) : m));
  return { known: documents.length > 0, doc, page, file, docTitle, text };
}
