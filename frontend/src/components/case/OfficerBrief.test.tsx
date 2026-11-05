import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentReport } from "@/lib/types";
import { GENERATED_NOTE } from "../explain/AnalysisTrail";
import { OfficerBrief } from "./OfficerBrief";

afterEach(cleanup);

const base: AgentReport = { agent: "case_officer", role: "Briefs the reviewer", status: "ok", checks_run: 0, flag_ids: [], summary: "", verdicts: [], next_steps: [], handoff: "", cost_usd: "0.0004", seconds: 3 };
const officer = (over: Partial<AgentReport> = {}): AgentReport => ({ ...base, ...over });
const finance: AgentReport = { ...base, agent: "finance", summary: "Ran 9 checks." };

describe("OfficerBrief", () => {
  it("shows the officer's summary at once, labelled as generated text, with the next steps one click away", async () => {
    const user = userEvent.setup();
    render(<OfficerBrief agents={[finance, officer({ summary: "The balance on row 9 does not add up.", next_steps: ["Ask for the original statement (bs_running_balance)", "Check the contract price (x_invoice_vs_contract)"] })]} />);
    const brief = screen.getByRole("region", { name: /Case officer.s summary/ });
    expect(within(brief).getByText("The balance on row 9 does not add up.")).toBeInTheDocument();
    expect(within(brief).getByText(GENERATED_NOTE)).toBeInTheDocument();
    expect(screen.queryByText("Ran 9 checks.")).toBeNull();
    const toggle = within(brief).getByRole("button", { name: /^Suggested next steps\s*\(2\)$/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(brief).queryByRole("list")).toBeNull();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(brief).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Ask for the original statement (bs_running_balance)", "Check the contract price (x_invoice_vs_contract)"]);
    await user.click(toggle);
    expect(within(brief).queryByRole("list")).toBeNull();
  });

  it("has no next-steps control when the officer gave none", () => {
    render(<OfficerBrief agents={[officer({ summary: "One thing stands out." })]} />);
    expect(screen.getByText("One thing stands out.")).toBeInTheDocument();
    expect(screen.queryByText(/Suggested next steps/)).toBeNull();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it.each([
    ["no team report at all", undefined],
    ["a null list", null],
    ["an empty list", []],
    ["a team without an officer", [finance]],
    ["an officer with nothing to do", [officer({ status: "skipped", summary: "No flags at medium or above. Nothing to escalate." })]],
  ] as const)("shows nothing for %s", (_name, agents) => {
    const { container } = render(<OfficerBrief agents={agents as AgentReport[] | null | undefined} onOpenTimeline={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says in one line that the officer could not run, and that the flags stand", () => {
    render(<OfficerBrief agents={[officer({ status: "failed", summary: "The case officer could not write a summary." })]} />);
    const note = screen.getByRole("status");
    expect(note).toHaveTextContent("The case officer could not write a summary");
    expect(note).toHaveTextContent("Every flag stands as it was.");
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("links to the full trail only when told where it is, in both the summary and the failure note", async () => {
    const user = userEvent.setup();
    const open = vi.fn();
    const { rerender } = render(<OfficerBrief agents={[officer({ summary: "S" })]} onOpenTimeline={open} />);
    await user.click(screen.getByRole("button", { name: "Review team" }));
    expect(open).toHaveBeenCalledTimes(1);
    rerender(<OfficerBrief agents={[officer({ status: "failed" })]} onOpenTimeline={open} />);
    await user.click(screen.getByRole("button", { name: "Review team" }));
    expect(open).toHaveBeenCalledTimes(2);
    rerender(<OfficerBrief agents={[officer({ summary: "S" })]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps the text as plain text", async () => {
    const user = userEvent.setup();
    render(<OfficerBrief agents={[officer({ summary: "<b>bold</b> and\nsecond line", next_steps: ["<i>x</i>"] })]} />);
    await user.click(screen.getByRole("button", { name: /Suggested next steps/ }));
    expect(screen.getByText(/<b>bold<\/b> and/)).toBeInTheDocument();
    expect(screen.getByText("<i>x</i>")).toBeInTheDocument();
    expect(document.querySelector("b, i")).toBeNull();
  });
});
