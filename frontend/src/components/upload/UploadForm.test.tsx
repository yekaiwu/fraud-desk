import "@/test/mockEnv";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import { resetMockState, state } from "@/lib/mock/state";
import { nav, resetNav } from "@/test/navMock";
import { filesFromDrop } from "./FileDropzone";
import { UploadForm } from "./UploadForm";

vi.mock("next/navigation", async () => (await import("@/test/navMock")).navModule());

const pdf = (name: string, body = "%PDF-1.7 fake") => new File([body], name, { type: "application/pdf" });
const sized = (f: File, size: number) => (Object.defineProperty(f, "size", { value: size }), f);

beforeEach(() => {
  resetNav();
  resetMockState();
  state.latencyMs = 0;
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("upload form", () => {
  it("blocks 41 files, a 26 MB file and a .pdf whose bytes are text; duplicates only warn", { timeout: 30000 }, async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    const input = screen.getByLabelText(/Choose files/);
    await user.upload(input, Array.from({ length: 41 }, (_, i) => pdf(`f${i}.pdf`)));
    expect(await screen.findAllByText(/Too many files: 41/)).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
    cleanup();

    render(<UploadForm />);
    const input2 = screen.getByLabelText(/Choose files/);
    await user.upload(input2, sized(pdf("big.pdf"), 26 * 1024 * 1024));
    expect(await screen.findByText(/Larger than 25 MB/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Remove big.pdf" }));
    await user.upload(input2, pdf("fake.pdf", "just some text, not a pdf"));
    expect(await screen.findByText(/Not a PDF, JPEG or PNG/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Remove fake.pdf" }));

    await user.upload(input2, [pdf("same.pdf"), pdf("same.pdf")]);
    expect((await screen.findAllByText(/Same name and size as another file/)).length).toBe(2);
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
  });

  it("builds the exact FormData, shows progress, and redirects to the case with the job", { timeout: 20000 }, async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "createCase");
    render(<UploadForm />);
    await user.click(screen.getByRole("radio", { name: /Corporate onboarding \(KYC\)/ }));
    await user.type(screen.getByLabelText(/Reference/), "R-77");
    await user.type(screen.getByLabelText(/Applicant UEN/), "201912345a");
    await user.upload(screen.getByLabelText(/Choose files/), [pdf("a.pdf"), pdf("b.pdf")]);
    await screen.findByText("a.pdf");
    await user.click(screen.getByRole("button", { name: "Start analysis" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const form = spy.mock.calls[0][0] as FormData;
    expect(form.get("use_case")).toBe("kyc");
    expect(form.getAll("files")).toHaveLength(2);
    expect(form.get("reference")).toBe("R-77");
    expect(form.has("applicant_name")).toBe(false);
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith(expect.stringMatching(/^\/cases\/c_mock1\?job=j_mock1$/)));
  });

  it("keeps the form and focuses an alert when the API rejects the upload", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "createCase").mockRejectedValue(new ApiErrorException({ code: "unsupported_media_type", message: "x" }, 415));
    render(<UploadForm />);
    await user.upload(screen.getByLabelText(/Choose files/), pdf("a.pdf"));
    await screen.findByText("a.pdf");
    await user.click(screen.getByRole("button", { name: "Start analysis" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Only PDF, JPEG and PNG.");
    expect(screen.getByText("a.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
    await waitFor(() => expect(alert.parentElement).toHaveFocus());
  });

  it("mentions the fictitious-data rule and has visible labels", () => {
    render(<UploadForm />);
    expect(screen.getByText(/Use fictitious documents only in demos/)).toBeInTheDocument();
    expect(screen.getByText(/An invalid UEN is accepted and checked later/)).toBeInTheDocument();
  });

  it("offers three case types as radios, each with the lane it normally goes to", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => (r as HTMLInputElement).value)).toEqual(["loan", "kyc", "aml"]);
    expect(screen.getByRole("radio", { name: /Loan application/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Loan application/ })).toHaveAccessibleName(/Loan review/);
    expect(screen.getByRole("radio", { name: /Corporate onboarding/ })).toHaveAccessibleName(/Enhanced due diligence/);
    await user.click(screen.getByRole("radio", { name: /Source of funds \(AML\)/ }));
    expect(screen.getByRole("radio", { name: /Source of funds/ })).toBeChecked();
    const checklist = screen.getByRole("region", { name: "Documents usually needed" });
    expect(within(checklist).getByText("Source-of-funds declaration")).toBeInTheDocument();
    expect(within(checklist).queryByText("Financial statements")).toBeNull();
    const summary = screen.getByRole("region", { name: "Review and start" });
    expect(within(summary).getByText("AML investigation")).toBeInTheDocument();
  });

  it("ticks the checklist from file names only and marks the rest Not yet added, without blocking", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    const checklist = screen.getByRole("region", { name: "Documents usually needed" });
    expect(within(checklist).getAllByText("Not yet added")).toHaveLength(4);
    expect(within(checklist).getByText(/Based on file names; the system reads the content afterwards/)).toBeInTheDocument();
    await user.upload(screen.getByLabelText(/Choose files/), [pdf("Acme_FY2024_Financial_Statements.pdf"), pdf("DBS-statement-Jan.pdf"), pdf("scan001.pdf")]);
    await screen.findByText("scan001.pdf");
    const rows = within(checklist).getAllByRole("listitem");
    const state = (label: string) => rows.find((r) => r.textContent?.includes(label))?.getAttribute("data-found");
    expect(state("Financial statements")).toBe("true");
    expect(state("Bank statements")).toBe("true");
    expect(state("Invoices")).toBe("false");
    expect(state("Contracts or purchase orders")).toBe("false");
    expect(within(checklist).getAllByText("Not yet added")).toHaveLength(2);
    expect(within(checklist).getByText(/1 file could not be matched by name/)).toBeInTheDocument();
    expect(screen.getByText("Looks like: Bank statements")).toBeInTheDocument();
    // missing documents never block the button
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
  });

  it("does not tick the checklist for a file that cannot be uploaded", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    await user.upload(screen.getByLabelText(/Choose files/), pdf("invoice-7.pdf", "plain text"));
    await screen.findByText(/Not a PDF, JPEG or PNG/);
    const checklist = screen.getByRole("region", { name: "Documents usually needed" });
    expect(within(checklist).getAllByText("Not yet added")).toHaveLength(4);
  });

  it("summarises the case: file count, size, usual set and what is missing", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    const summary = screen.getByRole("region", { name: "Review and start" });
    expect(within(summary).getByText("None added yet")).toBeInTheDocument();
    expect(within(summary).getByRole("button", { name: "Start analysis" })).toBeDisabled();
    expect(within(summary).getByText("Add at least one file to continue.")).toBeInTheDocument();
    await user.upload(screen.getByLabelText(/Choose files/), [pdf("bank statement mar.pdf"), pdf("tax invoice 7.pdf")]);
    await screen.findByText("bank statement mar.pdf");
    expect(within(summary).getByText(/2 files, 26 B/)).toBeInTheDocument();
    expect(within(summary).getByText("2 of 4 added")).toBeInTheDocument();
    expect(within(summary).getByText(/Not yet added: Financial statements, Contracts or purchase orders/)).toBeInTheDocument();
    expect(within(summary).getByText(/You can still start/)).toBeInTheDocument();
  });

  it("shows a calm progress state with a link to the case after the case is created", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    await user.upload(screen.getByLabelText(/Choose files/), pdf("a.pdf"));
    await screen.findByText("a.pdf");
    await user.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(await screen.findByText("Reading documents...")).toBeInTheDocument();
    expect(screen.getByText("Running checks...")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the case" })).toHaveAttribute("href", "/cases/c_mock1?job=j_mock1");
  });

  it.each([
    [413, "file_too_large", "File too large"],
    [422, "validation_error", "Check the form"],
    [429, "rate_limited", ""],
    [503, "busy", "Server is busy"],
  ])("explains a %i %s rejection and keeps the files", async (status, code, title) => {
    const user = userEvent.setup();
    vi.spyOn(api, "createCase").mockRejectedValue(new ApiErrorException({ code, message: "m" }, status));
    render(<UploadForm />);
    await user.upload(screen.getByLabelText(/Choose files/), pdf("a.pdf"));
    await screen.findByText("a.pdf");
    await user.click(screen.getByRole("button", { name: "Start analysis" }));
    const alert = await screen.findByRole("alert");
    if (title) expect(alert).toHaveTextContent(title);
    expect(screen.getByText("a.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled();
  });

  it("removes one file or all of them", async () => {
    const user = userEvent.setup();
    render(<UploadForm />);
    await user.upload(screen.getByLabelText(/Choose files/), [pdf("a.pdf"), pdf("b.pdf")]);
    await screen.findByText("b.pdf");
    await user.click(screen.getByRole("button", { name: "Remove a.pdf" }));
    expect(screen.queryByText("a.pdf")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Remove all" }));
    expect(screen.queryByText("b.pdf")).toBeNull();
  });
});

describe("filesFromDrop", () => {
  it("falls back to the plain file list and skips hidden files", async () => {
    const dt = { items: [], files: [pdf("a.pdf"), pdf(".DS_Store")] } as unknown as DataTransfer;
    expect((await filesFromDrop(dt)).map((f) => f.name)).toEqual(["a.pdf"]);
  });

  it("walks dropped folders", async () => {
    const file = (name: string): FileSystemFileEntry =>
      ({ isFile: true, isDirectory: false, name, file: (ok: (f: File) => void) => ok(pdf(name)) }) as unknown as FileSystemFileEntry;
    const dir = (children: FileSystemEntry[]): FileSystemDirectoryEntry => {
      let served = false;
      return {
        isFile: false,
        isDirectory: true,
        createReader: () => ({ readEntries: (ok: (e: FileSystemEntry[]) => void) => (served ? ok([]) : ((served = true), ok(children))) }),
      } as unknown as FileSystemDirectoryEntry;
    };
    const tree = dir([file("one.pdf"), file(".hidden"), dir([file("two.pdf")])]);
    const dt = { items: [{ kind: "file", webkitGetAsEntry: () => tree }], files: [] } as unknown as DataTransfer;
    expect((await filesFromDrop(dt)).map((f) => f.name).sort()).toEqual(["one.pdf", "two.pdf"]);
  });
});
