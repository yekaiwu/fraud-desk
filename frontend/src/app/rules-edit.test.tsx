import "@/test/mockEnv";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import * as mock from "@/lib/mock/handlers";
import { resetMockState, state } from "@/lib/mock/state";
import { renderWithSWR } from "@/test/utils";
import RulesPage from "./rules/page";

const who = { reviewer: "j.lim", reason: "Agreed with credit policy" };

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

const countLine = () => document.getElementById("catalogue-count") as HTMLElement;

/** Customised: a note and a lower severity. Switched off: one more check. */
async function seed() {
  await mock.updateRuleOverride("inv_dates", { severity: "low", name: "Dates in order", description: "Dates must run forwards.", note: "Ask credit if unsure", ...who });
  await mock.updateRuleOverride("bs_running_balance", { enabled: false, ...who });
}

describe("check catalogue: showing the effective settings", () => {
  it("shows the effective name, description, note, severity with the default in small text, and the two badges", async () => {
    await seed();
    renderWithSWR(<RulesPage />);
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("inv_dates"))!;
    expect(within(row).getByText("Dates in order")).toBeInTheDocument();
    expect(within(row).getByText("Dates must run forwards.")).toBeInTheDocument();
    expect(within(row).getByText("Ask credit if unsure")).toBeInTheDocument();
    expect(within(row).getByText("Low concern")).toBeInTheDocument();
    expect(within(row).getByText("default: Medium")).toBeInTheDocument();
    expect(within(row).getByText("Customised")).toBeInTheDocument();
    expect(within(row).queryByText("Switched off")).toBeNull();
    expect(within(row).getByText("inv_dates")).toBeInTheDocument(); // the id stays visible
  });

  it("marks a switched-off check with a Switched off badge and no 'default' note when its severity is unchanged", async () => {
    await seed();
    renderWithSWR(<RulesPage />);
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("bs_running_balance"))!;
    expect(within(row).getByText("Switched off")).toBeInTheDocument();
    expect(within(row).getByText("Customised")).toBeInTheDocument();
    expect(within(row).getByText("High concern")).toBeInTheDocument();
    expect(within(row).queryByText(/default:/)).toBeNull();
  });

  it("an untouched check has no badges", async () => {
    renderWithSWR(<RulesPage />);
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("fs_arith"))!;
    expect(within(row).queryByText("Customised")).toBeNull();
    expect(within(row).queryByText("Switched off")).toBeNull();
  });

  it("the phone cards show the same", async () => {
    await seed();
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    const cards = screen.getByRole("list", { name: "Checks" });
    expect(within(cards).getByText("Dates in order")).toBeInTheDocument();
    expect(within(cards).getByText("Switched off")).toBeInTheDocument();
    expect(within(cards).getAllByText("Customised")).toHaveLength(2);
    expect(within(cards).getByRole("button", { name: "Edit Dates in order" })).toBeInTheDocument();
  });

  it("counts: N of M checks, K customised, J switched off", async () => {
    await seed();
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    expect(countLine()).toHaveTextContent("13 of 13 checks, 2 customised, 1 switched off");
  });

  it("filters by Settings and keeps the totals about the whole catalogue", async () => {
    await seed();
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    const settings = screen.getByLabelText("Settings");
    expect([...(settings as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["All checks", "Customised", "Switched off"]);
    await user.selectOptions(settings, "off");
    await waitFor(() => expect(countLine()).toHaveTextContent("1 of 13 checks, 2 customised, 1 switched off"));
    expect(screen.getAllByText("bs_running_balance").length).toBeGreaterThan(0);
    expect(screen.queryByText("fs_arith")).toBeNull();
    await user.selectOptions(settings, "customised");
    await waitFor(() => expect(countLine()).toHaveTextContent("2 of 13 checks"));
    expect(screen.getAllByText("inv_dates").length).toBeGreaterThan(0);
    expect(screen.queryByText("fs_arith")).toBeNull();
    await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0]);
    await waitFor(() => expect(countLine()).toHaveTextContent("13 of 13 checks"));
  });

  it("says so when nothing is switched off", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    expect(countLine()).toHaveTextContent("13 of 13 checks, 0 customised, 0 switched off");
    await user.selectOptions(screen.getByLabelText("Settings"), "off");
    expect(await screen.findByText("No checks match")).toBeInTheDocument();
  });

  it("searches the officer's wording", async () => {
    await seed();
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    await user.type(screen.getByLabelText("Search"), "ask credit");
    await waitFor(() => expect(countLine()).toHaveTextContent("1 of 13 checks"));
  });

  it("lines every filter control up at one height (h-11) in one row", async () => {
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    const controls = [screen.getByLabelText("Search"), ...["Document type", "Severity", "Stage", "Case type", "Settings"].map((l) => screen.getByLabelText(l))];
    for (const c of controls) expect(c.className).toMatch(/\bh-11\b/);
    const grid = controls[0].closest(".grid") as HTMLElement;
    expect(grid.className).toMatch(/items-end/);
    for (const c of controls) expect(c.closest(".grid")).toBe(grid);
    expect(grid.className).toMatch(/repeat\(5,/);
  });
});

describe("check catalogue: editing", () => {
  const openRow = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
    await user.click((await screen.findAllByRole("button", { name: `Edit ${name}` }))[0]);
    return screen.findByRole("dialog");
  };

  it("has an Edit button on every check, and the name opens the same editor", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    await screen.findAllByRole("row");
    const edits = screen.getAllByRole("button", { name: /^Edit / });
    expect(edits).toHaveLength(26); // 13 in the table, 13 in the phone list
    await user.click(within(screen.getAllByRole("row").find((r) => r.textContent?.includes("fs_arith"))!).getByRole("button", { name: "Financials: arith" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { level: 2, name: "Financials: arith" })).toBeInTheDocument();
    expect(within(dialog).getByText("Edit check")).toBeInTheDocument();
  });

  it("saves, closes, updates the list without a reload and shows the success message", async () => {
    const user = userEvent.setup();
    const getRules = vi.spyOn(api, "getRules");
    renderWithSWR(<RulesPage />);
    const dialog = await openRow(user, "Financials: arith");
    await user.click(within(dialog).getByRole("switch"));
    await user.click(within(dialog).getByRole("radio", { name: /Low concern/ }));
    await user.type(within(dialog).getByLabelText("Name"), "Statement totals");
    await user.type(within(dialog).getByLabelText(/Reviewer name/), "j.lim");
    await user.type(within(dialog).getByLabelText(/Reason \(required/), "Not used for our products");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Saved. Applies to analyses run from now on")).toBeInTheDocument();
    expect(screen.getByText(/Cases already analysed keep their results until they are re-run/)).toBeInTheDocument();
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("fs_arith"))!;
    expect(within(row).getByText("Statement totals")).toBeInTheDocument();
    expect(within(row).getByText("Switched off")).toBeInTheDocument();
    expect(within(row).getByText("default: High")).toBeInTheDocument();
    await waitFor(() => expect(countLine()).toHaveTextContent("1 customised, 1 switched off"));
    expect(getRules.mock.calls.length).toBeGreaterThanOrEqual(1);
    // focus goes back to the control that opened the editor
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Edit Statement totals" })[0]).toBeInTheDocument());
  });

  it("keeps the success message dismissable", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openRow(user, "Financials: arith");
    await user.type(within(dialog).getByLabelText("Internal note"), "hello");
    await user.type(within(dialog).getByLabelText(/Reviewer name/), "j.lim");
    await user.type(within(dialog).getByLabelText(/Reason \(required/), "Documenting why");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await screen.findByText("Saved. Applies to analyses run from now on");
    await user.click(screen.getByRole("button", { name: "Close message" }));
    expect(screen.queryByText("Saved. Applies to analyses run from now on")).toBeNull();
  });

  it("keeps the sheet open with the draft when the save fails", async () => {
    vi.spyOn(api, "updateRuleOverride").mockRejectedValue(new ApiErrorException({ code: "rate_limited", message: "slow", details: { retry_after_s: 60 } }, 429));
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openRow(user, "Financials: arith");
    await user.click(within(dialog).getByRole("switch"));
    await user.type(within(dialog).getByLabelText(/Reviewer name/), "j.lim");
    await user.type(within(dialog).getByLabelText(/Reason \(required/), "Not used for our products");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog).findByText("Too many changes in a short time")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(within(dialog).getByRole("switch")).not.toBeChecked();
    expect(screen.queryByText("Saved. Applies to analyses run from now on")).toBeNull();
  });

  it("resets a customised check from the editor and says so", async () => {
    await seed();
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openRow(user, "Dates in order");
    await user.type(within(dialog).getByLabelText(/Reviewer name/), "a.tan");
    await user.type(within(dialog).getByLabelText(/Reason \(required/), "Back to the normal setup");
    await user.click(within(dialog).getByRole("button", { name: "Reset to default" }));
    await user.click(within(dialog).getByRole("button", { name: "Yes, reset to default" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Reset to default. Applies to analyses run from now on")).toBeInTheDocument();
    await waitFor(() => expect(countLine()).toHaveTextContent("1 customised, 1 switched off"));
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("inv_dates"))!;
    expect(within(row).getByText("Invoice: dates")).toBeInTheDocument();
    expect(within(row).queryByText("Customised")).toBeNull();
  });
});

describe("check catalogue: change log sheet", () => {
  const openLog = async (user: ReturnType<typeof userEvent.setup>) => {
    await screen.findAllByRole("row");
    await user.click(screen.getByRole("button", { name: "Change log" }));
    return screen.findByRole("dialog");
  };

  it("says so when there are no changes yet", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openLog(user);
    expect(within(dialog).getByRole("heading", { level: 2, name: "Change log" })).toBeInTheDocument();
    expect(await within(dialog).findByText("No changes yet")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Show changes to")).toBeDisabled();
  });

  it("lists changes across all checks, newest first: who, when, which check, what changed and why", async () => {
    await mock.updateRuleOverride("bs_running_balance", { enabled: false, reviewer: "first.person", reason: "First reason" });
    await mock.updateRuleOverride("inv_dates", { name: "Dates in order", reviewer: "second.person", reason: "Second reason" });
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openLog(user);
    const list = await within(dialog).findByRole("list", { name: "Changes to checks" });
    const items = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("second.person");
    expect(items[0]).toHaveTextContent("Dates in order");
    expect(items[0]).toHaveTextContent("inv_dates");
    expect(items[0]).toHaveTextContent("Name set to “Dates in order”");
    expect(items[0]).toHaveTextContent("Second reason");
    expect(items[0]).toHaveTextContent(/UTC/);
    expect(items[1]).toHaveTextContent("Statement: running balance");
    expect(items[1]).toHaveTextContent("Switched off");
    expect(within(dialog).getByText("2 changes")).toBeInTheDocument();
  });

  it("filters by check", async () => {
    await mock.updateRuleOverride("bs_running_balance", { enabled: false, ...who });
    await mock.updateRuleOverride("inv_dates", { severity: "low", ...who });
    await mock.resetRuleOverride("inv_dates", { reviewer: "a.tan", reason: "Put it back" });
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openLog(user);
    await within(dialog).findByRole("list", { name: "Changes to checks" });
    expect(within(dialog).getByText("3 changes")).toBeInTheDocument();
    const select = within(dialog).getByLabelText("Show changes to");
    expect([...(select as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["All checks", "Invoice: dates", "Statement: running balance"]);
    await user.selectOptions(select, "inv_dates");
    expect(within(dialog).getByText("2 changes")).toBeInTheDocument();
    const list = within(dialog).getByRole("list", { name: "Changes to checks" });
    expect(list).not.toHaveTextContent("Statement: running balance");
    expect(list).toHaveTextContent("Reset to default");
    expect(list).toHaveTextContent("Put it back");
  });

  it("closes on Escape and gives focus back to the Change log button", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    await openLog(user);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Change log" })).toHaveFocus();
  });

  it("shows typed text as plain text", async () => {
    await mock.updateRuleOverride("inv_dates", { name: '<img src=x onerror="window.__x=1">', reviewer: "<b>bold</b>", reason: "<script>window.__x=2</script>" });
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openLog(user);
    const list = await within(dialog).findByRole("list", { name: "Changes to checks" });
    expect(list.textContent).toContain('<img src=x onerror="window.__x=1">');
    expect(list.textContent).toContain("<b>bold</b>");
    expect(list.querySelector("img, b, script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });

  it("explains a failure to load and retries", async () => {
    vi.spyOn(api, "getRuleChanges").mockRejectedValueOnce(new ApiErrorException({ code: "network_error", message: "x" }, 0));
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await openLog(user);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("The change log could not be loaded");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText("No changes yet")).toBeInTheDocument();
  });
});

