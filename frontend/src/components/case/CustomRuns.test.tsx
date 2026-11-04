import "@/test/mockEnv";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { buildTimeline, timelineExport } from "@/lib/timeline";
import type { CaseDetail } from "@/lib/types";
import { withCustomRuns } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";
import { AnalysisTrailBody } from "../explain/AnalysisTrail";
import { CaseTimeline } from "./CaseTimeline";
import { CoveragePanel } from "./CoveragePanel";
import { FindingsPanel } from "./FindingsPanel";

afterEach(cleanup);

const plain = (): CaseDetail => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
const custom = (): CaseDetail => withCustomRuns(plain());

describe("custom rules in the case timeline", () => {
  it("lists a custom rule that ran and found nothing, one that fired and one that did not apply, in the Rules step", async () => {
    const user = userEvent.setup();
    renderWithSWR(<CaseTimeline c={custom()} onShowFlag={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    const step = screen.getByRole("button", { name: /^\d?.*Checking the rules|Rules/i }).closest("li") as HTMLElement;
    const table = within(step).getByRole("table", { name: "Checks of this step" });

    const fired = within(table).getByText("Heavy borrowing").closest("tr") as HTMLElement;
    expect(within(fired).getByText("Custom")).toBeInTheDocument();
    expect(within(fired).getByText(/by alice, version 2/)).toBeInTheDocument();
    expect(within(fired).getByText("ran")).toBeInTheDocument();
    expect(within(fired).getByText("Checked 1 financial statement: 1 matched.")).toBeInTheDocument();
    expect(within(fired).getByText(/Flags medium when, on a financial statement/)).toBeInTheDocument(); // the readback

    const quiet = within(table).getByText("Expired passport").closest("tr") as HTMLElement;
    expect(within(quiet).getByText("ran")).toBeInTheDocument(); // ran, found nothing: still listed
    expect(within(quiet).getByText(/by bob, version 1, adopted/)).toBeInTheDocument();
    expect(within(quiet).getByText("Checked 1 passport: 0 matched.")).toBeInTheDocument();
    expect(within(quiet).getByText("-")).toBeInTheDocument(); // zero flags

    const skipped = within(table).getByText("Big receipts").closest("tr") as HTMLElement;
    expect(within(skipped).getByText("skipped")).toBeInTheDocument();
    expect(within(skipped).getByText("no usable document of the rule's type")).toBeInTheDocument();
    // the owner's name is shown as given, the technical id stays visible
    expect(within(fired).getByText("cus_3f9a1c2b7d")).toBeInTheDocument();
  });

  it("falls back to the readback when a custom run has neither a note nor a reason", async () => {
    const user = userEvent.setup();
    const c = custom();
    c.check_runs = c.check_runs.map((r) => (r.rule === "cus_77aa00bb11" ? { ...r, note: null, reason: null } : r));
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    const row = screen.getByText("Expired passport").closest("tr") as HTMLElement;
    expect(within(row).getByText("Flags low when a passport's date of expiry is before the case date.")).toBeInTheDocument();
  });

  it("labels a custom flag with the Custom badge and its author, and a built-in flag with neither", async () => {
    const user = userEvent.setup();
    const onShowFlag = vi.fn();
    renderWithSWR(<CaseTimeline c={custom()} onShowFlag={onShowFlag} />);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    const flagRow = screen.getAllByText(/Custom rule \(unreviewed\)/)[0].closest("li") as HTMLElement;
    expect(within(flagRow).getByText("Custom")).toBeInTheDocument();
    expect(within(flagRow).getByText(/Heavy borrowing, by alice/)).toBeInTheDocument();
    await user.click(within(flagRow).getByRole("button", { name: "Show on document" }));
    expect(onShowFlag).toHaveBeenCalledWith("flag-custom-1");
    // no other flag row carries a Custom badge
    const rows = screen.getAllByRole("button", { name: "Show on document" }).map((b) => b.closest("li") as HTMLElement);
    expect(rows.length).toBeGreaterThan(1);
    expect(rows.filter((li) => within(li).queryByText("Custom") !== null)).toHaveLength(1);
  });

  it("says in the step headline that custom rules looked at the case, and shows the facts the step recorded", () => {
    const steps = buildTimeline(custom());
    const rules = steps.find((s) => s.key === "rules")!;
    expect(rules.headline).toContain("Custom rules: 3 rules looked at the case, 1 flag.");
    expect(rules.facts).toEqual(expect.arrayContaining([["custom checks", "3"], ["custom flags", "1"]]));
    expect(rules.checks.filter((r) => r.custom)).toHaveLength(3);
    expect(rules.flags.some((f) => f.rule === "cus_3f9a1c2b7d")).toBe(true);
  });

  it("leaves the headline alone when no custom rule ran", () => {
    expect(buildTimeline(plain()).find((s) => s.key === "rules")!.headline).not.toContain("Custom rules");
  });

  it("carries the custom stamp in the JSON download, and null for built-in checks", () => {
    const c = custom();
    const out = timelineExport(c, buildTimeline(c)) as { steps: { step: string; checks: { rule: string; custom: { author: string; version: number; readback: string } | null }[] }[] };
    const checks = out.steps.find((s) => s.step === "rules")!.checks;
    const mine = checks.find((r) => r.rule === "cus_3f9a1c2b7d")!;
    expect(mine.custom).toMatchObject({ author: "alice", version: 2 });
    expect(mine.custom?.readback).toMatch(/^Flags medium/);
    expect(checks.filter((r) => !r.rule.startsWith("cus_")).every((r) => r.custom === null)).toBe(true);
    expect(checks.find((r) => r.rule === "cus_77aa00bb11")?.custom?.author).toBe("bob"); // present with 0 flags
  });

  it("renders owner-written names as text", async () => {
    const user = userEvent.setup();
    const c = custom();
    c.check_runs = c.check_runs.map((r) => (r.rule === "cus_3f9a1c2b7d" && r.custom ? { ...r, custom: { ...r.custom, name: "<img src=x onerror=alert(1)>", author: "<b>eve</b>" } } : r));
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getAllByText("<img src=x onerror=alert(1)>").length).toBeGreaterThan(0);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
  });
});

describe("custom rules in the findings", () => {
  function panel(c: CaseDetail) {
    const numbered = numberFlags(c.findings, c.flags);
    render(
      <FindingsPanel
        findings={c.findings} numbered={numbered} selectedFlagId={null} onSelectFlag={vi.fn()} onShowDocument={vi.fn()} onDecide={vi.fn()} canDecide
        partial={false} partialReasons={[]} coverage={c.coverage ?? null} documents={c.documents} files={c.files} caseId={`case-${Math.random()}`} explainCase={c}
      />,
    );
  }

  it("says who wrote the rule behind a finding and that it is unreviewed", async () => {
    const user = userEvent.setup();
    panel(custom());
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    expect(screen.getByText("Custom rule by alice (unreviewed)")).toBeInTheDocument();
    const card = screen.getByText("Custom rule by alice (unreviewed)").closest("li") as HTMLElement;
    expect(within(card).getByText("Custom")).toBeInTheDocument();
  });

  it("still says custom and unreviewed when the case view has no run to read the author from", async () => {
    const user = userEvent.setup();
    const c = custom();
    const numbered = numberFlags(c.findings, c.flags);
    render(
      <FindingsPanel findings={c.findings} numbered={numbered} selectedFlagId={null} onSelectFlag={vi.fn()} onShowDocument={vi.fn()} onDecide={vi.fn()} canDecide partial={false} partialReasons={[]} coverage={null} caseId="no-explain" />,
    );
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    expect(screen.getByText("Custom rule (unreviewed)")).toBeInTheDocument();
  });

  it("does not label a built-in finding", async () => {
    const user = userEvent.setup();
    panel(plain());
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    expect(screen.queryByText("Custom")).toBeNull();
    expect(screen.queryByText(/unreviewed/)).toBeNull();
  });
});

describe("custom rules and coverage", () => {
  it("says custom rules are not counted in coverage, and keeps them out of the 'looked up outside' list", () => {
    const c = custom();
    renderWithSWR(<CoveragePanel c={c} />);
    expect(screen.getByTestId("custom-coverage")).toHaveTextContent("Custom rules: 2 ran, 1 skipped. Not counted in coverage.");
    expect(screen.queryByRole("region", { name: "Looked up outside the documents" })).toBeNull();
    // the figures are the case's own, untouched by the custom runs
    expect(screen.getByText(new RegExp(`${c.coverage!.ran}`))).toBeInTheDocument();
  });

  it("shows no custom line when no custom rule ran", () => {
    renderWithSWR(<CoveragePanel c={plain()} />);
    expect(screen.queryByTestId("custom-coverage")).toBeNull();
  });

  it("counts a custom rule neither as a check that did not run nor as a flagged check in the analysis trail", () => {
    const trail = (c: CaseDetail) => {
      const { container, unmount } = renderWithSWR(<AnalysisTrailBody c={c} onGoCoverage={() => {}} />);
      const step4 = within(container).getByRole("heading", { level: 3, name: "Ran the checks" }).closest("li") as HTMLElement;
      const text = {
        notRun: within(step4).queryByText(/did not run, and why/)?.textContent ?? null,
        ran: step4.querySelector("strong")?.textContent ?? null,
        flagged: /(\d+) of them raised at least one flag/.exec(step4.textContent ?? "")?.[1] ?? null,
      };
      unmount();
      return text;
    };
    // the same figures with and without custom runs: they sit outside coverage
    expect(trail(custom())).toEqual(trail(plain()));
    renderWithSWR(<AnalysisTrailBody c={custom()} onGoCoverage={() => {}} />);
    const note = screen.getByTestId("custom-runs-note");
    expect(note).toHaveTextContent("3 custom rules were used, 1 flag raised.");
    expect(note).toHaveTextContent("Custom rules: 2 ran, 1 skipped. Not counted in coverage.");
  });

  it("lists which custom rules ran, by whom, with their wording", () => {
    renderWithSWR(<AnalysisTrailBody c={custom()} onGoCoverage={() => {}} />);
    const note = screen.getByTestId("custom-runs-note");
    expect(within(note).getByText("Heavy borrowing")).toBeInTheDocument();
    expect(within(note).getByText(/Expired passport/)).toBeInTheDocument();
    expect(within(note).getByText(/by bob, version 1 \(adopted\): no flags/)).toBeInTheDocument();
    expect(within(note).getByText(/Big receipts/).closest("li")).toHaveTextContent("did not apply");
  });
});
