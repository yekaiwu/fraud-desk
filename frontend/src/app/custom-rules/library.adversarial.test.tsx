/**
 * Adversarial tests for the lists around the assistant: My rules, Public rules (adopt and unadopt), the requests to the
 * tech team, the Custom section of the catalogue page and the Custom rules page itself. Loading, empty and error states,
 * double clicks, reasons, who may do what, and text that must never be read as markup.
 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SWRConfig } from "swr";
import { CatalogueCustomSection } from "@/components/custom-rules/CatalogueSection";
import { CustomTickets } from "@/components/custom-rules/CustomTickets";
import { MyCustomRules } from "@/components/custom-rules/MyCustomRules";
import { SharedCustomRules } from "@/components/custom-rules/SharedCustomRules";
import CustomRulesPage from "@/app/rules/custom/page";
import { adopted, errorBody, json, routedFetch, ruleView, shared, ticket } from "@/test/customRulesFixtures";
import { renderWithSWR } from "@/test/utils";

type Routes = Parameters<typeof routedFetch>[0];

const IMG = '<img src=x onerror="window.__pwned=1">';
const SCRIPT = "<script>window.__pwned=2</script>";

function nothingInjected() {
  expect(document.querySelector("img")).toBeNull();
  expect(document.querySelector("script")).toBeNull();
  expect(document.querySelector("iframe")).toBeNull();
  expect(document.querySelector("[onerror]")).toBeNull();
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
}

const wrap = (ui: React.ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------------------------------------ My rules

describe("My rules", () => {
  function mine(routes: Routes) {
    const f = routedFetch({ "GET /custom-rules": () => json({ mine: [], adopted: [] }), ...routes });
    vi.stubGlobal("fetch", f.fn);
    const props = { onEdit: vi.fn(), onNew: vi.fn() };
    wrap(<MyCustomRules {...props} />);
    return { f, user: userEvent.setup(), ...props };
  }
  const POST_PUBLISH = "POST /custom-rules/cus_3f9a1c2b7d/publish";

  it("shows a loading status while the list is on its way, and no empty state yet", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    mine({ "GET /custom-rules": async () => (await gate, json({ mine: [ruleView()], adopted: [] })) });
    expect(await screen.findByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Loading your rules...")).toBeInTheDocument();
    expect(screen.queryByText("You have not written a rule yet")).toBeNull();
    release();
    expect(await screen.findByRole("heading", { name: "Heavy borrowing" })).toBeInTheDocument();
  });

  it("explains a load failure and Retry asks again", async () => {
    let n = 0;
    const { f, user } = mine({ "GET /custom-rules": () => (n++ === 0 ? errorBody("internal_error", "boom", 500) : json({ mine: [ruleView()], adopted: [] })) });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByRole("heading", { name: "Heavy borrowing" })).toBeInTheDocument();
    expect(f.count("GET /custom-rules")).toBe(2);
  });

  it("shows rule names, descriptions and authors that contain markup as text only", async () => {
    mine({
      "GET /custom-rules": () =>
        json({
          mine: [ruleView({ name: `mine ${IMG}`, readback: `readback ${SCRIPT}`, owner: "<b>eve</b>" })],
          adopted: [adopted({ name: `adopted ${IMG}`, author: "<i>mallory</i>", readback: `adopted readback ${SCRIPT}` })],
        }),
    });
    expect(await screen.findByRole("heading", { name: /mine <img/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /adopted <img/ })).toBeInTheDocument();
    expect(screen.getAllByText(/readback <script>/).length).toBeGreaterThan(0);
    expect(screen.getByText(/By <i>mallory<\/i>/)).toBeInTheDocument();
    nothingInjected();
    expect(document.querySelector("b, i")).toBeNull();
  });

  it("gives each card's buttons names that tell the rules apart (two rules, two 'Switch off' buttons)", async () => {
    mine({ "GET /custom-rules": () => json({ mine: [ruleView(), ruleView({ rule_id: "cus_bbbbbbbbbb", name: "Second rule" })], adopted: [] }) });
    await screen.findByRole("heading", { name: "Second rule" });
    for (const verb of ["Switch off", "Make public", "Delete", "New version of", "Details of"]) {
      expect(screen.getByRole("button", { name: `${verb} Heavy borrowing` })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: `${verb} Second rule` })).toBeInTheDocument();
    }
  });

  it("a public rule offers 'Take back' and not 'Make public'; a private one the other way round", async () => {
    mine({ "GET /custom-rules": () => json({ mine: [ruleView({ visibility: "public", published_version: 1 }), ruleView({ rule_id: "cus_bbbbbbbbbb", name: "Second rule" })], adopted: [] }) });
    await screen.findByRole("heading", { name: "Second rule" });
    expect(screen.getByRole("button", { name: "Take back Heavy borrowing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Make public Heavy borrowing" })).toBeNull();
    expect(screen.getByRole("button", { name: "Make public Second rule" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take back Second rule" })).toBeNull();
  });

  describe("the reason dialog", () => {
    async function open(extra: Routes = {}) {
      const s = mine({ "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [] }), ...extra });
      await s.user.click(await screen.findByRole("button", { name: "Make public Heavy borrowing" }));
      const dialog = await screen.findByRole("dialog");
      return { ...s, dialog };
    }

    it("needs five real characters: spaces around four do not count", async () => {
      const { user, dialog, f } = await open();
      const go = within(dialog).getByRole("button", { name: "Make public" });
      await user.type(within(dialog).getByLabelText("Reason"), "   abcd   ");
      expect(go).toBeDisabled();
      await user.type(within(dialog).getByLabelText("Reason"), "e");
      expect(go).toBeEnabled();
      expect(f.count(POST_PUBLISH)).toBe(0);
    });

    it("Enter in the reason box with too short a reason sends nothing", async () => {
      const { user, dialog, f } = await open();
      await user.type(within(dialog).getByLabelText("Reason"), "abc");
      await user.keyboard("{Enter}");
      expect(f.count(POST_PUBLISH)).toBe(0);
    });

    it("Cancel closes it without calling the API, and reopening starts with an empty reason", async () => {
      const { user, dialog, f } = await open();
      await user.type(within(dialog).getByLabelText("Reason"), "Share with the desk");
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByLabelText("Reason")).toBeNull());
      expect(f.count(POST_PUBLISH)).toBe(0);
      await user.click(screen.getByRole("button", { name: "Make public Heavy borrowing" }));
      expect(await screen.findByLabelText("Reason")).toHaveValue("");
    });

    it("trims the reason, and a double click on the confirm button makes one request", async () => {
      let release!: () => void;
      const gate = new Promise<void>((r) => (release = r));
      const { user, dialog, f } = await open({
        [POST_PUBLISH]: async () => (await gate, json({ rule: ruleView({ visibility: "public", published_version: 1 }) })),
      });
      await user.type(within(dialog).getByLabelText("Reason"), "  Share with the desk  ");
      await user.dblClick(within(dialog).getByRole("button", { name: "Make public" }));
      release();
      expect(await screen.findByText(/is public\. Other accounts can adopt it/)).toBeInTheDocument();
      expect(f.count(POST_PUBLISH)).toBe(1);
      expect(f.calls.find((c) => c.path.endsWith("/publish"))?.body).toEqual({ reason: "Share with the desk" });
    });

    it.each([
      ["rule_retired", 409, "This rule was deleted"],
      ["already_public", 409, "Already public"],
      ["rule_limit", 409, "Too many active rules"],
      ["forbidden", 403, "Not available to this account"],
    ])("explains %s, keeps the dialog open and the reason typed", async (code, status, title) => {
      const { user, dialog } = await open({ [POST_PUBLISH]: () => errorBody(code, "server says no", status) });
      await user.type(within(dialog).getByLabelText("Reason"), "Share with the desk");
      await user.click(within(dialog).getByRole("button", { name: "Make public" }));
      expect(await within(dialog).findByText(title)).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Reason")).toHaveValue("Share with the desk");
      expect(screen.queryByText(/is public\./)).toBeNull();
    });

    it("says what making a rule public means before the reason is asked for", async () => {
      const { dialog } = await open();
      expect(within(dialog).getByText(/with your name on it, labelled custom and unreviewed/)).toBeInTheDocument();
      expect(within(dialog).getByText(/frozen once it is public/)).toBeInTheDocument();
    });

    it("deleting is styled as a danger action and says the past stays explainable", async () => {
      const s = mine({ "GET /custom-rules": () => json({ mine: [ruleView()], adopted: [] }) });
      await s.user.click(await screen.findByRole("button", { name: "Delete Heavy borrowing" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByRole("button", { name: "Delete rule" })).toBeInTheDocument();
      expect(within(dialog).getByText(/stay explainable/)).toBeInTheDocument();
    });
  });

  it("a deleted rule can only be looked at: no switch, publish, edit or delete", async () => {
    mine({ "GET /custom-rules": () => json({ mine: [ruleView({ status: "retired", name: "Gone rule" })], adopted: [] }) });
    expect(await screen.findByText("Deleted rules (1)")).toBeInTheDocument();
    const li = screen.getByRole("heading", { name: "Gone rule", hidden: true }).closest("li") as HTMLElement;
    expect(within(li).getByText("Deleted")).toBeInTheDocument();
    expect(within(li).getAllByRole("button", { hidden: true }).map((b) => b.textContent?.trim())).toEqual(["Details of Gone rule"]);
  });

  it("stopping use of an adopted rule that the server says is not adopted is explained and the rule stays listed", async () => {
    const { user } = mine({
      "GET /custom-rules": () => json({ mine: [], adopted: [adopted()] }),
      "POST /custom-rules/cus_77aa00bb11/unadopt": () => errorBody("not_adopted", "x", 409),
    });
    await user.click(await screen.findByRole("button", { name: "Stop using Expired passport" }));
    expect(await screen.findByText("Not adopted")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Expired passport" })).toBeInTheDocument();
  });

  it("refreshes the list after a change, so the new state is what the officer sees", async () => {
    let enabled = true;
    const { f, user } = mine({
      "GET /custom-rules": () => json({ mine: [ruleView({ enabled })], adopted: [] }),
      "POST /custom-rules/cus_3f9a1c2b7d/enabled": () => ((enabled = false), json({ rule: ruleView({ enabled: false }) })),
    });
    await user.click(await screen.findByRole("button", { name: "Switch off Heavy borrowing" }));
    await user.type(within(await screen.findByRole("dialog")).getByLabelText("Reason"), "Not needed");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Switch off" }));
    expect(await screen.findByRole("button", { name: "Switch on Heavy borrowing" })).toBeInTheDocument();
    expect(screen.getByText("Switched off")).toBeInTheDocument();
    expect(f.count("GET /custom-rules")).toBeGreaterThanOrEqual(2);
  });

  it("the details sheet quotes the reasons in the change log as text, with a sorted version list, and explains a failure", async () => {
    const { user } = mine({
      "GET /custom-rules": () => json({ mine: [ruleView({ version: 2 })], adopted: [] }),
      "GET /custom-rules/cus_3f9a1c2b7d": () =>
        json(
          ruleView({
            version: 2, description: `desc ${IMG}`,
            versions: [
              { version: 1, spec: ruleView().spec, spec_hash: "h1", readback: "readback one", created_at: "2026-11-01T09:00:00Z", created_by: "alice", reason: `why ${SCRIPT}` },
              { version: 2, spec: ruleView().spec, spec_hash: "h2", readback: "readback two", created_at: "2026-11-02T09:00:00Z", created_by: "alice", reason: "Tighter" },
            ],
          }),
        ),
      "GET /custom-rules/changes": () => errorBody("internal_error", "no log", 500),
    });
    await user.click(await screen.findByRole("button", { name: "Details of Heavy borrowing" }));
    expect(await screen.findByText(/desc <img/)).toBeInTheDocument();
    const versions = screen.getAllByText(/^Version \d/, { selector: "p" }).map((p) => p.textContent);
    expect(versions[0]).toMatch(/^Version 2/);
    expect(screen.getByText(/why <script>/)).toBeInTheDocument();
    nothingInjected();
    // the change log failing does not hide the rule
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(screen.getByText("readback two")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------------- Public rules

describe("Public rules", () => {
  function pub(routes: Routes, admin = false) {
    const f = routedFetch({ "GET /custom-rules/shared": () => json({ rules: [] }), "GET /custom-rules": () => json({ mine: [], adopted: [] }), ...routes });
    vi.stubGlobal("fetch", f.fn);
    wrap(<SharedCustomRules admin={admin} />);
    return { f, user: userEvent.setup() };
  }
  const ADOPT = "POST /custom-rules/cus_77aa00bb11/adopt";
  const UNADOPT = "POST /custom-rules/cus_77aa00bb11/unadopt";

  it("shows a loading status, then the rules; and a load failure with Retry", async () => {
    let n = 0;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const { f, user } = pub({
      "GET /custom-rules/shared": async () => {
        if (n++ === 0) {
          await gate;
          return errorBody("internal_error", "boom", 500);
        }
        return json({ rules: [shared()] });
      },
    });
    expect(await screen.findByText("Loading public rules...")).toBeInTheDocument();
    expect(screen.queryByText("No public rules yet")).toBeNull();
    release();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByRole("heading", { name: "Expired passport" })).toBeInTheDocument();
    expect(f.count("GET /custom-rules/shared")).toBe(2);
  });

  it("another account's rule: attributed to its author, labelled Custom and unreviewed, with the author's words as text", async () => {
    pub({ "GET /custom-rules/shared": () => json({ rules: [shared({ name: `n ${IMG}`, author: "<b>eve</b>", description: `d\n${SCRIPT}`, readback: `r ${IMG}` })] }) });
    const li = (await screen.findByRole("heading", { name: /n <img/ })).closest("li") as HTMLElement;
    expect(within(li).getByText("Custom")).toBeInTheDocument();
    expect(within(li).getByText("custom, unreviewed")).toBeInTheDocument();
    expect(within(li).getByText("<b>eve</b>")).toBeInTheDocument();
    expect(within(li).getByText(/<script>window/)).toBeInTheDocument();
    nothingInjected();
    expect(document.querySelector("b")).toBeNull();
  });

  it("uses the singular for one account and plural otherwise", async () => {
    pub({ "GET /custom-rules/shared": () => json({ rules: [shared({ adoptions: 1 }), shared({ rule_id: "cus_bbbbbbbbbb", name: "Other", adoptions: 0 })] }) });
    expect(await screen.findByText("1 account uses it")).toBeInTheDocument();
    expect(screen.getByText("0 accounts use it")).toBeInTheDocument();
  });

  it("the officer's own public rule can be neither adopted nor taken back from here, even by an administrator", async () => {
    pub({ "GET /custom-rules/shared": () => json({ rules: [shared({ own: true, author: "alice" })] }) }, true);
    const li = (await screen.findByRole("heading", { name: "Expired passport" })).closest("li") as HTMLElement;
    expect(within(li).getByText("Your own rule. Manage it under My rules.")).toBeInTheDocument();
    expect(within(li).queryByRole("button")).toBeNull();
    expect(within(li).getByText("(you)", { exact: false })).toBeInTheDocument();
  });

  it("adopting makes one request even on a double click, and the card then says Adopted (v1) with a Stop using button", async () => {
    let on = false;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const { f, user } = pub({
      "GET /custom-rules/shared": () => json({ rules: [shared({ adopted: on, adopted_version: on ? 1 : null })] }),
      [ADOPT]: async () => (await gate, (on = true), json({ adoption: adopted() })),
    });
    await user.dblClick(await screen.findByRole("button", { name: "Adopt Expired passport" }));
    release();
    expect(await screen.findByText("Adopted (v1)")).toBeInTheDocument();
    expect(f.count(ADOPT)).toBe(1);
    expect(screen.getByRole("button", { name: "Stop using Expired passport" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Adopt Expired passport" })).toBeNull();
  });

  it("while one rule is being adopted the other cards stay usable", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const { user } = pub({
      "GET /custom-rules/shared": () => json({ rules: [shared(), shared({ rule_id: "cus_bbbbbbbbbb", name: "Other rule" })] }),
      [ADOPT]: async () => (await gate, json({ adoption: adopted() })),
    });
    await user.click(await screen.findByRole("button", { name: "Adopt Expired passport" }));
    expect(screen.getByRole("button", { name: "Adopt Other rule" })).toBeEnabled();
    release();
  });

  it.each([
    ["own_rule", 409, "This is your own rule"],
    ["already_adopted", 409, "Already adopted"],
    ["not_public", 409, "Not public"],
    ["rule_retired", 409, "This rule was deleted"],
    ["user_budget_used", 429, "Your model budget for today is used up"],
  ])("a refused adoption (%s) is explained and nothing is shown as adopted", async (code, status, title) => {
    const { user } = pub({ "GET /custom-rules/shared": () => json({ rules: [shared()] }), [ADOPT]: () => errorBody(code, "no", status) });
    await user.click(await screen.findByRole("button", { name: "Adopt Expired passport" }));
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.queryByText(/Adopted \(v/)).toBeNull();
    expect(screen.queryByText(/You adopted/)).toBeNull();
    expect(screen.getByRole("button", { name: "Adopt Expired passport" })).toBeEnabled(); // can try again
  });

  it("an adoption is pinned to the version adopted: a newer public version is offered, never applied silently", async () => {
    const { f, user } = pub({
      "GET /custom-rules/shared": () => json({ rules: [shared({ version: 2, adopted: true, adopted_version: 1 })] }),
      [ADOPT]: () => json({ adoption: adopted({ version: 2, latest_version: 2 }) }),
    });
    expect(await screen.findByText("Adopted (v1)")).toBeInTheDocument();
    expect(screen.getByText("Update available: you use version 1.")).toBeInTheDocument();
    expect(f.count(ADOPT)).toBe(0);
    await user.click(screen.getByRole("button", { name: "Adopt version 2 of Expired passport" }));
    await screen.findByText(/You adopted Expired passport \(version 2\)/);
  });

  it("stopping use removes the adoption and says it is no longer running for the officer", async () => {
    let on = true;
    const { f, user } = pub({
      "GET /custom-rules/shared": () => json({ rules: [shared({ adopted: on, adopted_version: on ? 1 : null })] }),
      [UNADOPT]: () => ((on = false), json({ rule_id: "cus_77aa00bb11", adopted: false })),
    });
    await user.click(await screen.findByRole("button", { name: "Stop using Expired passport" }));
    expect(await screen.findByText("You stopped using Expired passport.")).toBeInTheDocument();
    expect(f.count(UNADOPT)).toBe(1);
    expect(await screen.findByRole("button", { name: "Adopt Expired passport" })).toBeInTheDocument();
  });

  it("an ordinary officer sees no administrator controls; an administrator sees them on other accounts' rules only", async () => {
    const rules = [shared(), shared({ rule_id: "cus_bbbbbbbbbb", name: "Mine", own: true, author: "alice" })];
    const plain = pub({ "GET /custom-rules/shared": () => json({ rules }) }, false);
    await screen.findByRole("heading", { name: "Mine" });
    expect(screen.queryByRole("button", { name: /administrator/ })).toBeNull();
    expect(plain.f.count("GET /custom-rules/shared")).toBeGreaterThan(0);
    cleanup();
    vi.unstubAllGlobals();
    pub({ "GET /custom-rules/shared": () => json({ rules }) }, true);
    await screen.findByRole("heading", { name: "Mine" });
    expect(screen.getAllByRole("button", { name: /Take back \(administrator\)/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Take back (administrator) Expired passport" })).toBeInTheDocument();
  });

  it("an administrator's take-back needs a reason, sends it, and an error leaves the dialog open", async () => {
    const UNPUB = "POST /custom-rules/cus_77aa00bb11/unpublish";
    let n = 0;
    const { f, user } = pub(
      {
        "GET /custom-rules/shared": () => json({ rules: [shared()] }),
        [UNPUB]: () => (n++ === 0 ? errorBody("not_public", "already private", 409) : json({ rule: ruleView({ rule_id: "cus_77aa00bb11" }) })),
      },
      true,
    );
    await user.click(await screen.findByRole("button", { name: "Take back (administrator) Expired passport" }));
    const dialog = await screen.findByRole("dialog");
    const go = within(dialog).getByRole("button", { name: "Take back" });
    await user.type(within(dialog).getByLabelText("Reason"), "abcd");
    expect(go).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Reason"), "e");
    await user.click(go);
    expect(await within(dialog).findByText("Not public")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(go);
    expect(await screen.findByText("Expired passport was taken back.")).toBeInTheDocument();
    expect(f.calls.filter((c) => c.path.endsWith("/unpublish")).map((c) => c.body)).toEqual([{ reason: "abcde" }, { reason: "abcde" }]);
  });
});

// ----------------------------------------------------------------------------------------------------------- Tickets

describe("Requests to the tech team", () => {
  function tix(routes: Routes, admin = false) {
    const f = routedFetch({ "GET /custom-rules/tickets": () => json({ tickets: [] }), ...routes });
    vi.stubGlobal("fetch", f.fn);
    wrap(<CustomTickets admin={admin} />);
    return { f, user: userEvent.setup() };
  }
  const SEND = "POST /custom-rules/tickets";

  it("shows loading, the empty state, and an error with Retry", async () => {
    let n = 0;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const { user } = tix({
      "GET /custom-rules/tickets": async () => {
        if (n++ === 0) {
          await gate;
          return errorBody("internal_error", "boom", 500);
        }
        return json({ tickets: [] });
      },
    });
    expect(await screen.findByText("Loading requests...")).toBeInTheDocument();
    expect(screen.queryByText("No requests yet")).toBeNull();
    release();
    await user.click(await screen.findByRole("button", { name: /Retry/ }));
    expect(await screen.findByText("No requests yet")).toBeInTheDocument();
  });

  it("lists a ticket with its id, status, origin, what could not be built and the original words", async () => {
    tix({
      "GET /custom-rules/tickets": () =>
        json({ tickets: [ticket({ doc_types: ["financial_statement", "bank_statement"], desired_severity: "medium", missing: [{ phrase: "from different places", reason: "field_not_extracted" }, { phrase: "trend", reason: "needs_trend" }] })] }),
    });
    expect(await screen.findByText("crt_9c0d1e2f3a4b")).toBeInTheDocument();
    expect(screen.getByText("Submitted to the tech team")).toBeInTheDocument();
    expect(screen.getByText("Could not be built as a rule")).toBeInTheDocument();
    expect(screen.getByText("Wanted: Medium concern")).toBeInTheDocument();
    const missing = screen.getByRole("list", { name: "What could not be built" });
    expect(within(missing).getAllByRole("listitem")).toHaveLength(2);
    expect(within(missing).getByText(/the system does not read this from the documents/)).toBeInTheDocument();
    expect(within(missing).getByText(/needs a trend over more than this year and last year/)).toBeInTheDocument();
    expect(screen.getByText(/about financial statement, bank statement/)).toBeInTheDocument();
    expect(screen.getByText("Your original words")).toBeInTheDocument();
  });

  it("does not break on a status, origin or reason it has never heard of, and shows no severity badge when none was wanted", async () => {
    tix({
      "GET /custom-rules/tickets": () =>
        json({ tickets: [ticket({ status: "on_hold" as never, origin: "mystery" as never, desired_severity: null, missing: [{ phrase: "x", reason: "brand_new_reason" as never }] })] }),
    });
    expect(await screen.findByText("on_hold")).toBeInTheDocument();
    expect(screen.getByText("mystery")).toBeInTheDocument();
    expect(screen.getByText(/brand_new_reason/)).toBeInTheDocument();
    expect(screen.queryByText(/^Wanted:/)).toBeNull();
  });

  it("shows ticket text, including the original words, as text", async () => {
    tix({ "GET /custom-rules/tickets": () => json({ tickets: [ticket({ intent: `intent ${IMG}`, original_words: `words\n${SCRIPT}`, missing: [{ phrase: `phrase ${IMG}`, reason: "needs_external" }] })] }) });
    expect(await screen.findByText(/intent <img/)).toBeInTheDocument();
    expect(screen.getByText(/phrase <img/)).toBeInTheDocument();
    expect(screen.getByText(/words/, { selector: "p" })).toHaveTextContent("<script>window.__pwned=2</script>");
    nothingInjected();
  });

  it("names the account on a ticket only for an administrator", async () => {
    const routes = { "GET /custom-rules/tickets": () => json({ tickets: [ticket({ account: "carol" })] }) };
    tix(routes, false);
    await screen.findByText("crt_9c0d1e2f3a4b");
    expect(screen.queryByText(/carol/)).toBeNull();
    cleanup();
    vi.unstubAllGlobals();
    tix(routes, true);
    expect(await screen.findByText(/carol,/)).toBeInTheDocument();
  });

  describe("the idea form (no model is involved)", () => {
    it("needs at least 8 characters of real text, sends the text trimmed with no severity unless chosen, and never calls the drafts route", async () => {
      const { f, user } = tix({ [SEND]: () => json({ ticket: ticket({ origin: "officer" }), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: false }, 201) });
      const box = await screen.findByLabelText("What should be checked?");
      const send = screen.getByRole("button", { name: "Send to the tech team" });
      expect(send).toBeDisabled();
      await user.type(box, "  short  ");
      expect(send).toBeDisabled();
      await user.clear(box);
      await user.type(box, "  flag if the director has other companies  ");
      await user.click(send);
      expect(await screen.findByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).")).toBeInTheDocument();
      expect(f.calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/custom-rules/tickets", body: { text: "flag if the director has other companies" } }]);
      expect(box).toHaveValue("");
    });

    it("offers info, low and medium only, and sends the chosen severity", async () => {
      const { f, user } = tix({ [SEND]: () => json({ ticket: ticket(), message: "ok", existing: false }, 201) });
      const select = await screen.findByLabelText("How serious should a flag be?");
      const options = within(select).getAllByRole("option").map((o) => o.textContent);
      expect(options).toHaveLength(4);
      expect(options.join("|").toLowerCase()).not.toContain("high");
      await user.selectOptions(select, "medium");
      await user.type(screen.getByLabelText("What should be checked?"), "flag if the director has other companies");
      await user.click(screen.getByRole("button", { name: "Send to the tech team" }));
      await screen.findByText("Sent");
      expect(f.calls.find((c) => c.method === "POST")?.body).toEqual({ text: "flag if the director has other companies", severity: "medium" });
    });

    it("refreshes the list after sending, so the new request appears", async () => {
      let sent = false;
      const { user } = tix({
        "GET /custom-rules/tickets": () => json({ tickets: sent ? [ticket({ id: "crt_new000000001", origin: "officer" })] : [] }),
        [SEND]: () => ((sent = true), json({ ticket: ticket({ id: "crt_new000000001" }), message: "ok", existing: false }, 201)),
      });
      await screen.findByText("No requests yet");
      await user.type(screen.getByLabelText("What should be checked?"), "flag if the director has other companies");
      await user.click(screen.getByRole("button", { name: "Send to the tech team" }));
      expect(await screen.findByText("crt_new000000001")).toBeInTheDocument();
      expect(screen.getByText("Sent by you")).toBeInTheDocument();
    });

    it("a repeat of an earlier request (existing) still shows the server's message", async () => {
      const { user } = tix({ [SEND]: () => json({ ticket: ticket(), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: true }, 200) });
      await user.type(await screen.findByLabelText("What should be checked?"), "flag if the director has other companies");
      await user.click(screen.getByRole("button", { name: "Send to the tech team" }));
      expect(await screen.findByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).")).toBeInTheDocument();
    });

    it("explains a failure, keeps the words typed and does not claim it was sent", async () => {
      const { user } = tix({ [SEND]: () => errorBody("rate_limited", "slow", 429, { retry_after_s: 30 }) });
      const box = await screen.findByLabelText("What should be checked?");
      await user.type(box, "flag if the director has other companies");
      await user.click(screen.getByRole("button", { name: "Send to the tech team" }));
      expect(await screen.findByText("Slow down")).toBeInTheDocument();
      expect(box).toHaveValue("flag if the director has other companies");
      expect(screen.queryByText("Sent")).toBeNull();
    });

    it("caps the text at 1000 characters and has a labelled form", async () => {
      tix({});
      expect(await screen.findByLabelText("What should be checked?")).toHaveAttribute("maxlength", "1000");
      expect(screen.getByRole("form", { name: "Send an idea to the tech team" })).toBeInTheDocument();
    });
  });
});

// -------------------------------------------------------------------------------------- catalogue page: Custom rules

describe("the Custom rules section of the rule catalogue", () => {
  function section(me: Record<string, unknown> | "fail", routes: Routes = {}) {
    const f = routedFetch({
      "GET /me": () => (me === "fail" ? errorBody("unauthorized", "no", 401) : json(me)),
      "GET /custom-rules": () => json({ mine: [], adopted: [] }),
      ...routes,
    });
    vi.stubGlobal("fetch", f.fn);
    const view = renderWithSWR(<CatalogueCustomSection />);
    return { f, view };
  }

  it("is absent, and asks the server nothing about custom rules, when the feature is off, unknown or /me fails", async () => {
    for (const me of [{ reviewer: "alice", admin: false, custom_rules: false }, { reviewer: "alice", admin: false }, "fail" as const]) {
      const { f, view } = section(me);
      await waitFor(() => expect(f.count("GET /me")).toBeGreaterThan(0));
      await new Promise((r) => setTimeout(r, 30));
      expect(screen.queryByText("Custom rules")).toBeNull();
      expect(f.calls.some((c) => c.path.startsWith("/custom-rules"))).toBe(false);
      view.unmount();
    }
  });

  it("lists the viewer's own active rules and adopted public rules with a Custom badge each, and not a deleted rule", async () => {
    section(
      { reviewer: "alice", admin: false, custom_rules: true },
      {
        "GET /custom-rules": () =>
          json({
            mine: [
              ruleView(),
              ruleView({ rule_id: "cus_bbbbbbbbbb", name: "Off rule", enabled: false, visibility: "public", published_version: 1 }),
              ruleView({ rule_id: "cus_cccccccccc", name: "Deleted rule", status: "retired" }),
            ],
            adopted: [adopted()],
          }),
      },
    );
    const list = await screen.findByRole("list", { name: "Your custom rules" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    for (const li of items) expect(within(li).getByText("Custom")).toBeInTheDocument();
    expect(within(list).queryByText("Deleted rule")).toBeNull();
    expect(within(list).getByText("Switched off")).toBeInTheDocument();
    expect(within(list).getByText("Public")).toBeInTheDocument();
    expect(within(list).getByText("Adopted from bob")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open custom rules" })).toHaveAttribute("href", "/rules/custom");
    expect(screen.getByText("Written by officers, not reviewed by the development team")).toBeInTheDocument();
  });

  it("shows names as text", async () => {
    section({ reviewer: "alice", admin: false, custom_rules: true }, { "GET /custom-rules": () => json({ mine: [ruleView({ name: `n ${IMG}`, readback: `r ${SCRIPT}` })], adopted: [adopted({ author: "<b>x</b>" })] }) });
    expect(await screen.findByText(/n <img/)).toBeInTheDocument();
    nothingInjected();
    expect(document.querySelector("b")).toBeNull();
  });

  it("says there are none, with a link to write one, once the answer has come back empty", async () => {
    section({ reviewer: "alice", admin: false, custom_rules: true });
    expect(await screen.findByText(/You have no custom rules/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Custom rules" })).toHaveAttribute("href", "/rules/custom");
  });

  it("does not claim 'You have no custom rules' while the list is still loading", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    section({ reviewer: "alice", admin: false, custom_rules: true }, { "GET /custom-rules": async () => (await gate, json({ mine: [ruleView()], adopted: [] })) });
    await screen.findByRole("link", { name: "Open custom rules" });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText(/You have no custom rules/)).toBeNull();
    release();
    expect(await screen.findByText("Heavy borrowing")).toBeInTheDocument();
  });

  it("does not claim 'You have no custom rules' when the list could not be loaded", async () => {
    section({ reviewer: "alice", admin: false, custom_rules: true }, { "GET /custom-rules": () => errorBody("internal_error", "boom", 500) });
    await screen.findByRole("link", { name: "Open custom rules" });
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByText(/You have no custom rules/)).toBeNull();
  });
});

// ------------------------------------------------------------------------------------------------ the page itself

describe("the Custom rules page", () => {
  const me = (over: Record<string, unknown> = {}) => () => json({ reviewer: "alice", admin: false, custom_rules: true, ...over });
  function page(routes: Routes = {}) {
    const f = routedFetch({
      "GET /me": me(),
      "GET /custom-rules": () => json({ mine: [], adopted: [] }),
      "GET /custom-rules/shared": () => json({ rules: [] }),
      "GET /custom-rules/tickets": () => json({ tickets: [] }),
      ...routes,
    });
    vi.stubGlobal("fetch", f.fn);
    renderWithSWR(<CustomRulesPage />);
    return { f, user: userEvent.setup() };
  }

  it("does not say 'not available' while it is still finding out who is signed in", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    page({ "GET /me": async () => (await gate, me()()) });
    await screen.findByRole("status");
    expect(screen.queryByText("Custom rules are not available")).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
    release();
    expect(await screen.findByRole("tablist", { name: "Custom rules" })).toBeInTheDocument();
  });

  it("says not available, and asks for nothing else, when /me cannot be read or says nothing about custom rules", async () => {
    const a = page({ "GET /me": () => errorBody("internal_error", "boom", 500) });
    expect(await screen.findByText("Custom rules are not available")).toBeInTheDocument();
    expect(a.f.calls.every((c) => c.path === "/me")).toBe(true);
    cleanup();
    vi.unstubAllGlobals();
    const b = page({ "GET /me": () => json({ reviewer: "alice", admin: false }) });
    expect(await screen.findByText("Custom rules are not available")).toBeInTheDocument();
    expect(b.f.calls.every((c) => c.path === "/me")).toBe(true);
  });

  it("an administrator who is not signed in with an account still does not see the feature", async () => {
    page({ "GET /me": me({ reviewer: null, admin: true, custom_rules: false }) });
    expect(await screen.findByText("Custom rules are not available")).toBeInTheDocument();
  });

  it("the tabs follow the arrow keys, Home and End, and only the selected tab is in the tab order", async () => {
    const { user } = page();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1", "-1"]);
    tabs[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "My rules" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "My rules" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Requests to the tech team" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "New rule" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Requests to the tech team" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "New rule" })).toHaveAttribute("aria-selected", "true");
  });

  it("the selected panel is the only one a screen reader is given, and it is named by its tab", async () => {
    const { user } = page();
    await screen.findAllByRole("tab");
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("New rule");
    await user.click(screen.getByRole("tab", { name: "Public rules" }));
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Public rules");
  });

  it("never calls the model route (a paid call) just by opening the page or its tabs", async () => {
    const { f, user } = page();
    await screen.findAllByRole("tab");
    for (const name of ["My rules", "Public rules", "Requests to the tech team", "New rule"]) await user.click(screen.getByRole("tab", { name }));
    await new Promise((r) => setTimeout(r, 30));
    expect(f.calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("an administrator sees the administrator's take-back on the Public tab, an ordinary officer does not", async () => {
    const routes = { "GET /custom-rules/shared": () => json({ rules: [shared()] }) };
    const admin = page({ ...routes, "GET /me": me({ admin: true }) });
    await admin.user.click(await screen.findByRole("tab", { name: "Public rules" }));
    expect(await screen.findByRole("button", { name: /Take back \(administrator\)/ })).toBeInTheDocument();
    cleanup();
    vi.unstubAllGlobals();
    const plain = page(routes);
    await plain.user.click(await screen.findByRole("tab", { name: "Public rules" }));
    await screen.findByRole("heading", { name: "Expired passport" });
    expect(screen.queryByRole("button", { name: /administrator/ })).toBeNull();
  });

  it("a ticket sent from the assistant shows up under Requests without a reload", async () => {
    let sent = false;
    const { user } = page({
      "POST /custom-rules/drafts": () =>
        json({
          draft_id: "crd_5b2e9f0a1c3d", turn: 1, turns_left: 5, status: "unsupported", understood: "x", spec: null, spec_hash: null, readback: null, candidates: [], questions: [], assumptions: [],
          phrase_map: [], unsupported_parts: [{ phrase: "social media", reason: "doc_not_extracted" }], closest_templates: [], will_check: [], will_not_check: [], refusal: null, duplicates: [], ideas: [],
          ticket: null, notes: [], dry_run: null, save_allowed: false, needs_narrower_ack: false, edit_of: null, model_calls: 1,
        }),
      "POST /custom-rules/tickets": () => ((sent = true), json({ ticket: ticket(), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: false }, 201)),
      "GET /custom-rules/tickets": () => json({ tickets: sent ? [ticket()] : [] }),
    });
    await user.type(await screen.findByLabelText(/Describe the check in plain English/), "flag if the company posts on social media");
    await user.click(screen.getByRole("button", { name: /Ask the assistant/ }));
    await user.click(await screen.findByRole("button", { name: "Send the missing part to the tech team" }));
    await screen.findByText("This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).");
    await user.click(screen.getByRole("tab", { name: "Requests to the tech team" }));
    expect(await screen.findByText("crt_9c0d1e2f3a4b")).toBeInTheDocument();
  });
});
