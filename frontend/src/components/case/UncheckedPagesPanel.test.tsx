import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { uncheckedPageList } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { CaseDetail } from "@/lib/types";
import { UncheckedPagesPanel } from "./UncheckedPagesPanel";

afterEach(cleanup);

/** A case whose coverage says which pages were not reviewed, as the backend now reports them. */
function withUnchecked(): CaseDetail {
  const c = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
  const f = c.files[0];
  const pages = [
    { file_id: f.id, page: 2, reason: "not_checked" as const },
    { file_id: f.id, page: 3, reason: "low_confidence" as const },
    { file_id: f.id, page: 4, reason: "no_document" as const },
  ];
  return { ...c, coverage: { ...c.coverage!, pages_unchecked: 3, unchecked_pages: pages } };
}

describe("UncheckedPagesPanel", () => {
  it("lists every page that no check looked at, with the reason", () => {
    const c = withUnchecked();
    render(<UncheckedPagesPanel c={c} onView={() => {}} />);
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent(new RegExp(`page 2 of ${c.files[0].name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "i"));
    expect(items[0]).toHaveTextContent("Its document was received but not checked.");
    expect(items[1]).toHaveTextContent("We were not confident what kind of page this is.");
    expect(items[2]).toHaveTextContent("It does not belong to any document we recognised.");
  });

  it("opens the page when View page is pressed", async () => {
    const c = withUnchecked();
    const onView = vi.fn();
    render(<UncheckedPagesPanel c={c} onView={onView} />);
    await userEvent.click(screen.getAllByRole("button", { name: /View page/ })[1]);
    expect(onView).toHaveBeenCalledWith(c.files[0].id, 3);
  });

  it("is the target of 'See which' (id unreviewed-pages) and renders nothing when every page was reviewed", () => {
    const c = withUnchecked();
    const { container, rerender } = render(<UncheckedPagesPanel c={c} onView={() => {}} />);
    expect(container.querySelector("#unreviewed-pages")).not.toBeNull();
    rerender(<UncheckedPagesPanel c={{ ...c, coverage: { ...c.coverage!, pages_unchecked: 0, unchecked_pages: [] } }} onView={() => {}} />);
    expect(container.querySelector("#unreviewed-pages")).toBeNull();
  });
});

describe("uncheckedPageList", () => {
  it("prefers the backend's list", () => {
    const c = withUnchecked();
    expect(uncheckedPageList(c)).toHaveLength(3);
  });

  it("derives the list when an older analysis comes back with an empty one but a non-zero count", () => {
    const c = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
    const f = c.files[0];
    const list = uncheckedPageList({
      ...c,
      coverage: { ...c.coverage!, pages_unchecked: 1, unchecked_pages: [] },
      documents: c.documents.map((d) => (d.file_id === f.id && d.page_start === 1 ? { ...d, status: "received_not_checked" as const } : d)),
    });
    expect(list.length).toBeGreaterThan(0);
  });

  it("falls back to the case's own pages when the backend did not send a list (older analyses)", () => {
    const c = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
    const f = c.files[0];
    const list = uncheckedPageList({
      ...c,
      coverage: { ...c.coverage!, unchecked_pages: undefined },
      documents: c.documents.map((d) => (d.file_id === f.id && d.page_start === 1 ? { ...d, status: "received_not_checked" as const } : d)),
    });
    expect(list.length).toBeGreaterThan(0);
    expect(list.every((u) => u.file_id && u.page >= 1)).toBe(true);
    expect(list.some((u) => u.reason === "not_checked")).toBe(true);
  });
});
