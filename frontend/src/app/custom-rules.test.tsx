import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { adopted, json, routedFetch, ruleView, shared, ticket } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";
import RulesPage from "./rules/page";

const me = (over: Record<string, unknown> = {}) => () => json({ reviewer: "alice", admin: false, custom_rules: true, ...over });
const DRAFT = { draft_id: "crd_5b2e9f0a1c3d", turn: 1, turns_left: 5, status: "ready", understood: "", spec: ruleView().spec, spec_hash: "h", readback: "Flags medium when X.", candidates: [], questions: [], assumptions: [], phrase_map: [], unsupported_parts: [], closest_templates: [], will_check: [], will_not_check: [], refusal: null, duplicates: [], ideas: [], ticket: null, notes: [], dry_run: null, save_allowed: true, needs_narrower_ack: false, edit_of: null, model_calls: 1 };
const DRY = { spec_hash: "h", cases_considered: 1, evaluated: 1, fired: 0, skipped_missing: 0, scanned_evaluated: 0, scanned_fired: 0, fire_rate: 0, warnings: [], examples: [], needs_confirmation: false };

function setup(routes: Parameters<typeof routedFetch>[0] = {}) {
  const f = routedFetch({
    "GET /me": me(),
    "GET /rules": () => json({ rules: [] }),
    "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [adopted()] }),
    "GET /custom-rules/shared": () => json({ rules: [shared()] }),
    "GET /custom-rules/tickets": () => json({ tickets: [ticket()] }),
    ...routes,
  });
  vi.stubGlobal("fetch", f.fn);
  renderWithSWR(<RulesPage />);
  return { f, user: userEvent.setup() };
}
const create = () => screen.findByRole("button", { name: "Create custom check" });
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Custom checks on the check catalogue page", () => {
  it("shows the button and the section when the server has custom rules on", async () => {
    setup();
    expect(await create()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change log" })).toBeInTheDocument();
    const section = await screen.findByRole("region", { name: "Custom checks" });
    expect(section.querySelector(".label-caps")).toHaveTextContent("Written by officers, not reviewed by the development team");
  });

  it.each([
    ["the server has them off", { custom_rules: false }],
    ["an older server does not report it", { custom_rules: undefined }],
    ["nobody is signed in by account", { reviewer: null, custom_rules: false }],
  ])("shows no button and no section, mentions custom checks nowhere and asks for no custom data, when %s", async (_why, over) => {
    const { f } = setup({ "GET /me": me(over) });
    await waitFor(() => expect(f.count("GET /me")).toBeGreaterThan(0));
    await screen.findByRole("button", { name: "Change log" });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: "Create custom check" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Custom checks" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/custom (check|rule)/i);
    expect(f.calls.some((c) => c.path.startsWith("/custom-rules"))).toBe(false);
  });

  it("shows no button while /me is loading or when it fails", async () => {
    const { f } = setup({ "GET /me": () => new Promise<Response>(() => {}) });
    await screen.findByRole("button", { name: "Change log" });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: "Create custom check" })).toBeNull();
    expect(f.calls.some((c) => c.path.startsWith("/custom-rules"))).toBe(false);
    cleanup();
    vi.unstubAllGlobals();
    setup({ "GET /me": () => json({ error: { code: "internal_error", message: "boom" } }, 500) });
    await screen.findByRole("button", { name: "Change log" });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: "Create custom check" })).toBeNull();
  });

  it("shows the three tabs, with My checks first", async () => {
    setup();
    const section = await screen.findByRole("region", { name: "Custom checks" });
    const tabs = within(section).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["My checks", "Public checks", "Requests to the tech team"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  });

  it("opens each tab with its content", async () => {
    const { user } = setup();
    expect(await screen.findByRole("heading", { name: "Heavy borrowing" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Expired passport" })).toBeVisible(); // adopted
    await user.click(screen.getByRole("tab", { name: "Public checks" }));
    expect(await screen.findByRole("heading", { name: "Expired passport" })).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Requests to the tech team" }));
    expect(await screen.findByText("crt_9c0d1e2f3a4b")).toBeVisible();
  });

  it("an administrator sees every account's requests marked with the account", async () => {
    const { user } = setup({ "GET /me": me({ admin: true }), "GET /custom-rules/tickets": () => json({ tickets: [ticket({ account: "carol" })] }) });
    await user.click(await screen.findByRole("tab", { name: "Requests to the tech team" }));
    expect(await screen.findByText(/carol,/)).toBeInTheDocument();
  });

  it("the button opens the popup with the assistant, and the popup is not there before", async () => {
    const { user } = setup();
    const button = await create();
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(button);
    const dialog = await screen.findByRole("dialog", { name: "Create a custom check" });
    expect(within(dialog).getByLabelText(/Describe the check in plain English/)).toBeVisible();
  });

  it("closes on Escape and on Close, and throws an unsaved draft away", async () => {
    const { user } = setup();
    await user.click(await create());
    await user.type(await screen.findByLabelText(/Describe the check in plain English/), "flag invoices over a hundred thousand");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Create custom check" }));
    expect(await screen.findByLabelText(/Describe the check in plain English/)).toHaveValue("");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("the empty My checks tab offers the same popup", async () => {
    const { user } = setup({ "GET /custom-rules": () => json({ mine: [], adopted: [] }) });
    await user.click(await screen.findByRole("button", { name: "Create a custom check" }));
    expect(await screen.findByRole("dialog", { name: "Create a custom check" })).toBeInTheDocument();
  });

  it("a new version of a check opens the popup in edit mode for that check", async () => {
    const { user } = setup();
    await user.click(await screen.findByRole("button", { name: "New version of Heavy borrowing" }));
    const dialog = await screen.findByRole("dialog", { name: "Create a custom check" });
    expect(within(dialog).getByText("New version of Heavy borrowing")).toBeVisible();
  });

  it("writes a check inside the popup, keeps the saved view there, and 'Go to my checks' closes it onto the My checks tab", async () => {
    let saved = false;
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: saved ? [ruleView()] : [], adopted: [] }),
      "POST /custom-rules/drafts": () => json(DRAFT),
      "POST /custom-rules/drafts/crd_5b2e9f0a1c3d/dry-run": () => json(DRY),
      "POST /custom-rules": () => ((saved = true), json({ rule: ruleView(), ticket: null }, 201)),
    });
    await user.click(await screen.findByRole("tab", { name: "Public checks" })); // somewhere else first
    await user.click(await create());
    await user.type(await screen.findByLabelText(/Describe the check in plain English/), "flag borrowings over twice equity");
    await user.click(screen.getByRole("button", { name: /Ask the assistant/ }));
    await screen.findByText(/Evaluated 1 stored case/);
    await user.type(screen.getByLabelText("Name"), "Heavy borrowing");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "Loan desk asked");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    const dialog = screen.getByRole("dialog", { name: "Create a custom check" });
    expect(await within(dialog).findByText("Saved: Heavy borrowing")).toBeVisible(); // stays in the popup
    await waitFor(() => expect(f.count("GET /custom-rules")).toBeGreaterThan(1)); // the list was refreshed
    await user.click(within(dialog).getByRole("button", { name: "Go to my checks" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("tab", { name: "My checks" })).toHaveAttribute("aria-selected", "true");
    expect(within(screen.getByRole("tabpanel", { name: "My checks" })).getByRole("heading", { name: "Heavy borrowing" })).toBeVisible();
    await waitFor(() => expect(screen.getByRole("region", { name: "Custom checks" }).parentElement).toHaveFocus());
  });

  it("opening the popup again after a save starts a fresh request", async () => {
    const { user } = setup({
      "POST /custom-rules/drafts": () => json(DRAFT),
      "POST /custom-rules/drafts/crd_5b2e9f0a1c3d/dry-run": () => json(DRY),
      "POST /custom-rules": () => json({ rule: ruleView(), ticket: null }, 201),
    });
    await user.click(await create());
    await user.type(await screen.findByLabelText(/Describe the check in plain English/), "flag borrowings over twice equity");
    await user.click(screen.getByRole("button", { name: /Ask the assistant/ }));
    await screen.findByText(/Evaluated 1 stored case/);
    await user.type(screen.getByLabelText("Name"), "Heavy borrowing");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "Loan desk asked");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    await screen.findByText("Saved: Heavy borrowing");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Create custom check" }));
    expect(await screen.findByLabelText(/Describe the check in plain English/)).toHaveValue("");
    expect(screen.queryByText("Saved: Heavy borrowing")).toBeNull();
  });

  it("never calls the model route (a paid call) just by opening the page, the popup or the tabs", async () => {
    const { f, user } = setup();
    await user.click(await create());
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    for (const name of ["Public checks", "Requests to the tech team", "My checks"]) await user.click(screen.getByRole("tab", { name }));
    await new Promise((r) => setTimeout(r, 30));
    expect(f.calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});
