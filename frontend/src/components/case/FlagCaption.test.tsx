import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeDocNamer } from "@/lib/docNames";
import { numberFlags } from "@/lib/flags";
import type { Document, FileRef, Flag } from "@/lib/types";
import { FlagCaption } from "./FlagCaption";

afterEach(cleanup);

const docs = [
  { id: "d1", file_id: "f1", page_start: 1, page_end: 1, doc_type: "bank_statement", fields: {}, tables: {} },
  { id: "d2", file_id: "f2", page_start: 1, page_end: 1, doc_type: "bank_statement", fields: {}, tables: {} },
] as unknown as Document[];
const files = [
  { id: "f1", name: "statement-apr.pdf", pages: [{ page: 1 }] },
  { id: "f2", name: "statement-may.pdf", pages: [{ page: 1 }] },
] as unknown as FileRef[];

const flag = (over: Partial<Flag> = {}): Flag => ({
  id: "x", rule: "pdf_editor_software", stage: "forensics", severity: "medium", document_ids: ["d1"], table: null, field: null, row: null,
  region: null, extra_regions: [], evidence: "Producer='Adobe Photoshop 25.1'; Creator='Adobe Photoshop 25.1'", explanation: "The file names an image editor as its software.", disposition: "open", ...over,
});

function show(f: Flag, current = { fileId: "f1", page: 1 }) {
  const onNavigate = vi.fn();
  const nf = numberFlags([], [f]).all[0];
  render(<FlagCaption selected={nf} documents={docs} names={makeDocNamer(docs, files)} onNavigate={onNavigate} current={current} />);
  return onNavigate;
}

describe("FlagCaption", () => {
  it("says a flag with no region is about the file itself, and shows what was found", () => {
    show(flag());
    const c = screen.getByTestId("flag-caption");
    expect(c).toHaveTextContent("Medium");
    expect(c).toHaveTextContent("applies to the whole file, not one spot on a page");
    expect(c).toHaveTextContent("The file names an image editor as its software.");
    expect(c).toHaveTextContent(/about the file itself/);
    expect(c).toHaveTextContent("Adobe Photoshop 25.1");
  });

  it("lists the documents of a multi-document flag as links to their first pages", async () => {
    const onNavigate = show(flag({ document_ids: ["d1", "d2"] }));
    expect(screen.getByText("Documents this applies to")).toBeInTheDocument();
    const links = screen.getAllByRole("button");
    expect(links).toHaveLength(2);
    await userEvent.setup().click(links[1]);
    expect(onNavigate).toHaveBeenCalledWith({ fileId: "f2", page: 1 });
  });

  it("says a flag with neither region nor document concerns the case as a whole", () => {
    show(flag({ document_ids: [] }));
    expect(screen.getByTestId("flag-caption")).toHaveTextContent(/case as a whole/);
  });

  it("names the related evidence by letter, and offers a link when it is on another page", async () => {
    const onNavigate = show(
      flag({
        region: { file_id: "f1", page: 1, x: 0.5, y: 0.3, w: 0.1, h: 0.02 },
        extra_regions: [
          { file_id: "f1", page: 1, x: 0.5, y: 0.2, w: 0.1, h: 0.02, label: "opening balance" },
          { file_id: "f2", page: 1, x: 0.1, y: 0.1, w: 0.1, h: 0.02, label: "prior statement" },
        ],
      }),
    );
    expect(screen.getByText("opening balance")).toBeInTheDocument();
    expect(screen.getByText("prior statement")).toBeInTheDocument();
    expect(screen.getAllByText(/^[ab]$/)).toHaveLength(2);
    await userEvent.setup().click(screen.getByRole("button", { name: /^on / }));
    expect(onNavigate).toHaveBeenCalledWith({ fileId: "f2", page: 1 });
    expect(screen.queryByText(/about the file itself/)).toBeNull(); // a marked figure needs no such note
  });
});
