import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { packUrl } from "@/lib/api";
import { CaseActions } from "./CaseActions";

afterEach(cleanup);
const spec = (id: string) => buildCase(SPECS.find((s) => s.id === id)!);

function setup(id: string, isCurrent = true) {
  const p = { onRerun: vi.fn(), onAddDocs: vi.fn(), onDelete: vi.fn() };
  render(<CaseActions c={spec(id)} isCurrent={isCurrent} {...p} />);
  return p;
}

describe("CaseActions", () => {
  it("opens a menu with pack, export, re-run, add documents and delete, and runs the chosen action", async () => {
    const user = userEvent.setup();
    const p = setup("c_loan_fraud");
    const trigger = screen.getByRole("button", { name: "More actions" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Case pack", "Export JSON", "Re-run analysis", "Add documents", "Delete case..."]);
    expect(screen.getByRole("menuitem", { name: "Case pack" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("menuitem", { name: "Export JSON" })).toHaveAttribute("download");
    await user.click(screen.getByRole("menuitem", { name: "Re-run analysis" }));
    expect(p.onRerun).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("closes on Escape and returns focus", async () => {
    const user = userEvent.setup();
    setup("c_loan_fraud");
    const trigger = screen.getByRole("button", { name: "More actions" });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("moves with the arrow keys", async () => {
    const user = userEvent.setup();
    setup("c_loan_fraud");
    const trigger = screen.getByRole("button", { name: "More actions" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    await vi.waitFor(() => expect(screen.getByRole("menuitem", { name: "Case pack" })).toHaveFocus());
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Export JSON" })).toHaveFocus();
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Delete case..." })).toHaveFocus();
  });

  it("keeps re-run and add documents visible but disabled, with the reason, on a closed case and an old version", async () => {
    const user = userEvent.setup();
    const p = setup("c_aml_closed");
    await user.click(screen.getByRole("button", { name: "More actions" }));
    const rerun = screen.getByRole("menuitem", { name: /Re-run analysis/ });
    expect(rerun).toHaveAttribute("aria-disabled", "true");
    expect(rerun).toHaveTextContent("Not on a closed case.");
    await user.click(rerun);
    expect(p.onRerun).not.toHaveBeenCalled();
    cleanup();
    setup("c_loan_v2", false);
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("menuitem", { name: /Add documents/ })).toHaveTextContent("Only on the current version.");
    expect(screen.getByRole("menuitem", { name: "Case pack" })).toHaveAttribute("href", packUrl("c_loan_v2", "html", spec("c_loan_v2").version));
  });

  it("offers only delete while the case is being analysed", async () => {
    const user = userEvent.setup();
    setup("c_kyc_processing");
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Delete case..."]);
  });
});
