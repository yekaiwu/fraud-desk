import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dryRun, draft, debtDraft } from "@/test/customRulesFixtures";
import { CandidateList } from "./CandidateList";
import { CustomBadge } from "./CustomBadge";
import { DryRunPanel } from "./DryRunPanel";
import { DuplicateNotes } from "./DuplicateNotes";
import { IdeasList } from "./IdeasList";
import { QuestionCard } from "./QuestionCard";
import { SaveRuleForm } from "./SaveRuleForm";
import { WillWillNot } from "./WillWillNot";

afterEach(cleanup);

const question = debtDraft().questions[0];

describe("CustomBadge", () => {
  it("says Custom, and for a known author who wrote it and that it is unreviewed", () => {
    render(<CustomBadge info={{ author: "alice", version: 2 }} />);
    const badge = screen.getByText("Custom").closest("span") as HTMLElement;
    expect(badge).toHaveAttribute("title", "Custom, by alice, v2, unreviewed");
    expect(within(badge).getByText(": Custom, by alice, v2, unreviewed")).toHaveClass("sr-only");
  });
  it("still says it is not reviewed when the author is not known", () => {
    render(<CustomBadge />);
    expect(screen.getByText("Custom").closest("span")).toHaveAttribute("title", expect.stringMatching(/not reviewed/));
  });
});

describe("QuestionCard", () => {
  it("marks the suggestion, selects nothing, and sends only what was clicked", async () => {
    const onAnswer = vi.fn();
    const user = userEvent.setup();
    render(<QuestionCard q={question} busy={false} onAnswer={onAnswer} />);
    expect(screen.getByText(/The assistant suggests/)).toHaveTextContent("More than 2 times equity");
    expect(screen.getByText(/Nothing is applied until you choose/)).toBeInTheDocument();
    for (const r of screen.getAllByRole("radio")) expect(r).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Use this answer" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /More than 3 times equity/ }));
    await user.click(screen.getByRole("button", { name: "Use this answer" }));
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith("q1", "x3");
  });
  it("is a group the keyboard can use: arrow keys move between the options", async () => {
    const user = userEvent.setup();
    render(<QuestionCard q={question} busy={false} onAnswer={vi.fn()} />);
    expect(screen.getByRole("group", { name: "What counts as a lot of debt?" })).toBeInTheDocument();
    await user.tab();
    await user.keyboard("{ArrowDown}");
    expect(screen.getAllByRole("radio").some((r) => (r as HTMLInputElement).checked)).toBe(true);
  });
  it("shows no suggestion when the question has none", () => {
    render(<QuestionCard q={{ ...question, default: null }} busy={false} onAnswer={vi.fn()} />);
    expect(screen.queryByText("Suggested")).toBeNull();
    expect(screen.queryByText(/The assistant suggests/)).toBeNull();
  });
  it("blocks a second click while an answer is being sent", () => {
    render(<QuestionCard q={question} busy onAnswer={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Use this answer" })).toBeDisabled();
  });
});

describe("CandidateList", () => {
  it("offers one button per reading and reports the index", async () => {
    const onChoose = vi.fn();
    const user = userEvent.setup();
    const spec = draft().spec!;
    render(<CandidateList busy={false} onChoose={onChoose} candidates={[{ index: 0, spec, readback: "one" }, { index: 1, spec, readback: "two" }]} />);
    await user.click(screen.getByRole("button", { name: /reading 2/ }));
    expect(onChoose).toHaveBeenCalledWith(1);
  });
});

describe("WillWillNot", () => {
  it("has the two blocks with the wording the officer was promised", () => {
    render(<WillWillNot willCheck={["It reads A"]} willNotCheck={["It skips B"]} />);
    expect(within(screen.getByRole("region", { name: "This rule WILL check" })).getByText("It reads A")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "It will NOT check" })).getByText("It skips B")).toBeInTheDocument();
  });
});

describe("IdeasList", () => {
  it("renders nothing without ideas", () => {
    const { container } = render(<IdeasList ideas={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("links a related built-in to the catalogue and offers a weaker rule as a request", async () => {
    const onTry = vi.fn();
    const user = userEvent.setup();
    render(<IdeasList onTry={onTry} ideas={[{ kind: "related_builtin", rule: "fs_balance", text: "Total assets equal liabilities plus equity." }, { kind: "weaker_rule", text: "Flags low when X.", spec: draft().spec }]} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/rules?check=fs_balance");
    await user.click(screen.getByRole("button", { name: "Use this as my request" }));
    expect(onTry).toHaveBeenCalledWith("Flags low when X.");
  });
});

describe("DuplicateNotes", () => {
  it("separates an identical match (an error) from a similar one (a warning)", () => {
    render(
      <DuplicateNotes
        notes={[
          { kind: "custom", level: "identical", rule: "cus_0123456789", name: "Mine", what: "Flags low when A.", action: "Change the severity of that rule instead" },
          { kind: "builtin", level: "similar", rule: "fs_balance", what: "Balance sheet adds up.", action: "Look at it." },
        ]}
      />,
    );
    const notices = screen.getAllByRole("alert");
    expect(notices).toHaveLength(1); // only the identical one is an error
    expect(within(notices[0]).getByText("You already have this rule")).toBeInTheDocument();
    expect(screen.getByText("A built-in check looks at something close")).toBeInTheDocument();
    expect(screen.getByText("Change the severity of that rule instead")).toBeInTheDocument();
  });
  it("renders nothing for no notes", () => {
    const { container } = render(<DuplicateNotes notes={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DryRunPanel", () => {
  const noop = () => {};
  it("shows evaluated, fired, skipped and scanned numbers", () => {
    render(<DryRunPanel result={dryRun()} loading={false} error={null} onRun={noop} confirmed={false} onConfirm={noop} />);
    const dl = screen.getByText("Evaluated").closest("dl") as HTMLElement;
    expect(dl).toHaveTextContent("Evaluated41of 200 looked at");
    expect(dl).toHaveTextContent("Fired615%");
    expect(dl).toHaveTextContent("Skipped, field missing3");
    expect(dl).toHaveTextContent("On scans or photos92 fired");
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByText(/Nothing was changed on any case/)).toBeInTheDocument();
  });
  it("asks for Save anyway on a warning", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<DryRunPanel result={dryRun({ fired: 0, warnings: [{ code: "fires_on_none", message: "It fired on none." }], needs_confirmation: true })} loading={false} error={null} onRun={noop} confirmed={false} onConfirm={onConfirm} />);
    expect(screen.getByText("It fired on none.")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Save anyway/ }));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });
  it("shows no cases to test on as a warning, and a loading state", () => {
    const { rerender } = render(<DryRunPanel result={dryRun({ evaluated: 0, fired: 0, fire_rate: 0, warnings: [{ code: "no_cases", message: "No stored case has an invoice to test this on." }], needs_confirmation: true })} loading={false} error={null} onRun={noop} confirmed onConfirm={noop} />);
    expect(screen.getByText("No stored case has an invoice to test this on.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Save anyway/ })).toBeChecked();
    rerender(<DryRunPanel result={null} loading error={null} onRun={noop} confirmed={false} onConfirm={noop} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
  });
});

describe("SaveRuleForm", () => {
  it("never saves a rule whose test was for another rule", async () => {
    const onSave = vi.fn();
    const user = userEvent.setup();
    render(<SaveRuleForm draft={draft({ spec_hash: "hash-new" })} dryRun={dryRun({ spec_hash: "hash-old" })} warningsConfirmed={false} saving={false} onSave={onSave} initialName="Big invoices" />);
    await user.type(screen.getByLabelText(/Why are you adding/), "Credit policy");
    expect(screen.getByRole("button", { name: "Save rule" })).toBeDisabled();
    expect(screen.getByText(/the test on stored cases/)).toBeInTheDocument();
  });
  it("trims the name and reason it sends", async () => {
    const onSave = vi.fn();
    const user = userEvent.setup();
    render(<SaveRuleForm draft={draft()} dryRun={dryRun()} warningsConfirmed={false} saving={false} onSave={onSave} />);
    await user.type(screen.getByLabelText("Name"), "  Big invoices  ");
    await user.type(screen.getByLabelText(/Why are you adding/), "  Credit policy  ");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    expect(onSave).toHaveBeenCalledWith({ name: "Big invoices", description: "", reason: "Credit policy", accept_narrower: false, submit_ticket: false });
  });
  it("needs a name of at least three characters", async () => {
    const user = userEvent.setup();
    render(<SaveRuleForm draft={draft()} dryRun={dryRun()} warningsConfirmed={false} saving={false} onSave={vi.fn()} />);
    await user.type(screen.getByLabelText("Name"), "ab");
    await user.type(screen.getByLabelText(/Why are you adding/), "Credit policy");
    expect(screen.getByRole("button", { name: "Save rule" })).toBeDisabled();
    expect(screen.getByText(/Still needed to save: a name/)).toBeInTheDocument();
  });
  it("does not offer a ticket once one was filed, or when nothing is unsupported", () => {
    const { rerender } = render(<SaveRuleForm draft={debtDraft({ questions: [], save_allowed: true })} dryRun={dryRun({ spec_hash: "hash-debt" })} warningsConfirmed={false} saving={false} onSave={vi.fn()} />);
    expect(screen.getByRole("checkbox", { name: /send the part it cannot check/ })).toBeInTheDocument();
    rerender(<SaveRuleForm draft={debtDraft({ questions: [], save_allowed: true, ticket: { id: "crt_1a2b3c4d5e6f", status: "submitted", message: "x" } })} dryRun={dryRun({ spec_hash: "hash-debt" })} warningsConfirmed={false} saving={false} onSave={vi.fn()} />);
    expect(screen.queryByRole("checkbox", { name: /send the part it cannot check/ })).toBeNull();
  });
});
