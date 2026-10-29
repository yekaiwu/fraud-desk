import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { CaseHeader } from "./CaseHeader";
import { neighbours } from "./CaseNeighbours";
import type { QueueItem } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const c = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);

describe("CaseHeader", () => {
  it("names the applicant as the page title with UEN, reference, badges and the way back", () => {
    render(<CaseHeader c={{ ...c, applicant: { name: "Acme Trading Pte. Ltd.", uen: "201234567A", source: "upload" } }} versions={[]} onVersion={() => {}} />);
    expect(screen.getByRole("heading", { level: 1, name: "Acme Trading Pte. Ltd." })).toBeInTheDocument();
    expect(screen.getByText("201234567A")).toHaveClass("num");
    expect(screen.getByText("Loan application")).toBeInTheDocument();
    expect(screen.getByText("Ready for review")).toBeInTheDocument();
    expect(screen.getByText(c.reference ?? c.id)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to worklist" })).toHaveAttribute("href", "/");
    expect(screen.getByText(/^Received /)).toBeInTheDocument();
    expect(screen.queryByText("Version 2")).toBeNull();
  });

  it("renders an untrusted applicant name as literal text", () => {
    render(<CaseHeader c={{ ...c, applicant: { name: "<img src=x onerror=alert(1)>", uen: null, source: "upload" } }} versions={[]} onVersion={() => {}} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });

  it("shows a version tag once the case has been re-run, and 'Waiting' while documents are awaited", () => {
    render(<CaseHeader c={{ ...c, version: 2, status: "awaiting_documents" }} versions={[]} onVersion={() => {}} />);
    expect(screen.getByText("Version 2")).toBeInTheDocument();
    expect(screen.getByText(/^Waiting \d/)).toBeInTheDocument();
  });

  it("copies the reference and says so", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CaseHeader c={c} versions={[]} onVersion={() => {}} />);
    await user.click(screen.getByRole("button", { name: `Copy reference ${c.reference ?? c.id}` }));
    expect(writeText).toHaveBeenCalledWith(c.reference ?? c.id);
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("reports when the clipboard is blocked", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    document.execCommand = vi.fn().mockReturnValue(false);
    render(<CaseHeader c={c} versions={[]} onVersion={() => {}} />);
    await user.click(screen.getByRole("button", { name: /^Copy reference/ }));
    await waitFor(() => expect(screen.getByText("Could not copy")).toBeInTheDocument());
  });

  it("links to the previous and next case when the worklist is known", () => {
    render(<CaseHeader c={c} versions={[]} onVersion={() => {}} nav={{ prev: "c_a", next: "c_b", position: 2, total: 5 }} />);
    expect(screen.getByRole("link", { name: /Previous case/ })).toHaveAttribute("href", "/cases/c_a");
    expect(screen.getByRole("link", { name: /Next case/ })).toHaveAttribute("href", "/cases/c_b");
    expect(screen.getByText("2 of 5")).toBeInTheDocument();
  });
});

const q = (id: string, status: QueueItem["status"] = "open"): QueueItem => ({ case_id: id, status }) as QueueItem;

describe("neighbours", () => {
  it("skips closed cases and gives no navigation when the case is not on the page", () => {
    const items = [q("a"), q("b", "closed"), q("c"), q("d")];
    expect(neighbours(items, "c")).toEqual({ prev: "a", next: "d", position: 3, total: 4 });
    expect(neighbours(items, "a")).toEqual({ prev: null, next: "c", position: 1, total: 4 });
    expect(neighbours(items, "zzz")).toBeNull();
    expect(neighbours([q("only")], "only")).toBeNull();
  });
});
