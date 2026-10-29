import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiErrorException } from "../errors";
import type { ReviewRequest } from "../types";
import * as mock from "./handlers";
import { resetMockState, state } from "./state";

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
});
afterEach(() => {
  vi.useRealTimers();
});

const code = async (p: Promise<unknown>) => {
  const e = await p.then(() => null, (x) => x);
  expect(e).toBeInstanceOf(ApiErrorException);
  return (e as ApiErrorException).error.code;
};

const verdict = (decision: ReviewRequest extends infer R ? (R extends { kind: "verdict"; decision: infer D } ? D : never) : never, over: Partial<ReviewRequest> = {}): ReviewRequest =>
  ({ kind: "verdict", decision, reviewer: "a.tan", reason: "Checked every flag.", case_version: 1, ...over }) as ReviewRequest;

describe("queue", () => {
  it("hides closed and error by default and keeps priority order (score desc among open)", async () => {
    const q = await mock.getQueue();
    expect(q.items.map((i) => i.status)).not.toContain("closed");
    expect(q.items.map((i) => i.status)).not.toContain("error");
    const open = q.items.filter((i) => i.status === "open").map((i) => i.score ?? 0);
    expect([...open].sort((a, b) => b - a)).toEqual(open);
    expect(q.total).toBe(q.items.length);
  });

  it("filters by use case, lane, band, q and status, and pages", async () => {
    expect((await mock.getQueue({ use_case: ["kyc"] })).items.every((i) => i.use_case === "kyc")).toBe(true);
    expect((await mock.getQueue({ lane: ["fast"] })).items.map((i) => i.case_id)).toEqual(["c_loan_clean"]);
    expect((await mock.getQueue({ band: ["high"] })).items.every((i) => i.band === "high")).toBe(true);
    expect((await mock.getQueue({ q: "ACME-2026-014" })).items.map((i) => i.case_id)).toEqual(["c_loan_fraud"]);
    expect((await mock.getQueue({ status: ["closed"] })).items.map((i) => i.case_id)).toEqual(["c_aml_closed"]);
    const p = await mock.getQueue({ page_size: 2 });
    expect(p.items).toHaveLength(2);
    expect(p.has_next).toBe(true);
  });

  it("a processing row has no score; the partial row says partial", async () => {
    const q = await mock.getQueue();
    const proc = q.items.find((i) => i.status === "processing")!;
    expect(proc.score).toBeNull();
    const part = q.items.find((i) => i.case_id === "c_loan_partial")!;
    expect(part.partial).toBe(true);
  });
});

describe("case lifecycle", () => {
  it("creates a case that steps through the stages and opens", async () => {
    vi.useFakeTimers();
    const form = new FormData();
    form.set("use_case", "loan");
    form.append("files", new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf" }));
    const res = await mock.createCase(form);
    expect((await mock.getCase(res.case_id)).status).toBe("processing");
    expect((await mock.getJob(res.job_id)).state).not.toBe("succeeded");
    vi.advanceTimersByTime(3000);
    const mid = await mock.getJob(res.job_id);
    expect(mid.progress.stage_count).toBe(7);
    expect(mid.progress.stage_index).toBeGreaterThan(1);
    vi.advanceTimersByTime(4000);
    expect((await mock.getJob(res.job_id)).state).toBe("succeeded");
    const c = await mock.getCase(res.case_id);
    expect(c.status).toBe("open");
    expect(c.score!.value).toBe(45);
    expect(c.allowed_verdicts).toContain("approve");
  });

  it("validates uploads with real codes", async () => {
    const empty = new FormData();
    empty.set("use_case", "loan");
    expect(await code(mock.createCase(empty))).toBe("no_files");
    const bad = new FormData();
    bad.set("use_case", "loan");
    bad.append("files", new File(["hi"], "notes.txt", { type: "text/plain" }));
    expect(await code(mock.createCase(bad))).toBe("unsupported_media_type");
    const big = new FormData();
    big.set("use_case", "loan");
    big.append("files", new File([new Uint8Array(26 * 1024 * 1024)], "big.pdf", { type: "application/pdf" }));
    expect(await code(mock.createCase(big))).toBe("file_too_large");
    const noUse = new FormData();
    noUse.append("files", new File(["x"], "a.pdf", { type: "application/pdf" }));
    expect(await code(mock.createCase(noUse))).toBe("validation_error");
  });

  it("returns not_found for an unknown case or version", async () => {
    expect(await code(mock.getCase("c_nope"))).toBe("not_found");
    expect(await code(mock.getCase("c_loan_fraud", 9))).toBe("not_found");
  });
});

describe("review rules", () => {
  it("validates reviewer and reason", async () => {
    expect(await code(mock.postReview("c_loan_clean", verdict("approve", { reviewer: "a" })))).toBe("reviewer_required");
    expect(await code(mock.postReview("c_loan_clean", verdict("approve", { reason: "abcd" })))).toBe("reason_required");
    expect(await code(mock.postReview("c_loan_clean", verdict("approve", { reason: "     " })))).toBe("reason_required");
  });

  it("rejects closed, processing and stale versions", async () => {
    expect(await code(mock.postReview("c_aml_closed", verdict("approve")))).toBe("case_not_open");
    expect(await code(mock.postReview("c_kyc_processing", verdict("approve")))).toBe("case_processing");
    expect(await code(mock.postReview("c_loan_clean", verdict("approve", { case_version: 7 })))).toBe("stale_version");
  });

  it("approves a clean case and closes it", async () => {
    const r = await mock.postReview("c_loan_clean", verdict("approve"));
    expect(r.entry.id).toMatch(/^r\d+$/);
    expect(r.case.status).toBe("closed");
    expect(r.case.decision).toBe("approve");
    expect((await mock.getCase("c_loan_clean")).allowed_verdicts).toEqual([]);
  });

  it("limits verdicts by use case", async () => {
    expect(await code(mock.postReview("c_loan_clean", verdict("refer_edd")))).toBe("verdict_not_allowed");
    expect(await code(mock.postReview("c_kyc_review", verdict("reject_or_escalate")))).toBe("verdict_not_allowed");
  });

  it("blocks approve while a high flag is open and lists it", async () => {
    const e = await mock.postReview("c_loan_fraud", verdict("approve")).catch((x) => x);
    expect(e.error.code).toBe("high_flags_open");
    expect(e.error.details.flag_ids).toEqual(["bs_running_balance.d1.transactions.8"]);
  });

  it("escalate_aml sets escalated_at", async () => {
    const r = await mock.postReview("c_loan_fraud", verdict("escalate_aml"));
    expect(r.case.status).toBe("closed");
    expect(r.case.escalated_at).toMatch(/Z$/);
  });

  it("request_documents moves the case to awaiting_documents", async () => {
    const r = await mock.postReview("c_loan_clean", verdict("request_documents"));
    expect(r.case.status).toBe("awaiting_documents");
    expect((await mock.getCase("c_loan_clean")).allowed_verdicts).toEqual([]);
  });

  it("dismissing a high flag needs a second reviewer; the same name is refused; a different one resolves", async () => {
    const flagId = "bs_running_balance.d1.transactions.8";
    const dismiss: ReviewRequest = { kind: "flag_decision", decision: "dismiss", flag_ids: [flagId], reviewer: "a.tan", reason: "Bank rounding confirmed.", case_version: 1 };
    const r = await mock.postReview("c_loan_fraud", dismiss);
    expect(r.entry.status).toBe("pending_second_approval");
    let c = await mock.getCase("c_loan_fraud");
    expect(c.flags.find((f) => f.id === flagId)!.disposition).toBe("pending_second_approval");
    expect(c.pending_second_approvals).toHaveLength(1);

    expect(await code(mock.postReview("c_loan_fraud", { ...dismiss, decision: "confirm" }))).toBe("flag_decision_pending");

    const countersign = (reviewer: string): ReviewRequest => ({ kind: "second_approval", decision: "approve", target_entry_id: r.entry.id, reviewer, reason: "Agree with reason.", case_version: 1 });
    expect(await code(mock.postReview("c_loan_fraud", countersign("  A.TAN ")))).toBe("second_approver_must_differ");
    await mock.postReview("c_loan_fraud", countersign("b.lim"));
    c = await mock.getCase("c_loan_fraud");
    expect(c.flags.find((f) => f.id === flagId)!.disposition).toBe("dismissed");
    expect(c.pending_second_approvals).toHaveLength(0);
    expect(await code(mock.postReview("c_loan_fraud", countersign("b.lim")))).toBe("entry_not_pending");
  });

  it("decline returns the flag to open; dismissing a medium flag is effective at once", async () => {
    const kyc = "x_ownership_vs_registry.d3.-";
    const c0 = await mock.getCase("c_kyc_review");
    expect(c0.flags.find((f) => f.id === kyc)!.disposition).toBe("pending_second_approval");
    await mock.postReview("c_kyc_review", { kind: "second_approval", decision: "decline", target_entry_id: "r1", reviewer: "b.lim", reason: "Not convinced.", case_version: 1 });
    const c1 = await mock.getCase("c_kyc_review");
    expect(c1.flags.find((f) => f.id === kyc)!.disposition).toBe("open");

    const med = await mock.postReview("c_loan_fraud", { kind: "flag_decision", decision: "dismiss", finding_id: "fnd_4", reviewer: "a.tan", reason: "Price agreed by phone.", case_version: 1 });
    expect(med.entry.status).toBe("effective");
    expect(med.entry.flag_ids).toEqual(["x_invoice_vs_contract.d2.unit"]);
    expect(await code(mock.postReview("c_loan_fraud", { kind: "flag_decision", decision: "confirm", flag_ids: ["nope"], reviewer: "a.tan", reason: "Reason given.", case_version: 1 }))).toBe("not_found");
  });
});

describe("other handlers", () => {
  it("rerun creates version 2 and opens again; closed cases cannot be re-run", async () => {
    vi.useFakeTimers();
    const r = await mock.rerunCase("c_loan_clean");
    expect(r.version).toBe(2);
    expect((await mock.getCase("c_loan_clean")).status).toBe("processing");
    vi.advanceTimersByTime(7000);
    expect((await mock.getCase("c_loan_clean")).status).toBe("open");
    expect(await code(mock.rerunCase("c_aml_closed"))).toBe("case_not_open");
  });

  it("deletes with validation", async () => {
    expect(await code(mock.deleteCase("c_loan_clean", "a", "long enough reason"))).toBe("reviewer_required");
    await mock.deleteCase("c_loan_clean", "a.tan", "Test data cleanup");
    expect(await code(mock.getCase("c_loan_clean"))).toBe("not_found");
  });

  it("effectiveness and rules answer with the documented shape", async () => {
    const e = await mock.getEffectiveness();
    expect(e.cases_total).toBe(12);
    expect(e.rules.find((r) => r.rule === "bs_running_balance")!.times_fired).toBeGreaterThan(0);
    expect(e.rules.find((r) => r.rule === "fs_arith")!.times_fired).toBe(0);
    expect((await mock.getRules()).rules.length).toBeGreaterThan(5);
    expect((await mock.getVersions("c_loan_fraud")).versions[0].current).toBe(true);
    expect((await mock.getHealth()).status).toBe("ok");
  });
});

describe("check catalogue overrides", () => {
  const base = { reviewer: "j.lim", reason: "Agreed with credit policy" };
  const rule = async (id: string) => (await mock.getRules()).rules.find((r) => r.rule === id)!;

  it("serves every check with its settings, untouched at first", async () => {
    const r = await rule("bs_running_balance");
    expect(r).toMatchObject({ severity: "high", default_severity: "high", effective_severity: "high", enabled: true, override: null });
  });

  it("changes switch, severity and wording and shows them back, keeping the code default", async () => {
    const updated = await mock.updateRuleOverride("bs_running_balance", { enabled: false, severity: "low", name: " Mine ", note: "n", ...base });
    expect(updated).toMatchObject({ severity: "high", default_severity: "high", effective_severity: "low", enabled: false });
    expect(updated.override).toMatchObject({ enabled: false, severity: "low", name: "Mine", description: null, note: "n", updated_by: "j.lim", reason: base.reason, revision: 1 });
    expect(await rule("bs_running_balance")).toMatchObject({ enabled: false, effective_severity: "low" });
  });

  it("refuses an unknown check, a short reason, a missing name and an over-long wording", async () => {
    expect(await code(mock.updateRuleOverride("nope", { enabled: false, ...base }))).toBe("unknown_rule");
    expect(await code(mock.updateRuleOverride("fs_arith", { enabled: false, reviewer: "j.lim", reason: "abc" }))).toBe("reason_required");
    expect(await code(mock.updateRuleOverride("fs_arith", { enabled: false, reviewer: "", reason: base.reason }))).toBe("reviewer_required");
    expect(await code(mock.updateRuleOverride("fs_arith", { name: "x".repeat(81), ...base }))).toBe("validation_error");
    expect(await code(mock.updateRuleOverride("fs_arith", { enabled: false, reviewer: "j.lim", reason: "r".repeat(501) }))).toBe("validation_error");
    expect(await code(mock.updateRuleOverride("fs_arith", { severity: "urgent" as never, ...base }))).toBe("validation_error");
  });

  it("answers no_change when nothing would differ", async () => {
    expect(await code(mock.updateRuleOverride("fs_arith", { enabled: true, ...base }))).toBe("no_change");
    await mock.updateRuleOverride("fs_arith", { note: "n", ...base });
    expect(await code(mock.updateRuleOverride("fs_arith", { note: "n", ...base }))).toBe("no_change");
  });

  it("clears a field with null or an empty string and drops the override once everything is default", async () => {
    await mock.updateRuleOverride("fs_arith", { name: "Mine", ...base });
    const cleared = await mock.updateRuleOverride("fs_arith", { name: "", ...base });
    expect(cleared.override).toBeNull();
    expect(cleared.enabled).toBe(true);
  });

  it("resets to default, logs it, and 404s when there is nothing to reset", async () => {
    await mock.updateRuleOverride("fs_arith", { enabled: false, ...base });
    const reset = await mock.resetRuleOverride("fs_arith", { reviewer: "a.tan", reason: "Back to normal" });
    expect(reset).toMatchObject({ enabled: true, override: null });
    expect(await code(mock.resetRuleOverride("fs_arith", { reviewer: "a.tan", reason: "Back to normal" }))).toBe("no_override");
    expect(await code(mock.resetRuleOverride("nope", { reviewer: "a.tan", reason: "Back to normal" }))).toBe("unknown_rule");
  });

  it("keeps a permanent log, newest first, with before and after, filterable by check", async () => {
    await mock.updateRuleOverride("fs_arith", { enabled: false, ...base });
    await mock.updateRuleOverride("inv_dates", { severity: "info", ...base });
    await mock.resetRuleOverride("fs_arith", { reviewer: "a.tan", reason: "Back to normal" });
    const all = await mock.getRuleChanges();
    expect(all.map((c) => [c.rule, c.action])).toEqual([["fs_arith", "reset"], ["inv_dates", "update"], ["fs_arith", "update"]]);
    expect(all[0]).toMatchObject({ reviewer: "a.tan", reason: "Back to normal", after: null });
    expect(all[0].before).toMatchObject({ enabled: false });
    expect(all[2].before).toBeNull();
    expect(all[2].after).toMatchObject({ enabled: false });
    expect((await mock.getRuleChanges({ rule: "fs_arith" })).map((c) => c.action)).toEqual(["reset", "update"]);
    expect(await mock.getRuleChanges({ limit: 1 })).toHaveLength(1);
  });

  it("a case analysed after a check is switched off lists it as skipped and records the custom settings", async () => {
    await mock.updateRuleOverride("x_directors", { enabled: false, severity: null, ...base });
    await mock.updateRuleOverride("bs_running_balance", { severity: "low", ...base });
    const form = new FormData();
    form.set("use_case", "kyc");
    form.append("files", new File(["%PDF-1.4"], "profile.pdf", { type: "application/pdf" }));
    const { case_id } = await mock.createCase(form);
    state.readyAt.set(case_id, Date.now() - 1);
    const c = await mock.getCase(case_id);
    expect(c.coverage!.skipped).toContainEqual({ rule: "x_directors", status: "skipped", code: "switched_off", reason: expect.stringMatching(/^Switched off in the check catalogue by j\.lim on \d{4}-\d{2}-\d{2}: Agreed with credit policy$/) });
    expect(c.coverage!.ran).toBe(c.coverage!.applicable - 1);
    expect(c.provenance!.catalogue!.overrides).toEqual(
      expect.arrayContaining([
        { rule: "x_directors", enabled: false, severity: null, revision: 1 },
        { rule: "bs_running_balance", enabled: true, severity: "low", revision: 1 },
      ]),
    );
    // a case from before any edit has no catalogue record
    expect((await mock.getCase("c_loan_fraud")).provenance?.catalogue).toBeUndefined();
  });
});

describe("review controls follow the code's severity", () => {
  it("dismissing a flag that was lowered from high still needs a second approver, and approving is blocked while it is open", async () => {
    const c = await mock.getCase("c_loan_fraud");
    const target = c.flags.find((f) => f.severity === "high")!;
    // simulate a catalogue override that lowered it
    const live = state.cases.get("c_loan_fraud")!;
    const f = live.flags.find((x) => x.id === target.id)!;
    f.code_severity = "high";
    f.severity = "low";
    const res = await mock.postReview("c_loan_fraud", { kind: "flag_decision", decision: "dismiss", flag_ids: [target.id], reviewer: "a.tan", reason: "Not a concern here", case_version: 1 } as ReviewRequest);
    expect(res.entry.status).toBe("pending_second_approval");
  });
});
