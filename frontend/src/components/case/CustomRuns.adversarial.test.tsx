/**
 * Adversarial tests for custom rules on a case: a rule that found nothing is still on the timeline, custom rules never
 * move the coverage figures, a custom flag says who wrote the rule, and everything an owner typed is shown as text.
 */
import "@/test/mockEnv";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { buildTimeline, timelineExport } from "@/lib/timeline";
import type { CaseDetail, CheckRun } from "@/lib/types";
import { runInfo, withCustomRuns } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";
import { AnalysisTrailBody } from "../explain/AnalysisTrail";
import { CoverageExplainer } from "../explain/CoverageExplainer";
import { CaseTimeline } from "./CaseTimeline";
import { CoveragePanel } from "./CoveragePanel";
import { FindingsPanel } from "./FindingsPanel";

afterEach(cleanup);

const IMG = '<img src=x onerror="window.__pwned=1">';
const SCRIPT = "<script>window.__pwned=2</script>";

const plain = (): CaseDetail => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
const custom = (): CaseDetail => withCustomRuns(plain());

function nothingInjected() {
  expect(document.querySelector("img")).toBeNull();
  expect(document.querySelector("script")).toBeNull();
  expect(document.querySelector("[onerror]")).toBeNull();
  expect(document.querySelector("b, i")).toBeNull();
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
}

/** A case with exactly the custom runs given (and none of the helper's flags), for the corner cases. */
function withRuns(runs: CheckRun[]): CaseDetail {
  const c = plain();
  return { ...c, check_runs: [...c.check_runs, ...runs] };
}
const run = (rule: string, over: Partial<CheckRun> = {}): CheckRun => ({ rule, stage: "rules", status: "ran", flags: 0, seconds: 0, note: "Checked 1 invoice: 0 matched.", custom: runInfo({ rule_id: rule }), ...over });

async function expandAll() {
  await userEvent.setup().click(screen.getByRole("button", { name: "Expand all" }));
}

function rulesStep(): HTMLElement {
  return screen.getByRole("button", { name: /^Running checks/ }).closest("li") as HTMLElement;
}

describe("a custom rule that found nothing is visible on the case", () => {
  it("appears in the Rules step's list of checks with zero flags, ran, and says who wrote it", async () => {
    const c = withRuns([run("cus_0000000001", { custom: runInfo({ rule_id: "cus_0000000001", name: "Quiet rule", author: "dana", version: 3 }) })]);
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await expandAll();
    const table = within(rulesStep()).getByRole("table", { name: "Checks of this step" });
    const row = within(table).getByText("Quiet rule").closest("tr") as HTMLElement;
    expect(within(row).getByText("ran")).toBeInTheDocument();
    expect(within(row).getByText("-")).toBeInTheDocument();
    expect(within(row).getByText("Custom")).toBeInTheDocument();
    expect(within(row).getByText(/by dana, version 3/)).toBeInTheDocument();
    expect(within(row).getByText("cus_0000000001")).toBeInTheDocument();
  });

  it("is in the JSON the officer downloads, with its author and version, even with zero flags", () => {
    const c = withRuns([run("cus_0000000001", { custom: runInfo({ rule_id: "cus_0000000001", name: "Quiet rule", author: "dana", version: 3 }) })]);
    const out = timelineExport(c, buildTimeline(c)) as { steps: { step: string; checks: { rule: string; flags: number; custom: { author: string; version: number; name: string } | null }[] }[] };
    const entry = out.steps.find((s) => s.step === "rules")!.checks.find((r) => r.rule === "cus_0000000001")!;
    expect(entry.flags).toBe(0);
    expect(entry.custom).toMatchObject({ author: "dana", version: 3, name: "Quiet rule" });
  });

  it("a skipped custom rule is listed as skipped with its reason, not hidden", async () => {
    const c = withRuns([run("cus_0000000002", { status: "skipped", note: null, reason: "no usable document of the rule's type", code: "custom_no_document", custom: runInfo({ rule_id: "cus_0000000002", name: "Receipts rule" }) })]);
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await expandAll();
    const row = within(rulesStep()).getByText("Receipts rule").closest("tr") as HTMLElement;
    expect(within(row).getByText("skipped")).toBeInTheDocument();
    expect(within(row).getByText("no usable document of the rule's type")).toBeInTheDocument();
  });

  it("does not crash on a custom-looking run that has no stamp (an older or damaged record): it shows the rule id", async () => {
    const c = withRuns([{ rule: "cus_0000000003", stage: "rules", status: "ran", flags: 0, seconds: 0 }]);
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await expandAll();
    expect(within(rulesStep()).getAllByText("cus_0000000003").length).toBeGreaterThan(0);
  });

  it("keeps the same step headline numbers a built-in stage has, and adds the custom tally only when custom rules ran", () => {
    expect(buildTimeline(plain()).find((s) => s.key === "rules")!.headline).not.toMatch(/Custom rules/);
    expect(buildTimeline(custom()).find((s) => s.key === "rules")!.headline).toMatch(/Custom checks: 3 checks looked at the case, 1 flag\./);
    const one = custom();
    one.stages = one.stages.map((s) => (s.stage === "rules" ? { ...s, detail: { ...s.detail, custom_checks: 1, custom_flags: 0 } } : s));
    expect(buildTimeline(one).find((s) => s.key === "rules")!.headline).toMatch(/Custom checks: 1 check looked at the case, 0 flags\./);
  });
});

describe("custom flags", () => {
  it("a flag from a custom rule carries the Custom badge, the rule's name and its author; a flag with no run to read them from still does not crash", async () => {
    const c = custom();
    c.check_runs = c.check_runs.filter((r) => r.rule !== "cus_3f9a1c2b7d"); // the run is gone, the flag remains
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await expandAll();
    expect(screen.getAllByText("cus_3f9a1c2b7d").length).toBeGreaterThan(0);
  });

  it("a finding of a custom rule is marked, and a finding from a built-in check next to it is not", async () => {
    const user = userEvent.setup();
    const c = custom();
    render(
      <FindingsPanel
        findings={c.findings} numbered={numberFlags(c.findings, c.flags)} selectedFlagId={null} onSelectFlag={vi.fn()} onShowDocument={vi.fn()} onDecide={vi.fn()} canDecide
        partial={false} partialReasons={[]} coverage={c.coverage ?? null} documents={c.documents} files={c.files} caseId="adv-1" explainCase={c}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    const cards = screen.getAllByRole("listitem").filter((li) => li.id.startsWith("flag-"));
    const marked = cards.filter((li) => within(li).queryByText("Custom") !== null);
    expect(marked).toHaveLength(1);
    expect(within(marked[0]).getByText("Custom rule by alice (unreviewed)")).toBeInTheDocument();
    // a custom flag is never a hard stop, whatever else it says
    expect(within(marked[0]).queryByText(/Not eligible for the fast lane/)).toBeNull();
  });

  it("author and rule names with markup are text everywhere a custom flag is shown", async () => {
    const user = userEvent.setup();
    const c = custom();
    c.check_runs = c.check_runs.map((r) => (r.rule === "cus_3f9a1c2b7d" && r.custom ? { ...r, custom: { ...r.custom, name: `name ${IMG}`, author: "<b>eve</b>", readback: `readback ${SCRIPT}` } } : r));
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    await expandAll();
    expect(screen.getAllByText(/name <img/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/<b>eve<\/b>/).length).toBeGreaterThan(0);
    nothingInjected();
    cleanup();
    render(
      <FindingsPanel
        findings={c.findings} numbered={numberFlags(c.findings, c.flags)} selectedFlagId={null} onSelectFlag={vi.fn()} onShowDocument={vi.fn()} onDecide={vi.fn()} canDecide
        partial={false} partialReasons={[]} coverage={c.coverage ?? null} documents={c.documents} files={c.files} caseId="adv-2" explainCase={c}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Medium concern/ }));
    expect(screen.getByText(/Custom rule by <b>eve<\/b> \(unreviewed\)/)).toBeInTheDocument();
    nothingInjected();
  });
});

describe("custom rules and coverage", () => {
  /** Everything the coverage panel says apart from the one custom line. */
  function coverageText(c: CaseDetail): string {
    const { container, unmount } = renderWithSWR(<CoveragePanel c={c} />);
    container.querySelector('[data-testid="custom-coverage"]')?.remove();
    const text = container.textContent ?? "";
    unmount();
    return text;
  }

  it("changes none of the coverage figures when custom rules ran, were skipped or raised flags", () => {
    expect(coverageText(custom())).toBe(coverageText(plain()));
  });

  it("many skipped custom rules do not lower or touch coverage, nor add to 'did not run'", () => {
    const skipped = Array.from({ length: 12 }, (_, i) => run(`cus_${String(i).padStart(10, "0")}`, { status: "skipped", note: null, reason: "no usable document of the rule's type", code: "custom_no_document" }));
    const c = withRuns(skipped);
    expect(coverageText(c)).toBe(coverageText(plain()));
    renderWithSWR(<CoveragePanel c={c} />);
    expect(screen.getByTestId("custom-coverage")).toHaveTextContent("Custom checks: 0 ran, 12 skipped. Not counted in coverage.");
  });

  it("the coverage explainer counts only built-in checks as ran / skipped / failed", () => {
    const states = (c: CaseDetail) => {
      const { container, unmount } = renderWithSWR(<CoverageExplainer c={c} />);
      const t = container.textContent ?? "";
      unmount();
      return t;
    };
    expect(states(custom())).toBe(states(plain()));
  });

  it("says nothing about custom rules on a case where none ran", () => {
    renderWithSWR(<AnalysisTrailBody c={plain()} onGoCoverage={() => {}} />);
    expect(screen.queryByTestId("custom-runs-note")).toBeNull();
    expect(screen.queryByText(/Not counted in coverage/)).toBeNull();
  });

  it("uses the singular for one custom rule, and 'did not apply' for a skipped one", () => {
    const c = withRuns([run("cus_0000000001", { custom: runInfo({ rule_id: "cus_0000000001", name: "Only rule" }) })]);
    renderWithSWR(<AnalysisTrailBody c={c} onGoCoverage={() => {}} />);
    expect(screen.getByTestId("custom-runs-note")).toHaveTextContent("1 custom rule was used, 0 flags raised.");
  });

  it("owner-written names, authors and wording in the trail and coverage are text", () => {
    const c = withRuns([run("cus_0000000001", { custom: runInfo({ rule_id: "cus_0000000001", name: `name ${IMG}`, author: "<b>eve</b>", readback: `readback ${SCRIPT}` }) })]);
    renderWithSWR(<AnalysisTrailBody c={c} onGoCoverage={() => {}} />);
    const note = screen.getByTestId("custom-runs-note");
    expect(note).toHaveTextContent("name <img");
    expect(note).toHaveTextContent("<b>eve</b>");
    nothingInjected();
  });
});
