import "@/test/mockEnv";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EffectivenessPage from "@/app/effectiveness/page";
import * as mock from "@/lib/mock/handlers";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { resetMockState, state } from "@/lib/mock/state";
import type { CaseDetail, CoverageSkip } from "@/lib/types";
import { renderWithSWR } from "@/test/utils";
import { CoveragePanel } from "../case/CoveragePanel";
import { AnalysisTrailBody } from "../explain/AnalysisTrail";
import { ExplainFinding } from "../explain/FindingExplainer";
import { CustomCatalogueNote } from "./CustomCatalogueNote";

const who = { reviewer: "j.lim", reason: "Agreed with credit policy" };

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

const byId = (id: string): CaseDetail => buildCase(SPECS.find((s) => s.id === id)!);

describe("the officer's wording shows wherever a check is named", () => {
  it("the finding explainer uses the custom name and description, and keeps the id as small text", async () => {
    await mock.updateRuleOverride("bs_running_balance", { name: "Balance walk", description: "Walks every balance from the top.", ...who });
    const c = byId("c_loan_fraud");
    const f = c.findings[0];
    const user = userEvent.setup();
    renderWithSWR(<ExplainFinding c={c} finding={f} flags={c.flags.filter((x) => f.flag_ids.includes(x.id))} />);
    await user.click(screen.getByRole("button", { name: "Why was this flagged?" }));
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByText("Walks every balance from the top.")).toBeInTheDocument();
    expect(within(panel).getByText("Balance walk")).toBeInTheDocument();
    expect(within(panel).queryByText("Statement: running balance")).toBeNull();
    expect(within(panel).getAllByText("bs_running_balance").length).toBeGreaterThan(0);
  });

  it("the finding explainer keeps the derived name and the code description for an untouched check", async () => {
    const c = byId("c_loan_fraud");
    const f = c.findings[0];
    const user = userEvent.setup();
    renderWithSWR(<ExplainFinding c={c} finding={f} flags={c.flags.filter((x) => f.flag_ids.includes(x.id))} />);
    await user.click(screen.getByRole("button", { name: "Why was this flagged?" }));
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByText("Each balance equals the previous balance plus credit minus debit.")).toBeInTheDocument();
    expect(within(panel).getByText("Statement: running balance")).toBeInTheDocument();
  });

  it("the Control performance table uses the custom name and description", async () => {
    await mock.updateRuleOverride("bs_running_balance", { name: "Balance walk", description: "Walks every balance.", ...who });
    renderWithSWR(<EffectivenessPage />);
    await waitFor(() => expect(screen.getAllByText("Balance walk").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Walks every balance.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("bs_running_balance").length).toBeGreaterThan(0);
  });

  it("the coverage panel's 'did not run' list uses the custom name and says 'Switched off in the check catalogue'", async () => {
    await mock.updateRuleOverride("bs_running_balance", { enabled: false, name: "Balance walk", ...who });
    const base = byId("c_loan_fraud");
    const skipped: CoverageSkip[] = [{ rule: "bs_running_balance", status: "skipped", code: "switched_off", reason: "Switched off in the check catalogue by j.lim on 2026-10-28: Agreed with credit policy" }, { rule: "fs_arith", status: "skipped", reason: "requires financial_statement" }];
    const c: CaseDetail = { ...base, coverage: { ...base.coverage!, applicable: 21, ran: 19, ratio: 19 / 21, skipped } };
    renderWithSWR(<CoveragePanel c={c} />);
    const region = screen.getByRole("region", { name: "Checks that did not run" });
    const group = within(region).getByRole("region", { name: "Switched off in the check catalogue" });
    expect(within(group).getByText(/never a clearance/)).toBeInTheDocument();
    expect(within(group).getByText(/by j\.lim on 2026-10-28: Agreed with credit policy/)).toBeInTheDocument();
    await waitFor(() => expect(within(group).getAllByText("Balance walk")).toHaveLength(2)); // in the detail line and as a chip
    // the other group keeps its derived name
    expect(within(region).getByText("Financial statement: arith")).toBeInTheDocument();
  });
});

describe("analysis trail: custom catalogue settings", () => {
  const withCatalogue = (c: CaseDetail): CaseDetail => ({
    ...c,
    provenance: {
      ...c.provenance!,
      catalogue: {
        overrides: [
          { rule: "bs_running_balance", enabled: false, severity: null, revision: 2 },
          { rule: "inv_dates", enabled: true, severity: "low", revision: 1 },
        ],
        hash: "ab".repeat(32),
      },
    },
  });

  it("shows a quiet line in the checks step and in the footer, with an expandable list", async () => {
    await mock.updateRuleOverride("bs_running_balance", { name: "Balance walk", enabled: false, ...who });
    const user = userEvent.setup();
    renderWithSWR(<AnalysisTrailBody c={withCatalogue(byId("c_loan_fraud"))} onGoCoverage={() => {}} />);
    const notes = screen.getAllByText("Custom catalogue settings were used: 2 checks changed");
    expect(notes).toHaveLength(2);
    const step = screen.getByRole("heading", { level: 3, name: "Ran the checks" }).closest("li") as HTMLElement;
    expect(within(step).getByText("Custom catalogue settings were used: 2 checks changed")).toBeInTheDocument();
    const footer = screen.getByRole("heading", { level: 3, name: "Reproducibility" }).closest("section") as HTMLElement;
    expect(within(footer).getByText("Custom catalogue settings were used: 2 checks changed")).toBeInTheDocument();
    const details = within(step).getByText("Which checks").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    await user.click(within(step).getByText("Which checks"));
    expect(details.open).toBe(true);
    await waitFor(() => expect(within(details).getByText("Balance walk")).toBeInTheDocument());
    expect(details).toHaveTextContent("switched off");
    expect(details).toHaveTextContent("Invoice: dates");
    expect(details).toHaveTextContent("severity set to low concern");
    expect(details).toHaveTextContent("revision 2");
  });

  it("says '1 check changed' for one", () => {
    renderWithSWR(<CustomCatalogueNote catalogue={{ overrides: [{ rule: "inv_dates", enabled: false, severity: null, revision: 1 }], hash: "x" }} />);
    expect(screen.getByText("Custom catalogue settings were used: 1 check changed")).toBeInTheDocument();
  });

  it("adds nothing for a case analysed with the defaults", () => {
    const c = byId("c_loan_fraud");
    expect(c.provenance!.catalogue).toBeUndefined();
    renderWithSWR(<AnalysisTrailBody c={c} onGoCoverage={() => {}} />);
    expect(screen.queryByText(/Custom catalogue settings/)).toBeNull();
  });

  it("adds nothing when the record is empty or the case has no record", () => {
    renderWithSWR(<CustomCatalogueNote catalogue={{ overrides: [], hash: "x" }} />);
    renderWithSWR(<CustomCatalogueNote catalogue={null} />);
    expect(screen.queryByText(/Custom catalogue settings/)).toBeNull();
  });
});
