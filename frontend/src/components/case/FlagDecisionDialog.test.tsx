import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiErrorException } from "@/lib/errors";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import * as api from "@/lib/api";
import { FlagDecisionDialog, decisionTitle } from "./FlagDecisionDialog";

vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), postReview: vi.fn() }));

const c = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
const highFlag = c.findings[0].flag_ids[0];
const mediumFlag = c.findings[1].flag_ids[0];

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(api.postReview).mockReset();
});
afterEach(cleanup);

function open(target: Parameters<typeof FlagDecisionDialog>[0]["target"]) {
  const handlers = { onClose: vi.fn(), onDone: vi.fn(), onSelectFlag: vi.fn(), onReload: vi.fn() };
  render(<FlagDecisionDialog c={c} target={target} {...handlers} />);
  return { ...handlers, dialog: screen.getByRole("dialog", { hidden: true }) };
}

describe("decisionTitle", () => {
  it("words one, many and whole-finding decisions", () => {
    expect(decisionTitle(false, false, 1)).toBe("Mark this as not a concern");
    expect(decisionTitle(true, false, 3)).toBe("Mark 3 as real concerns");
    expect(decisionTitle(false, false, 3)).toBe("Mark 3 as not a concern");
    expect(decisionTitle(false, true, 4)).toBe("Mark all 4 as not a concern");
    expect(decisionTitle(true, true, 1)).toBe("Mark this as a real concern");
  });
});

describe("FlagDecisionDialog", () => {
  it("describes the concern in words, not by check id, and warns that a high dismissal needs a second approver", () => {
    const { dialog } = open({ decision: "dismiss", flagIds: [highFlag] });
    const d = within(dialog);
    expect(d.getByRole("heading", { name: "Mark this as not a concern" })).toBeInTheDocument();
    expect(d.getByText("High concern")).toBeInTheDocument();
    expect(d.getByText(/running balance on this row/)).toBeInTheDocument();
    expect(d.getByText(/Bank statement/)).toBeInTheDocument();
    expect(dialog.textContent).not.toMatch(/bs_running_balance/);
    expect(d.getByRole("note")).toHaveTextContent("A second reviewer must approve this dismissal");
  });

  it("does not show the second-approver note for a medium dismissal or for confirming", () => {
    const first = open({ decision: "dismiss", flagIds: [mediumFlag] });
    expect(within(first.dialog).queryByRole("note")).toBeNull();
    cleanup();
    const second = open({ decision: "confirm", flagIds: [highFlag] });
    expect(within(second.dialog).queryByRole("note")).toBeNull();
    expect(within(second.dialog).getByRole("heading", { name: "Mark this as a real concern" })).toBeInTheDocument();
  });

  it("requires a reviewer and a reason of at least 5 characters, with a counter, before anything is sent", async () => {
    const user = userEvent.setup();
    const { dialog } = open({ decision: "dismiss", flagIds: [mediumFlag] });
    const d = within(dialog);
    await user.type(d.getByLabelText(/Reviewer name/), "a.tan");
    await user.type(d.getByLabelText(/Reason \(required/), "abcd");
    expect(d.getByText("4/1000")).toBeInTheDocument();
    await user.click(d.getByRole("button", { name: "Mark this as not a concern" }));
    expect(d.getByText("Enter a reason of at least 5 characters.")).toBeInTheDocument();
    expect(api.postReview).not.toHaveBeenCalled();
  });

  it("sends the exact request for a flag and closes", async () => {
    const user = userEvent.setup();
    vi.mocked(api.postReview).mockResolvedValue({ entry: { id: "r9" } } as never);
    const { dialog, onDone, onClose } = open({ decision: "dismiss", flagIds: [mediumFlag] });
    const d = within(dialog);
    await user.type(d.getByLabelText(/Reviewer name/), "a.tan");
    await user.type(d.getByLabelText(/Reason \(required/), "Matches the contract amendment.");
    await user.click(d.getByRole("button", { name: "Mark this as not a concern" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("r9"));
    expect(api.postReview).toHaveBeenCalledWith(c.id, { kind: "flag_decision", decision: "dismiss", reviewer: "a.tan", reason: "Matches the contract amendment.", case_version: c.version, flag_ids: [mediumFlag] });
    expect(onClose).toHaveBeenCalled();
  });

  it("sends finding_id for a whole finding", async () => {
    const user = userEvent.setup();
    vi.mocked(api.postReview).mockResolvedValue({ entry: { id: "r2" } } as never);
    const { dialog } = open({ decision: "confirm", findingId: "fnd_3" });
    const d = within(dialog);
    expect(d.getByRole("heading", { name: "Mark all 2 as a real concern" })).toBeInTheDocument();
    await user.type(d.getByLabelText(/Reviewer name/), "a.tan");
    await user.type(d.getByLabelText(/Reason \(required/), "Both invoices are unpaid.");
    await user.click(d.getByRole("button", { name: "Mark all 2 as a real concern" }));
    await waitFor(() => expect(api.postReview).toHaveBeenCalled());
    expect(vi.mocked(api.postReview).mock.calls[0][1]).toMatchObject({ kind: "flag_decision", decision: "confirm", finding_id: "fnd_3" });
  });

  it("keeps the dialog open and shows the error, naming blocking concerns by number", async () => {
    const user = userEvent.setup();
    vi.mocked(api.postReview).mockRejectedValue(
      new ApiErrorException({ code: "high_flags_open", message: "x", details: { flag_ids: [highFlag] } }, 409, "req-1"),
    );
    const { dialog, onSelectFlag, onClose } = open({ decision: "confirm", flagIds: [mediumFlag] });
    const d = within(dialog);
    await user.type(d.getByLabelText(/Reviewer name/), "a.tan");
    await user.type(d.getByLabelText(/Reason \(required/), "Reasonable grounds.");
    await user.click(d.getByRole("button", { name: "Mark this as a real concern" }));
    const alert = await d.findByRole("alert");
    const link = within(alert).getByRole("button", { name: "Concern 1" });
    await user.click(link);
    expect(onSelectFlag).toHaveBeenCalledWith(highFlag);
    expect(onClose).not.toHaveBeenCalled();
  });
});
