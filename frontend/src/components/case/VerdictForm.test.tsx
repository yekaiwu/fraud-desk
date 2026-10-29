import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, finding, flag, SPECS } from "@/lib/mock/fixtures";
import type { CaseDetail } from "@/lib/types";
import { suggestedVerdict, VerdictForm } from "./VerdictForm";

afterEach(cleanup);

const clean = buildCase(SPECS.find((s) => s.id === "c_loan_clean")!);
const fraud: CaseDetail = {
  ...buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!),
  allowed_verdicts: ["approve", "request_documents", "escalate_aml", "reject_or_escalate"],
};

/** Clean AML case as the backend returns it: score 0, band low, but the lane falls back to the use case. */
const cleanAml: CaseDetail = { ...clean, use_case: "aml", lane: "aml", allowed_verdicts: ["approve", "request_documents", "escalate_aml"] };
const cleanKyc: CaseDetail = { ...clean, use_case: "kyc", lane: "edd", allowed_verdicts: ["approve", "request_documents", "refer_edd"] };

describe("suggestedVerdict", () => {
  it("does not suggest escalation for a clean AML case", () => {
    expect(cleanAml.score!.value).toBe(0);
    expect(suggestedVerdict(cleanAml)).toBeNull();
  });

  it("does not suggest EDD referral for a clean KYC case", () => {
    expect(suggestedVerdict(cleanKyc)).toBeNull();
  });

  it("suggests approve for a clean fast-lane case", () => {
    expect(suggestedVerdict(clean)).toBe("approve");
  });

  it("suggests the lane verdict when the band is elevated", () => {
    expect(fraud.score!.band).not.toBe("low");
    expect(suggestedVerdict(fraud)).toBe("reject_or_escalate");
    expect(suggestedVerdict({ ...cleanAml, score: { ...cleanAml.score!, value: 40, band: "medium" } })).toBe("escalate_aml");
  });

  it("suggests the lane verdict on a hard-stop finding even when the band is low", () => {
    const f = flag({ id: "x.1", rule: "x", severity: "high", documents: ["d1"], evidence: "e", explanation: "x" });
    const hard = finding("fnd_h", "Hard stop", "high", [f], "s", true);
    expect(suggestedVerdict({ ...cleanAml, findings: [hard] })).toBe("escalate_aml");
  });

  it("never suggests approve when a hard-stop finding exists", () => {
    const f = flag({ id: "x.1", rule: "x", severity: "high", documents: ["d1"], evidence: "e", explanation: "x" });
    expect(suggestedVerdict({ ...clean, findings: [finding("fnd_h", "Hard stop", "high", [f], "s", true)] })).toBeNull();
  });

  it("suggests nothing without a lane or score", () => {
    expect(suggestedVerdict({ ...cleanAml, lane: null })).toBeNull();
    expect(suggestedVerdict({ ...cleanAml, score: null })).toBeNull();
  });
});

describe("VerdictForm suggestion badge", () => {
  const props = { onDone: vi.fn(), onSelectFlag: vi.fn(), onReload: vi.fn() };

  it("shows no Suggested badge on a clean AML case", () => {
    render(<VerdictForm c={cleanAml} {...props} />);
    expect(screen.getByRole("radio", { name: /Escalate to AML/ })).toBeInTheDocument();
    expect(screen.queryByText("Suggested")).toBeNull();
  });

  it("shows the badge on the lane verdict for an elevated case", () => {
    render(<VerdictForm c={fraud} {...props} />);
    expect(screen.getByText("Suggested").closest("label")).toHaveTextContent("Reject or escalate");
  });
});
