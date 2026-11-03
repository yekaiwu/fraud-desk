import { describe, expect, it, vi } from "vitest";
import { extraLetter, isWholePage, locusOf, locusPhrase, placesOf, targetPage, touchesPage, type DocSpan } from "./flagLocus";
import type { Region } from "./types";

const docs: DocSpan[] = [
  { id: "d1", file_id: "f1", page_start: 1, page_end: 2 },
  { id: "d2", file_id: "f1", page_start: 3, page_end: 3 },
  { id: "d3", file_id: "f2", page_start: 1, page_end: 1 },
];
const box: Region = { file_id: "f1", page: 2, x: 0.5, y: 0.4, w: 0.1, h: 0.02 };
const whole: Region = { file_id: "f1", page: 3, x: 0, y: 0, w: 1, h: 1, label: "unclassified" };

describe("locusOf: every flag is one of four kinds, decided from its own data", () => {
  it("a region on a page is a box", () => {
    expect(locusOf({ region: box, document_ids: ["d1"] }, docs)).toEqual({ kind: "box", region: box });
  });
  it("a region over the whole page is a page, not a box hiding at the edge", () => {
    expect(locusOf({ region: whole, document_ids: ["d2"] }, docs).kind).toBe("page");
    expect(isWholePage({ w: 0.9, h: 0.9 })).toBe(true);
    expect(isWholePage({ w: 0.89, h: 1 })).toBe(false);
  });
  it("no region but documents is a document-level flag, listing the first page of each document", () => {
    expect(locusOf({ region: null, document_ids: ["d1", "d3"] }, docs)).toEqual({
      kind: "document",
      pages: [
        { fileId: "f1", page: 1, docId: "d1" },
        { fileId: "f2", page: 1, docId: "d3" },
      ],
    });
  });
  it("no region and no known document is a case-level flag", () => {
    expect(locusOf({ region: null, document_ids: [] }, docs).kind).toBe("case");
    expect(locusOf({ region: undefined, document_ids: ["d9"] }, docs).kind).toBe("case");
  });
  it("a region that cannot be drawn counts as no region, so the file is framed instead of nothing being shown", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(locusOf({ region: { ...box, x: NaN }, document_ids: ["d1"] }, docs).kind).toBe("document");
    expect(locusOf({ region: { ...box, w: 0 }, document_ids: [] }, docs).kind).toBe("case");
    warn.mockRestore();
  });
});

describe("where the viewer goes", () => {
  it("opens the region's page, else the first page of the first document, else nowhere", () => {
    expect(targetPage({ region: box, document_ids: ["d3"] }, docs)).toEqual({ fileId: "f1", page: 2 });
    expect(targetPage({ region: null, document_ids: ["d2", "d3"] }, docs)).toEqual({ fileId: "f1", page: 3 });
    expect(targetPage({ region: null, document_ids: [] }, docs)).toBeNull();
  });
  it("a document-level flag marks every page of its documents, a box only its own page", () => {
    const meta = { region: null, document_ids: ["d1"] };
    expect(touchesPage(meta, docs, "f1", 1)).toBe(true);
    expect(touchesPage(meta, docs, "f1", 2)).toBe(true);
    expect(touchesPage(meta, docs, "f1", 3)).toBe(false);
    expect(touchesPage(meta, docs, "f2", 1)).toBe(false);
    expect(touchesPage({ region: box, document_ids: ["d1"] }, docs, "f1", 1)).toBe(false);
    expect(touchesPage({ region: box, document_ids: ["d1"] }, docs, "f1", 2)).toBe(true);
    expect(touchesPage({ region: null, document_ids: [] }, docs, "f1", 1)).toBe(false);
  });
  it("says in plain words what each kind means", () => {
    expect(locusPhrase({ kind: "box", region: box })).toBe("marked on the page");
    expect(locusPhrase({ kind: "page", region: whole })).toBe("applies to the whole page");
    expect(locusPhrase({ kind: "document", pages: [] })).toMatch(/whole file/);
    expect(locusPhrase({ kind: "case" })).toMatch(/case as a whole/);
  });
});

describe("placesOf: every place a flag points at", () => {
  const at = (page: number, label?: string): Region => ({ file_id: "f1", page, x: 0.1, y: 0.1, w: 0.1, h: 0.02, label });
  it("is the main region first, then the related ones lettered a, b, c", () => {
    const p = placesOf({ region: at(1, "closing balance"), extra_regions: [at(1, "opening balance"), at(2)], field: "closing_balance" });
    expect(p.map((x) => [x.key, x.letter, x.label])).toEqual([
      ["main", null, "closing balance"],
      ["a", "a", "opening balance"],
      ["b", "b", "related figure"],
    ]);
    expect(extraLetter(0)).toBe("a");
  });
  it("names an unlabelled main place after its field, and skips places that cannot be drawn", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const p = placesOf({ region: { ...at(1), label: null }, extra_regions: [{ ...at(1), w: 0 }, at(3, "credit")], field: "gst_amount" });
    expect(p.map((x) => x.label)).toEqual(["gst amount", "credit"]);
    expect(p[1].letter).toBe("b"); // letters follow the flag's own list, so a skipped place does not shift the others
    warn.mockRestore();
  });
  it("is empty for a flag with no region and none related", () => {
    expect(placesOf({ region: null, extra_regions: [], field: null })).toEqual([]);
  });
});
