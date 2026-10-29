import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { CaseDetail, Finding } from "@/lib/types";
import { RecommendationHero, topConcerns } from "./RecommendationHero";
import { formUnavailableReason } from "./ReviewPanel";

afterEach(cleanup);

const spec = (id: string) => buildCase(SPECS.find((s) => s.id === id)!);
const fraud: CaseDetail = { ...spec("c_loan_fraud"), allowed_verdicts: ["approve", "request_documents", "escalate_aml", "reject_or_escalate"] };
const clean: CaseDetail = { ...spec("c_loan_clean"), allowed_verdicts: ["approve", "request_documents", "refer_edd"] };
const partial = spec("c_loan_partial");

function hero(c: CaseDetail, over: Partial<Parameters<typeof RecommendationHero>[0]> = {}) {
  const props = {
    c,
    isCurrent: true,
    why: formUnavailableReason(c, true),
    onRecord: vi.fn(),
    onShowFindings: vi.fn(),
    onOpenActivity: vi.fn(),
    onRerun: vi.fn(),
    onAddDocs: vi.fn(),
    actions: <button type="button">More actions</button>,
    ...over,
  };
  render(<RecommendationHero {...props} />);
  return props;
}

const f = (id: string, severity: Finding["severity"], weight: number, title = id): Finding => ({ id, group_key: id, title, severity, flag_ids: [], document_ids: [], rules: [], weight, summary: "", hard_stop: false });

describe("topConcerns", () => {
  it("orders by severity then weight, leaves out informational findings and limits to three", () => {
    const { shown, concerns } = topConcerns([f("a", "info", 0), f("b", "medium", 15), f("c", "high", 10), f("d", "high", 30), f("e", "low", 5), f("g", "medium", 20)]);
    expect(shown.map((x) => x.id)).toEqual(["d", "c", "g"]);
    expect(concerns).toBe(5);
  });
});

describe("RecommendationHero", () => {
  it("leads with score, the labelled suggestion, the top reasons and the one way to decide", async () => {
    const user = userEvent.setup();
    const p = hero(fraud);
    expect(screen.getByRole("img", { name: /^Risk score \d+ of 100, high$/ })).toBeInTheDocument();
    expect(screen.getByText("Suggested next step (you decide)")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Reject or escalate" })).toBeInTheDocument();
    const why = screen.getAllByRole("listitem");
    expect(why).toHaveLength(Math.min(3, topConcerns(fraud.findings).concerns));
    const more = fraud.findings.length - why.length;
    if (more > 0) {
      await user.click(screen.getByRole("button", { name: `+${more} more ${more === 1 ? "finding" : "findings"}` }));
      expect(p.onShowFindings).toHaveBeenCalled();
    }
    await user.click(screen.getByRole("button", { name: "Record decision" }));
    expect(p.onRecord).toHaveBeenCalledTimes(1);
  });

  it("'See which' jumps to the list of unreviewed pages, not just to the findings", async () => {
    const withGap: CaseDetail = { ...fraud, coverage: { ...fraud.coverage!, pages_total: 19, pages_unchecked: 3 } };
    const onShowUnreviewed = vi.fn();
    const props = hero(withGap, { onShowUnreviewed });
    await userEvent.click(screen.getByRole("button", { name: "See which" }));
    expect(onShowUnreviewed).toHaveBeenCalledTimes(1);
    expect(props.onShowFindings).not.toHaveBeenCalled();
  });

  it("shows checks completed in words", () => {
    hero(fraud);
    const bar = screen.getByRole("progressbar", { name: "Checks completed" });
    expect(bar.getAttribute("aria-valuetext")).toMatch(/^\d+ of \d+ checks completed$/);
  });

  it("is calm for a low-risk fast-lane case and still says a person confirms", () => {
    hero(clean);
    expect(screen.getByRole("heading", { level: 2, name: "Fast lane" })).toBeInTheDocument();
    expect(screen.getByText("Low risk, high coverage. A person still confirms.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record decision" })).toBeEnabled();
  });

  it("makes a partial score visible and never calls it fast lane", () => {
    hero(partial);
    expect(screen.getByRole("img", { name: /^Risk score at least \d+ of 100/ })).toHaveTextContent("≥");
    expect(screen.getByText(/Partial score: some checks or pages did not run/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Fast lane" })).toBeNull();
    expect(screen.getByText(/pages? we could not review/)).toBeInTheDocument();
  });

  it("says nothing stands out, without suggesting a lane, for a clean case that is not fast lane", () => {
    hero({ ...clean, lane: "aml", use_case: "aml" });
    expect(screen.getByRole("heading", { level: 2, name: "Nothing stands out yet" })).toBeInTheDocument();
  });

  it("disables Record decision and says why when no decision is possible", () => {
    hero(spec("c_loan_awaiting"));
    expect(screen.getByRole("button", { name: "Record decision" })).toBeDisabled();
    expect(screen.getByText("Waiting for documents.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add documents" })).toBeInTheDocument();
  });

  it("offers a re-run, not a decision, when the analysis failed", async () => {
    const user = userEvent.setup();
    const p = hero(spec("c_loan_error"));
    expect(screen.getByRole("button", { name: "Record decision" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Re-run analysis" }));
    expect(p.onRerun).toHaveBeenCalled();
  });

  it("shows the recorded decision for a closed case", () => {
    hero(spec("c_aml_closed"));
    expect(screen.getByRole("heading", { level: 2, name: "Escalate to AML investigation" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
    expect(screen.getByRole("button", { name: "See decision history" })).toBeInTheDocument();
  });
});
