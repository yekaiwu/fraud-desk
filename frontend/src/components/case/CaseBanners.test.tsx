import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { CaseBanners, PARTIAL_TEXT } from "./CaseBanners";

afterEach(cleanup);
const spec = (id: string) => buildCase(SPECS.find((s) => s.id === id)!);

function banners(c: ReturnType<typeof spec>, isCurrent = true) {
  const p = { onGoCoverage: vi.fn(), onGoReview: vi.fn(), onRerun: vi.fn() };
  const r = render(<CaseBanners c={c} isCurrent={isCurrent} currentHref={`/cases/${c.id}`} {...p} />);
  return { ...p, ...r };
}

describe("CaseBanners", () => {
  it("renders nothing for a clean, current case", () => {
    const { container } = banners(spec("c_loan_clean"));
    expect(container).toBeEmptyDOMElement();
  });

  it("explains a partial score with humanised reasons and a link to coverage", async () => {
    const user = userEvent.setup();
    const p = banners(spec("c_loan_partial"));
    expect(screen.getByText(PARTIAL_TEXT)).toBeInTheDocument();
    expect(screen.getByText("The extract stage failed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "See the coverage panel" }));
    expect(p.onGoCoverage).toHaveBeenCalled();
  });

  it("points an old version back to the current one", () => {
    banners(spec("c_loan_v2"), false);
    expect(screen.getByText(/Viewing version \d, read-only\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to current" })).toHaveAttribute("href", "/cases/c_loan_v2");
  });

  it("shows a failed analysis as an alert with a re-run, only on the current version", async () => {
    const user = userEvent.setup();
    const p = banners(spec("c_loan_error"));
    expect(screen.getByRole("alert")).toHaveTextContent("Analysis failed");
    await user.click(screen.getByRole("button", { name: "Re-run" }));
    expect(p.onRerun).toHaveBeenCalled();
    cleanup();
    banners(spec("c_loan_error"), false);
    expect(screen.queryByRole("button", { name: "Re-run" })).toBeNull();
  });

  it("counts dismissals waiting for a second reviewer", async () => {
    const user = userEvent.setup();
    const c = spec("c_kyc_fraud");
    const p = banners({ ...c, pending_second_approvals: [{ entry_id: "r1", flag_ids: ["a"], reviewer: "a.tan", created_at: "2026-10-27T10:00:00Z" }] });
    expect(screen.getByText("1 dismissal is waiting for a second reviewer.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open Decision & activity" }));
    expect(p.onGoReview).toHaveBeenCalled();
  });
});
