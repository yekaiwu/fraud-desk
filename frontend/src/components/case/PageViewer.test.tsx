import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { PageViewer } from "./PageViewer";

afterEach(cleanup);
const fraud = () => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);

function setup(over: { current?: { fileId: string; page: number }; documents?: boolean; selected?: string | null } = {}) {
  const c = fraud();
  const numbered = numberFlags(c.findings, c.flags);
  const onNavigate = vi.fn();
  const onSelectFlag = vi.fn();
  const current = over.current ?? { fileId: "f1", page: 1 };
  const ui = (cur = current) => (
    <PageViewer
      caseId={c.id}
      files={c.files}
      documents={over.documents === false ? undefined : c.documents}
      flags={numbered.all}
      selectedFlagId={over.selected ?? null}
      onSelectFlag={onSelectFlag}
      current={cur}
      onNavigate={onNavigate}
    />
  );
  const view = render(ui());
  return { c, numbered, onNavigate, onSelectFlag, view, ui };
}

describe("PageViewer toolbar", () => {
  it("offers a document and page picker with friendly names and flagged counts", async () => {
    const user = userEvent.setup();
    const { onNavigate, c } = setup();
    const picker = screen.getByRole("combobox", { name: "Document and page" });
    const options = within(picker).getAllByRole("option").map((o) => o.textContent);
    expect(options[0]).toMatch(/^Bank statement.*page 1 \(\d+ flagged\)$/);
    expect(options.some((o) => /^Invoice/.test(o ?? ""))).toBe(true);
    expect(options.join(" ")).not.toMatch(/\bf\d\b/);
    const last = c.files[c.files.length - 1];
    await user.selectOptions(picker, `${last.id}:1`);
    expect(onNavigate).toHaveBeenCalledWith({ fileId: last.id, page: 1 });
  });

  it("falls back to tidied file names when documents are not supplied", () => {
    setup({ documents: false });
    const picker = screen.getByRole("combobox", { name: "Document and page" });
    expect(within(picker).getAllByRole("option")[0].textContent).toMatch(/^Bank-statement-sep-2026, page 1/);
  });

  it("moves between pages, disabled at the ends, with 'Page n of m'", async () => {
    const user = userEvent.setup();
    const { c, onNavigate } = setup({ current: { fileId: "f1", page: 1 } });
    const count = c.files[0].pages.length;
    expect(screen.getByText(`Page 1 of ${count}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    if (count > 1) {
      await user.click(screen.getByRole("button", { name: "Next page" }));
      expect(onNavigate).toHaveBeenCalledWith({ fileId: "f1", page: 2 });
    }
  });

  it("zooms with Fit, 150% and 200%", async () => {
    const user = userEvent.setup();
    setup();
    const group = screen.getByRole("group", { name: "Zoom" });
    expect(within(group).getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(group).getByRole("button", { name: "150%" }));
    expect(within(group).getByRole("button", { name: "150%" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "false");
  });

  it("lists pages with flags and counts, and jumps to one", async () => {
    const user = userEvent.setup();
    const { onNavigate } = setup();
    const chip = screen.getAllByRole("button", { pressed: false }).find((b) => /Invoice/.test(b.textContent ?? "") && /\d$/.test(b.textContent ?? ""));
    expect(chip).toBeDefined();
    await user.click(chip!);
    expect(onNavigate).toHaveBeenCalledWith(expect.objectContaining({ page: expect.any(Number) }));
  });
});

describe("PageViewer page", () => {
  it("presents the page image lazily with the file name in its alt text, and numbered region markers", () => {
    setup();
    const img = screen.getByRole("img", { name: /Page 1 of .*\.pdf, \d+ flagged region/ });
    expect(img).toHaveAttribute("loading", "lazy");
    expect(screen.getByRole("region", { name: /Page image/ })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Flag \d+,/ }).length).toBeGreaterThan(0);
    expect(document.querySelector(".region-chip")).toHaveTextContent(/^\d+$/);
  });

  it("shows a key for the outlines on the page, and what is marked", () => {
    setup();
    const key = screen.getByRole("list", { name: "Key to the outlines" });
    expect(key).toHaveTextContent(/concern/);
    expect(screen.getByText("The numbers match the findings list.")).toBeInTheDocument();
  });

  it("starts with a skeleton, then says the image is unavailable on error, and retries", async () => {
    const user = userEvent.setup();
    setup();
    expect(document.querySelector(".skeleton")).not.toBeNull();
    const img = screen.getByRole("img", { name: /Page 1 of/ });
    fireEvent.error(img);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Page image unavailable");
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("img", { name: /Page 1 of/ })).toBeInTheDocument();
  });

  it("removes the skeleton when the image loads", () => {
    setup();
    fireEvent.load(screen.getByRole("img", { name: /Page 1 of/ }));
    expect(document.querySelector(".skeleton")).toBeNull();
  });

  it("warns when a page could not be read", () => {
    const c = fraud();
    c.files[0].pages[0] = { ...c.files[0].pages[0], error: "no text layer" };
    const numbered = numberFlags(c.findings, c.flags);
    render(<PageViewer caseId={c.id} files={c.files} documents={c.documents} flags={numbered.all} selectedFlagId={null} onSelectFlag={vi.fn()} current={{ fileId: "f1", page: 1 }} onNavigate={vi.fn()} />);
    expect(screen.getByText("This page could not be read")).toBeInTheDocument();
    expect(screen.getByText("no text layer")).toBeInTheDocument();
  });

  it("selects a flag from its marker", async () => {
    const user = userEvent.setup();
    const { onSelectFlag } = setup();
    await user.click(screen.getAllByRole("button", { name: /^Flag 1,/ })[0]);
    expect(onSelectFlag).toHaveBeenCalledTimes(1);
  });
});

describe("PageViewer without files", () => {
  it("says there is nothing to show", () => {
    render(<PageViewer caseId="c" files={[]} flags={[]} selectedFlagId={null} onSelectFlag={vi.fn()} current={{ fileId: "", page: 1 }} onNavigate={vi.fn()} />);
    expect(screen.getByText("No pages to show")).toBeInTheDocument();
  });
});
