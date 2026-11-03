import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { item } from "./testItem";
import { QueueCards, QueueTable, type Selection } from "./QueueTable";
import { SelectionBar } from "./SelectionBar";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
afterEach(cleanup);

const items = [item({ case_id: "c_a", applicant_name: "Alpha Pte. Ltd." }), item({ case_id: "c_b", applicant_name: "Bravo Pte. Ltd.", reference: "REF-B" })];

function selection(selected: string[] = []) {
  const onToggle = vi.fn<(id: string) => void>();
  const onToggleAll = vi.fn<() => void>();
  const sel: Selection = { selected: new Set(selected), onToggle, onToggleAll };
  return Object.assign(sel, { onToggle, onToggleAll });
}

describe("worklist selection", () => {
  it("shows no tick boxes unless selection is on", () => {
    render(<QueueTable items={items} query={{}} ageNow={0} onSort={() => {}} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("ticks one row, labelled with its applicant and reference, without opening the case", () => {
    const sel = selection();
    render(<QueueTable items={items} query={{}} ageNow={0} onSort={() => {}} selection={sel} />);
    const opened: string[] = [];
    for (const a of screen.getAllByRole("link")) a.addEventListener("click", (e) => (e.preventDefault(), opened.push(a.getAttribute("href") ?? "")));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Bravo Pte. Ltd., REF-B" }));
    expect(sel.onToggle).toHaveBeenCalledWith("c_b");
    expect(opened).toEqual([]);
  });

  it("ticks every row of the page from the header box, and shows it ticked when all are", () => {
    const sel = selection();
    const { rerender } = render(<QueueTable items={items} query={{}} ageNow={0} onSort={() => {}} selection={sel} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all cases on this page" }));
    expect(sel.onToggleAll).toHaveBeenCalled();
    rerender(<QueueTable items={items} query={{}} ageNow={0} onSort={() => {}} selection={selection(["c_a", "c_b"])} />);
    expect(screen.getByRole("checkbox", { name: "Select all cases on this page" })).toBeChecked();
  });

  it("has the same tick boxes on the small-screen cards", () => {
    const sel = selection(["c_a"]);
    render(<QueueCards items={items} query={{}} ageNow={0} selection={sel} />);
    expect(screen.getByRole("checkbox", { name: "Select Alpha Pte. Ltd." })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Bravo Pte. Ltd., REF-B" }));
    expect(sel.onToggle).toHaveBeenCalledWith("c_b");
  });
});

describe("SelectionBar", () => {
  const props = { pageCount: 5, onStart: vi.fn(), onDone: vi.fn(), onSelectPage: vi.fn(), onClear: vi.fn(), onDelete: vi.fn() };

  it("starts as a single button", async () => {
    const onStart = vi.fn();
    render(<SelectionBar {...props} selecting={false} count={0} onStart={onStart} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Select and delete" }));
    expect(onStart).toHaveBeenCalled();
  });

  it("cannot delete with nothing selected, and counts what is", () => {
    const { rerender } = render(<SelectionBar {...props} selecting count={0} />);
    expect(screen.getByRole("button", { name: /Delete\s+selected/ })).toBeDisabled();
    rerender(<SelectionBar {...props} selecting count={3} />);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete 3 selected" })).toBeEnabled();
  });
});
