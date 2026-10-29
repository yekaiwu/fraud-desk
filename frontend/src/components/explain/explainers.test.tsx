import "@/test/mockEnv";
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import { buildCase, finding, flag, FILE_STATEMENT, SPECS, statementDoc } from "@/lib/mock/fixtures";
import * as handlers from "@/lib/mock/handlers";
import { resetMockState, state } from "@/lib/mock/state";
import type { CaseDetail, EffectivenessResponse } from "@/lib/types";
import { renderWithSWR } from "@/test/utils";
import { PerformanceSummary } from "../effectiveness/PerformanceSummary";
import { QueueTable } from "../queue/QueueTable";
import { NotCheckedList } from "../case/DocumentsPanel";
import { ExplainChecks, ExplainPages } from "./CoverageExplainer";
import { ExplainFinding } from "./FindingExplainer";
import { ExplainBlocksFastLane, ExplainLanes, ExplainNextStep } from "./NextStepExplainer";
import { ExplainScore } from "./ScoreExplainer";

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

const byId = (id: string): CaseDetail => buildCase(SPECS.find((s) => s.id === id)!);

function cappedCase(): CaseDetail {
  const flags = [1, 2, 3, 4].map((n) => flag({ id: `f${n}`, rule: `bs_rule_${n}`, severity: "high", documents: ["d1"], evidence: "e", explanation: "x" }));
  return buildCase({
    id: "c_cap", reference: null, use_case: "loan", status: "open", ageSeconds: 10, applicantName: "Cap Co", files: [FILE_STATEMENT()], documents: [statementDoc()],
    flags, findings: flags.map((f, i) => finding(`fnd_${i + 1}`, `Finding ${i + 1}`, "high", [f], "s")), lane: "loan",
  });
}

async function open(name: string | RegExp) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name }));
  return { user, panel: await screen.findByRole("dialog") };
}

describe("risk score explainer", () => {
  it("shows this case's points by severity, the total, the bands and the sentence about proof", async () => {
    renderWithSWR(<ExplainScore c={byId("c_loan_fraud")} />);
    const { panel } = await open("How the risk score is calculated");
    const table = within(panel).getByRole("table", { name: /Points added by this case/ });
    expect(within(table).getByRole("row", { name: /High concern 1 30 30/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Medium concern 3 15 45/ })).toBeInTheDocument();
    expect(within(panel).getByText(/Total 75, so the score is 75\./)).toBeInTheDocument();
    // the weights and bands come from the method response
    expect(await within(panel).findByText(/High concern 30, Medium concern 15, Low concern 5, For information 0\./)).toBeInTheDocument();
    const bands = await within(panel).findByRole("table", { name: "Score bands" });
    expect(within(bands).getByRole("row", { name: /High risk 60 to 100 \(this case\)/ })).toBeInTheDocument();
    expect(within(bands).getByRole("row", { name: /Low risk 0 to 29$/ })).toBeInTheDocument();
    expect(within(panel).getByText("A high score means stronger or more concerns, not proof of fraud.")).toBeInTheDocument();
    // technical detail is collapsed and carries the ruleset and the formula
    const tech = panel.querySelector("details") as HTMLDetailsElement;
    expect(tech.open).toBe(false);
    expect(tech).toHaveTextContent("fnd_1");
    expect(tech).toHaveTextContent("2026.10.1");
  });

  it("says Total 120, capped at 100 for a capped score", async () => {
    renderWithSWR(<ExplainScore c={cappedCase()} />);
    const { panel } = await open("How the risk score is calculated");
    expect(within(panel).getByText(/Total 120, capped at 100\./)).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Capped at 100/ })).toBeInTheDocument();
  });

  it("calls a partial score a floor", async () => {
    renderWithSWR(<ExplainScore c={byId("c_loan_partial")} />);
    const { panel } = await open("How the risk score is calculated");
    expect(within(panel).getByText(/at least 20/)).toBeInTheDocument();
    expect(await within(panel).findByRole("row", { name: /Low risk 0 to 29 \(this case, at least\)/ })).toBeInTheDocument();
    expect(within(panel).getByText(/The extract stage failed/)).toBeInTheDocument();
  });

  it("explains a clean case with no findings", async () => {
    renderWithSWR(<ExplainScore c={byId("c_loan_clean")} />);
    const { panel } = await open("How the risk score is calculated");
    expect(within(panel).getByText("No findings were raised, so the score is 0.")).toBeInTheDocument();
    expect(within(panel).queryByRole("table", { name: /Points added/ })).toBeNull();
  });

  it("still shows the case's numbers when /explain/method is not there (404)", async () => {
    vi.spyOn(api, "getExplainMethod").mockRejectedValue(new ApiErrorException({ code: "not_found", message: "no such route" }, 404));
    renderWithSWR(<ExplainScore c={cappedCase()} />);
    const { panel } = await open("How the risk score is calculated");
    expect(await within(panel).findByText("The method details are not available right now.")).toBeInTheDocument();
    expect(within(panel).getByText(/Total 120, capped at 100\./)).toBeInTheDocument();
    expect(within(panel).getByText(/is in the/)).toHaveTextContent("High risk");
  });

  it("is fetched once and only when an explainer opens", async () => {
    const spy = vi.spyOn(handlers, "getExplainMethod");
    renderWithSWR(
      <>
        <ExplainScore c={byId("c_loan_fraud")} />
        <ExplainNextStep c={byId("c_loan_fraud")} />
      </>,
    );
    expect(spy).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    await screen.findByRole("table", { name: "Score bands" });
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "How the suggested next step was reached" }));
    await screen.findByRole("list", { name: "What the fast lane requires" });
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("checks completed explainer", () => {
  it("gives N of M, the reasons checks did not run, the pages not reviewed and the warning", async () => {
    renderWithSWR(<ExplainChecks c={byId("c_loan_fraud")} />);
    const { panel } = await open("What checks completed means");
    expect(within(panel).getByText("18 of 21 checks completed")).toBeInTheDocument();
    expect(within(panel).getByText(/Why 3 checks did not run/i)).toBeInTheDocument();
    expect(within(panel).getByText(/No usable financial statement was found/)).toBeInTheDocument();
    expect(within(panel).getByText(/1 of 4 pages were? not reviewed|1 of 4 pages was not reviewed/)).toBeInTheDocument();
    expect(within(panel).getByText("A check that did not run is not a clearance.")).toBeInTheDocument();
    expect(await within(panel).findByText(/Checks completed is the share/)).toBeInTheDocument();
  });

  it("counts the pages against the fast-lane figure for a case with unreviewed pages", async () => {
    renderWithSWR(<ExplainChecks c={byId("c_loan_partial")} />);
    const { panel } = await open("What checks completed means");
    expect(await within(panel).findByText(/the figure used for the fast lane is 13%/)).toBeInTheDocument();
  });
});

describe("pages we could not review", () => {
  it("says why this case's pages are listed", async () => {
    const c = byId("c_loan_partial");
    renderWithSWR(<ExplainPages c={c} />);
    const { panel } = await open("Why pages are not reviewed");
    expect(within(panel).getByText("no checks for this document type")).toBeInTheDocument();
    expect(within(panel).getByText(/3 of 4 pages not reviewed/)).toBeInTheDocument();
  });

  it("is offered from the list when the case is passed, and absent otherwise", () => {
    const c = byId("c_loan_partial");
    renderWithSWR(<NotCheckedList items={c.not_checked_documents} onView={() => {}} documents={c.documents} files={c.files} explainCase={c} />);
    expect(screen.getByRole("button", { name: "Why pages are not reviewed" })).toBeInTheDocument();
    cleanup();
    renderWithSWR(<NotCheckedList items={c.not_checked_documents} onView={() => {}} documents={c.documents} files={c.files} />);
    expect(screen.queryByRole("button", { name: "Why pages are not reviewed" })).toBeNull();
  });
});

describe("suggested next step and fast lane", () => {
  it("lists case type, band, blocking findings, coverage, lane and outcome, with ticks for the fast-lane list", async () => {
    renderWithSWR(<ExplainNextStep c={byId("c_loan_hardstop")} />);
    const { panel } = await open("How the suggested next step was reached");
    const table = within(panel).getByRole("table", { name: /How the suggestion was reached/ });
    expect(within(table).getByRole("row", { name: /Case type Loan application/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Blocks the fast lane 1 finding/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Lane Loan review/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Suggested outcome Reject or escalate/ })).toBeInTheDocument();
    const list = await within(panel).findByRole("list", { name: "What the fast lane requires" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(within(items[2]).getByText("Not met")).toBeInTheDocument();
    expect(within(items[0]).getByText("Met")).toBeInTheDocument();
    expect(within(panel).getByText("You decide; nothing is approved or rejected automatically.")).toBeInTheDocument();
    expect(panel.querySelector("details")).toHaveTextContent("pdf_editor_software");
  });

  it("explains an AML lane reached through a routing check on a KYC-style case, using the method's list", async () => {
    const c = byId("c_aml_graph");
    c.use_case = "kyc";
    renderWithSWR(<ExplainNextStep c={c} />);
    const { panel } = await open("How the suggested next step was reached");
    expect(await within(panel).findByText(/It is in the AML lane although the case is Onboarding \(KYC\)/)).toBeInTheDocument();
  });

  it("explains the Blocks the fast lane badge with the plain names of the blocking checks", async () => {
    const c = byId("c_loan_hardstop");
    renderWithSWR(<ExplainBlocksFastLane c={c} finding={c.findings[0]} />);
    const { panel } = await open("Why this blocks the fast lane");
    expect(await within(panel).findByText(/PDF: editor software/)).toHaveTextContent("(this case)");
    expect(within(panel).getByText("Statement: pass through")).toBeInTheDocument();
    expect(within(panel).getByText(/does not change the score/)).toBeInTheDocument();
  });

  it("falls back gracefully without the method", async () => {
    vi.spyOn(api, "getExplainMethod").mockRejectedValue(new TypeError("network"));
    renderWithSWR(<ExplainNextStep c={byId("c_loan_hardstop")} />);
    const { panel } = await open("How the suggested next step was reached");
    expect(await within(panel).findByText("The method details are not available right now.")).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Lane Loan review/ })).toBeInTheDocument();
  });

  it("explains every lane, Routine review and the fast-lane list in the worklist", async () => {
    renderWithSWR(<ExplainLanes />);
    const { panel } = await open("What the lanes mean");
    for (const lane of ["Loan review", "Enhanced due diligence", "AML investigation", "Fast lane", "Routine review"]) expect(within(panel).getByText(lane)).toBeInTheDocument();
    expect(await within(panel).findByText("At least 80% of the applicable checks completed, counting pages we could not review")).toBeInTheDocument();
  });
});

describe("why was this flagged", () => {
  it("shows the check's description and tampering target, the evidence, where, the points and the technical detail", async () => {
    const c = byId("c_loan_fraud");
    const f = c.findings[0];
    renderWithSWR(<ExplainFinding c={c} finding={f} flags={c.flags.filter((x) => f.flag_ids.includes(x.id))} />);
    const { panel } = await open("Why was this flagged?");
    expect(await within(panel).findByText("Each balance equals the previous balance plus credit minus debit.")).toBeInTheDocument();
    expect(within(panel).getByText("Looks for: edited balance.")).toBeInTheDocument();
    expect(within(panel).getByText(/Printed balance/)).toBeInTheDocument();
    expect(within(panel).getAllByText(/Bank statement/).length).toBeGreaterThan(0);
    expect(within(panel).getByText("30")).toBeInTheDocument();
    expect(within(panel).getByText("high concern")).toBeInTheDocument();
    expect(within(panel).getByText("A finding is something to check, not proof of fraud.")).toBeInTheDocument();
    const tech = panel.querySelector("details") as HTMLDetailsElement;
    expect(tech.open).toBe(false);
    expect(tech).toHaveTextContent("bs_running_balance v1");
    expect(tech).toHaveTextContent("fnd_1");
  });
});

describe("worklist headers", () => {
  it("puts a question mark on Risk, Lane and status and Checks completed", async () => {
    const q = await handlers.getQueue();
    renderWithSWR(<QueueTable items={q.items} query={{}} ageNow={0} onSort={() => {}} />);
    const table = screen.getByRole("table");
    for (const name of ["How the risk score is calculated", "What the lanes mean", "What checks completed means"]) {
      expect(within(table).getByRole("button", { name })).toBeInTheDocument();
    }
    const user = userEvent.setup();
    await user.click(within(table).getByRole("button", { name: "How the risk score is calculated" }));
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByRole("table", { name: "Score bands" })).toBeInTheDocument();
  });
});

const data: EffectivenessResponse = {
  generated_for: { use_case: null, since: null }, cases_total: 10, cases_decided: 4, median_seconds_to_verdict: 540,
  rules: [
    { rule: "bs_running_balance", stage: "rules", default_severity: "high", description: "d", cases_fired: 3, times_fired: 4, confirmed: 3, dismissed: 1, pending: 0, open: 0, dismissal_rate: 0.25, median_seconds_to_decide: 210, cases_ran: 9, cases_skipped: 1 },
    { rule: "inv_dates", stage: "rules", default_severity: "medium", description: "d", cases_fired: 0, times_fired: 0, confirmed: 0, dismissed: 0, pending: 0, open: 0, dismissal_rate: null, median_seconds_to_decide: null, cases_ran: 9, cases_skipped: 0 },
  ],
};

describe("control performance", () => {
  it("defines each tile with this view's numbers and the one-sentence caveat", async () => {
    renderWithSWR(<PerformanceSummary data={data} />);
    const user = userEvent.setup();
    const expectations: [string, RegExp][] = [
      ["What “Cases decided” means", /4 of 10 cases in this view have a recorded verdict/],
      ["What “Checks that fired” means", /1 of 2 checks fired, raising 4 flags/],
      ["What “Confirmed vs dismissed” means", /3 confirmed and 1 dismissed, so 75% confirmed/],
      ["What “Median time to decide” means", /9 min across 4 decided cases/],
    ];
    for (const [name, text] of expectations) {
      await user.click(screen.getByRole("button", { name }));
      const panel = await screen.findByRole("dialog");
      expect(within(panel).getByText(text)).toBeInTheDocument();
      expect(within(panel).getByText("These figures measure what reviewers decided, not whether a flag was truly fraud.")).toBeInTheDocument();
      await user.keyboard("{Escape}");
    }
  });
});
