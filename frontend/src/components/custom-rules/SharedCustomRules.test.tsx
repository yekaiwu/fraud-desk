import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SWRConfig } from "swr";
import { adopted, errorBody, json, routedFetch, shared } from "@/test/customRulesFixtures";
import { SharedCustomRules } from "./SharedCustomRules";

function setup(routes: Parameters<typeof routedFetch>[0], admin = false) {
  const f = routedFetch({ "GET /custom-rules/shared": () => json({ rules: [] }), "GET /custom-rules": () => json({ mine: [], adopted: [] }), ...routes });
  vi.stubGlobal("fetch", f.fn);
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <SharedCustomRules admin={admin} />
    </SWRConfig>,
  );
  return { f, user: userEvent.setup() };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Public custom rules", () => {
  it("says nothing changes until a rule is adopted, and shows the empty state", async () => {
    setup({});
    expect(await screen.findByText("No public rules yet")).toBeInTheDocument();
    expect(screen.getByText("Written by officers, not reviewed by the development team")).toBeInTheDocument();
    expect(screen.getByText(/Nothing here changes your results until you adopt a rule/)).toBeInTheDocument();
  });

  it("shows the author, the custom and unreviewed labels and the readback of each public rule", async () => {
    setup({ "GET /custom-rules/shared": () => json({ rules: [shared()] }) });
    const li = (await screen.findByRole("heading", { name: "Expired passport" })).closest("li") as HTMLElement;
    expect(within(li).getByText("Custom")).toBeInTheDocument();
    expect(within(li).getByText("custom, unreviewed")).toBeInTheDocument();
    expect(within(li).getByText("bob")).toBeInTheDocument();
    expect(within(li).getByText(/Flags low when a passport's date of expiry is before the case date/)).toBeInTheDocument();
    expect(within(li).getByText("Passports must be in date.")).toBeInTheDocument();
    expect(within(li).getByText("3 accounts use it")).toBeInTheDocument();
  });

  it("adopts a rule with one click and tells the officer when it starts to apply", async () => {
    let on = false;
    const { f, user } = setup({
      "GET /custom-rules/shared": () => json({ rules: [shared({ adopted: on, adopted_version: on ? 1 : null })] }),
      "POST /custom-rules/cus_77aa00bb11/adopt": () => ((on = true), json({ adoption: adopted() })),
    });
    await user.click(await screen.findByRole("button", { name: "Adopt Expired passport" }));
    expect(await screen.findByText(/You adopted Expired passport \(version 1\). It runs on analyses you start from now on./)).toBeInTheDocument();
    expect(f.count("POST /custom-rules/cus_77aa00bb11/adopt")).toBe(1);
    expect(await screen.findByText("Adopted (v1)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop using Expired passport" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Adopt Expired passport" })).toBeNull();
  });

  it("shows that an update is available and adopts the new version", async () => {
    const { f, user } = setup({
      "GET /custom-rules/shared": () => json({ rules: [shared({ version: 2, adopted: true, adopted_version: 1 })] }),
      "POST /custom-rules/cus_77aa00bb11/adopt": () => json({ adoption: adopted({ version: 2 }) }),
    });
    expect(await screen.findByText("Update available: you use version 1.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Adopt version 2/ }));
    await screen.findByText(/You adopted Expired passport \(version 2\)/);
    expect(f.count("POST /custom-rules/cus_77aa00bb11/adopt")).toBe(1);
  });

  it("stops using an adopted rule", async () => {
    const { f, user } = setup({
      "GET /custom-rules/shared": () => json({ rules: [shared({ adopted: true, adopted_version: 1 })] }),
      "POST /custom-rules/cus_77aa00bb11/unadopt": () => json({ rule_id: "cus_77aa00bb11", adopted: false }),
    });
    await user.click(await screen.findByRole("button", { name: "Stop using Expired passport" }));
    expect(await screen.findByText("You stopped using Expired passport.")).toBeInTheDocument();
    expect(f.count("POST /custom-rules/cus_77aa00bb11/unadopt")).toBe(1);
  });

  it("does not let an author adopt their own rule", async () => {
    setup({ "GET /custom-rules/shared": () => json({ rules: [shared({ own: true, author: "alice" })] }) });
    await screen.findByRole("heading", { name: "Expired passport" });
    expect(screen.getByText("Your own rule. Manage it under My rules.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Adopt/ })).toBeNull();
    expect(screen.getByText("(you)", { exact: false })).toBeInTheDocument();
  });

  it("explains a refused adoption", async () => {
    const { user } = setup({
      "GET /custom-rules/shared": () => json({ rules: [shared()] }),
      "POST /custom-rules/cus_77aa00bb11/adopt": () => errorBody("not_public", "This rule is no longer public.", 409),
    });
    await user.click(await screen.findByRole("button", { name: "Adopt Expired passport" }));
    expect(await screen.findByText("Not public")).toBeInTheDocument();
  });

  it("shows an administrator, and only an administrator, a way to take back someone else's public rule", async () => {
    const routes = { "GET /custom-rules/shared": () => json({ rules: [shared()] }) };
    const plain = setup(routes);
    await screen.findByRole("heading", { name: "Expired passport" });
    expect(screen.queryByRole("button", { name: /Take back/ })).toBeNull();
    cleanup();
    vi.unstubAllGlobals();
    const { f, user } = setup({ ...routes, "POST /custom-rules/cus_77aa00bb11/unpublish": () => json({ rule: {} }) }, true);
    await user.click(await screen.findByRole("button", { name: /Take back \(administrator\)/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/becomes private again/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Reason"), "Wrong threshold");
    await user.click(within(dialog).getByRole("button", { name: "Take back" }));
    expect(await screen.findByText("Expired passport was taken back.")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path.endsWith("/unpublish"))?.body).toEqual({ reason: "Wrong threshold" });
    expect(plain.f.count("POST /custom-rules/cus_77aa00bb11/unpublish")).toBe(0);
  });

  it("renders a rule name or description full of markup as text", async () => {
    setup({ "GET /custom-rules/shared": () => json({ rules: [shared({ name: "<script>window.__x=1</script>", description: "<img src=x onerror=alert(1)>" })] }) });
    await screen.findByRole("heading", { name: "<script>window.__x=1</script>" });
    expect(document.querySelector("script[src], img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  });
});
