import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import { BatchDeleteDialog } from "./BatchDeleteDialog";

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), deleteCase: vi.fn() }));

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(api.deleteCase).mockReset();
});
afterEach(cleanup);

function open(ids: string[]) {
  const handlers = { onClose: vi.fn(), onDone: vi.fn() };
  render(<BatchDeleteDialog open ids={ids} {...handlers} />);
  return handlers;
}

async function fill(user: ReturnType<typeof userEvent.setup>, reviewer: string, reason: string) {
  await user.type(screen.getByLabelText(/Reviewer/i), reviewer);
  await user.type(screen.getByLabelText(/Reason/i), reason);
}

describe("BatchDeleteDialog", () => {
  it("names how many cases go and asks for the reviewer and reason once", async () => {
    open(["c_1", "c_2", "c_3"]);
    expect(screen.getByRole("heading", { name: "Delete 3 cases?" })).toBeInTheDocument();
    expect(screen.getAllByLabelText(/Reviewer/i)).toHaveLength(1);
    expect(screen.getAllByLabelText(/Reason/i)).toHaveLength(1);
  });

  it("deletes nothing until a reviewer and a reason are given", async () => {
    const user = userEvent.setup();
    const { onDone } = open(["c_1", "c_2"]);
    await user.click(screen.getByRole("button", { name: "Delete 2 cases" }));
    expect(api.deleteCase).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("deletes every selected case with the same reviewer and reason", async () => {
    const user = userEvent.setup();
    vi.mocked(api.deleteCase).mockResolvedValue();
    const { onDone } = open(["c_1", "c_2"]);
    await fill(user, "Jay", "Clearing test uploads");
    await user.click(screen.getByRole("button", { name: "Delete 2 cases" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(api.deleteCase).toHaveBeenNthCalledWith(1, "c_1", "Jay", "Clearing test uploads");
    expect(api.deleteCase).toHaveBeenNthCalledWith(2, "c_2", "Jay", "Clearing test uploads");
    expect(onDone).toHaveBeenCalledWith({ deleted: ["c_1", "c_2"], failed: [] });
  });

  it("names a case that is still being analysed and still deletes the others", async () => {
    const user = userEvent.setup();
    vi.mocked(api.deleteCase).mockImplementation(async (id) => {
      if (id === "c_2") throw new ApiErrorException({ code: "case_processing", message: "The case is still being analysed." }, 409);
    });
    const { onDone } = open(["c_1", "c_2", "c_3"]);
    await fill(user, "Jay", "Clearing test uploads");
    await user.click(screen.getByRole("button", { name: "Delete 3 cases" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onDone).toHaveBeenCalledWith({ deleted: ["c_1", "c_3"], failed: [{ id: "c_2", why: "still being analysed" }] });
    expect(await screen.findByRole("alert")).toHaveTextContent("1 could not be deleted");
    expect(screen.getByRole("alert")).toHaveTextContent("c_2 (still being analysed)");
  });
});
