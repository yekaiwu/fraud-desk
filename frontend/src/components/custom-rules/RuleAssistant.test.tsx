import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SWRConfig } from "swr";
import { debtDraft, DEBT_READBACK, draft, dryRun, errorBody, INVOICE_READBACK, json, routedFetch, ruleView, ticket } from "@/test/customRulesFixtures";
import type { DraftResponse } from "@/lib/types";
import { RuleAssistant, type EditTarget } from "./RuleAssistant";

const DRAFTS = "POST /custom-rules/drafts";
const TURNS = "POST /custom-rules/drafts/crd_5b2e9f0a1c3d/turns";
const DRY = "POST /custom-rules/drafts/crd_5b2e9f0a1c3d/dry-run";
const SAVE = "POST /custom-rules";
const TICKETS = "POST /custom-rules/tickets";
const REQUEST = "flag as medium if the firm has a lot of debt from different places";

function setup(routes: Parameters<typeof routedFetch>[0], edit: EditTarget | null = null) {
  const f = routedFetch(routes);
  vi.stubGlobal("fetch", f.fn);
  const props = { onSaved: vi.fn(), onCancelEdit: vi.fn(), onTicket: vi.fn(), onOpenRules: vi.fn() };
  const user = userEvent.setup();
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <RuleAssistant edit={edit} {...props} />
    </SWRConfig>,
  );
  return { f, user, ...props };
}

async function ask(user: ReturnType<typeof userEvent.setup>, text = REQUEST) {
  await user.type(screen.getByLabelText(/Describe the check in plain English/), text);
  await user.click(screen.getByRole("button", { name: /Ask the assistant/ }));
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RuleAssistant: the worked example, end to end", () => {
  it("asks, shows what it will and will not check, waits for an answer, tests, then saves with the tick and a ticket", async () => {
    const { f, user, onSaved } = setup({
      [DRAFTS]: () => json(debtDraft()),
      [TURNS]: () => json(debtDraft({ turn: 2, turns_left: 4, questions: [], save_allowed: true, model_calls: 0 })),
      [DRY]: () => json(dryRun({ spec_hash: "hash-debt" })),
      [SAVE]: () => json({ rule: ruleView(), ticket: { id: "crt_1a2b3c4d5e6f", status: "submitted", message: "This will be submitted to the tech team (ticket crt_1a2b3c4d5e6f)." } }, 201),
    });
    await ask(user);

    // turn 1: partly supported, with a question and the two blocks
    expect(await screen.findByText("Part of this can be checked")).toBeInTheDocument();
    expect(f.calls[0]).toEqual({ method: "POST", path: "/custom-rules/drafts", body: { text: REQUEST } });
    expect(screen.getByRole("heading", { name: "This rule WILL check" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "It will NOT check" })).toBeInTheDocument();
    expect(screen.getByText('"from different places": the system does not read this from the documents')).toBeInTheDocument();
    expect(screen.getAllByText(DEBT_READBACK).length).toBeGreaterThan(0);
    expect(screen.getByText("Debt is read as current plus non-current borrowings, compared with total equity.")).toBeInTheDocument();

    // the suggested answer is marked but nothing is chosen, nothing is sent, nothing can be saved yet
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByText("Suggested")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this answer" })).toBeDisabled();
    expect(screen.queryByRole("form", { name: "Save this rule" })).toBeNull();
    expect(f.count(TURNS)).toBe(0);
    expect(f.count(TICKETS)).toBe(0);
    expect(screen.getByRole("button", { name: "Send the missing part to the tech team" })).toBeInTheDocument();

    // the officer picks the 2x option: an answer turn, no model call
    await user.click(screen.getByRole("radio", { name: /More than 2 times equity/ }));
    await user.click(screen.getByRole("button", { name: "Use this answer" }));
    await screen.findByRole("form", { name: "Save this rule" });
    expect(f.calls.find((c) => c.path.endsWith("/turns"))?.body).toEqual({ answer: { question_id: "q1", option_id: "x2" } });
    // the dry run is made on its own for this exact rule
    expect(await screen.findByText(/Evaluated 41 stored cases, fired on 6/)).toBeInTheDocument();
    expect(f.count(DRY)).toBe(1);

    // saving needs a name, a reason and the tick that it checks less
    const save = screen.getByRole("button", { name: "Save rule" });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText("Name"), "Heavy borrowing");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "Loan desk asked for it");
    expect(save).toBeDisabled();
    expect(screen.getByText(/Still needed to save: the tick that this rule checks less/)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /checks less than I asked for/ }));
    await user.click(screen.getByRole("checkbox", { name: /send the part it cannot check to the tech team/ }));
    expect(save).toBeEnabled();
    await user.click(save);

    expect(await screen.findByText("Saved: Heavy borrowing")).toBeInTheDocument();
    expect(f.calls.find((c) => c.method === "POST" && c.path === "/custom-rules")?.body).toEqual({
      draft_id: "crd_5b2e9f0a1c3d", name: "Heavy borrowing", reason: "Loan desk asked for it", accept_narrower: true, confirm_warnings: false, submit_ticket: true,
    });
    expect(screen.getByText("This will be submitted to the tech team (ticket crt_1a2b3c4d5e6f).")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ rule_id: "cus_3f9a1c2b7d" }));
    expect(f.unhandled).toEqual([]);
  });

  it("sends the missing part to the tech team on request, without a model, and then shows the ticket", async () => {
    const { f, user, onTicket } = setup({
      [DRAFTS]: () => json(debtDraft()),
      [TICKETS]: () => json({ ticket: ticket(), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: false }, 201),
    });
    await ask(user);
    await user.click(await screen.findByRole("button", { name: "Send the missing part to the tech team" }));
    expect(await screen.findByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path === "/custom-rules/tickets")?.body).toEqual({ draft_id: "crd_5b2e9f0a1c3d" });
    expect(screen.queryByRole("button", { name: "Send the missing part to the tech team" })).toBeNull();
    expect(onTicket).toHaveBeenCalled();
  });
});

describe("RuleAssistant: the request box", () => {
  it("keeps the button off until the request is long enough, counts characters and sends the picked severity", async () => {
    const { f, user } = setup({ [DRAFTS]: () => json(draft({ save_allowed: false })) });
    const button = screen.getByRole("button", { name: /Ask the assistant/ });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText(/Describe the check/), "invoice total over 100000");
    expect(screen.getByText("25 of 1000 characters")).toBeInTheDocument();
    expect(button).toBeEnabled();
    await user.selectOptions(screen.getByLabelText("How serious should a flag be?"), "low");
    await user.click(button);
    await screen.findByText("Ready to save");
    expect(f.calls[0].body).toEqual({ text: "invoice total over 100000", severity: "low" });
  });

  it("offers info, low and medium and never high", () => {
    setup({});
    const select = screen.getByLabelText("How serious should a flag be?");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["As I wrote it above", "For information", "Low concern", "Medium concern"]);
  });

  it("starts a new version of a rule when editing, with the rule named", async () => {
    const { f, user } = setup({ [DRAFTS]: () => json(draft({ edit_of: "cus_3f9a1c2b7d", save_allowed: false })) }, { id: "cus_3f9a1c2b7d", name: "Heavy borrowing" });
    expect(screen.getByText("New version of Heavy borrowing")).toBeInTheDocument();
    await ask(user, "invoice total over 50000");
    await screen.findByText("Ready to save");
    expect(f.calls[0].body).toEqual({ text: "invoice total over 50000", edit_of: "cus_3f9a1c2b7d" });
  });

  it("can cancel an edit", async () => {
    const { user, onCancelEdit } = setup({}, { id: "cus_3f9a1c2b7d", name: "Heavy borrowing" });
    await user.click(screen.getByRole("button", { name: "Cancel the change" }));
    expect(onCancelEdit).toHaveBeenCalled();
  });
});

describe("RuleAssistant: every status", () => {
  it("ready: shows the rule in words, tests it, saves with a name and a reason and no extra tick", async () => {
    const { f, user, onSaved } = setup({
      [DRAFTS]: () => json(draft()),
      [DRY]: () => json(dryRun()),
      [SAVE]: () => json({ rule: ruleView({ name: "Big invoices" }), ticket: null }, 201),
    });
    await ask(user, "flag as low if an invoice total is more than 100000");
    expect(await screen.findByText("Ready to save")).toBeInTheDocument();
    expect(screen.getAllByText(INVOICE_READBACK).length).toBeGreaterThan(0);
    expect(screen.queryByRole("checkbox", { name: /checks less than I asked for/ })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /Save anyway/ })).toBeNull();
    await screen.findByText(/Evaluated 41 stored cases/);
    const save = screen.getByRole("button", { name: "Save rule" });
    await user.type(screen.getByLabelText("Name"), "Big invoices");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "abcd");
    expect(save).toBeDisabled(); // a reason is at least 5 characters
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "e");
    expect(save).toBeEnabled();
    await user.click(save);
    await screen.findByText("Saved: Big invoices");
    expect(f.calls.find((c) => c.path === "/custom-rules" && c.method === "POST")?.body).toEqual({
      draft_id: "crd_5b2e9f0a1c3d", name: "Big invoices", reason: "abcde", accept_narrower: false, confirm_warnings: false, submit_ticket: false,
    });
    expect(onSaved).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Go to my checks" })).toBeInTheDocument();
  });

  it("a dry run with a warning needs Save anyway before the rule can be saved, and sends confirm_warnings", async () => {
    const { f, user } = setup({
      [DRAFTS]: () => json(draft()),
      [DRY]: () => json(dryRun({ fired: 0, fire_rate: 0, warnings: [{ code: "fires_on_none", message: "It fired on none of the 41 cases evaluated." }], needs_confirmation: true })),
      [SAVE]: () => json({ rule: ruleView(), ticket: null }, 201),
    });
    await ask(user, "flag as low if an invoice total is more than 100000");
    expect(await screen.findByText("It fired on none of the 41 cases evaluated.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "Big invoices");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "Credit policy");
    const save = screen.getByRole("button", { name: "Save rule" });
    expect(save).toBeDisabled();
    expect(screen.getByText(/Still needed to save: "Save anyway" under the test/)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Save anyway/ }));
    expect(save).toBeEnabled();
    await user.click(save);
    await screen.findByText(/^Saved:/);
    expect(f.calls.find((c) => c.path === "/custom-rules" && c.method === "POST")?.body).toMatchObject({ confirm_warnings: true });
  });

  it("a rule that fires too often warns too, and the warning goes away when the test is run again for a changed rule", async () => {
    const { user } = setup({
      [DRAFTS]: () => json(draft()),
      [DRY]: () => json(dryRun({ fired: 30, evaluated: 41, fire_rate: 0.73, warnings: [{ code: "fires_too_often", message: "It fired on 30 of 41 cases (73%); a rule that fires that often is noise." }], needs_confirmation: true })),
    });
    await ask(user, "flag as low if an invoice total is more than 1");
    expect(await screen.findByText(/a rule that fires that often is noise/)).toBeInTheDocument();
    expect(screen.getByText("73%")).toBeInTheDocument();
  });

  it("shows a failed test with a way to run it again, and keeps saving off", async () => {
    let n = 0;
    const { user } = setup({
      [DRAFTS]: () => json(draft()),
      [DRY]: () => (++n === 1 ? errorBody("internal_error", "The test could not read the store.", 500) : json(dryRun())),
    });
    await ask(user, "flag as low if an invoice total is more than 100000");
    expect(await screen.findByText("The test could not read the store.")).toBeInTheDocument();
    expect(screen.getByText(/Still needed to save:.*the test on stored cases/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run the test" }));
    expect(await screen.findByText(/Evaluated 41 stored cases/)).toBeInTheDocument();
  });

  it("needs_clarification: asks the question, applies nothing, and a reply in words is a text turn", async () => {
    const q = draft({
      status: "needs_clarification", spec: null, spec_hash: null, readback: null, save_allowed: false, will_check: [],
      understood: "Flag an invoice with an old date.",
      questions: [{ id: "q1", slot: "field", text: "Which date do you mean?", default: null, options: [{ id: "inv", label: "Invoice date", value: "invoice.invoice_date" }, { id: "due", label: "Due date", value: "invoice.due_date" }] }],
      notes: ["The request does not say which date."],
    });
    const { f, user } = setup({ [DRAFTS]: () => json(q), [TURNS]: () => json(draft({ turn: 2, save_allowed: false })), [DRY]: () => json(dryRun()) });
    await ask(user, "flag an invoice with an old date");
    expect(await screen.findByText("A few questions first")).toBeInTheDocument();
    expect(screen.getByText("Which date do you mean?")).toBeInTheDocument();
    expect(screen.getByText("The request does not say which date.")).toBeInTheDocument();
    expect(screen.queryByText("Suggested")).toBeNull(); // no proposed default for this one
    expect(screen.queryByRole("form", { name: "Save this rule" })).toBeNull();
    await user.type(screen.getByLabelText("Or answer in your own words"), "the invoice date");
    await user.click(screen.getByRole("button", { name: "Send reply" }));
    await screen.findByText("Ready to save");
    expect(f.calls.find((c) => c.path.endsWith("/turns"))?.body).toEqual({ text: "the invoice date" });
  });

  it("ambiguous: lists each reading in words and continues the same draft with the chosen one", async () => {
    const amb = draft({
      status: "ambiguous", spec: null, spec_hash: null, readback: null, save_allowed: false, will_check: [],
      candidates: [
        { index: 0, spec: draft().spec!, readback: "Flags low when an invoice's total is greater than 50,000.00." },
        { index: 1, spec: draft().spec!, readback: "Flags low when an invoice's subtotal is greater than 50,000.00." },
      ],
    });
    const { f, user } = setup({ [DRAFTS]: () => json(amb), [TURNS]: () => json(draft({ turn: 2 })), [DRY]: () => json(dryRun()) });
    await ask(user, "flag invoices over 50000");
    const list = await screen.findByRole("list", { name: "Possible readings" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /Use this one \(reading 2\)/ }));
    await screen.findByText("Ready to save");
    expect(f.calls.find((c) => c.path.endsWith("/turns"))?.body).toEqual({ choose_candidate: 1 });
  });

  it("unsupported: says it will be submitted to the tech team with the ticket id, lists what is possible, offers no save", async () => {
    const un = draft({
      status: "unsupported", spec: null, spec_hash: null, readback: null, save_allowed: false, will_check: [],
      understood: "Flag companies that post about money problems.",
      unsupported_parts: [{ phrase: "posts on social media", reason: "doc_not_extracted", detail: "" }],
      ideas: [
        { kind: "template", text: "a field that is absent or empty on a document of that type", template: "field_missing" },
        { kind: "related_builtin", text: "A receipt must name its merchant.", rule: "rcpt_merchant" },
        { kind: "weaker_rule", text: "Flags low when an invoice's total is greater than 100,000.00.", spec: draft().spec },
      ],
      ticket: { id: "crt_9c0d1e2f3a4b", status: "submitted", message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b)." },
    });
    const { f, user } = setup({ [DRAFTS]: () => json(un) });
    await ask(user, "flag if the company posts about money problems on social media");
    expect(await screen.findByText("This is beyond what a custom rule can do")).toBeInTheDocument();
    expect(screen.getByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).")).toBeInTheDocument();
    expect(screen.getByText(/the system does not read this kind of document/)).toBeInTheDocument();
    expect(screen.getByText("What is possible instead")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open this check in the catalogue" })).toHaveAttribute("href", "/rules?check=rcpt_merchant");
    expect(screen.queryByRole("form", { name: "Save this rule" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Send the missing part/ })).toBeNull(); // already sent automatically
    expect(f.count(TICKETS)).toBe(0);
    // a weaker rule can be tried as a new request
    await user.click(screen.getByRole("button", { name: "Use this as my request" }));
    expect(screen.getByLabelText(/Describe the check in plain English/)).toHaveValue("Flags low when an invoice's total is greater than 100,000.00.");
  });

  it("refused: shows the reason, says it is not sent on, and has no ticket button and no save", async () => {
    const refused = draft({
      status: "refused", spec: null, spec_hash: null, readback: null, save_allowed: false, will_check: [], understood: "",
      refusal: { reason: "protected_attribute", message: "A rule may not look at nationality, sex, age or date of birth, or at people's names." },
      model_calls: 0,
    });
    const { f, user } = setup({ [DRAFTS]: () => json(refused) });
    await ask(user, "flag directors of a certain nationality");
    expect(await screen.findByText("This request was refused")).toBeInTheDocument();
    expect(screen.getByText(/A rule may not look at nationality/)).toBeInTheDocument();
    expect(screen.getByText(/It is not sent to the tech team/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /tech team/ })).toBeNull();
    expect(screen.queryByRole("form", { name: "Save this rule" })).toBeNull();
    expect(f.count(TICKETS)).toBe(0);
    expect(screen.getByRole("button", { name: "Start a new request" })).toBeInTheDocument();
  });

  it("an identical built-in blocks saving and points to the check in the catalogue", async () => {
    const dup = draft({
      save_allowed: false,
      duplicates: [{ kind: "builtin", level: "identical", rule: "rcpt_merchant", name: "A receipt must name its merchant.", what: "A receipt must name its merchant.", action: "This is already checked by rcpt_merchant, which flags at medium now. To change how serious it is, change its severity in the check catalogue (Check catalogue > rcpt_merchant)." }],
    });
    const { user } = setup({ [DRAFTS]: () => json(dup) });
    await ask(user, "flag a receipt with no merchant name");
    expect(await screen.findByText("A built-in check already does exactly this")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open rcpt_merchant in the check catalogue" })).toHaveAttribute("href", "/rules?check=rcpt_merchant");
    expect(screen.queryByRole("form", { name: "Save this rule" })).toBeNull();
    expect(screen.getByText("This rule cannot be saved as it stands")).toBeInTheDocument();
  });

  it("a near-duplicate is only a warning and does not stop saving", async () => {
    const near = draft({ duplicates: [{ kind: "custom", level: "similar", rule: "cus_0123456789", name: "Large invoices", what: "Flags low when an invoice's total is greater than 90,000.00.", action: "Check you need both." }] });
    const { user } = setup({ [DRAFTS]: () => json(near), [DRY]: () => json(dryRun()) });
    await ask(user, "flag as low if an invoice total is more than 100000");
    expect(await screen.findByText("A custom rule looks at something close")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Save this rule" })).toBeInTheDocument();
  });
});

describe("RuleAssistant: when something goes wrong", () => {
  it("a used-up budget explains itself and still lets the idea go to the tech team without a model", async () => {
    const { f, user } = setup({
      [DRAFTS]: () => errorBody("user_budget_used", "Your daily model budget is used.", 429),
      [TICKETS]: () => json({ ticket: ticket(), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: false }, 201),
    });
    await ask(user);
    expect(await screen.findByText("Your model budget for today is used up")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send this idea to the tech team" }));
    expect(await screen.findByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).")).toBeInTheDocument();
    expect(f.calls.find((c) => c.path === "/custom-rules/tickets")?.body).toEqual({ text: REQUEST });
  });

  it("an unavailable assistant says so and offers the tech team", async () => {
    const { user } = setup({ [DRAFTS]: () => errorBody("authoring_unavailable", "The rule assistant is not available right now.", 503) });
    await ask(user);
    expect(await screen.findByText("The rule assistant is not available")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send this idea to the tech team" })).toBeInTheDocument();
  });

  it("a turn limit explains that the idea can go to the tech team", async () => {
    const { user } = setup({
      [DRAFTS]: () => json(debtDraft()),
      [TURNS]: () => errorBody("draft_turn_limit", "Start again or send it to the tech team.", 409),
    });
    await ask(user);
    await user.click(await screen.findByRole("radio", { name: /More than equity/ }));
    await user.click(screen.getByRole("button", { name: "Use this answer" }));
    expect(await screen.findByText("Too many turns on one request")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send this idea to the tech team" })).toBeInTheDocument();
  });

  it("a duplicate found at save time links to the built-in check", async () => {
    const { user } = setup({
      [DRAFTS]: () => json(draft()),
      [DRY]: () => json(dryRun()),
      [SAVE]: () => errorBody("duplicate_builtin", "A built-in check already does this.", 409, { rule: "rcpt_merchant", what: "A receipt must name its merchant.", action: "Change its severity in the check catalogue." }),
    });
    await ask(user, "flag as low if an invoice total is more than 100000");
    await screen.findByText(/Evaluated 41 stored cases/);
    await user.type(screen.getByLabelText("Name"), "Big invoices");
    await user.type(screen.getByLabelText(/Why are you adding this rule/), "Credit policy");
    await user.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("A built-in check already does this")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open rcpt_merchant in the check catalogue" })).toHaveAttribute("href", "/rules?check=rcpt_merchant");
    expect(screen.queryByText(/^Saved:/)).toBeNull();
  });

  it("an answer the server cannot use is explained and the question stays", async () => {
    const { user } = setup({ [DRAFTS]: () => json(debtDraft()), [TURNS]: () => errorBody("bad_answer", "That answer gives an invalid rule.", 422) });
    await ask(user);
    await user.click(await screen.findByRole("radio", { name: /More than 3 times equity/ }));
    await user.click(screen.getByRole("button", { name: "Use this answer" }));
    expect(await screen.findByText("That answer cannot be used")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /More than 3 times equity/ })).toBeInTheDocument();
  });

  it("warns on the last turn that the conversation ends, with the way out", async () => {
    const last = debtDraft({ turn: 6, turns_left: 0 });
    const { user } = setup({ [DRAFTS]: () => json(last) });
    await ask(user);
    expect(await screen.findByText("That was the last of 6 turns")).toBeInTheDocument();
  });
});

describe("RuleAssistant: text is text", () => {
  it("shows what the model and the officer wrote as plain text, never as markup", async () => {
    const evil = '<img src=x onerror="window.__owned=1"> <b>bold</b>';
    const d: DraftResponse = debtDraft({
      understood: evil,
      assumptions: [evil],
      questions: [{ id: "q1", slot: "multiplier", text: evil, default: null, options: [{ id: "a", label: evil, value: "1" }, { id: "b", label: "two", value: "2" }] }],
      unsupported_parts: [{ phrase: evil, reason: "field_not_extracted", detail: evil }],
      will_not_check: [evil],
    });
    const { user } = setup({ [DRAFTS]: () => json(d) });
    await ask(user, evil + " a lot of debt");
    await screen.findByText("Part of this can be checked");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("main b, section b, form b")).toBeNull();
    expect(screen.getAllByText(evil, { exact: false }).length).toBeGreaterThan(3);
    expect((window as unknown as { __owned?: number }).__owned).toBeUndefined();
  });
});

describe("RuleAssistant: starting over", () => {
  it("clears the draft and the request when the officer starts a new request", async () => {
    const { user } = setup({ [DRAFTS]: () => json(draft({ save_allowed: false })) });
    await ask(user, "flag as low if an invoice total is more than 100000");
    await screen.findByText("Ready to save");
    await user.click(screen.getByRole("button", { name: "Start a new request" }));
    expect(screen.getByLabelText(/Describe the check in plain English/)).toHaveValue("");
    await waitFor(() => expect(screen.queryByText("Ready to save")).toBeNull());
  });
});
