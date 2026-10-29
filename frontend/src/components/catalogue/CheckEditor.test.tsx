import "@/test/mockEnv";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import useSWR from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import * as mock from "@/lib/mock/handlers";
import { resetMockState, state } from "@/lib/mock/state";
import { renderWithSWR } from "@/test/utils";
import { CheckEditor, type SavedKind } from "./CheckEditor";
import type { RuleInfo } from "@/lib/types";

const who = { reviewer: "j.lim", reason: "Agreed with credit policy" };

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

/** The editor the way the Check catalogue page mounts it: an opener button, the catalogue from the API, the sheet. */
function Harness({ ruleId = "bs_running_balance", onSaved = vi.fn() }: { ruleId?: string; onSaved?: (u: RuleInfo, k: SavedKind) => void }) {
  const { data } = useSWR("rules", () => api.getRules());
  const [open, setOpen] = useState(false);
  const rule = data?.rules.find((r) => r.rule === ruleId) ?? null;
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} disabled={!data}>
        Open editor
      </button>
      {data ? (
        <CheckEditor
          rule={rule}
          rules={data.rules}
          open={open}
          onClose={() => setOpen(false)}
          onSaved={(u, k) => {
            onSaved(u, k);
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

async function openEditor(props: Parameters<typeof Harness>[0] = {}) {
  const user = userEvent.setup();
  renderWithSWR(<Harness {...props} />);
  const opener = await waitFor(() => {
    const b = screen.getByRole("button", { name: "Open editor" });
    expect(b).toBeEnabled();
    return b;
  });
  await user.click(opener);
  const dialog = await screen.findByRole("dialog");
  return { user, dialog, opener };
}

const saveButton = (d: HTMLElement) => within(d).getByRole("button", { name: /Save changes|Saving/ });
const reviewerField = (d: HTMLElement) => within(d).getByLabelText(/Reviewer name/);
const reasonField = (d: HTMLElement) => within(d).getByLabelText(/Reason \(required/);

async function fillWho(user: ReturnType<typeof userEvent.setup>, d: HTMLElement, reviewer = who.reviewer, reason = who.reason) {
  await user.clear(reviewerField(d));
  await user.type(reviewerField(d), reviewer);
  await user.type(reasonField(d), reason);
}

describe("opening and closing", () => {
  it("shows the check's current name with the eyebrow, puts focus inside, and keeps Save off", async () => {
    const { dialog } = await openEditor();
    expect(within(dialog).getByText("Edit check")).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { level: 2, name: "Statement: running balance" })).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(saveButton(dialog)).toBeDisabled();
    expect(within(dialog).getByText("Change something to save.")).toBeInTheDocument();
  });

  it("shows the officer's name as the title once one is set", async () => {
    await mock.updateRuleOverride("bs_running_balance", { name: "Balance walk", ...who });
    const { dialog } = await openEditor();
    expect(within(dialog).getByRole("heading", { level: 2, name: "Balance walk" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Balance walk");
  });

  it("closes on Escape and returns focus to the opener", async () => {
    const { user, opener } = await openEditor();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(opener).toHaveFocus();
  });

  it("closes on Cancel and on the close button, discarding the draft", async () => {
    const { user, dialog, opener } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(opener);
    expect(within(await screen.findByRole("dialog")).getByRole("switch")).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("status, severity and wording controls", () => {
  it("has a labelled switch whose explanation changes with its state", async () => {
    const { user, dialog } = await openEditor();
    const sw = within(dialog).getByRole("switch", { name: "Switched on" });
    expect(sw).toBeChecked();
    expect(within(dialog).getByText("This check runs on every analysis it applies to.")).toBeInTheDocument();
    await user.click(sw);
    expect(sw).not.toBeChecked();
    expect(within(dialog).getByText("This check is switched off. It will not run on new analyses.")).toBeInTheDocument();
    expect(sw).toHaveAccessibleDescription(/switched off/);
    await user.click(sw);
    expect(within(dialog).getByText("This check runs on every analysis it applies to.")).toBeInTheDocument();
  });

  it("offers the four severities as one radio group and marks the default", async () => {
    const { user, dialog } = await openEditor();
    const radios = within(dialog).getAllByRole("radio");
    expect(radios.map((r) => r.closest("label")!.textContent)).toEqual([
      expect.stringContaining("High concern"),
      expect.stringContaining("Medium concern"),
      expect.stringContaining("Low concern"),
      expect.stringContaining("For information"),
    ]);
    expect(within(dialog).getByRole("radio", { name: /High concern/ })).toBeChecked();
    expect(within(dialog).getAllByText("Default")).toHaveLength(1);
    expect(within(dialog.querySelector("label:has(input[value=high])") as HTMLElement).getByText("Default")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: /Low concern/ }));
    expect(within(dialog).getByRole("radio", { name: /Low concern/ })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: /High concern/ })).not.toBeChecked();
    // the default marker stays on the code default, not on the choice
    expect(within(dialog.querySelector("label:has(input[value=high])") as HTMLElement).getByText("Default")).toBeInTheDocument();
  });

  it("shows the code defaults as placeholders, live counters and a 'Use default wording' link per field", async () => {
    const { user, dialog } = await openEditor();
    const name = within(dialog).getByLabelText("Name");
    expect(name).toHaveAttribute("placeholder", "Statement: running balance");
    expect(within(dialog).getByLabelText("What it checks")).toHaveAttribute("placeholder", expect.stringContaining("Each balance equals the previous balance"));
    expect(within(dialog).getByText("0/80")).toBeInTheDocument();
    expect(within(dialog).getByText("0/400")).toBeInTheDocument();
    expect(within(dialog).getByText("0/600")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Use default wording/ })).toBeNull();
    await user.type(name, "Mine");
    expect(within(dialog).getByText("4/80")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Use default wording: Name/ }));
    expect(name).toHaveValue("");
    expect(within(dialog).queryByRole("button", { name: /Use default wording/ })).toBeNull();
  });
});

describe("validation", () => {
  it("requires a reason of at least 5 characters and tells the officer how many are left", async () => {
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    expect(saveButton(dialog)).toBeDisabled();
    expect(within(dialog).getByText(/Add your name and a reason of at least 5 characters to save\./)).toBeInTheDocument();
    await user.type(reviewerField(dialog), "j.lim");
    await user.type(reasonField(dialog), "abcd");
    await user.tab();
    expect(within(dialog).getByText("Enter a reason of at least 5 characters.")).toBeInTheDocument();
    expect(reasonField(dialog)).toHaveAttribute("aria-invalid", "true");
    expect(saveButton(dialog)).toBeDisabled();
    await user.type(reasonField(dialog), "e");
    expect(within(dialog).queryByText("Enter a reason of at least 5 characters.")).toBeNull();
    expect(saveButton(dialog)).toBeEnabled();
  });

  it("stops a reason over 500 characters (the catalogue's limit) with its own counter", async () => {
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    await user.type(reviewerField(dialog), "j.lim");
    await user.click(reasonField(dialog));
    fireEvent.change(reasonField(dialog), { target: { value: "r".repeat(501) } });
    await user.tab();
    expect(within(dialog).getByText("501/500")).toBeInTheDocument();
    expect(within(dialog).getByText("The reason can be at most 500 characters.")).toBeInTheDocument();
    expect(within(dialog).getByText("Shorten the reason to 500 characters or fewer.")).toBeInTheDocument();
    expect(saveButton(dialog)).toBeDisabled();
    fireEvent.change(reasonField(dialog), { target: { value: "r".repeat(500) } });
    expect(saveButton(dialog)).toBeEnabled();
  });

  it("requires the reviewer's name", async () => {
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    await user.type(reasonField(dialog), who.reason);
    await user.click(reviewerField(dialog));
    await user.tab();
    expect(within(dialog).getByText(/Reviewer name must be 2 to 64 characters/)).toBeInTheDocument();
    expect(saveButton(dialog)).toBeDisabled();
  });

  it("remembers the reviewer's name from the last save", async () => {
    window.localStorage.setItem("gldb.reviewer", "a.tan");
    const { dialog } = await openEditor();
    expect(reviewerField(dialog)).toHaveValue("a.tan");
  });

  it("stops a name over 80 characters, with a red counter", async () => {
    const { user, dialog } = await openEditor();
    await fillWho(user, dialog);
    const name = within(dialog).getByLabelText("Name");
    fireEvent.change(name, { target: { value: "x".repeat(81) } });
    expect(within(dialog).getByText("81/80")).toBeInTheDocument();
    expect(within(dialog).getByText("The name can be at most 80 characters (now 81).")).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(saveButton(dialog)).toBeDisabled();
    expect(within(dialog).getByText(/Shorten the wording that is too long/)).toBeInTheDocument();
    fireEvent.change(name, { target: { value: "x".repeat(80) } });
    expect(saveButton(dialog)).toBeEnabled();
  });

  it("stops a description over 400 and a note over 600 characters", async () => {
    const { user, dialog } = await openEditor();
    await fillWho(user, dialog);
    fireEvent.change(within(dialog).getByLabelText("What it checks"), { target: { value: "y".repeat(401) } });
    expect(within(dialog).getByText(/The description can be at most 400 characters/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("What it checks"), { target: { value: "y".repeat(400) } });
    fireEvent.change(within(dialog).getByLabelText("Internal note"), { target: { value: "z".repeat(601) } });
    expect(within(dialog).getByText(/The note can be at most 600 characters/)).toBeInTheDocument();
    expect(saveButton(dialog)).toBeDisabled();
  });

  it("does not let a save through when nothing changed, even with a valid reason, and again after changing back", async () => {
    const { user, dialog } = await openEditor();
    await fillWho(user, dialog);
    expect(saveButton(dialog)).toBeDisabled();
    expect(within(dialog).getByText("Change something to save.")).toBeInTheDocument();
    const sw = within(dialog).getByRole("switch");
    await user.click(sw);
    expect(saveButton(dialog)).toBeEnabled();
    await user.click(sw);
    expect(saveButton(dialog)).toBeDisabled();
    // typing the code's own wording is not a change either
    await user.type(within(dialog).getByLabelText("Name"), "Statement: running balance");
    expect(saveButton(dialog)).toBeDisabled();
  });
});

describe("what will happen", () => {
  const notice = (d: HTMLElement) => within(d.querySelector('section[aria-labelledby]:has(ul) ') ?? d);

  it("explains that nothing has changed yet, and when edits apply", async () => {
    const { dialog } = await openEditor();
    expect(within(dialog).getByText(/Nothing has been changed yet/)).toBeInTheDocument();
    expect(within(dialog).getByText(/apply to analyses run after you save\. Cases already analysed keep their results until they are re-run\./)).toBeInTheDocument();
  });

  it("switching off says coverage drops and it is not a clearance", async () => {
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    const fx = within(dialog).getByRole("heading", { name: "What will happen" }).closest("section")!;
    expect(within(fx as HTMLElement).getByText(/will not run\. Cases will show it as not completed, so their coverage drops\. A check that did not run is never a clearance\./)).toBeInTheDocument();
    expect(within(fx as HTMLElement).getByText(/apply to analyses run after you save/)).toBeInTheDocument();
    expect(notice(dialog)).toBeDefined();
  });

  it("lowering severity says fewer points and no fast-lane unblocking, with the real numbers once the method loads", async () => {
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("radio", { name: /Medium concern/ }));
    const fx = within(dialog).getByRole("heading", { name: "What will happen" }).closest("section") as HTMLElement;
    expect(within(fx).getByText(/Lower concern \(High concern to Medium concern\): findings add fewer points to the score\./)).toBeInTheDocument();
    expect(within(fx).getByText(/does not open the fast lane/)).toBeInTheDocument();
    await waitFor(() => expect(within(fx).getByText(/adds 15 instead of 30 points/)).toBeInTheDocument());
    expect(await within(dialog).findByText("30 points")).toBeInTheDocument();
  });

  it("raising severity says more points", async () => {
    await mock.updateRuleOverride("bs_running_balance", { severity: "low", ...who });
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("radio", { name: /Medium concern/ }));
    const fx = within(dialog).getByRole("heading", { name: "What will happen" }).closest("section") as HTMLElement;
    expect(within(fx).getByText(/Higher concern \(Low concern to Medium concern\): findings add more points/)).toBeInTheDocument();
  });

  it("wording changes are display only", async () => {
    const { user, dialog } = await openEditor();
    await user.type(within(dialog).getByLabelText("Internal note"), "hello");
    const fx = within(dialog).getByRole("heading", { name: "What will happen" }).closest("section") as HTMLElement;
    expect(within(fx).getByText(/Wording only changes how the check is described on screen/)).toBeInTheDocument();
    expect(within(fx).queryByText(/coverage drops/)).toBeNull();
  });

  it("warns extra for a stronger check (blocks the fast lane) once the method is known", async () => {
    const { user, dialog } = await openEditor({ ruleId: "x_directors" });
    await within(dialog).findByText("30 points");
    await user.click(within(dialog).getByRole("switch"));
    await waitFor(() => expect(within(dialog).getByText(/one of the stronger checks: a problem it finds blocks the fast lane/)).toBeInTheDocument());
    await user.click(within(dialog).getByRole("switch"));
    expect(within(dialog).queryByText(/one of the stronger checks/)).toBeNull();
    await user.click(within(dialog).getByRole("radio", { name: /Low concern/ }));
    expect(within(dialog).getByText(/Lowering its severity does not remove its block on the fast lane/)).toBeInTheDocument();
  });

  it("warns when the check is the only one left for its group", async () => {
    const { user, dialog } = await openEditor({ ruleId: "pdf_editor_software" });
    await user.click(within(dialog).getByRole("switch"));
    expect(within(dialog).getByText(/only switched-on check in the group "File forensics"/)).toBeInTheDocument();
  });
});

describe("saving", () => {
  it("sends only what changed with the reviewer and reason, tells the page, remembers the name", async () => {
    const onSaved = vi.fn();
    const spy = vi.spyOn(api, "updateRuleOverride");
    const { user, dialog } = await openEditor({ onSaved });
    await user.click(within(dialog).getByRole("switch"));
    await user.click(within(dialog).getByRole("radio", { name: /Low concern/ }));
    await user.type(within(dialog).getByLabelText("Name"), "  Balance walk ");
    await fillWho(user, dialog);
    await user.click(saveButton(dialog));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith("bs_running_balance", { enabled: false, severity: "low", name: "Balance walk", reviewer: "j.lim", reason: who.reason });
    expect(onSaved.mock.calls[0][1]).toBe("saved");
    expect(onSaved.mock.calls[0][0]).toMatchObject({ rule: "bs_running_balance", enabled: false, effective_severity: "low" });
    expect(window.localStorage.getItem("gldb.reviewer")).toBe("j.lim");
    expect(state.overrides.get("bs_running_balance")).toMatchObject({ enabled: false, severity: "low", name: "Balance walk", updated_by: "j.lim" });
    expect(state.changes).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("disables the buttons and says Saving while the request runs", async () => {
    let finish: (v: RuleInfo) => void = () => {};
    vi.spyOn(api, "updateRuleOverride").mockReturnValue(new Promise<RuleInfo>((r) => (finish = r)));
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    await fillWho(user, dialog);
    await user.click(saveButton(dialog));
    expect(within(dialog).getByRole("button", { name: "Saving..." })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    finish((await mock.getRules()).rules[0]);
  });

  const failWith = (code: string, message: string, status: number, details?: Record<string, unknown>) =>
    vi.spyOn(api, "updateRuleOverride").mockRejectedValue(new ApiErrorException({ code, message, details }, status));

  async function trySave(fail: () => unknown) {
    fail();
    const { user, dialog } = await openEditor();
    await user.click(within(dialog).getByRole("switch"));
    await user.type(within(dialog).getByLabelText("Name"), "My draft name");
    await fillWho(user, dialog);
    await user.click(saveButton(dialog));
    const alert = await within(dialog).findByRole("alert");
    return { user, dialog, alert };
  }

  function expectDraftKept(dialog: HTMLElement) {
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(within(dialog).getByLabelText("Name")).toHaveValue("My draft name");
    expect(reasonField(dialog)).toHaveValue(who.reason);
    expect(reviewerField(dialog)).toHaveValue(who.reviewer);
    expect(within(dialog).getByRole("switch")).not.toBeChecked();
  }

  it("422 no_change: says nothing new to save and keeps the draft", async () => {
    const { dialog, alert } = await trySave(() => failWith("no_change", "No change.", 422));
    expect(within(alert).getByText("Nothing to save")).toBeInTheDocument();
    expect(within(alert).getByText(/already in place/)).toBeInTheDocument();
    expectDraftKept(dialog);
  });

  it("404 unknown_rule: says the check is gone and keeps the draft", async () => {
    const { dialog, alert } = await trySave(() => failWith("unknown_rule", "Unknown.", 404));
    expect(within(alert).getByText("This check is no longer in the catalogue")).toBeInTheDocument();
    expect(within(alert).getByText(/Nothing was saved/)).toBeInTheDocument();
    expectDraftKept(dialog);
  });

  it("429 rate_limited: says to wait and keeps the draft", async () => {
    const { dialog, alert } = await trySave(() => failWith("rate_limited", "Slow down.", 429, { retry_after_s: 30 }));
    expect(within(alert).getByText("Too many changes in a short time")).toBeInTheDocument();
    expect(within(alert).getByText(/Wait 30 seconds and try again/)).toBeInTheDocument();
    expectDraftKept(dialog);
  });

  it("network failure: says nothing was saved and keeps the draft", async () => {
    const { dialog, alert } = await trySave(() => failWith("network_error", "Cannot reach the API.", 0));
    expect(within(alert).getByText("Cannot reach the server")).toBeInTheDocument();
    expect(within(alert).getByText(/Nothing was saved/)).toBeInTheDocument();
    expectDraftKept(dialog);
  });

  it("422 validation: lists the problems and keeps the draft", async () => {
    const { dialog, alert } = await trySave(() => failWith("validation_error", "Bad.", 422, { errors: [{ field: "name", message: "at most 80 characters" }] }));
    expect(within(alert).getByText("Some settings are not valid")).toBeInTheDocument();
    expect(within(alert).getByText(/Name: at most 80 characters/)).toBeInTheDocument();
    expectDraftKept(dialog);
  });

  it("an unexpected server error still gets a message and keeps the draft", async () => {
    const { dialog, alert } = await trySave(() => failWith("internal_error", "Boom.", 500));
    expect(within(alert).getByText("Something went wrong")).toBeInTheDocument();
    expectDraftKept(dialog);
  });

  it("lets the officer try again after an error and clears the message when they edit", async () => {
    const { user, dialog } = await trySave(() => failWith("network_error", "x", 0));
    await user.type(within(dialog).getByLabelText("Name"), "!");
    expect(within(dialog).queryByRole("alert")).toBeNull();
    vi.restoreAllMocks();
    await user.click(saveButton(dialog));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(state.overrides.get("bs_running_balance")?.name).toBe("My draft name!");
  });
});

describe("reset to default", () => {
  it("is not offered for a check that is already at its defaults", async () => {
    const { dialog } = await openEditor();
    expect(within(dialog).queryByRole("heading", { name: "Reset to default" })).toBeNull();
  });

  it("needs a name and reason, asks to confirm, then resets and tells the page", async () => {
    await mock.updateRuleOverride("bs_running_balance", { enabled: false, severity: "low", note: "n", ...who });
    const onSaved = vi.fn();
    const spy = vi.spyOn(api, "resetRuleOverride");
    const { user, dialog } = await openEditor({ onSaved });
    const section = within(dialog).getByRole("heading", { name: "Reset to default" }).closest("section") as HTMLElement;
    // no reason yet: no confirmation, the reason field is flagged
    await user.click(within(section).getByRole("button", { name: "Reset to default" }));
    expect(within(dialog).queryByText("Put this check back to its defaults?")).toBeNull();
    expect(within(dialog).getByText("Enter a reason of at least 5 characters.")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    await fillWho(user, dialog, "a.tan", "Back to the normal setup");
    await user.click(within(section).getByRole("button", { name: "Reset to default" }));
    expect(within(dialog).getByText("Put this check back to its defaults?")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    // keep editing hides the confirmation
    await user.click(within(section).getByRole("button", { name: "Keep editing" }));
    expect(within(dialog).queryByText("Put this check back to its defaults?")).toBeNull();
    await user.click(within(section).getByRole("button", { name: "Reset to default" }));
    await user.click(within(section).getByRole("button", { name: "Yes, reset to default" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith("bs_running_balance", { reviewer: "a.tan", reason: "Back to the normal setup" });
    expect(onSaved.mock.calls[0][1]).toBe("reset");
    expect(onSaved.mock.calls[0][0]).toMatchObject({ enabled: true, override: null });
    expect(state.overrides.has("bs_running_balance")).toBe(false);
    expect(state.changes.at(-1)).toMatchObject({ action: "reset", reviewer: "a.tan", reason: "Back to the normal setup" });
  });

  it("warns that unsaved edits are discarded", async () => {
    await mock.updateRuleOverride("bs_running_balance", { note: "n", ...who });
    const { user, dialog } = await openEditor();
    await user.type(within(dialog).getByLabelText("Name"), "typed");
    await fillWho(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Reset to default" }));
    expect(within(dialog).getByText(/The edits you have not saved will be discarded/)).toBeInTheDocument();
  });

  it("404 no_override: says it is already at its defaults and leaves the sheet open", async () => {
    await mock.updateRuleOverride("bs_running_balance", { note: "n", ...who });
    vi.spyOn(api, "resetRuleOverride").mockRejectedValue(new ApiErrorException({ code: "no_override", message: "none" }, 404));
    const { user, dialog } = await openEditor();
    await fillWho(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Reset to default" }));
    await user.click(within(dialog).getByRole("button", { name: "Yes, reset to default" }));
    const alert = await within(dialog).findByRole("alert");
    expect(within(alert).getByText("Already at its defaults")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBe(dialog);
  });
});

describe("history for this check", () => {
  it("says so when there is none", async () => {
    const { dialog } = await openEditor();
    expect(await within(dialog).findByText(/No changes yet\. This check has always used its default settings\./)).toBeInTheDocument();
  });

  it("lists the check's changes newest first with who, when, what and why, and ignores other checks", async () => {
    await mock.updateRuleOverride("bs_running_balance", { enabled: false, ...who, reviewer: "first.person", reason: "First reason" });
    await mock.updateRuleOverride("fs_arith", { note: "other", ...who });
    await mock.updateRuleOverride("bs_running_balance", { severity: "low", ...who, reviewer: "second.person", reason: "Second reason" });
    const { dialog } = await openEditor();
    const list = await within(dialog).findByRole("list", { name: "Changes to this check" });
    const items = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("second.person");
    expect(items[0]).toHaveTextContent("Severity changed from High concern (default) to Low concern");
    expect(items[0]).toHaveTextContent("Second reason");
    expect(items[1]).toHaveTextContent("first.person");
    expect(items[1]).toHaveTextContent("Switched off");
    expect(items[1]).toHaveTextContent("First reason");
    expect(within(list).getAllByText(/UTC/)).toHaveLength(2);
    expect(list).not.toHaveTextContent("other");
  });

  it("shows the first five and offers the rest", async () => {
    for (let i = 0; i < 7; i++) await mock.updateRuleOverride("bs_running_balance", { note: `note ${i}`, ...who });
    const { user, dialog } = await openEditor();
    const list = await within(dialog).findByRole("list", { name: "Changes to this check" });
    expect(within(list).getAllByText(/Internal note/)).toHaveLength(5);
    await user.click(within(dialog).getByRole("button", { name: "Show all 7 changes" }));
    expect(within(list).getAllByText(/Internal note/)).toHaveLength(7);
    await user.click(within(dialog).getByRole("button", { name: "Show fewer" }));
    expect(within(list).getAllByText(/Internal note/)).toHaveLength(5);
  });

  it("says when the history cannot be loaded and offers a retry", async () => {
    const spy = vi.spyOn(api, "getRuleChanges").mockRejectedValueOnce(new ApiErrorException({ code: "network_error", message: "x" }, 0));
    const { user, dialog } = await openEditor();
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("The change log could not be loaded");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByText(/No changes yet/)).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("refreshes after a save", async () => {
    const { user, dialog } = await openEditor();
    expect(await within(dialog).findByText(/No changes yet/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("switch"));
    await fillWho(user, dialog);
    await user.click(saveButton(dialog));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Open editor" }));
    const list = await screen.findByRole("list", { name: "Changes to this check" });
    expect(list).toHaveTextContent("Switched off");
  });
});

describe("typed text stays inert", () => {
  const evil = '<img src=x onerror="window.__pwned=1"> <script>window.__pwned=2</script>';

  it("renders names, notes and reasons as plain text in the sheet title, the fields and the history", async () => {
    await mock.updateRuleOverride("bs_running_balance", { name: evil, note: evil, description: evil, reviewer: "j.lim", reason: `why ${evil}` });
    const { dialog } = await openEditor();
    const title = within(dialog).getByRole("heading", { level: 2 });
    expect(title.textContent).toBe(evil);
    expect(within(dialog).getByLabelText("Name")).toHaveValue(evil);
    const list = await within(dialog).findByRole("list", { name: "Changes to this check" });
    expect(list.textContent).toContain("<img src=x onerror=");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
