/**
 * Where a flag points, as far as the page viewer is concerned. Every flag falls into exactly one of four kinds,
 * decided from the flag's own data (never from its rule name), so a check added tomorrow is handled the same way:
 *
 * - `box`: a region on a page (a figure, a row, a field): drawn as an outlined box.
 * - `page`: a region that covers the whole page (an unreadable or unclassified page): drawn as a frame round the page.
 * - `document`: no region, but one or more documents (file metadata, a statistic over several statements, a check
 *   against an outside register): the viewer opens the document's first page and frames it, with the reason beside it.
 * - `case`: neither a region nor a document (about the case as a whole): nothing to draw, so the viewer says so.
 */
import { clampRegion } from "./regions";
import type { Document, Flag, Region } from "./types";

/** A region this large in both directions marks a whole page, not a spot on it. */
export const WHOLE_PAGE = 0.9;

export type PageRef = { fileId: string; page: number; docId: string };
export type DocSpan = Pick<Document, "id" | "file_id" | "page_start" | "page_end">;

export type Locus = { kind: "box" | "page"; region: Region } | { kind: "document"; pages: PageRef[] } | { kind: "case" };

export function isWholePage(r: Pick<Region, "w" | "h">): boolean {
  return r.w >= WHOLE_PAGE && r.h >= WHOLE_PAGE;
}

export function locusOf(flag: Pick<Flag, "region" | "document_ids">, documents: DocSpan[]): Locus {
  // a region that cannot be drawn (not a number, empty, off the page) counts as no region: the file is framed instead
  if (flag.region && clampRegion(flag.region)) return { kind: isWholePage(flag.region) ? "page" : "box", region: flag.region };
  const pages: PageRef[] = [];
  for (const id of flag.document_ids) {
    const d = documents.find((x) => x.id === id);
    if (d) pages.push({ fileId: d.file_id, page: d.page_start, docId: d.id });
  }
  return pages.length > 0 ? { kind: "document", pages } : { kind: "case" };
}

/** The page to open for a flag: its region's page, else the first page of its first document. */
export function targetPage(flag: Pick<Flag, "region" | "document_ids">, documents: DocSpan[]): { fileId: string; page: number } | null {
  const l = locusOf(flag, documents);
  if (l.kind === "box" || l.kind === "page") return { fileId: l.region.file_id, page: l.region.page };
  if (l.kind === "document") return { fileId: l.pages[0].fileId, page: l.pages[0].page };
  return null;
}

/** Does this flag mark something on that page? (A document-level flag marks every page of its documents.) */
export function touchesPage(flag: Pick<Flag, "region" | "document_ids">, documents: DocSpan[], fileId: string, page: number): boolean {
  const l = locusOf(flag, documents);
  if (l.kind === "box" || l.kind === "page") return l.region.file_id === fileId && l.region.page === page;
  if (l.kind === "document") {
    return flag.document_ids.some((id) => {
      const d = documents.find((x) => x.id === id);
      return d !== undefined && d.file_id === fileId && page >= d.page_start && page <= d.page_end;
    });
  }
  return false;
}

/** A short, plain phrase for where the viewer is pointing, from the kind alone. */
export function locusPhrase(l: Locus): string {
  switch (l.kind) {
    case "box":
      return "marked on the page";
    case "page":
      return "applies to the whole page";
    case "document":
      return "applies to the whole file, not one spot on a page";
    default:
      return "applies to the case as a whole";
  }
}
