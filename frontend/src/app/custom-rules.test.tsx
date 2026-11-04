import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { adopted, json, routedFetch, ruleView, shared, ticket } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";
import CustomRulesPage from "./rules/custom/page";

const me = (over: Record<string, unknown> = {}) => () => json({ reviewer: "alice", admin: false, custom_rules: true, ...over });

function setup(routes: Parameters<typeof routedFetch>[0] = {}) {
  const f = routedFetch({
    "GET /me": me(),
    "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [adopted()] }),
    "GET /custom-rules/shared": () => json({ rules: [shared()] }),
    "GET /custom-rules/tickets": () => json({ tickets: [ticket()] }),
    ...routes,
  });
  vi.stubGlobal("fetch", f.fn);
  renderWithSWR(<CustomRulesPage />);
  return { f, user: userEvent.setup() };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Custom rules page", () => {
  it("says custom rules are not available when the server has them off, and fetches no rules", async () => {
    const { f } = setup({ "GET /me": me({ custom_rules: false }) });
    expect(await screen.findByText("Custom rules are not available")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(f.calls.some((c) => c.path.startsWith("/custom-rules"))).toBe(false);
  });

  it("says the same when nobody is signed in by account", async () => {
    setup({ "GET /me": me({ reviewer: null, custom_rules: false }) });
    expect(await screen.findByText("Custom rules are not available")).toBeInTheDocument();
  });

  it("shows the four tabs with the assistant first", async () => {
    setup();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["New rule", "My rules", "Public rules", "Requests to the tech team"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { level: 1, name: "Custom rules" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Describe the check in plain English/)).toBeVisible();
  });

  it("opens each tab with its content", async () => {
    const { user } = setup();
    await user.click(await screen.findByRole("tab", { name: "My rules" }));
    expect(await screen.findByRole("heading", { name: "Heavy borrowing" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Expired passport" })).toBeVisible(); // adopted
    await user.click(screen.getByRole("tab", { name: "Public rules" }));
    expect(await screen.findByText("Written by officers, not reviewed by the development team")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Requests to the tech team" }));
    expect(await screen.findByText("crt_9c0d1e2f3a4b")).toBeVisible();
  });

  it("keeps a half-written request when the officer looks at another tab and comes back", async () => {
    const { user } = setup();
    const box = await screen.findByLabelText(/Describe the check in plain English/);
    await user.type(box, "flag invoices over a hundred thousand");
    await user.click(screen.getByRole("tab", { name: "My rules" }));
    await user.click(screen.getByRole("tab", { name: "New rule" }));
    expect(screen.getByLabelText(/Describe the check in plain English/)).toHaveValue("flag invoices over a hundred thousand");
  });

  it("a new version of a rule opens the assistant for that rule", async () => {
    const { user } = setup();
    await user.click(await screen.findByRole("tab", { name: "My rules" }));
    await user.click(await screen.findByRole("button", { name: "New version of Heavy borrowing" }));
    expect(screen.getByRole("tab", { name: "New rule" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("New version of Heavy borrowing")).toBeVisible();
  });

  it("an administrator sees every account's requests marked with the account", async () => {
    const { user } = setup({ "GET /me": me({ admin: true }), "GET /custom-rules/tickets": () => json({ tickets: [ticket({ account: "carol" })] }) });
    await user.click(await screen.findByRole("tab", { name: "Requests to the tech team" }));
    expect(await screen.findByText(/carol,/)).toBeInTheDocument();
  });

  it("goes to My rules from the saved screen", async () => {
    const { user } = setup({
      "POST /custom-rules/drafts": () => json({ draft_id: "crd_5b2e9f0a1c3d", turn: 1, turns_left: 5, status: "ready", understood: "", spec: ruleView().spec, spec_hash: "h", readback: "Flags medium when X.", candidates: [], questions: [], assumptions: [], phrase_map: [], unsupported_parts: [], closest_templates: [], will_check: [], will_not_check: [], refusal: null, duplicates: [], ideas: [], ticket: null, notes: [], dry_run: null, save_allowed: true, needs_narrower_ack: false, edit_of: null, model_calls: 1 }),
      "POST /custom-rules/drafts/crd_5b2e9f0a1c3d/dry-run": () => json({ spec_hash: "h", cases_considered: 1, evaluated: 1, fired: 0, skipped_missing: 0, scanned_evaluated: 0, scanned_fired: 0, fire_rate: 0, warnings: [], examples: [], needs_confirmation: false }),
      "POST /custom-rules": () => json({ rule: ruleView(), ticket: null }, 201),
    });
    await user.type(await screen.findByLabelText(/Describe the check in plain English/), "flag borrowings over twice equity");
    await user.click(screen.getByRole("button", { name: /Ask the assistant/ }));
    await screen.findByText(/Evaluated 1 stored case/);
    await user.type(screen.getByLabelText("Name"), "Heavy borrowing");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "Loan desk asked");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    await user.click(await screen.findByRole("button", { name: "Go to my rules" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "My rules" })).toHaveAttribute("aria-selected", "true"));
    expect(within(screen.getByRole("tabpanel", { name: "My rules" })).getByRole("heading", { name: "Heavy borrowing" })).toBeVisible();
  });
});
