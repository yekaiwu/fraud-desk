import "@/test/mockEnv";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { resetMockState, state } from "@/lib/mock/state";
import { ApiErrorException } from "@/lib/errors";
import type { EffectivenessResponse, RuleEffectiveness } from "@/lib/types";
import { renderWithSWR } from "@/test/utils";
import EffectivenessPage from "./effectiveness/page";
import RulesPage from "./rules/page";

const rule = (o: Partial<RuleEffectiveness>): RuleEffectiveness => ({
  rule: "r", stage: "rules", default_severity: "medium", description: "d", cases_fired: 1, times_fired: 1, confirmed: 0, dismissed: 0,
  pending: 0, open: 0, dismissal_rate: null, median_seconds_to_decide: null, cases_ran: 1, cases_skipped: 0, ...o,
});
const resp = (rules: RuleEffectiveness[], decided = 5): EffectivenessResponse => ({ generated_for: { use_case: null, since: null }, cases_total: 9, cases_decided: decided, median_seconds_to_verdict: 540, rules });

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("effectiveness page", () => {
  it("shows the empty message when nothing is decided but keeps the table", async () => {
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(resp([rule({ rule: "bs_a", times_fired: 0, cases_fired: 0 }), rule({ rule: "bs_b", times_fired: 2 })], 0));
    renderWithSWR(<EffectivenessPage />);
    expect(await screen.findByText("No decisions recorded yet")).toBeInTheDocument();
    expect(screen.getByText(/Rule statistics appear after reviewers confirm or dismiss flags/)).toBeInTheDocument();
    expect(screen.getAllByText("bs_b").length).toBeGreaterThan(0);
  });

  it("shows summary tiles computed only from the response", async () => {
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(
      resp([
        rule({ rule: "a", times_fired: 4, confirmed: 3, dismissed: 1, dismissal_rate: 0.25 }),
        rule({ rule: "b", times_fired: 2, confirmed: 1, dismissed: 3, dismissal_rate: 0.75 }),
        rule({ rule: "c", times_fired: 0, cases_fired: 0 }),
      ]),
    );
    renderWithSWR(<EffectivenessPage />);
    const group = await screen.findByRole("group", { name: "Summary" });
    expect(within(group).getByText("Cases decided").nextElementSibling).toHaveTextContent("5");
    expect(within(group).getByText("of 9 cases in this view")).toBeInTheDocument();
    expect(within(group).getByText("Checks that fired").nextElementSibling).toHaveTextContent("2");
    expect(within(group).getByText("of 3 checks, 6 flags raised")).toBeInTheDocument();
    expect(within(group).getByText("50% confirmed")).toBeInTheDocument(); // 4 of 8 decisions
    expect(within(group).getByText("4 confirmed, 4 dismissed")).toBeInTheDocument();
    expect(within(group).getByText("9 min")).toBeInTheDocument();
  });

  it("flags checks that analysts mostly dismiss in a 'worth a closer look' panel", async () => {
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(resp([rule({ rule: "bs_pass_through", times_fired: 8, confirmed: 2, dismissed: 6, dismissal_rate: 0.75 })]));
    renderWithSWR(<EffectivenessPage />);
    const panel = await screen.findByRole("region", { name: "Worth a closer look" });
    expect(panel).toHaveTextContent("Statement: pass through");
    expect(panel).toHaveTextContent("75% dismissed of 8 decided");
  });

  it("sorts from the column headers, with aria-sort, and toggles direction", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(
      resp([rule({ rule: "b_rule", times_fired: 9, median_seconds_to_decide: 600 }), rule({ rule: "a_rule", times_fired: 3, median_seconds_to_decide: 60 })]),
    );
    renderWithSWR(<EffectivenessPage />);
    const table = await screen.findByRole("table", { name: "Checks that fired" });
    const first = () => within(table).getAllByRole("row")[1];
    expect(first()).toHaveTextContent("b_rule");
    expect(within(table).getByRole("columnheader", { name: /Times fired/ })).toHaveAttribute("aria-sort", "descending");
    await user.click(within(table).getByRole("button", { name: /^Time to decide/ }));
    expect(first()).toHaveTextContent("a_rule"); // quickest first
    expect(within(table).getByRole("columnheader", { name: /Time to decide/ })).toHaveAttribute("aria-sort", "ascending");
    await user.click(within(table).getByRole("button", { name: /^Time to decide/ }));
    expect(first()).toHaveTextContent("b_rule");
    await user.click(within(table).getByRole("button", { name: /Check/ }));
    expect(first()).toHaveTextContent("a_rule");
  });

  it("shows the plain name first with the rule id as small secondary text", async () => {
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(resp([rule({ rule: "bs_running_balance", description: "Balances add up." })]));
    renderWithSWR(<EffectivenessPage />);
    const table = await screen.findByRole("table", { name: "Checks that fired" });
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Statement: running balance");
    expect(row).toHaveTextContent("Balances add up.");
    expect(within(row).getByText("bs_running_balance").tagName).toBe("CODE");
  });

  it("marks Noisy? in text, text-equivalents the bars, groups never-fired rules collapsed", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(
      resp([
        rule({ rule: "noisy_rule", times_fired: 6, cases_fired: 6, confirmed: 3, dismissed: 3, dismissal_rate: 0.5 }),
        rule({ rule: "good_rule", times_fired: 7, cases_fired: 6, confirmed: 5, dismissed: 1, pending: 0, open: 1, dismissal_rate: 0.1667 }),
        rule({ rule: "silent_rule", times_fired: 0, cases_fired: 0 }),
      ]),
    );
    renderWithSWR(<EffectivenessPage />);
    const table = await screen.findByRole("table", { name: "Checks that fired" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("good_rule"); // most fired first
    expect(rows[0]).toHaveTextContent("5 confirmed, 1 dismissed, 0 pending, 1 open");
    expect(rows[0]).not.toHaveTextContent("Noisy?");
    expect(rows[1]).toHaveTextContent("Noisy?");
    expect(rows[1]).toHaveTextContent("50%");
    expect(screen.queryByText("silent_rule")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Never fired \(1\)/ }));
    expect((await screen.findAllByText("silent_rule")).length).toBeGreaterThan(0);
    await user.selectOptions(screen.getByLabelText("Sort by"), "dismissal_rate");
    expect(within(screen.getByRole("table", { name: "Checks that fired" })).getAllByRole("row")[1]).toHaveTextContent("noisy_rule");
  });

  it("downloads a CSV of the current rules", async () => {
    const user = userEvent.setup();
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob | MediaSource) => (blobs.push(b as Blob), "blob:x"));
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.spyOn(api, "getEffectiveness").mockResolvedValue(resp([rule({ rule: "a_rule", times_fired: 2 }), rule({ rule: "b_rule", times_fired: 0 })]));
    renderWithSWR(<EffectivenessPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: /Download CSV/ }));
    expect(blobs).toHaveLength(1);
    const text = await blobs[0].text();
    expect(text.trim().split("\n")).toHaveLength(3);
    expect(text).toContain("a_rule");
    expect(text).toContain("b_rule");
  });

  it("shows an error with retry, and renders mock data by default", async () => {
    const spy = vi.spyOn(api, "getEffectiveness").mockRejectedValueOnce(new ApiErrorException({ code: "network_error", message: "x" }));
    const user = userEvent.setup();
    renderWithSWR(<EffectivenessPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cannot reach the API");
    spy.mockRestore();
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: "Checks that fired" })).toBeInTheDocument();
  });
});

describe("rules page", () => {
  it("lists readable checks and filters by stage", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    expect((await screen.findAllByText("bs_running_balance")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Statement: running balance").length).toBeGreaterThan(0);
    await user.selectOptions(screen.getByLabelText("Stage"), "forensics");
    await waitFor(() => expect(screen.queryByText("bs_running_balance")).toBeNull());
    expect(screen.getAllByText("pdf_editor_software").length).toBeGreaterThan(0);
  });

  it("searches, filters by document type and severity, and recovers from an empty result", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    await screen.findAllByText("fs_arith");
    await user.selectOptions(screen.getByLabelText("Document type"), "financial_statement");
    await waitFor(() => expect(screen.queryByText("x_directors")).toBeNull());
    expect(screen.getAllByText("fs_arith").length).toBeGreaterThan(0);
    await user.selectOptions(screen.getByLabelText("Severity"), "low");
    expect(await screen.findByText("No checks match")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0]);
    expect((await screen.findAllByText("x_directors")).length).toBeGreaterThan(0);
    await user.type(screen.getByLabelText("Search"), "edited balance");
    await waitFor(() => expect(screen.queryByText("fs_arith")).toBeNull());
    expect(screen.getAllByText("bs_running_balance").length).toBeGreaterThan(0);
  });

  it("shows what each check catches and where it applies as chips", async () => {
    renderWithSWR(<RulesPage />);
    await screen.findAllByText("bs_running_balance");
    expect(screen.getAllByText("edited balance").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bank statement").length).toBeGreaterThan(0);
    expect(screen.getAllByText("High concern").length).toBeGreaterThan(0);
  });
});
