import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SWRConfig } from "swr";
import { errorBody, json, routedFetch, ticket } from "@/test/customRulesFixtures";
import { CustomTickets } from "./CustomTickets";

function setup(routes: Parameters<typeof routedFetch>[0], admin = false) {
  const f = routedFetch({ "GET /custom-rules/tickets": () => json({ tickets: [] }), ...routes });
  vi.stubGlobal("fetch", f.fn);
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <CustomTickets admin={admin} />
    </SWRConfig>,
  );
  return { f, user: userEvent.setup() };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Requests to the tech team", () => {
  it("explains the empty list", async () => {
    setup({});
    expect(await screen.findByText("No requests yet")).toBeInTheDocument();
  });

  it("lists each request with its id, status, what could not be built and the original words", async () => {
    setup({ "GET /custom-rules/tickets": () => json({ tickets: [ticket(), ticket({ id: "crt_aaaaaaaaaaaa", origin: "partial", status: "done", desired_severity: null, missing: [{ phrase: "from different places", reason: "field_not_extracted" }], intent: "Debt from several lenders", doc_types: ["financial_statement"] })] }) });
    const list = await screen.findByRole("list", { name: "Requests to the tech team" });
    const items = Array.from(list.children) as HTMLElement[];
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("crt_9c0d1e2f3a4b")).toBeInTheDocument();
    expect(within(items[0]).getByText("Submitted to the tech team")).toBeInTheDocument();
    expect(within(items[0]).getByText("Could not be built as a rule")).toBeInTheDocument();
    expect(within(items[0]).getByText("Wanted: Low concern")).toBeInTheDocument();
    expect(within(items[0]).getByText(/posts on social media.: the system does not read this kind of document/)).toBeInTheDocument();
    expect(within(items[0]).getByText("flag if the company posts about money problems on social media")).toBeInTheDocument();
    expect(within(items[1]).getByText("Done")).toBeInTheDocument();
    expect(within(items[1]).getByText("The part a rule could not check")).toBeInTheDocument();
    expect(within(items[1]).getByText(/about financial statement/)).toBeInTheDocument();
    expect(within(items[1]).queryByText(/Wanted:/)).toBeNull();
  });

  it("shows the account only to an administrator, who sees everyone's requests", async () => {
    setup({ "GET /custom-rules/tickets": () => json({ tickets: [ticket({ account: "carol" })] }) }, true);
    expect(await screen.findByText(/carol,/)).toBeInTheDocument();
    cleanup();
    vi.unstubAllGlobals();
    setup({ "GET /custom-rules/tickets": () => json({ tickets: [ticket({ account: "alice" })] }) }, false);
    await screen.findByText("crt_9c0d1e2f3a4b");
    expect(screen.queryByText(/alice,/)).toBeNull();
  });

  it("sends an idea as written, with the severity, and needs no model", async () => {
    let tickets = [] as ReturnType<typeof ticket>[];
    const { f, user } = setup({
      "GET /custom-rules/tickets": () => json({ tickets }),
      "POST /custom-rules/tickets": () => ((tickets = [ticket()]), json({ ticket: ticket(), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: false }, 201)),
    });
    const send = await screen.findByRole("button", { name: "Send to the tech team" });
    expect(send).toBeDisabled();
    await user.type(screen.getByLabelText("What should be checked?"), "flag companies that post about money problems");
    await user.selectOptions(screen.getByLabelText("How serious should a flag be?"), "medium");
    await user.click(send);
    expect(await screen.findByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).")).toBeInTheDocument();
    expect(f.calls.find((c) => c.method === "POST")?.body).toEqual({ text: "flag companies that post about money problems", severity: "medium" });
    expect(f.calls.some((c) => c.path.includes("/drafts"))).toBe(false);
    expect(await screen.findByRole("list", { name: "Requests to the tech team" })).toBeInTheDocument();
  });

  it("explains a refused request", async () => {
    const { user } = setup({ "POST /custom-rules/tickets": () => errorBody("draft_refused", "refused", 409) });
    await user.type(await screen.findByLabelText("What should be checked?"), "something long enough to send");
    await user.click(screen.getByRole("button", { name: "Send to the tech team" }));
    expect(await screen.findByText("A refused request is not sent on")).toBeInTheDocument();
  });

  it("shows a list that cannot be loaded with a retry for transient failures", async () => {
    setup({ "GET /custom-rules/tickets": () => errorBody("internal_error", "boom", 500) });
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
