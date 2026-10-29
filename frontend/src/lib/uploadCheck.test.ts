import { describe, expect, it } from "vitest";
import { buildCaseForm, countProblem, duplicateKeys, fileProblem, sniffMagic } from "./uploadCheck";

const file = (bytes: number[] | string, name = "a.pdf") => new File([typeof bytes === "string" ? bytes : new Uint8Array(bytes)], name);

describe("upload checks", () => {
  it("accepts PDF, JPEG and PNG magic bytes", async () => {
    expect(await sniffMagic(file("%PDF-1.7 ..."))).toBe(true);
    expect(await sniffMagic(file([0xff, 0xd8, 0xff, 0xe0]))).toBe(true);
    expect(await sniffMagic(file([0x89, 0x50, 0x4e, 0x47, 0x0d]))).toBe(true);
  });
  it("rejects a .pdf whose bytes are text", async () => {
    expect(await sniffMagic(file("hello, I am text", "fake.pdf"))).toBe(false);
    expect(fileProblem({ name: "fake.pdf", size: 16 }, false)).toMatch(/Not a PDF/);
  });
  it("blocks a 26 MB file and an empty one", () => {
    expect(fileProblem({ name: "big.pdf", size: 26 * 1024 * 1024 }, true)).toMatch(/25 MB/);
    expect(fileProblem({ name: "e.pdf", size: 0 }, true)).toMatch(/empty/);
    expect(fileProblem({ name: "ok.pdf", size: 25 * 1024 * 1024 }, true)).toBeNull();
  });
  it("blocks 0 and 41 files", () => {
    expect(countProblem(0)).toBeTruthy();
    expect(countProblem(40)).toBeNull();
    expect(countProblem(41)).toMatch(/Too many/);
  });
  it("flags duplicates by name and size", () => {
    expect(duplicateKeys([{ name: "a", size: 1 }, { name: "a", size: 1 }, { name: "a", size: 2 }]).size).toBe(1);
  });
  it("builds the exact FormData", () => {
    const f1 = file("%PDF-1", "one.pdf");
    const f2 = file("%PDF-2", "two.pdf");
    const form = buildCaseForm({ use_case: "loan", files: [f1, f2], reference: " R-1 ", applicant_name: "", applicant_uen: "201912345A" });
    expect(form.get("use_case")).toBe("loan");
    expect(form.getAll("files")).toHaveLength(2);
    expect(form.get("reference")).toBe("R-1");
    expect(form.has("applicant_name")).toBe(false);
    expect(form.get("applicant_uen")).toBe("201912345A");
  });
});
