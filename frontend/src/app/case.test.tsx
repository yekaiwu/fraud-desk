import "@/test/mockEnv";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Component, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as mock from "@/lib/mock/handlers";
import { resetMockState, state } from "@/lib/mock/state";
import { nav, resetNav } from "@/test/navMock";
import { clearViewport, renderWithSWR, setViewport } from "@/test/utils";
import CasePage from "./cases/[id]/page";

vi.mock("next/navigation", async () => (await import("@/test/navMock")).navModule());

beforeEach(() => {
  resetNav();
  resetMockState();
  state.latencyMs = 0;
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  clearViewport();
  cleanup();
});

async function openCase(id: string, query = "") {
  nav.route = { id };
  nav.params = new URLSearchParams(query);
  renderWithSWR(<CasePage />);
  await screen.findByRole("heading", { level: 1 });
}
const getCaseState = (id: string) => state.cases.get(id)!;

describe("clean case (fixture 4)", () => {
  it("says no flags were raised, is Low and Fast lane, and Record decision works", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_clean");
    expect(await screen.findByText("No concerns were found.")).toBeInTheDocument();
    expect(screen.getByText("21 of 21 checks completed.")).toBeInTheDocument();
    expect(screen.getByText("No flags is not proof of authenticity; see coverage.")).toBeInTheDocument();
    expect(document.querySelector('[data-band="low"]')).toHaveTextContent("Low");
    expect(screen.getAllByText("Fast lane").length).toBeGreaterThan(0);
    expect(screen.getByText("Low risk, high coverage. A person still confirms.")).toBeInTheDocument();
    expect(screen.queryByText(/Partial analysis/)).toBeNull();
    await user.click(screen.getByRole("button", { name: /Coverage details/ }));
    expect(screen.getByText("No signal found (not a guarantee of authenticity)", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/No AI-generator provenance hint found\. This does not mean the document is authentic\./)).toBeInTheDocument();
    const decide = screen.getAllByRole("button", { name: "Record decision" })[0];
    expect(decide).toBeEnabled();
    await user.click(decide);
    const radios = screen.getAllByRole("radio");
    expect(radios[0]).toHaveAccessibleName(/^Approve/);
    for (const r of radios) expect(r).not.toBeChecked();
    expect(screen.getByText("Suggested")).toBeInTheDocument();
  });
});

describe("partial case (fixture 5)", () => {
  it("never reads as safe", async () => {
    await openCase("c_loan_partial");
    expect(await screen.findByText(/Partial analysis\. Some checks or pages did not run/)).toBeInTheDocument();
    expect(screen.getByText("The extract stage failed")).toBeInTheDocument();
    expect(screen.getByText("Document d4 could not be read")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Risk score at least 20 of 100, low" })).toHaveTextContent("≥20");
    expect(screen.getByText(/Partial score: some checks or pages did not run, so the real score may be higher/)).toBeInTheDocument();
    expect(screen.queryByText("Fast lane")).toBeNull();
    expect(screen.getByText("3 pages we could not review")).toBeInTheDocument();
    expect(screen.getByText("Pages we could not review (1)")).toBeInTheDocument();
  });
});

describe("selecting flags", () => {
  it("marks the box pressed, puts flag= in the URL and moves with j and k", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_fraud");
    const box = await screen.findByRole("button", { name: /^Flag 1, bs_running_balance, high severity, page 1/ });
    expect(box).toHaveAttribute("aria-pressed", "false");
    await user.click(box);
    expect(box).toHaveAttribute("aria-pressed", "true");
    expect(nav.router.replace).toHaveBeenLastCalledWith(expect.stringContaining("flag=bs_running_balance.d1.transactions.8"), { scroll: false });
    await user.keyboard("j");
    expect(screen.getByRole("button", { name: /^Flag 2,/ })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("k");
    expect(screen.getByRole("button", { name: /^Flag 1,/ })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /^Flag 1,/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("navigates the viewer to the page of the flag, and shortcuts are off while typing", async () => {
    const user = userEvent.setup();
    const c = getCaseState("c_loan_fraud");
    c.flags[0].region = { ...c.flags[0].region!, page: 2 };
    await openCase("c_loan_fraud");
    expect(await screen.findByText("Page 2 of 3")).toBeInTheDocument(); // opens on the first flagged page
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /Show on document \(flag 1\)/ }));
    expect(await screen.findByText("Page 2 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Flag 1,.*page 2$/ })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Record decision" })[0]);
    await user.click(screen.getAllByRole("radio")[0]);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const reason = screen.getByLabelText(/Reason \(required/);
    await user.type(reason, "jjkk[[]");
    expect(reason).toHaveValue("jjkk[]");
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  });

  it("lists a region-less flag with 'No location on a page' and goes to the document's first page", async () => {
    const user = userEvent.setup();
    const c = getCaseState("c_loan_fraud");
    c.flags[4].region = null;
    c.flags[4].document_ids = ["d2"];
    await openCase("c_loan_fraud");
    const medium = await screen.findByRole("button", { name: /Medium concern/ });
    if (medium.getAttribute("aria-expanded") === "false") await user.click(medium); // groups stay as the officer left them
    expect(screen.getByText(/No location on a page/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Flag 5,/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Show on document \(flag 5\)/ }));
    expect(await screen.findByText("Page 1 of 1")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /invoice-INV-0042\.pdf/ })).toBeInTheDocument();
  });
});

describe("untrusted text", () => {
  it("renders evidence, names and reasons as literal text", async () => {
    const user = userEvent.setup();
    const c = getCaseState("c_loan_fraud");
    c.flags[0].evidence = "<script>alert(1)</script>";
    c.applicant.name = "<img src=x onerror=alert(1)>";
    c.review.push({ id: "r1", kind: "verdict", reviewer: "a.tan", decision: "request_documents", reason: "</td>", flag_ids: [], finding_id: null, target_entry_id: null, status: "effective", case_version: 1, created_at: "2026-10-27T10:00:00Z" });
    const scripts = document.querySelectorAll("script").length;
    await openCase("c_loan_fraud");
    await user.click((await screen.findAllByRole("button", { name: "Evidence" }))[0]);
    expect(await screen.findByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Decision & activity/ }));
    expect(screen.getByText("</td>")).toBeInTheDocument();
    expect(document.querySelectorAll("script").length).toBe(scripts);
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });
});

describe("record decision sheet", () => {
  async function toSheet(id: string) {
    const user = userEvent.setup();
    await openCase(id);
    await user.click((await screen.findAllByRole("button", { name: "Record decision" }))[0]);
    return user;
  }
  async function fill(user: ReturnType<typeof userEvent.setup>, reviewer: string, reason: string) {
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(screen.getByLabelText(/Reviewer name/), reviewer);
    await user.type(screen.getByLabelText(/Reason \(required/), reason);
    await user.click(screen.getByRole("button", { name: "Continue to review" }));
  }

  it("follows allowed_verdicts: no refer_edd for loan, nothing preselected, lane suggestion", async () => {
    await toSheet("c_loan_fraud");
    const names = screen.getAllByRole("radio").map((r) => r.getAttribute("value"));
    expect(names).toEqual(["approve", "request_documents", "escalate_aml", "reject_or_escalate"]);
    expect(names).not.toContain("refer_edd");
    expect(screen.getAllByRole("radio").some((r) => (r as HTMLInputElement).checked)).toBe(false);
    const suggested = screen.getByText("Suggested");
    expect(suggested.closest("label")).toHaveTextContent("Reject or escalate");
  });

  it("will not leave step 1 without an outcome", async () => {
    const user = await toSheet("c_loan_clean");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Choose a verdict.")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Reviewer name/)).toBeNull();
  });

  it("validates reviewer and reason, sends nothing when invalid and the exact request when valid", async () => {
    const spy = vi.spyOn(mock, "postReview");
    const user = await toSheet("c_loan_clean");
    await user.click(screen.getByRole("radio", { name: /Approve/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const reviewer = screen.getByLabelText(/Reviewer name/);
    const reason = screen.getByLabelText(/Reason \(required/);
    await user.type(reviewer, "a");
    await user.type(reason, "abcd");
    await user.tab();
    expect(await screen.findByText(/Enter a reason of at least 5 characters/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue to review" }));
    expect(screen.getByText(/2 to 64 characters/)).toBeInTheDocument();
    expect(screen.queryByText("Review and confirm")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
    await user.clear(reason);
    await user.type(reason, "     ");
    await user.click(screen.getByRole("button", { name: "Continue to review" }));
    expect(spy).not.toHaveBeenCalled();
    expect(reason).toHaveAttribute("aria-invalid", "true");
    await user.clear(reviewer);
    await user.type(reviewer, "a.tan");
    await user.clear(reason);
    await user.type(reason, "Statements agree with the profile.");
    expect(screen.getByText("34/1000")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue to review" }));
    // step 3 summarises outcome, reason and reviewer before anything is sent
    expect(screen.getByText("Review and confirm")).toBeInTheDocument();
    expect(screen.getByText("Statements agree with the profile.")).toBeInTheDocument();
    expect(screen.getByText("a.tan")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith("c_loan_clean", { kind: "verdict", decision: "approve", reviewer: "a.tan", reason: "Statements agree with the profile.", case_version: 1 });
    // success state: what was recorded, what happens next, the way back
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Decision recorded")).toBeInTheDocument();
    expect(within(dialog).getByText("What happens next")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Back to worklist" })).toHaveAttribute("href", "/");
    expect(await screen.findByText(/Recorded as r1/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stay on this case" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
    expect(window.localStorage.getItem("gldb.reviewer")).toBe("a.tan");
  });

  it("asks for an extra acknowledgement before approving a high-risk case and lists blocking flags when the server refuses (409)", async () => {
    const user = await toSheet("c_loan_fraud");
    await user.click(screen.getByRole("radio", { name: /^Approve/ }));
    await fill(user, "a.tan", "Looks fine to me");
    const caution = await screen.findByText(/Approving with incomplete or elevated-risk analysis/);
    expect(caution.textContent).toContain("the risk band is high");
    const submit = screen.getByRole("button", { name: "Approve" });
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I understand/ }));
    await user.click(submit);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("High flags still open");
    const link = within(alert).getByRole("button", { name: "bs_running_balance.d1.transactions.8" });
    expect(link).toBeInTheDocument();
    expect(alert).toHaveFocus();
    await user.click(link);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: /^Flag 1,/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the suspicion-formed clock note and the no-STR sentence, then the closed summary and clock", async () => {
    const user = await toSheet("c_aml_graph");
    await user.click(screen.getByRole("radio", { name: /Escalate to AML investigation/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText(/The tool does not file an STR/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Reviewer name/), "a.tan");
    await user.type(screen.getByLabelText(/Reason \(required/), "Funds routed through three companies.");
    await user.click(screen.getByRole("button", { name: "Continue to review" }));
    const text = await screen.findByText(/The suspicion-formed time will be recorded as/);
    expect(text.textContent).toMatch(/\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC/);
    expect(text.textContent).toContain("The tool does not file an STR");
    await user.click(screen.getByRole("checkbox", { name: /I understand/ }));
    await user.click(screen.getByRole("button", { name: "Escalate to AML investigation" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Decision recorded")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stay on this case" }));
    await user.click(screen.getByRole("tab", { name: /Decision & activity/ }));
    expect(await screen.findByText(/Closed: Escalate to AML investigation/)).toBeInTheDocument();
    expect(screen.getByText(/Suspicion-formed clock started/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Record decision" })).toBeNull();
  });

  it("keeps shortcuts off while typing in the sheet", async () => {
    const user = await toSheet("c_loan_fraud");
    await user.click(screen.getAllByRole("radio")[0]);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const reason = screen.getByLabelText(/Reason \(required/);
    await user.type(reason, "jjkk[[]");
    expect(reason).toHaveValue("jjkk[]");
    expect(nav.router.replace).not.toHaveBeenCalledWith(expect.stringContaining("flag="), expect.anything());
  });

  it("closes on Escape, returns focus to the opener and keeps a half-written reason", async () => {
    const user = await toSheet("c_loan_clean");
    const opener = screen.getAllByRole("button", { name: "Record decision" })[0];
    await user.click(screen.getByRole("radio", { name: /Approve/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(screen.getByLabelText(/Reason \(required/), "half written");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(opener).toHaveFocus();
    await user.click(opener);
    expect(screen.getByLabelText(/Reason \(required/)).toHaveValue("half written");
  });
});

describe("flag decisions and second approval", () => {
  it("dismissing a high flag warns, becomes pending, shows a card, and a different reviewer resolves it", async () => {
    const user = userEvent.setup();
    await openCase("c_kyc_fraud");
    await user.click(await screen.findByRole("button", { name: /Mark as not a concern.*\(flag 1\)/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/A second reviewer must approve this dismissal/)).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.type(within(dialog).getByLabelText(/Reviewer name/), "a.tan");
    await user.type(within(dialog).getByLabelText(/Reason \(required/), "Chart is an old draft.");
    await user.click(within(dialog).getByRole("button", { name: "Mark this as not a concern" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findAllByText("Needs 2nd approval")).not.toHaveLength(0);
    expect(screen.getByText(/2 dismissals are waiting for a second reviewer/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Decision & activity/ }));
    const cards = screen.getAllByRole("listitem").filter((li) => li.hasAttribute("data-entry"));
    expect(cards).toHaveLength(2);
  });

  it("blocks the same name in another case or with spaces, then accepts a different reviewer", async () => {
    const spy = vi.spyOn(mock, "postReview");
    const user = userEvent.setup();
    await openCase("c_kyc_fraud", "tab=review");
    const card = (await screen.findByText(/dismissed 1 flag/)).closest("li")!;
    await user.type(within(card).getByLabelText(/Reviewer name/), "  A.TAN ");
    await user.type(within(card).getByLabelText(/Reason for your decision/), "Agree with the register.");
    await user.click(within(card).getByRole("button", { name: "Approve the dismissal" }));
    expect(within(card).getByText("A different reviewer must countersign")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
    await user.clear(within(card).getByLabelText(/Reviewer name/));
    await user.type(within(card).getByLabelText(/Reviewer name/), "b.lim");
    await user.click(within(card).getByRole("button", { name: "Approve the dismissal" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][1]).toMatchObject({ kind: "second_approval", decision: "approve", target_entry_id: "r1", reviewer: "b.lim" });
    await waitFor(() => expect(screen.queryByText(/dismissed 1 flag/)).toBeNull());
    await user.click(screen.getByRole("tab", { name: /Findings/ }));
    expect((await screen.findAllByText(/1 dismissed|^Dismissed$/)).length).toBeGreaterThan(0);
  });
});

describe("cases without a verdict form", () => {
  it.each([
    ["c_aml_closed", "Closed: Escalate to AML investigation", ""],
    ["c_loan_awaiting", "Waiting for documents.", ""],
    ["c_loan_error", "Analysis failed", ""],
  ])("%s", async (id, text) => {
    const user = userEvent.setup();
    await openCase(id);
    if (id !== "c_loan_error") await user.click(screen.getByRole("tab", { name: /Decision & activity/ }));
    expect((await screen.findAllByText(new RegExp(text))).length).toBeGreaterThan(0);
    expect(screen.queryByRole("form", { name: "Record decision" })).toBeNull();
    if (id === "c_loan_awaiting") expect(screen.getByRole("form", { name: "Add documents" })).toBeInTheDocument();
    const record = screen.queryAllByRole("button", { name: "Record decision" });
    if (id === "c_aml_closed") expect(record).toHaveLength(0);
    else for (const b of record) expect(b).toBeDisabled();
  });

  it("shows only the stepper while processing", async () => {
    await openCase("c_kyc_processing", "job=j_c_kyc_processing");
    expect(await screen.findByText(/Analysing the documents/)).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(await screen.findByText(/Stage \d of 7: \w+/)).toBeInTheDocument();
  });

  it("is read-only on an older version", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_v2", "v=1");
    expect(await screen.findByText(/Viewing version 1, read-only/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to current" })).toHaveAttribute("href", "/cases/c_loan_v2");
    await user.click(screen.getByRole("tab", { name: /Decision & activity/ }));
    expect(screen.getAllByText(/Viewing an old version/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("form", { name: "Record decision" })).toBeNull();
    expect(screen.getByLabelText("Version")).toHaveDisplayValue(/v1/);
  });

  it("a missing case triggers Next's not-found", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    class Boundary extends Component<{ children: ReactNode }, { err: string | null }> {
      state = { err: null as string | null };
      static getDerivedStateFromError(e: Error) {
        return { err: e.message };
      }
      render() {
        return this.state.err ? <p>boundary: {this.state.err}</p> : this.props.children;
      }
    }
    nav.route = { id: "nope" };
    renderWithSWR(
      <Boundary>
        <CasePage />
      </Boundary>,
    );
    expect(await screen.findByText("boundary: NEXT_NOT_FOUND")).toBeInTheDocument();
  });
});

describe("layout and a11y", () => {
  it("uses ARIA tabs with arrow keys on desktop", async () => {
    const user = userEvent.setup();
    setViewport(1280);
    await openCase("c_aml_graph");
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Findings3", "Documents", "Graph", "Decision & activity"]);
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[0].id);
    tabs[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Documents" })).toHaveFocus();
  });

  it("puts the viewer in its own tab and adds a Record decision bar on a phone", async () => {
    const user = userEvent.setup();
    setViewport(390);
    await openCase("c_aml_graph");
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Findings3", "Page", "Docs", "Graph", "Decision & activity"]);
    expect(screen.queryByRole("region", { name: /Page image/ })).toBeNull();
    await user.click(screen.getAllByRole("button", { name: /Show on document.*flag 1/ })[0]);
    expect(await screen.findByRole("region", { name: /Page image/ })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Record decision" })).toHaveLength(2);
  });

  it("shows the graph for AML as a table by default on a phone", async () => {
    const user = userEvent.setup();
    setViewport(390);
    await openCase("c_aml_graph", "tab=graph");
    expect(await screen.findByRole("table", { name: /Relationships between parties/ })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Entity graph/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Graph" }));
    expect(screen.getByRole("img", { name: /Entity graph, \d+ parties, \d+ relationships, \d+ flagged/ })).toBeInTheDocument();
  });

  it("has a Keyboard help dialog that returns focus", async () => {
    const user = userEvent.setup();
    await openCase("c_loan_clean");
    const opener = await screen.findByRole("button", { name: "Keyboard help" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Next / previous flag")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(opener).toHaveFocus();
  });
});
