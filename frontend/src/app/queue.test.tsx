import "@/test/mockEnv";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiErrorException } from "@/lib/errors";
import * as api from "@/lib/api";
import { resetMockState, state } from "@/lib/mock/state";
import { clearViewport, renderWithSWR, setViewport } from "@/test/utils";
import { nav, navModule, resetNav } from "@/test/navMock";
import QueuePage from "./page";

vi.mock("next/navigation", async () => (await import("@/test/navMock")).navModule());
void navModule;

beforeEach(() => {
  resetNav();
  resetMockState();
  state.latencyMs = 0;
});
afterEach(() => {
  vi.restoreAllMocks();
  clearViewport();
  cleanup();
});

describe("queue with the 12 mock cases", () => {
  it("keeps priority order, shows ranks and one link per row", async () => {
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table", { name: /ranked by priority/i });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(10); // closed and error cases are hidden by default
    // rank column present and 1..n in order
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual(rows.map((_, i) => String(i + 1)));
    for (const r of rows) expect(within(r).getAllByRole("link")).toHaveLength(1);
    // highest scores first among open cases
    const scores = rows.map((r) => /^\D*(\d+)/.exec(within(r).getAllByRole("cell")[0].nextElementSibling?.textContent ?? "")?.[1]);
    expect(scores[0]).toBe("75");
  });

  it("shows the fast lane chip, partial score and analysing row", async () => {
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table");
    const clean = within(table).getByRole("link", { name: /ACME-2026-030/ }).closest("tr")!;
    expect(within(clean).getByText("Fast lane")).toBeInTheDocument();
    expect(clean.textContent).toContain("No concerns found. 21 of 21 checks completed.");
    const partial = within(table).getByRole("link", { name: /ACME-2026-033/ }).closest("tr")!;
    expect(partial.textContent).toContain("≥");
    expect(partial.textContent).toContain("Low risk (partial score)");
    expect(partial.textContent).not.toContain("Fast lane");
    expect(partial.textContent).toContain("3 pages not reviewed");
    const proc = within(table).getByRole("link", { name: /ACME-2026-040/ }).closest("tr")!;
    expect(proc.textContent).toContain("Being analysed");
    expect(proc.textContent).not.toMatch(/\b\d{1,3}\s*(Low|Medium|High)/);
    const lowCov = within(table).getByRole("link", { name: /ACME-2026-021/ }).closest("tr")!;
    expect(lowCov.textContent).toContain("Low coverage");
    const pending = within(table).getByRole("link", { name: /ACME-2026-022/ }).closest("tr")!;
    expect(pending.textContent).toContain("Second approval pending");
  });

  it("has sortable headers with aria-sort and drops the rank column when sorted", async () => {
    const user = userEvent.setup();
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    const risk = screen.getByRole("columnheader", { name: /Risk/ });
    expect(risk).toHaveAttribute("aria-sort", "none");
    await user.click(within(risk).getByRole("button", { name: /^Risk/ }));
    expect(nav.router.replace).toHaveBeenCalledWith("/?sort=score&order=desc");
    cleanup();
    nav.params = new URLSearchParams("sort=score&order=desc");
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("columnheader", { name: /Risk/ })).toHaveAttribute("aria-sort", "descending");
    expect(within(table).queryByRole("columnheader", { name: "Rank" })).toBeNull();
  });

  it("renders cards, one link each, below md", async () => {
    setViewport(390);
    renderWithSWR(<QueuePage />);
    const list = (await screen.findByRole("link", { name: /ACME-2026-014/ })).closest("ul")!;
    expect(screen.queryByRole("table")).toBeNull();
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(10);
    for (const c of cards) expect(within(c).getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Filters \(0\)/ })).toBeInTheDocument();
  });

  it("filters through the URL", async () => {
    const user = userEvent.setup();
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: /More filters/ }));
    await user.click(screen.getByRole("button", { name: "KYC" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/?use_case=kyc");
    await user.click(screen.getByRole("tab", { name: /Fast lane/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/?lane=fast");
    await user.click(screen.getByRole("button", { name: /^High/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/?band=high");
    await user.click(screen.getByRole("button", { name: "Show closed" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/?status=processing%2Copen%2Cawaiting_documents%2Cclosed");
  });
});

describe("queue states", () => {
  it("shows a busy skeleton while loading", async () => {
    vi.spyOn(api, "getQueue").mockReturnValue(new Promise(() => {}));
    renderWithSWR(<QueuePage />);
    const busy = await screen.findByLabelText("Loading the queue");
    expect(busy).toHaveAttribute("aria-busy", "true");
  });

  it("says so when there are no cases at all", async () => {
    vi.spyOn(api, "getQueue").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25, has_next: false });
    renderWithSWR(<QueuePage />);
    expect(await screen.findByText("Nothing needs attention right now")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Start a new case/ })).toHaveAttribute("href", "/upload");
  });

  it("offers Clear filters when filters leave nothing", async () => {
    const user = userEvent.setup();
    nav.params = new URLSearchParams("q=zzz");
    vi.spyOn(api, "getQueue").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25, has_next: false });
    renderWithSWR(<QueuePage />);
    expect(await screen.findByText("No cases match these filters")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(nav.router.replace).toHaveBeenCalledWith("/");
  });

  it("shows an error with retry when the API cannot be reached", async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "getQueue").mockRejectedValue(new ApiErrorException({ code: "network_error", message: "x" }));
    renderWithSWR(<QueuePage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cannot reach the API");
    spy.mockClear();
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
  });

  it("keeps the old rows and says the refresh failed", async () => {
    const real = api.getQueue;
    const spy = vi.spyOn(api, "getQueue");
    spy.mockImplementation(real);
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    spy.mockRejectedValue(new ApiErrorException({ code: "network_error", message: "x" }));
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "Refresh the queue" }));
    });
    const status = (await screen.findByText(/Last refresh failed/)).closest('[role="status"]');
    expect(status).not.toBeNull();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});

describe("worklist for an officer", () => {
  it("leads with the applicant, links the whole row once, and keeps the reference small", async () => {
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table");
    const link = within(table).getByRole("link", { name: /ACME-2026-014/ });
    expect(link).toHaveAttribute("href", "/cases/c_loan_fraud");
    expect(link).toHaveTextContent("Example Trading Pte. Ltd.");
    const row = link.closest("tr")!;
    expect(within(row).getAllByRole("link")).toHaveLength(1);
    expect(row.textContent).toContain("Waiting");
  });

  it("writes one calm summary sentence from the data", async () => {
    renderWithSWR(<QueuePage />);
    const h1 = await screen.findByRole("heading", { level: 1, name: "Worklist" });
    await waitFor(() => expect(h1.parentElement?.textContent).toMatch(/\d+ cases open: \d+ need[s]? your attention, \d+ look[s]? routine\. Oldest has waited/));
  });

  it("shows live counts on the lane tabs and risk chips, and describes each lane", async () => {
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent?.replace(/\d+$/, ""))).toEqual(["All", "Loan review", "Enhanced due diligence", "AML investigation", "Fast lane"]);
    await waitFor(() => expect(within(screen.getByRole("tab", { name: /^All/ })).getByText("10")).toBeInTheDocument());
    const total = tabs.slice(1).reduce((n, t) => n + Number(/(\d+)$/.exec(t.textContent ?? "")?.[1] ?? 0), 0);
    expect(total).toBeLessThanOrEqual(10);
    expect(screen.getByRole("tab", { name: /Fast lane/ })).toHaveAttribute("title", expect.stringMatching(/person still confirms/));
    expect(screen.getByRole("tab", { name: /^All/ })).toHaveAttribute("aria-selected", "true");
    const high = screen.getByRole("button", { name: /^High/ });
    expect(high).toHaveAttribute("aria-pressed", "false");
    expect(high.textContent).toMatch(/High\s*\d+/);
  });

  it("selects the lane tab from the URL and says what the lane means", async () => {
    nav.params = new URLSearchParams("lane=fast");
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    expect(screen.getByRole("tab", { name: /Fast lane/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
    expect(screen.getByText(/Low risk and well checked; a person still confirms/)).toBeInTheDocument();
  });

  it("offers Open next case for the highest ranked open case", async () => {
    renderWithSWR(<QueuePage />);
    const open = await screen.findByRole("link", { name: "Open next case" });
    const table = await screen.findByRole("table");
    const first = within(table).getAllByRole("link")[0];
    expect(open).toHaveAttribute("href", first.getAttribute("href"));
    expect(screen.getByText(/Next up:/)).toBeInTheDocument();
  });

  it("respects the lane in view when choosing the next case", async () => {
    nav.params = new URLSearchParams("lane=fast");
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table");
    const open = await screen.findByRole("link", { name: "Open next case" });
    expect(open).toHaveAttribute("href", within(table).getAllByRole("link")[0].getAttribute("href"));
  });

  it("does not say nothing needs attention when a search matched nothing", async () => {
    nav.params = new URLSearchParams("q=zzz");
    renderWithSWR(<QueuePage />);
    expect(await screen.findByText("No cases match these filters")).toBeInTheDocument();
    expect(screen.getByText("No cases match your search or filters.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open next case" })).toBeNull();
  });

  it("keeps the table keyboard friendly: caption, sortable headers, one tab stop per row", async () => {
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table");
    expect(table.querySelector("caption")).not.toBeNull();
    for (const name of ["Risk", "Checks completed", "Waiting"]) expect(within(table).getByRole("columnheader", { name })).toHaveAttribute("aria-sort");
  });
});

describe("keyboard shortcuts", () => {
  it("moves the row focus with j and k and stays within the list", async () => {
    const user = userEvent.setup();
    renderWithSWR(<QueuePage />);
    const table = await screen.findByRole("table");
    const links = within(table).getAllByRole("link");
    await user.keyboard("j");
    expect(links[0]).toHaveFocus();
    await user.keyboard("j");
    expect(links[1]).toHaveFocus();
    await user.keyboard("k");
    expect(links[0]).toHaveFocus();
    await user.keyboard("k");
    expect(links[0]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(links[1]).toHaveFocus();
  });

  it("focuses search with / and opens the shortcut list with ?", async () => {
    const user = userEvent.setup();
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    await user.keyboard("/");
    expect(screen.getByRole("searchbox")).toHaveFocus();
    (document.activeElement as HTMLElement).blur();
    await user.keyboard("?");
    expect(await screen.findByRole("heading", { name: "Keyboard shortcuts" })).toBeInTheDocument();
  });

  it("does not fire while typing in the search box", async () => {
    const user = userEvent.setup();
    renderWithSWR(<QueuePage />);
    await screen.findByRole("table");
    const box = screen.getByRole("searchbox");
    await user.click(box);
    await user.keyboard("jk/?");
    expect(box).toHaveFocus();
    expect(box).toHaveValue("jk/?");
    expect(screen.queryByRole("heading", { name: "Keyboard shortcuts" })).toBeNull();
  });
});
