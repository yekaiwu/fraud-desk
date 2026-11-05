import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SWRConfig } from "swr";
import { adopted, errorBody, json, routedFetch, ruleView } from "@/test/customRulesFixtures";
import { MyCustomRules } from "./MyCustomRules";

function setup(routes: Parameters<typeof routedFetch>[0]) {
  const f = routedFetch({ "GET /custom-rules": () => json({ mine: [], adopted: [] }), ...routes });
  vi.stubGlobal("fetch", f.fn);
  const props = { onEdit: vi.fn(), onNew: vi.fn() };
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MyCustomRules {...props} />
    </SWRConfig>,
  );
  return { f, user: userEvent.setup(), ...props };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const card = (name: string) => screen.getByRole("heading", { name }).closest("li") as HTMLElement;

describe("My custom rules", () => {
  it("says so when there are none and offers to write one", async () => {
    const { user, onNew } = setup({});
    expect(await screen.findByText("You have not written a custom check yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create a custom check" }));
    expect(onNew).toHaveBeenCalled();
    expect(screen.getByText(/You have not adopted any public check/)).toBeInTheDocument();
  });

  it("lists my rules with the Custom badge, version, severity, state, visibility and the readback", async () => {
    setup({ "GET /custom-rules": () => json({ mine: [ruleView(), ruleView({ rule_id: "cus_aaaaaaaaaa", name: "Old passports", enabled: false, visibility: "public", published_version: 1, version: 2, adoptions: 3, severity: "low" })], adopted: [] }) });
    const a = await screen.findByRole("heading", { name: "Heavy borrowing" });
    const first = a.closest("li") as HTMLElement;
    expect(within(first).getByText("Custom")).toBeInTheDocument();
    expect(within(first).getByText("Medium concern")).toBeInTheDocument();
    expect(within(first).getByText("On")).toBeInTheDocument();
    expect(within(first).getByText("Private")).toBeInTheDocument();
    expect(within(first).getByText(/Flags medium when, on a financial statement/)).toBeInTheDocument();
    expect(within(first).getByText("Version 1")).toBeInTheDocument();
    const second = card("Old passports");
    expect(within(second).getByText("Switched off")).toBeInTheDocument();
    expect(within(second).getByText("Public (v1)")).toBeInTheDocument();
    expect(within(second).getByText("3 accounts use it")).toBeInTheDocument();
    // v2 is newer than the public v1: say so and offer to publish it
    expect(within(second).getByText(/Version 2 is private. Others still get version 1/)).toBeInTheDocument();
    expect(within(second).getByRole("button", { name: /Make version 2 public/ })).toBeInTheDocument();
  });

  it("switches a rule off only with a reason, and sends exactly that", async () => {
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [] }),
      "POST /custom-rules/cus_3f9a1c2b7d/enabled": () => json({ rule: ruleView({ enabled: false }) }),
    });
    await screen.findByRole("heading", { name: "Heavy borrowing" });
    await user.click(screen.getByRole("button", { name: "Switch off Heavy borrowing" }));
    const dialog = await screen.findByRole("dialog");
    const go = within(dialog).getByRole("button", { name: "Switch off" });
    expect(go).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Reason"), "abc");
    expect(go).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Reason"), "de");
    await user.click(go);
    expect(await screen.findByText("Heavy borrowing is switched off.")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path.endsWith("/enabled"))?.body).toEqual({ enabled: false, reason: "abcde" });
  });

  it("switches an off rule on", async () => {
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: [ruleView({ enabled: false })], adopted: [] }),
      "POST /custom-rules/cus_3f9a1c2b7d/enabled": () => json({ rule: ruleView() }),
    });
    await user.click(await screen.findByRole("button", { name: "Switch on Heavy borrowing" }));
    await user.type(within(await screen.findByRole("dialog")).getByLabelText("Reason"), "Needed again");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Switch on" }));
    await screen.findByText("Heavy borrowing is on.");
    expect(f.calls.find((c) => c.path.endsWith("/enabled"))?.body).toEqual({ enabled: true, reason: "Needed again" });
  });

  it("makes a rule public after telling the officer what that means, and takes it back", async () => {
    let visibility: "private" | "public" = "private";
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: [ruleView({ visibility, published_version: visibility === "public" ? 1 : null })], adopted: [] }),
      "POST /custom-rules/cus_3f9a1c2b7d/publish": () => ((visibility = "public"), json({ rule: ruleView({ visibility: "public", published_version: 1 }) })),
      "POST /custom-rules/cus_3f9a1c2b7d/unpublish": () => ((visibility = "private"), json({ rule: ruleView() })),
    });
    await user.click(await screen.findByRole("button", { name: "Make public Heavy borrowing" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Every signed-in account will see/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Nothing changes for anyone until they adopt it/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Reason"), "Share with the desk");
    await user.click(within(dialog).getByRole("button", { name: "Make public" }));
    expect(await screen.findByText(/is public. Other accounts can adopt it/)).toBeInTheDocument();
    expect(f.calls.find((c) => c.path.endsWith("/publish"))?.body).toEqual({ reason: "Share with the desk" });
    await waitFor(() => expect(screen.getByText("Public (v1)")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Take back Heavy borrowing" }));
    await user.type(within(await screen.findByRole("dialog")).getByLabelText("Reason"), "Not needed");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Take back" }));
    expect(await screen.findByText("Heavy borrowing is private again.")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path.endsWith("/unpublish"))?.body).toEqual({ reason: "Not needed" });
  });

  it("deletes (retires) a rule after a reason and moves it to the deleted list", async () => {
    let status: "active" | "retired" = "active";
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: [ruleView({ status })], adopted: [] }),
      "POST /custom-rules/cus_3f9a1c2b7d/retire": () => ((status = "retired"), json({ rule: ruleView({ status: "retired" }) })),
    });
    await user.click(await screen.findByRole("button", { name: "Delete Heavy borrowing" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Cases it ran on stay explainable/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Reason"), "No longer needed");
    await user.click(within(dialog).getByRole("button", { name: "Delete rule" }));
    expect(await screen.findByText("Heavy borrowing was deleted.")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path.endsWith("/retire"))?.body).toEqual({ reason: "No longer needed" });
    expect(await screen.findByText("Deleted rules (1)")).toBeInTheDocument();
    expect(screen.getByText("You have not written a custom check yet")).toBeInTheDocument();
  });

  it("keeps the dialog open and explains a refusal from the server", async () => {
    const { user } = setup({
      "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [] }),
      "POST /custom-rules/cus_3f9a1c2b7d/publish": () => errorBody("duplicate_custom", "Another public rule is identical.", 409, { rule_id: "cus_77aa00bb11", name: "Big debt" }),
    });
    await user.click(await screen.findByRole("button", { name: "Make public Heavy borrowing" }));
    await user.type(within(await screen.findByRole("dialog")).getByLabelText("Reason"), "Share it please");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Make public" }));
    expect(await screen.findByText("A custom rule already does this")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("opens a new version in the assistant for that rule", async () => {
    const { user, onEdit } = setup({ "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [] }) });
    await user.click(await screen.findByRole("button", { name: "New version of Heavy borrowing" }));
    expect(onEdit).toHaveBeenCalledWith({ id: "cus_3f9a1c2b7d", name: "Heavy borrowing" });
  });

  it("shows the details: every version, the last test and the change log", async () => {
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: [ruleView({ version: 2 })], adopted: [] }),
      "GET /custom-rules/cus_3f9a1c2b7d": () =>
        json(
          ruleView({
            version: 2, description: "Asked for by the loan desk",
            last_dry_run: { spec_hash: "h", cases_considered: 10, evaluated: 5, fired: 1, skipped_missing: 0, scanned_evaluated: 0, scanned_fired: 0, fire_rate: 0.2, warnings: [], examples: [], needs_confirmation: false },
            versions: [
              { version: 1, spec: ruleView().spec, spec_hash: "h1", readback: "Flags medium when v1", created_at: "2026-11-01T09:00:00Z", created_by: "alice", reason: "First draft" },
              { version: 2, spec: ruleView().spec, spec_hash: "h2", readback: "Flags medium when v2", created_at: "2026-11-02T09:00:00Z", created_by: "alice", reason: "Tighter" },
            ],
          }),
        ),
      "GET /custom-rules/changes": () => json({ changes: [{ id: 2, rule_id: "cus_3f9a1c2b7d", at: "2026-11-02T09:00:00Z", actor: "alice", reason: "Tighter", action: "new_version", version: 2, before: {}, after: {} }, { id: 1, rule_id: "cus_3f9a1c2b7d", at: "2026-11-01T09:00:00Z", actor: "alice", reason: "First draft", action: "create", version: 1, before: {}, after: {} }] }),
    });
    await user.click(await screen.findByRole("button", { name: "Details of Heavy borrowing" }));
    expect(await screen.findByText("Asked for by the loan desk")).toBeInTheDocument();
    expect(screen.getByText("Flags medium when v1")).toBeInTheDocument();
    expect(screen.getByText("Flags medium when v2")).toBeInTheDocument();
    expect(screen.getByText("(current)")).toBeInTheDocument();
    expect(screen.getByText(/Evaluated 5 stored cases, fired on 1/)).toBeInTheDocument();
    const sheet = within(screen.getByRole("dialog"));
    expect(await sheet.findByText("New version")).toBeInTheDocument();
    expect(sheet.getByText("Created")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path === "/custom-rules/changes")).toBeDefined();
  });

  it("lists the public rules I adopted, flags an update and stops using one", async () => {
    let list = [adopted({ latest_version: 2, update_available: true })];
    const { f, user } = setup({
      "GET /custom-rules": () => json({ mine: [], adopted: list }),
      "POST /custom-rules/cus_77aa00bb11/unadopt": () => ((list = []), json({ rule_id: "cus_77aa00bb11", adopted: false })),
    });
    const heading = await screen.findByRole("heading", { name: "Expired passport" });
    const li = heading.closest("li") as HTMLElement;
    expect(within(li).getByText("Custom")).toBeInTheDocument();
    expect(within(li).getByText(/By bob, version 1 adopted/)).toBeInTheDocument();
    expect(within(li).getByText(/Version 2 is public/)).toBeInTheDocument();
    await user.click(within(li).getByRole("button", { name: "Stop using Expired passport" }));
    expect(await screen.findByText("You stopped using Expired passport.")).toBeInTheDocument();
    expect(f.count("POST /custom-rules/cus_77aa00bb11/unadopt")).toBe(1);
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Expired passport" })).toBeNull());
  });

  it("marks an adopted rule that is no longer public as not running", async () => {
    setup({ "GET /custom-rules": () => json({ mine: [], adopted: [adopted({ active: false })] }) });
    expect(await screen.findByText("Not running: no longer public")).toBeInTheDocument();
  });

  it("shows a load failure with a retry", async () => {
    setup({ "GET /custom-rules": () => errorBody("feature_off", "Custom rules are switched off on this deployment (GLDB_CUSTOM_RULES).", 404) });
    expect(await screen.findByText("Custom rules are switched off")).toBeInTheDocument();
  });
});
