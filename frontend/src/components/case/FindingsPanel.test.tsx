import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { CaseDetail, Disposition } from "@/lib/types";
import { FindingsPanel } from "./FindingsPanel";

afterEach(cleanup);

const fraud = () => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
let caseSeq = 0;

function setup(over: { c?: CaseDetail; selectedFlagId?: string | null; canDecide?: boolean; named?: boolean; caseId?: string; partial?: boolean } = {}) {
  const c = over.c ?? fraud();
  const numbered = numberFlags(c.findings, c.flags);
  const handlers = { onSelectFlag: vi.fn(), onShowDocument: vi.fn(), onDecide: vi.fn() };
  const ui = (sel: string | null = over.selectedFlagId ?? null) => (
    <FindingsPanel
      findings={c.findings}
      numbered={numbered}
      selectedFlagId={sel}
      canDecide={over.canDecide ?? true}
      partial={over.partial ?? false}
      partialReasons={["stage_failed:rules"]}
      coverage={c.coverage ?? null}
      documents={over.named === false ? undefined : c.documents}
      files={over.named === false ? undefined : c.files}
      caseId={over.caseId ?? `case-${++caseSeq}`}
      {...handlers}
    />
  );
  const view = render(ui());
  return { c, numbered, view, ui, ...handlers };
}

describe("FindingsPanel grouping", () => {
  it("groups by severity with the glossary words and counts, highest first, only the high group open", () => {
    setup();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings[0]).toMatch(/^High concern1/);
    expect(headings[1]).toMatch(/^Medium concern3/);
    expect(screen.getByRole("button", { name: /High concern/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Medium concern/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("The running balance on this row is 10,000.00 higher than the previous balance plus the movement.")).toBeInTheDocument();
    expect(screen.queryByText(/No bank credit matches/)).toBeNull();
  });

  it("opens a collapsed group on click and remembers it for the case while the page stays loaded", async () => {
    const user = userEvent.setup();
    const first = setup({ caseId: "remember-me" });
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    expect(screen.getByText(/No bank credit matches/)).toBeInTheDocument();
    first.view.unmount();
    setup({ caseId: "remember-me" });
    expect(screen.getByRole("button", { name: /Medium concern/ })).toHaveAttribute("aria-expanded", "true");
    cleanup();
    setup({ caseId: "another-case" });
    expect(screen.getByRole("button", { name: /Medium concern/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the group and finding that own a selected flag", () => {
    const { c } = setup({ selectedFlagId: fraud().flags.find((f) => f.rule === "x_invoice_vs_receipts")!.id });
    expect(screen.getByRole("button", { name: /Medium concern/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Hide the 2 instances/ })).toBeInTheDocument();
    expect(c.findings.length).toBeGreaterThan(0);
  });

  it("keeps information-only findings in a collapsed 'For information' group", () => {
    const c = fraud();
    c.findings = c.findings.map((f) => ({ ...f, severity: "info" as const, weight: 0 }));
    c.flags = c.flags.map((f) => ({ ...f, severity: "info" as const }));
    setup({ c });
    expect(screen.getByRole("button", { name: /For information/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("No high, medium or low concerns were found.")).toBeInTheDocument();
    expect(screen.getByText(/This is not a clearance: 3 checks did not run/)).toBeInTheDocument();
  });
});

describe("a finding card", () => {
  it("leads with the explanation, the severity, the score effect and how many documents, and hides internals", () => {
    setup();
    const card = document.querySelector('[data-finding="fnd_1"]') as HTMLElement;
    expect(within(card).getByText("High concern")).toBeInTheDocument();
    expect(within(card).getByText("Adds 30 to the risk score")).toBeInTheDocument();
    expect(within(card).getByText("Seen on 1 document")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Bank statement/ })).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/bs_running_balance/);
    expect(card.textContent).not.toMatch(/Hard stop/);
  });

  it("shows the lane wording instead of 'Hard stop'", () => {
    const c = fraud();
    c.findings[0] = { ...c.findings[0], hard_stop: true };
    setup({ c });
    expect(screen.getByText("Blocks the fast lane")).toBeInTheDocument();
    expect(screen.queryByText(/Hard stop/i)).toBeNull();
  });

  it("names documents from the case, and never shows a raw id as text when names are missing", async () => {
    const user = userEvent.setup();
    setup({ named: false });
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\bd\d+\b/);
    expect(text).toMatch(/Document 1/);
  });

  it("'Show on document' jumps with reveal for the right flag", async () => {
    const user = userEvent.setup();
    const { onSelectFlag, c } = setup();
    await user.click(screen.getByRole("button", { name: /Show on document \(flag 1\)/ }));
    expect(onSelectFlag).toHaveBeenCalledWith(c.findings[0].flag_ids[0], { reveal: true });
  });

  it("jumps to the first page of a document when its name is chosen", async () => {
    const user = userEvent.setup();
    const { onShowDocument } = setup();
    await user.click(screen.getByRole("button", { name: /Bank statement/ }));
    expect(onShowDocument).toHaveBeenCalledWith("d1");
  });

  it("marks the card of the selected flag", () => {
    const c = fraud();
    setup({ c, selectedFlagId: c.findings[0].flag_ids[0] });
    expect(document.getElementById(`flag-${c.findings[0].flag_ids[0]}`)).toHaveAttribute("data-selected", "true");
  });
});

describe("evidence", () => {
  it("is collapsed, then shows facts, the check id and a raw view with a copy button", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const c = fraud();
    c.flags[0].evidence = "Printed balance 1084560.00; previous 65160.00 + credit 9400.00 = 74560.00";
    setup({ c });
    expect(screen.queryByText("Check: bs_running_balance")).toBeNull();
    await user.click(screen.getAllByRole("button", { name: "Evidence" })[0]);
    expect(screen.getByText("Check: bs_running_balance")).toBeInTheDocument();
    expect(screen.getByText("Printed balance")).toBeInTheDocument();
    expect(screen.getByText("1,084,560.00")).toBeInTheDocument();
    expect(screen.queryByText(/^Printed balance 1084560/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show raw evidence" }));
    expect(screen.getByText(c.flags[0].evidence)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy raw evidence" }));
    expect(writeText).toHaveBeenCalledWith(c.flags[0].evidence);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });
});

describe("deciding", () => {
  it("offers one clear pair per concern and opens the right decision", async () => {
    const user = userEvent.setup();
    const { onDecide, c } = setup();
    const id = c.findings[0].flag_ids[0];
    expect(screen.getAllByRole("button", { name: /Mark as a real concern/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /^(Confirm|Dismiss)/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Mark as not a concern\.\.\./ }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "dismiss", flagIds: [id] });
    await user.click(screen.getByRole("button", { name: /Mark as a real concern/ }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "confirm", flagIds: [id] });
  });

  it("hides decisions and selection when the case cannot be decided", () => {
    setup({ canDecide: false });
    expect(screen.queryByRole("button", { name: /Mark as/ })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("shows status and no actions while a dismissal waits for a second approver", () => {
    const c = fraud();
    c.flags[0].disposition = "pending_second_approval" as Disposition;
    setup({ c });
    expect(screen.getByText("Needs 2nd approval")).toBeInTheDocument();
    expect(screen.getByText("A dismissal is waiting for a second reviewer.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mark as/ })).toBeNull();
  });

  it("puts the finding-level bulk actions for a group of flags in a quiet menu", async () => {
    const user = userEvent.setup();
    const { onDecide, c } = setup({ selectedFlagId: null });
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    const card = document.querySelector('[data-finding="fnd_3"]') as HTMLElement;
    expect(within(card).queryByRole("button", { name: /Mark as/ })).toBeNull(); // instances are folded away
    await user.click(within(card).getByRole("button", { name: "Apply to all 2" }));
    expect(within(card).getAllByRole("menuitem")).toHaveLength(2);
    await user.click(within(card).getByRole("menuitem", { name: "Mark all 2 as not a concern..." }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "dismiss", findingId: "fnd_3" });
    expect(within(card).queryByRole("menu")).toBeNull();
    await user.click(within(card).getByRole("button", { name: "Apply to all 2" }));
    await user.keyboard("{Escape}");
    expect(within(card).queryByRole("menu")).toBeNull();
    expect(c.findings[2].flag_ids).toHaveLength(2);
  });

  it("lists instances behind a disclosure, each with its own pair", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    const card = document.querySelector('[data-finding="fnd_3"]') as HTMLElement;
    await user.click(within(card).getByRole("button", { name: /Review the 2 instances/ }));
    expect(within(card).getAllByRole("button", { name: /Mark as a real concern/ })).toHaveLength(2);
  });

  it("selects several flags for one bulk decision with the second-approver warning for a high one", async () => {
    const user = userEvent.setup();
    const { onDecide, c } = setup();
    await user.click(screen.getByRole("checkbox", { name: /Select.*flag 1/ }));
    const bar = screen.getByRole("region", { name: "Selected flags" });
    expect(within(bar).getByText("1 flags selected")).toBeInTheDocument();
    expect(within(bar).getByText(/A second reviewer must approve a dismissal/)).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: /Mark as not a concern: 1\.\.\./ }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "dismiss", flagIds: [c.findings[0].flag_ids[0]] });
    await user.click(within(bar).getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByRole("region", { name: "Selected flags" })).toBeNull();
  });
});

describe("empty and partial states", () => {
  const empty = (over: Partial<CaseDetail>) => {
    const c = { ...fraud(), findings: [], flags: [], ...over } as CaseDetail;
    return setup({ c, partial: over.partial });
  };

  it("says no concerns is not a clearance when checks did not run", () => {
    empty({});
    expect(screen.getByText("No concerns were found.")).toBeInTheDocument();
    expect(screen.getByText("This is not a clearance: 3 checks did not run.")).toBeInTheDocument();
  });

  it("states coverage plainly when every check ran", () => {
    const base = fraud();
    empty({ coverage: { ...base.coverage!, ran: 21, skipped: [] } });
    expect(screen.getByText("21 of 21 checks completed.")).toBeInTheDocument();
    expect(screen.getByText("No flags is not proof of authenticity; see coverage.")).toBeInTheDocument();
  });

  it("never reads as safe when the analysis is partial", () => {
    empty({ partial: true });
    expect(screen.getByText(/analysis is incomplete/)).toBeInTheDocument();
    expect(screen.getByText("The rules stage failed")).toBeInTheDocument();
    expect(screen.queryByText("No concerns were found.")).toBeNull();
  });

  it("shows a status skeleton while loading", () => {
    const c = fraud();
    render(
      <FindingsPanel findings={c.findings} numbered={numberFlags(c.findings, c.flags)} selectedFlagId={null} onSelectFlag={vi.fn()} onShowDocument={vi.fn()} onDecide={vi.fn()} canDecide partial={false} partialReasons={[]} coverage={null} loading />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading findings");
  });
});
