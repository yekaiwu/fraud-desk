import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlaggedPagesStrip, type FlaggedPage } from "./FlaggedPagesStrip";

afterEach(cleanup);

const many = (n: number): FlaggedPage[] => Array.from({ length: n }, (_, i) => ({ fileId: "f1", page: i + 1, count: (i % 4) + 1 }));
const nameOf = (_f: string, p: number) => `Bank statement, Jan 2026, page ${p}`;

function setup(pages: FlaggedPage[], current = { fileId: "f1", page: 1 }) {
  const onNavigate = vi.fn();
  render(<FlaggedPagesStrip pages={pages} current={current} nameOf={nameOf} onNavigate={onNavigate} />);
  return { onNavigate };
}

describe("FlaggedPagesStrip", () => {
  it("keeps every flagged page in one row that scrolls, however many there are", () => {
    setup(many(60));
    const row = screen.getByRole("group", { name: "Pages with flags" });
    expect(within(row).getAllByRole("button")).toHaveLength(60);
    // one line that scrolls sideways: a wrapping row would grow with the count and push the page image down
    expect(row.className).toContain("flex-nowrap");
    expect(row.className).toContain("overflow-x-auto");
    expect(row.className).not.toMatch(/(^|\s)flex-wrap(\s|$)/);
    expect(screen.getByText("(60)")).toBeInTheDocument();
  });

  it("marks the open page and jumps to another", async () => {
    const user = userEvent.setup();
    const { onNavigate } = setup(many(12), { fileId: "f1", page: 3 });
    const row = screen.getByRole("group", { name: "Pages with flags" });
    expect(within(row).getByRole("button", { name: /page 3/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(row).getByRole("button", { name: /page 4/ })).toHaveAttribute("aria-pressed", "false");
    await user.click(within(row).getByRole("button", { name: /page 9/ }));
    expect(onNavigate).toHaveBeenCalledWith({ fileId: "f1", page: 9 });
  });

  it("shows no arrows while everything fits, and an arrow for the side that has more", () => {
    setup(many(30));
    const row = screen.getByRole("group", { name: "Pages with flags" });
    // jsdom has no layout: pretend the row is 300 px wide over 2,000 px of chips
    Object.defineProperty(row, "clientWidth", { configurable: true, value: 300 });
    Object.defineProperty(row, "scrollWidth", { configurable: true, value: 2000 });
    fireEvent.scroll(row);
    expect(screen.queryByRole("button", { name: "Show earlier flagged pages" })).toBeNull();
    expect(screen.getByRole("button", { name: "Show later flagged pages" })).toBeInTheDocument();
    row.scrollLeft = 400;
    fireEvent.scroll(row);
    expect(screen.getByRole("button", { name: "Show earlier flagged pages" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show later flagged pages" })).toBeInTheDocument();
    row.scrollLeft = 1700;
    fireEvent.scroll(row);
    expect(screen.queryByRole("button", { name: "Show later flagged pages" })).toBeNull();
  });

  it("an arrow moves only the row, not the pages", async () => {
    const user = userEvent.setup();
    const { onNavigate } = setup(many(30));
    const row = screen.getByRole("group", { name: "Pages with flags" });
    Object.defineProperty(row, "clientWidth", { configurable: true, value: 300 });
    Object.defineProperty(row, "scrollWidth", { configurable: true, value: 2000 });
    fireEvent.scroll(row);
    await user.click(screen.getByRole("button", { name: "Show later flagged pages" }));
    expect(row.scrollLeft).toBeGreaterThan(0);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("renders nothing when no page is flagged", () => {
    const { container } = render(<FlaggedPagesStrip pages={[]} current={{ fileId: "f1", page: 1 }} nameOf={nameOf} onNavigate={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
