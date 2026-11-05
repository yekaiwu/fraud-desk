import "@/test/mockEnv";
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { AgentReport, CaseDetail } from "@/lib/types";
import { renderWithSWR } from "@/test/utils";
import { CaseTimeline } from "./CaseTimeline";

afterEach(cleanup);

const team: AgentReport[] = [
  { agent: "finance", role: "Arithmetic", status: "ok", checks_run: 22, flag_ids: ["flag-a"], summary: "Ran 22 checks.", verdicts: [], next_steps: [], handoff: "", cost_usd: "0", seconds: 0 },
  {
    agent: "challenger", role: "Argues the other side", status: "ok", checks_run: 0, flag_ids: ["flag-a"], summary: "Ruled on 1 flag(s); lowered 0, kept 1.", next_steps: [], handoff: "", cost_usd: "0.0017", seconds: 3.7,
    verdicts: [{ flag_id: "flag-a", verdict: "upheld", action: "kept", reason: "No passage in the documents explains it." }],
  },
  { agent: "case_officer", role: "Briefs the reviewer", status: "ok", checks_run: 0, flag_ids: [], summary: "Two things stand out.", verdicts: [], next_steps: ["Ask for the original statement."], handoff: "", cost_usd: "0.0008", seconds: 5.7 },
];

const fraud = (): CaseDetail => ({ ...buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!), agents: team });

describe("CaseTimeline", () => {
  it("lists every step in order, with times and what each produced", () => {
    renderWithSWR(<CaseTimeline c={fraud()} onShowFlag={() => {}} />);
    const list = screen.getByRole("list", { name: "Steps of this case, in order" });
    const titles = within(list).getAllByRole("button", { expanded: undefined }).map((b) => b.textContent ?? "");
    expect(titles[0]).toContain("Case received");
    const joined = titles.join(" | ");
    expect(joined.indexOf("Comparing documents")).toBeLessThan(joined.indexOf("The review team"));
    expect(joined.indexOf("The review team")).toBeLessThan(joined.indexOf("Scoring the case"));
  });

  it("opens the review team step by default and shows the challenger's ruling and the officer's next steps", () => {
    renderWithSWR(<CaseTimeline c={fraud()} onShowFlag={() => {}} />);
    expect(screen.getByText("No passage in the documents explains it.")).toBeInTheDocument();
    expect(screen.getByText(/Upheld, kept/)).toBeInTheDocument();
    expect(screen.getByText("Ask for the original statement.")).toBeInTheDocument();
  });

  it("opens and closes a step, and expands or collapses them all", async () => {
    const user = userEvent.setup();
    renderWithSWR(<CaseTimeline c={fraud()} onShowFlag={() => {}} />);
    // at first only the steps that raised flags and the team are open
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByText("No passage in the documents explains it.")).toBeNull();
    const team = screen.getByRole("button", { name: /The review team/ });
    expect(team).toHaveAttribute("aria-expanded", "false");
    await user.click(team);
    expect(team).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("No passage in the documents explains it.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByRole("button", { name: "Collapse all" })).toBeInTheDocument();
  });

  it("shows the flags a step raised and goes to one on the page", async () => {
    const user = userEvent.setup();
    const onShowFlag = vi.fn();
    const c = fraud();
    renderWithSWR(<CaseTimeline c={c} onShowFlag={onShowFlag} />);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    const buttons = screen.getAllByRole("button", { name: "Show on document" });
    expect(buttons.length).toBeGreaterThan(0);
    await user.click(buttons[0]);
    expect(onShowFlag).toHaveBeenCalledWith(expect.any(String));
  });

  it("tells what the checks of a step did, with the ones that did not run last", async () => {
    const user = userEvent.setup();
    renderWithSWR(<CaseTimeline c={fraud()} onShowFlag={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getAllByRole("table", { name: "Checks of this step" }).length).toBeGreaterThan(0);
  });

  it("has a download of the whole timeline as JSON", () => {
    renderWithSWR(<CaseTimeline c={fraud()} onShowFlag={() => {}} />);
    expect(screen.getByRole("button", { name: /Download as JSON/ })).toBeInTheDocument();
  });
});

describe("CaseTimeline with long unbroken text", () => {
  it("lets step cards, headlines, facts and flag text shrink and wrap (no sideways scroll at 375 px)", async () => {
    const user = userEvent.setup();
    const c = fraud();
    const long = "x".repeat(120);
    c.flags = c.flags.map((f, i) => (i === 0 ? { ...f, explanation: long } : f));
    renderWithSWR(<CaseTimeline c={c} onShowFlag={() => {}} />);
    const list = screen.getByRole("list", { name: "Steps of this case, in order" });
    for (const li of Array.from(list.children)) expect(li.className).toContain("min-w-0");
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    const wrapped = Array.from(list.querySelectorAll("span, dd, li")).filter((e) => e.textContent === long || e.textContent?.includes(long));
    expect(wrapped.some((e) => e.className.includes("wrap-anywhere"))).toBe(true);
    const headlines = Array.from(list.querySelectorAll("li > div > button > span > span.block")).filter((e) => e.className.includes("text-ink-2"));
    expect(headlines.length).toBeGreaterThan(0);
    for (const h of headlines) expect(h.className).toContain("wrap-anywhere");
  });
});
