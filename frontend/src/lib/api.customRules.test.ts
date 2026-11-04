import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { errorBody, json, draft, dryRun, ruleView, adopted, shared, ticket } from "@/test/customRulesFixtures";
import {
  adoptCustomRule,
  dryRunCustomDraft,
  getCustomCapabilities,
  getCustomDraft,
  getCustomRule,
  getCustomRuleChanges,
  listCustomRules,
  listCustomTickets,
  listSharedCustomRules,
  publishCustomRule,
  retireCustomRule,
  saveCustomRule,
  sendCustomTurn,
  setCustomRuleEnabled,
  startCustomDraft,
  submitCustomTicket,
  unadoptCustomRule,
  unpublishCustomRule,
} from "./api";
import { ApiErrorException } from "./errors";

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const call = (i = 0) => {
  const [url, init] = fetchMock.mock.calls[i];
  const u = new URL(String(url));
  return { path: u.pathname, query: u.searchParams, method: (init?.method as string | undefined) ?? "GET", body: init?.body ? JSON.parse(init.body as string) : undefined };
};

describe("custom rules API client", () => {
  it("starts a draft with only the fields that were given", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json(draft())));
    await startCustomDraft("flag big invoices");
    expect(call()).toMatchObject({ path: "/custom-rules/drafts", method: "POST", body: { text: "flag big invoices" } });
    await startCustomDraft("flag big invoices", "low", "cus_3f9a1c2b7d");
    expect(call(1).body).toEqual({ text: "flag big invoices", severity: "low", edit_of: "cus_3f9a1c2b7d" });
  });

  it("sends exactly one of text, answer or candidate on a turn", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json(draft())));
    await sendCustomTurn("crd_5b2e9f0a1c3d", { answer: { question_id: "q1", option_id: "x2" } });
    expect(call()).toMatchObject({ path: "/custom-rules/drafts/crd_5b2e9f0a1c3d/turns", method: "POST", body: { answer: { question_id: "q1", option_id: "x2" } } });
    await sendCustomTurn("crd_5b2e9f0a1c3d", { choose_candidate: 1 });
    expect(call(1).body).toEqual({ choose_candidate: 1 });
  });

  it("reads a draft and runs its dry run without a body", async () => {
    fetchMock.mockResolvedValueOnce(json(draft())).mockResolvedValueOnce(json(dryRun()));
    await getCustomDraft("crd_5b2e9f0a1c3d");
    expect(call()).toMatchObject({ path: "/custom-rules/drafts/crd_5b2e9f0a1c3d", method: "GET" });
    const r = await dryRunCustomDraft("crd_5b2e9f0a1c3d");
    expect(call(1)).toMatchObject({ path: "/custom-rules/drafts/crd_5b2e9f0a1c3d/dry-run", method: "POST", body: undefined });
    expect(r.fired).toBe(6);
  });

  it("saves with the confirmations as sent", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ rule: ruleView(), ticket: null }, 201)));
    const req = { draft_id: "crd_5b2e9f0a1c3d", name: "Heavy borrowing", reason: "Loan desk asked", accept_narrower: true, confirm_warnings: false, submit_ticket: true };
    const r = await saveCustomRule(req);
    expect(call()).toMatchObject({ path: "/custom-rules", method: "POST", body: req });
    expect(r.rule.rule_id).toBe("cus_3f9a1c2b7d");
  });

  it("lists my rules, the public ones, tickets and the capabilities", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ mine: [ruleView()], adopted: [adopted()] }))
      .mockResolvedValueOnce(json({ rules: [shared()] }))
      .mockResolvedValueOnce(json({ tickets: [ticket()] }))
      .mockResolvedValueOnce(json({ templates: [] }));
    expect((await listCustomRules()).adopted).toHaveLength(1);
    expect((await listSharedCustomRules()).rules[0].author).toBe("bob");
    expect((await listCustomTickets()).tickets[0].id).toBe("crt_9c0d1e2f3a4b");
    await getCustomCapabilities();
    expect([0, 1, 2, 3].map((i) => call(i).path)).toEqual(["/custom-rules", "/custom-rules/shared", "/custom-rules/tickets", "/custom-rules/capabilities"]);
  });

  it("makes every write a POST with the reason in the body", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ rule: ruleView() })));
    await setCustomRuleEnabled("cus_3f9a1c2b7d", false, "Too noisy");
    await publishCustomRule("cus_3f9a1c2b7d", "Share it");
    await unpublishCustomRule("cus_3f9a1c2b7d", "Take back");
    await retireCustomRule("cus_3f9a1c2b7d", "Not needed");
    expect([0, 1, 2, 3].map((i) => [call(i).path, call(i).method, call(i).body])).toEqual([
      ["/custom-rules/cus_3f9a1c2b7d/enabled", "POST", { enabled: false, reason: "Too noisy" }],
      ["/custom-rules/cus_3f9a1c2b7d/publish", "POST", { reason: "Share it" }],
      ["/custom-rules/cus_3f9a1c2b7d/unpublish", "POST", { reason: "Take back" }],
      ["/custom-rules/cus_3f9a1c2b7d/retire", "POST", { reason: "Not needed" }],
    ]);
  });

  it("adopts and stops using a public rule", async () => {
    fetchMock.mockResolvedValueOnce(json({ adoption: adopted() })).mockResolvedValueOnce(json({ rule_id: "cus_77aa00bb11", adopted: false }));
    await adoptCustomRule("cus_77aa00bb11");
    await unadoptCustomRule("cus_77aa00bb11");
    expect(call(0)).toMatchObject({ path: "/custom-rules/cus_77aa00bb11/adopt", method: "POST", body: {} });
    expect(call(1)).toMatchObject({ path: "/custom-rules/cus_77aa00bb11/unadopt", method: "POST" });
  });

  it("reads one rule and its change log, newest first as sent", async () => {
    fetchMock.mockResolvedValueOnce(json(ruleView())).mockResolvedValueOnce(json({ changes: [{ id: 2 }, { id: 1 }] }));
    await getCustomRule("cus_3f9a1c2b7d");
    const changes = await getCustomRuleChanges({ rule_id: "cus_3f9a1c2b7d", limit: 30 });
    expect(call(0).path).toBe("/custom-rules/cus_3f9a1c2b7d");
    expect(call(1).path).toBe("/custom-rules/changes");
    expect(call(1).query.get("rule_id")).toBe("cus_3f9a1c2b7d");
    expect(call(1).query.get("limit")).toBe("30");
    expect(changes.map((c) => c.id)).toEqual([2, 1]);
  });

  it("files a ticket from a draft or from words, without a model", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ ticket: ticket(), message: "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b).", existing: false }, 201)));
    const r = await submitCustomTicket({ text: "flag social media posts", severity: "low" });
    expect(call()).toMatchObject({ path: "/custom-rules/tickets", method: "POST", body: { text: "flag social media posts", severity: "low" } });
    expect(r.message).toContain("This will be submitted to the tech team");
  });

  it("turns an error body into an ApiErrorException with its code and details", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(errorBody("duplicate_builtin", "A built-in check already does this.", 409, { rule: "rcpt_merchant" })));
    const err = await saveCustomRule({ draft_id: "crd_5b2e9f0a1c3d" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiErrorException);
    expect((err as ApiErrorException).error.code).toBe("duplicate_builtin");
    expect((err as ApiErrorException).error.details).toEqual({ rule: "rcpt_merchant" });
    expect((err as ApiErrorException).status).toBe(409);
  });

  it("encodes an id it is given", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json(ruleView())));
    await getCustomRule("cus/../x");
    expect(call().path).toBe("/custom-rules/cus%2F..%2Fx");
  });
});
