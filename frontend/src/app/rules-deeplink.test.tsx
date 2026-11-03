import "@/test/mockEnv";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetMockState, state } from "@/lib/mock/state";
import { renderWithSWR } from "@/test/utils";
import RulesPage from "./rules/page";

let query = "check=bs_running_balance";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(query) }));

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
  query = "check=bs_running_balance";
});
afterEach(cleanup);

describe("Check catalogue opened from the Control performance page", () => {
  it("opens the editor of the check named in the link, and stays closed once closed", async () => {
    const user = userEvent.setup();
    renderWithSWR(<RulesPage />);
    const dialog = await screen.findByRole("dialog", { name: /running balance/i });
    expect(dialog).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: /^(Close|Cancel)/ })[0]);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /running balance/i })).toBeNull());
    expect(await screen.findAllByText("bs_running_balance")).not.toHaveLength(0); // the catalogue itself is still there
  });

  it("opens no editor without a link", async () => {
    query = "";
    renderWithSWR(<RulesPage />);
    await screen.findAllByText("bs_running_balance");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
