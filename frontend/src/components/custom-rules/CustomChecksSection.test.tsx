import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { adopted, json, routedFetch, ruleView, shared, ticket } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";
import { CustomChecksSection, type CustomChecksTab } from "./CustomChecksSection";

const props = { onNew: vi.fn(), onEdit: vi.fn() };

function Harness({ admin = false }: { admin?: boolean }) {
  const [tab, setTab] = useState<CustomChecksTab>("mine");
  const [revealKey, setRevealKey] = useState(0);
  return (
    <>
      <button type="button" onClick={() => setRevealKey((k) => k + 1)}>
        Reveal
      </button>
      <CustomChecksSection admin={admin} tab={tab} onTabChange={setTab} revealKey={revealKey} {...props} />
    </>
  );
}

function setup(routes: Parameters<typeof routedFetch>[0] = {}, ui = <Harness />) {
  const f = routedFetch({
    "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [adopted()] }),
    "GET /custom-rules/shared": () => json({ rules: [shared()] }),
    "GET /custom-rules/tickets": () => json({ tickets: [ticket()] }),
    ...routes,
  });
  vi.stubGlobal("fetch", f.fn);
  renderWithSWR(ui);
  return { f, user: userEvent.setup() };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  props.onNew.mockReset();
  props.onEdit.mockReset();
});

describe("the Custom checks section", () => {
  it("is a region titled Custom checks with the warning eyebrow and three tabs", async () => {
    setup();
    const section = await screen.findByRole("region", { name: "Custom checks" });
    expect(section.querySelector(".label-caps")).toHaveTextContent("Written by officers, not reviewed by the development team");
    expect(within(section).getAllByRole("tab").map((t) => t.textContent)).toEqual(["My checks", "Public checks", "Requests to the tech team"]);
  });

  it("shows my checks first, including the adopted ones, with a Custom badge", async () => {
    setup();
    const panel = await screen.findByRole("tabpanel", { name: "My checks" });
    expect(await within(panel).findByRole("heading", { name: "Heavy borrowing" })).toBeVisible();
    expect(within(panel).getByRole("heading", { name: "Expired passport" })).toBeVisible();
    expect(within(panel).getAllByText("Custom").length).toBeGreaterThan(0);
  });

  it("passes the new-check and new-version clicks up", async () => {
    const a = setup();
    await a.user.click(await screen.findByRole("button", { name: "New version of Heavy borrowing" }));
    expect(props.onEdit).toHaveBeenCalledWith({ id: "cus_3f9a1c2b7d", name: "Heavy borrowing" });
    cleanup();
    vi.unstubAllGlobals();
    const b = setup({ "GET /custom-rules": () => json({ mine: [], adopted: [] }) });
    await b.user.click(await screen.findByRole("button", { name: "Create a custom check" }));
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });

  it("passes the administrator flag to the Public and Requests tabs", async () => {
    const { user } = setup({ "GET /custom-rules/tickets": () => json({ tickets: [ticket({ account: "carol" })] }) }, <Harness admin />);
    await user.click(await screen.findByRole("tab", { name: "Public checks" }));
    expect(await screen.findByRole("button", { name: /Take back \(administrator\)/ })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Requests to the tech team" }));
    expect(await screen.findByText(/carol,/)).toBeInTheDocument();
  });

  it("takes focus when the reveal key changes, and not before", async () => {
    const { user } = setup();
    const region = await screen.findByRole("region", { name: "Custom checks" });
    await new Promise((r) => setTimeout(r, 20));
    expect(region.parentElement).not.toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Reveal" }));
    await vi.waitFor(() => expect(region.parentElement).toHaveFocus());
  });

  it("shows text from other officers as text only", async () => {
    const IMG = '<img src=x onerror="window.__pwned=1">';
    setup({ "GET /custom-rules": () => json({ mine: [ruleView({ name: `n ${IMG}` })], adopted: [] }) });
    expect((await screen.findAllByText(/n <img/)).length).toBeGreaterThan(0);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("[onerror]")).toBeNull();
  });
});
