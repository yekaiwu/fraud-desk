import "@/test/mockEnv";
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetMockState, state } from "@/lib/mock/state";
import { nav, resetNav } from "@/test/navMock";
import { clearViewport, renderWithSWR } from "@/test/utils";
import CasePage from "./cases/[id]/page";

vi.mock("next/navigation", async () => (await import("@/test/navMock")).navModule());

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

async function openCase(id: string) {
  nav.route = { id };
  renderWithSWR(<CasePage />);
  await screen.findByRole("heading", { level: 1 });
}

describe("explainers on the case page", () => {
  it("puts a question mark on the score, the checks meter and the suggested next step in the hero", async () => {
    await openCase("c_loan_fraud");
    for (const name of ["How the risk score is calculated", "What checks completed means", "How the suggested next step was reached"]) {
      expect(screen.getAllByRole("button", { name }).length).toBeGreaterThan(0);
    }
  });

  it("opens the score explanation from the gauge with this case's total", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_fraud");
    await user.click(screen.getAllByRole("button", { name: "How the risk score is calculated" })[0]);
    const panel = await screen.findByRole("dialog", { name: "How the risk score is calculated" });
    expect(within(panel).getByText(/Total 75, so the score is 75\./)).toBeInTheDocument();
  });

  it("gives every finding a Why was this flagged question mark, and the blocking label its own", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_hardstop");
    await user.click(screen.getByRole("button", { name: /Medium concern/ })); // medium findings start collapsed
    expect(screen.getAllByRole("button", { name: "Why was this flagged?" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Why this blocks the fast lane" })).toBeInTheDocument();
  });

  it("explains the coverage panel and the pages we could not review", async () => {
    await openCase("c_loan_partial");
    expect(screen.getAllByRole("button", { name: "What checks completed means" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("button", { name: "Why pages are not reviewed" })).toBeInTheDocument();
  });

  it("shows no score explainer while there is no score", async () => {
    await openCase("c_loan_error");
    expect(screen.queryByRole("button", { name: "How the risk score is calculated" })).toBeNull();
  });

  it("opens 'How this was analysed' as a sheet with the six steps", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_fraud");
    await user.click(screen.getByRole("button", { name: "How this was analysed" }));
    const dlg = await screen.findByRole("dialog", { name: "Example Trading Pte. Ltd." });
    expect(within(dlg).getByRole("heading", { level: 3, name: "Identified each document" })).toBeInTheDocument();
    expect(within(dlg).getByText(/Running the same documents again gives the same checks and the same flags\./)).toBeInTheDocument();
  });
});
