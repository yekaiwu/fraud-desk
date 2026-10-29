import { describe, expect, it } from "vitest";
import { CASE_TYPES, caseTypeOf, recogniseFiles } from "./uploadChecklist";

const f = (names: string[]) => names.map((name, i) => ({ id: i + 1, name }));

describe("case types", () => {
  it("offers loan, KYC and AML with the lane each normally goes to", () => {
    expect(CASE_TYPES.map((c) => [c.id, c.lane])).toEqual([
      ["loan", "loan"],
      ["kyc", "edd"],
      ["aml", "aml"],
    ]);
    expect(caseTypeOf("aml").title).toBe("Source of funds (AML)");
  });
});

describe("recogniseFiles (file names and types only)", () => {
  it("lists the loan families and marks all as not yet added for no files", () => {
    const r = recogniseFiles("loan", []);
    expect(r.rows.map((x) => x.label)).toEqual(["Financial statements", "Bank statements", "Invoices", "Contracts or purchase orders"]);
    expect(r.foundCount).toBe(0);
    expect(r.missing).toHaveLength(4);
  });

  it("ticks loan items from names, leaving financial statements out of bank statements", () => {
    const r = recogniseFiles("loan", f(["Acme_FY2024_Financial_Statements.pdf", "DBS-statement-Jan.pdf", "INV-0042.pdf", "photo.pdf"]));
    const by = Object.fromEntries(r.rows.map((x) => [x.id, x.files]));
    expect(by.financial_statements).toEqual(["Acme_FY2024_Financial_Statements.pdf"]);
    expect(by.bank_statements).toEqual(["DBS-statement-Jan.pdf"]);
    expect(by.invoices).toEqual(["INV-0042.pdf"]);
    expect(r.missing.map((x) => x.label)).toEqual(["Contracts or purchase orders"]);
    expect(r.unplaced).toEqual(["photo.pdf"]);
    expect(r.byFile.get(1)).toBe("Financial statements");
  });

  it("recognises the KYC family and credits an unnamed photo to the IDs", () => {
    const r = recogniseFiles("kyc", f(["company profile.pdf", "Certificate of Incorporation.pdf", "ownership_chart.png", "register of shareholders.pdf", "IMG_2041.jpg", "bank statement mar.pdf"]));
    expect(r.foundCount).toBe(6);
    expect(r.missing).toHaveLength(0);
    expect(r.byFile.get(5)).toBe("Directors' and owners' IDs");
  });

  it("recognises the AML family and does not tick IDs for a plain PDF with no hint", () => {
    const r = recogniseFiles("aml", f(["source of funds declaration.pdf", "tax invoice 7.pdf", "sale contract.pdf", "scan001.pdf"]));
    expect(r.rows.find((x) => x.id === "sof_declaration")?.found).toBe(true);
    expect(r.rows.find((x) => x.id === "invoices")?.found).toBe(true);
    expect(r.rows.find((x) => x.id === "contracts")?.found).toBe(true);
    expect(r.rows.find((x) => x.id === "ids")?.found).toBe(false);
    expect(r.rows.find((x) => x.id === "bank_statements")?.found).toBe(false);
    expect(r.unplaced).toEqual(["scan001.pdf"]);
  });
});
