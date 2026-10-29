import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { DocumentsPanel, NotCheckedList, docPages } from "./DocumentsPanel";

afterEach(cleanup);
const spec = (id: string) => buildCase(SPECS.find((s) => s.id === id)!);

describe("DocumentsPanel", () => {
  it("lists documents with friendly names, type, pages, a result in words and a concerns count", () => {
    const c = spec("c_loan_fraud");
    render(<DocumentsPanel c={c} onShowDocument={vi.fn()} onShowPage={vi.fn()} />);
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(c.documents.length);
    expect(within(table).getAllByText("Read OK").length).toBe(c.documents.length);
    expect(within(rows[0]).getByRole("button", { name: /Bank statement/ })).toBeInTheDocument();
    expect(within(rows[0]).getByText(/concern/)).toBeInTheDocument();
    expect(table.textContent).not.toMatch(/\bd[12]\b/);
    expect(docPages(c.documents[0])).toMatch(/^f1:1/);
  });

  it("opens the first page of the document when its row is chosen", async () => {
    const user = userEvent.setup();
    const onShowDocument = vi.fn();
    const c = spec("c_loan_fraud");
    render(<DocumentsPanel c={c} onShowDocument={onShowDocument} onShowPage={vi.fn()} />);
    await user.click(screen.getAllByRole("button", { name: /open in the viewer/ })[1]);
    expect(onShowDocument).toHaveBeenCalledWith(c.documents[1].id);
  });

  it("opens the document of the row that was clicked, wherever on the row, with no overlay that depends on the row being positioned", async () => {
    const user = userEvent.setup();
    const onShowDocument = vi.fn();
    const c = spec("c_loan_fraud");
    render(<DocumentsPanel c={c} onShowDocument={onShowDocument} onShowPage={vi.fn()} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    // Browsers disagree on whether a <tr> anchors an absolutely positioned child: an overlay in every row can then
    // stack over the whole table and the last row takes every click.
    for (const row of rows) expect(row.className).not.toMatch(/(^|\s)relative(\s|$)/);
    for (const b of screen.getAllByRole("button", { name: /open in the viewer/ })) expect(b.className).not.toContain("after:absolute");
    await user.click(within(rows[1]).getByText("Read OK"));
    expect(onShowDocument).toHaveBeenCalledTimes(1);
    expect(onShowDocument).toHaveBeenLastCalledWith(c.documents[1].id);
    await user.click(within(rows[0]).getByText(/concern/));
    expect(onShowDocument).toHaveBeenLastCalledWith(c.documents[0].id);
    expect(onShowDocument).toHaveBeenCalledTimes(2);
  });

  it("does not open the document when the reading details are used", async () => {
    const user = userEvent.setup();
    const onShowDocument = vi.fn();
    render(<DocumentsPanel c={spec("c_loan_fraud")} onShowDocument={onShowDocument} onShowPage={vi.fn()} />);
    await user.click(screen.getAllByText("How it was read")[0]);
    await user.click(screen.getAllByText(/Recognised as/)[0]);
    expect(onShowDocument).not.toHaveBeenCalled();
  });

  it("says in words and an icon when a document was not read or not reviewed", () => {
    const c = spec("c_loan_fraud");
    c.documents[0] = { ...c.documents[0], status: "partial", warnings: ["one table skipped"] };
    c.documents[1] = { ...c.documents[1], status: "failed", error: "bad scan" };
    render(<DocumentsPanel c={c} onShowDocument={vi.fn()} onShowPage={vi.fn()} />);
    expect(screen.getByText("Partly read")).toBeInTheDocument();
    expect(screen.getByText("Could not read")).toBeInTheDocument();
    expect(screen.getByText("Warnings: one table skipped")).toBeInTheDocument();
    expect(screen.getByText("Error: bad scan")).toBeInTheDocument();
  });

  it("flags low classifier confidence and offers the reading details", () => {
    const c = spec("c_loan_fraud");
    c.documents[0] = { ...c.documents[0], classification: { ...c.documents[0].classification, probability: 0.4 } };
    render(<DocumentsPanel c={c} onShowDocument={vi.fn()} onShowPage={vi.fn()} />);
    expect(screen.getByText("Low confidence about what kind of document this is")).toBeInTheDocument();
    expect(screen.getAllByText("How it was read").length).toBeGreaterThan(0);
  });

  it("explains an empty case", () => {
    const c = { ...spec("c_loan_fraud"), documents: [], not_checked_documents: [] };
    render(<DocumentsPanel c={c} onShowDocument={vi.fn()} onShowPage={vi.fn()} />);
    expect(screen.getByText("No documents were recognised")).toBeInTheDocument();
  });
});

describe("NotCheckedList", () => {
  const items = [{ document_id: "d4", doc_type: "boilerplate" as const, pages: "f1:3", reason: "classified as boilerplate: no checks apply" }];

  it("renders nothing when every page was reviewed", () => {
    const { container } = render(<NotCheckedList items={[]} onView={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names each page, gives the reason in plain English and views the page", async () => {
    const user = userEvent.setup();
    const onView = vi.fn();
    const c = spec("c_loan_fraud");
    render(<NotCheckedList items={items} onView={onView} documents={c.documents} files={c.files} />);
    expect(screen.getByRole("heading", { name: "Pages we could not review (1)" })).toBeInTheDocument();
    expect(screen.getByText(/not examined by any check/)).toBeInTheDocument();
    expect(screen.getByText(/standard wording/)).toBeInTheDocument();
    expect(screen.queryByText(/classified as boilerplate/)).toBeNull();
    expect(screen.getByTitle("d4 f1:3")).toHaveTextContent(/page 3$/);
    await user.click(screen.getByRole("button", { name: /View page/ }));
    expect(onView).toHaveBeenCalledWith("f1", 3);
  });

  it("falls back to the document type and page when the documents are not supplied", () => {
    render(<NotCheckedList items={items} onView={vi.fn()} />);
    expect(screen.getByText("Boilerplate page, page 3")).toBeInTheDocument();
  });
});
