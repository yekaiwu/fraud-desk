import { describe, expect, it } from "vitest";
import { deriveEntityGraph, isInAndOut, norm } from "./graph";
import { buildCase, doc, SPECS, statementDoc } from "./mock/fixtures";
import type { CaseDetail, Cell, Document, Flag, Row } from "./types";

const cell = (value: string): Cell => ({ value, kind: "text", source: "text_layer" });
const txRow = (index: number, cp: string | null, o: { credit?: string; debit?: string }): Row => ({
  index,
  cells: { ...(o.credit ? { credit: cell(o.credit) } : {}), ...(o.debit ? { debit: cell(o.debit) } : {}) },
  annotations: cp ? { counterparty: { label: cp, probability: 0.9, classifier: "rules", version: "1" } } : {},
  box: null,
});
const stmt = (rows: Row[]): Document =>
  doc("d1", "f1", 1, 1, "bank_statement", { tables: { transactions: { name: "transactions", columns: [], rows, pages: [1] } } });

const base = buildCase(SPECS.find((s) => s.id === "c_loan_clean")!);
const withDocs = (documents: Document[], flags: Flag[] = []): CaseDetail => ({ ...base, applicant: { name: "Example Trading Pte. Ltd.", source: "upload" }, documents, flags });

describe("norm", () => {
  it("merges token order, case and company suffixes", () => {
    expect(norm("Wong Jessica")).toBe(norm("Jessica Wong"));
    expect(norm("Acme Trading Pte. Ltd.")).toBe(norm("ACME TRADING"));
    expect(norm("Smith & Sons (Pte) Ltd")).toBe(norm("smith and sons"));
    expect(norm("Acme")).not.toBe(norm("Acme Holdings"));
  });
});

describe("deriveEntityGraph", () => {
  it("sums exactly in cents and merges counterparties that are both payer and payee", () => {
    const g = deriveEntityGraph(
      withDocs([
        stmt([
          txRow(0, "Gamma Holdings Pte Ltd", { credit: "20000.10" }),
          txRow(1, "GAMMA HOLDINGS", { credit: "0.20" }),
          txRow(2, "Gamma Holdings", { debit: "19500.00" }),
          txRow(3, null, { debit: "5.00" }),
        ]),
      ]),
    );
    const gamma = g.nodes.find((n) => n.label.startsWith("Gamma"))!;
    expect(isInAndOut(gamma)).toBe(true);
    expect(g.nodes.filter((n) => n.kind === "counterparty")).toHaveLength(1);
    const paid = g.edges.find((e) => e.kind === "paid")!;
    expect(paid.amountCents).toBe(2000030n);
    expect(paid.label).toBe("20,000.30");
    expect(paid.count).toBe(2);
    expect(g.edges.find((e) => e.kind === "paid_out")!.amountCents).toBe(1950000n);
  });

  it("flags an edge when a transactions flag matches its row, and the nodes it touches", () => {
    const flag = { id: "f", rule: "bs_pass_through", stage: "rules", severity: "medium", document_ids: ["d1"], table: "transactions", row: 1, extra_regions: [], evidence: "", explanation: "", disposition: "open" } as Flag;
    const g = deriveEntityGraph(withDocs([stmt([txRow(0, "Alpha", { credit: "1.00" }), txRow(1, "Beta", { credit: "2.00" })])], [flag]));
    expect(g.edges.find((e) => e.label === "2.00")!.flagged).toBe(true);
    expect(g.edges.find((e) => e.label === "1.00")!.flagged).toBe(false);
    expect(g.nodes.find((n) => n.label === "Beta")!.flagged).toBe(true);
    expect(g.nodes.find((n) => n.label === "Alpha")!.flagged).toBe(false);
  });

  it("caps counterparties at 8 per side and reports truncated", () => {
    const rows = Array.from({ length: 12 }, (_, i) => txRow(i, `Payer ${String.fromCharCode(65 + i)}`, { credit: `${(i + 1) * 100}.00` }));
    const g = deriveEntityGraph(withDocs([stmt(rows)]));
    expect(g.truncated).toBe(4);
    expect(g.nodes.filter((n) => n.kind === "counterparty")).toHaveLength(9);
    const more = g.edges.find((e) => e.target === "applicant" && e.source === "n:+more-in")!;
    expect(more.count).toBe(4);
    expect(more.amountCents).toBe(BigInt((1 + 2 + 3 + 4) * 10000));
  });

  it("builds owners and directors from tables, with the chart percent winning", () => {
    const chart = doc("d2", "f3", 1, 1, "ownership_chart", {
      tables: { holders: { name: "holders", columns: [], pages: [1], rows: [
        { index: 0, cells: { holder_name: cell("Jessica Wong"), percent: cell("60.00") }, annotations: {}, box: null },
        { index: 1, cells: { holder_name: cell("Beta Holdings Pte Ltd"), holder_type: cell("company"), percent: cell("40.00") }, annotations: {}, box: null },
      ] } },
    });
    const reg = doc("d3", "f3", 1, 1, "shareholder_register", {
      tables: { shareholders: { name: "shareholders", columns: [], pages: [1], rows: [
        { index: 0, cells: { holder_name: cell("Wong Jessica"), percent: cell("55.00") }, annotations: {}, box: null },
      ] } },
    });
    const prof = doc("d4", "f3", 1, 1, "company_profile", {
      tables: { directors: { name: "directors", columns: [], pages: [1], rows: [
        { index: 0, cells: { director_name: cell("Jessica WONG") }, annotations: {}, box: null },
      ] } },
    });
    const g = deriveEntityGraph(withDocs([chart, reg, prof]));
    const jess = g.nodes.filter((n) => n.label.toLowerCase().includes("jessica") || n.label.toLowerCase().includes("wong"));
    expect(jess).toHaveLength(1);
    expect(jess[0].roles.sort()).toEqual(["director", "owner"]);
    expect(g.edges.find((e) => e.kind === "owns" && e.source === jess[0].id)!.label).toBe("60.00%");
    expect(g.nodes.find((n) => n.label.startsWith("Beta"))!.kind).toBe("company");
    expect(g.edges.filter((e) => e.kind === "director")).toHaveLength(1);
  });

  it("derives invoiced edges and ignores totals it cannot read exactly", () => {
    const inv = doc("d5", "f2", 1, 1, "invoice", { fields: { supplier_name: cell("Alpha Supplies"), buyer_name: cell("Example Trading Pte. Ltd."), total: cell("13000.00") } });
    const bad = doc("d6", "f2", 1, 1, "invoice", { fields: { supplier_name: cell("Alpha Supplies"), buyer_name: cell("Example Trading"), total: cell("13000.005") } });
    const g = deriveEntityGraph(withDocs([inv, bad]));
    const e = g.edges.find((x) => x.kind === "invoiced")!;
    expect(e.target).toBe("applicant");
    expect(e.amountCents).toBe(1300000n);
    expect(e.docIds).toEqual(["d5", "d6"]);
  });

  it("works on the AML mock fixture and lists every edge exactly once", () => {
    const aml = buildCase(SPECS.find((s) => s.id === "c_aml_graph")!);
    const g = deriveEntityGraph(aml);
    expect(g.edges.length).toBeGreaterThan(2);
    expect(new Set(g.edges.map((e) => e.id)).size).toBe(g.edges.length);
    expect(g.nodes.some(isInAndOut)).toBe(true);
    expect(g.edges.some((e) => e.flagged)).toBe(true);
    void statementDoc;
  });
});
