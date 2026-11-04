import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { adopted, json, routedFetch, ruleView } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";
import { CatalogueCustomSection } from "./CatalogueSection";

function setup(routes: Parameters<typeof routedFetch>[0]) {
  const f = routedFetch(routes);
  vi.stubGlobal("fetch", f.fn);
  renderWithSWR(<CatalogueCustomSection />);
  return f;
}
const on = () => json({ reviewer: "alice", admin: false, custom_rules: true });
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("custom rules on the check catalogue page", () => {
  it("lists my own and my adopted rules with a Custom badge, and links to the custom rules page", async () => {
    setup({
      "GET /me": on,
      "GET /custom-rules": () => json({ mine: [ruleView(), ruleView({ rule_id: "cus_bbbbbbbbbb", name: "Gone", status: "retired" }), ruleView({ rule_id: "cus_cccccccccc", name: "Quiet one", enabled: false, visibility: "public", published_version: 1 })], adopted: [adopted()] }),
    });
    const list = await screen.findByRole("list", { name: "Your custom rules" });
    const items = Array.from(list.children) as HTMLElement[];
    expect(items).toHaveLength(3); // the deleted one is not listed
    expect(within(items[0]).getByText("Heavy borrowing")).toBeInTheDocument();
    expect(within(items[0]).getByText("Custom")).toBeInTheDocument();
    expect(within(items[0]).getByText(/Flags medium when, on a financial statement/)).toBeInTheDocument();
    expect(within(items[1]).getByText("Switched off")).toBeInTheDocument();
    expect(within(items[1]).getByText("Public")).toBeInTheDocument();
    expect(within(items[2]).getByText("Expired passport")).toBeInTheDocument();
    expect(within(items[2]).getByText("Adopted from bob")).toBeInTheDocument();
    expect(within(items[2]).getByText("Custom")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open custom rules" })).toHaveAttribute("href", "/rules/custom");
    expect(screen.queryByText("Gone")).toBeNull();
  });

  it("points to the assistant when there are no custom rules", async () => {
    setup({ "GET /me": on, "GET /custom-rules": () => json({ mine: [], adopted: [] }) });
    expect(await screen.findByText(/You have no custom rules/)).toBeInTheDocument();
  });

  it("is absent, and asks for no rules, when the feature is off or nobody is signed in", async () => {
    const f = setup({ "GET /me": () => json({ reviewer: "alice", admin: false, custom_rules: false }) });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("heading", { name: "Custom rules" })).toBeNull();
    expect(f.calls.map((c) => c.path)).toEqual(["/me"]);
  });
});
