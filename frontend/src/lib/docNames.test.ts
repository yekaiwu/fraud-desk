import { describe, expect, it } from "vitest";
import { docTypeLabel, makeDocNamer, monthYear, prettyFileName } from "./docNames";
import type { Document, FileRef } from "./types";

const cell = (value: string) => ({ value, kind: "text" as const, source: "text_layer" as const });
function doc(id: string, file_id: string, doc_type: Document["doc_type"], fields: Record<string, string> = {}, over: Partial<Document> = {}): Document {
  return {
    id, file_id, page_start: 1, page_end: 1, doc_type,
    classification: { label: doc_type, probability: 0.99, classifier: "rules", version: "1" },
    status: "ok", fields: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, cell(v)])), tables: {}, warnings: [], ...over,
  };
}
const file = (id: string, name: string, pages = 1): FileRef => ({
  id, name, sha256: "", size_bytes: 1, mime: "application/pdf", pages: Array.from({ length: pages }, (_, i) => ({ page: i + 1, width_pt: 595, height_pt: 842, rotation: 0, has_text_layer: true, is_scan: false, word_count: 1 })),
  pdf_meta: {}, added_in_version: 1,
});

const documents = [
  doc("d1", "f1", "bank_statement", { period_start: "2025-07-01" }, { page_end: 2 }),
  doc("d2", "f2", "bank_statement", { period_start: "2025-11-01" }),
  doc("d3", "f3", "invoice", { invoice_number: "INV-2025-0104" }),
  doc("d4", "f4", "financial_statement", { period_end: "2025-12-31" }),
  doc("d5", "f5", "company_profile"),
  doc("d6", "f6", "invoice", { invoice_number: "INV-2025-0104" }),
  doc("d7", "f3", "other", {}, { page_start: 2, page_end: 2, status: "received_not_checked" }),
  doc("d8", "f7", "national_id", { holder_name: "LIM HUI MIN" }),
];
const files = [file("f1", "bank_statement_2025-07.pdf", 2), file("f2", "b.pdf"), file("f3", "invoice_a.pdf", 2), file("f4", "fs.pdf"), file("f5", "profile.pdf"), file("f6", "invoice_copy.pdf"), file("f7", "id.pdf")];
const names = makeDocNamer(documents, files);

describe("document names", () => {
  it("names documents from what is printed on them", () => {
    expect(names.doc("d2")).toBe("Bank statement, Nov 2025");
    expect(names.doc("d4")).toBe("Financial statement, FY2025");
    expect(names.doc("d5")).toBe("Company profile");
    expect(names.doc("d8")).toBe("National ID: Lim Hui Min");
  });
  it("adds the file name when two names would be identical, and names an unclassified page after its file-mate", () => {
    expect(names.doc("d3")).toBe("Invoice INV-2025-0104 (invoice_a.pdf)");
    expect(names.doc("d6")).toBe("Invoice INV-2025-0104 (invoice_copy.pdf)");
    expect(names.doc("d7")).toBe("Unrecognised document in Invoice INV-2025-0104 (invoice_a.pdf)");
  });
  it("names an unclassified page by its file when nothing else shares the file", () => {
    const n = makeDocNamer([doc("d9", "f9", "boilerplate")], [file("f9", "terms.pdf")]);
    expect(n.doc("d9")).toBe("Boilerplate page (terms.pdf)");
  });
  it("names a page by the document that covers it", () => {
    expect(names.page("f1", 2)).toBe("Bank statement, Jul 2025, page 2");
    expect(names.page("f3", 2)).toBe("Unrecognised document in Invoice INV-2025-0104 (invoice_a.pdf), page 2");
  });
  it("never shows a raw id as the name, but keeps it for title attributes", () => {
    expect(names.doc("d99")).toBe("Document 99");
    expect(names.file("f99")).toBe("File 99");
    expect(names.docTitle("d1")).toBe("d1 (bank_statement_2025-07.pdf)");
  });
  it("falls back to the tidied file name for a file without documents", () => {
    const n = makeDocNamer([], files);
    expect(n.known).toBe(false);
    expect(n.file("f1")).toBe("Bank statement 2025-07");
    expect(n.page("f1", 1)).toBe("Bank statement 2025-07, page 1");
  });
  it("rewrites known ids in free text and leaves unknown ones", () => {
    expect(names.text("(row 20, d2) and d99; see f1 p2 or f1:1")).toBe("(row 20, Bank statement, Nov 2025) and d99; see Bank statement, Jul 2025, page 2 or Bank statement, Jul 2025, page 1");
  });
  it("formats months from the string, humanises types", () => {
    expect(monthYear("2025-07-31")).toBe("Jul 2025");
    expect(monthYear("garbage")).toBeNull();
    expect(docTypeLabel("source_of_funds_declaration")).toBe("Source of funds declaration");
    expect(docTypeLabel("new_thing")).toBe("New thing");
    expect(prettyFileName("a_b.PDF")).toBe("A b");
  });
});
