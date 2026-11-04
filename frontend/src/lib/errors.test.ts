import { describe, expect, it } from "vitest";
import { ApiErrorException, explainError, networkError } from "./errors";

const ex = (code: string, message = "server message", details?: Record<string, unknown>) => explainError({ code, message, details });

describe("explainError", () => {
  const table: [string, string][] = [
    ["network_error", "Cannot reach the API"],
    ["user_budget_used", "Your budget for today is used up"],
    ["bad_request", "Check the form"],
    ["validation_error", "Check the form"],
    ["not_found", "Not found"],
    ["case_processing", "Analysis still running"],
    ["case_not_open", "Case is not open"],
    ["high_flags_open", "High flags still open"],
    ["second_approver_must_differ", "A different reviewer is needed"],
    ["entry_not_pending", "Nothing to approve"],
    ["flag_decision_pending", "Decision pending"],
    ["stale_version", "Case changed"],
    ["file_too_large", "File too large"],
    ["unsupported_media_type", "Unsupported file"],
    ["too_many_files", "Wrong number of files"],
    ["no_files", "Wrong number of files"],
    ["reason_required", "Reason required"],
    ["reviewer_required", "Reviewer name required"],
    ["verdict_not_allowed", "Verdict not allowed here"],
    ["internal_error", "Something went wrong"],
    ["alert_not_open", "Already handled"],
    ["account_required", "Sign in with your own account"],
    ["feature_off", "Custom rules are switched off"],
    ["bad_answer", "That answer cannot be used"],
    ["draft_closed", "This request is finished"],
    ["draft_turn_limit", "Too many turns on one request"],
    ["draft_busy", "Another reply is being processed"],
    ["no_spec", "There is no rule to test yet"],
    ["draft_not_ready", "This rule cannot be saved yet"],
    ["narrower_not_accepted", "Tick the box first"],
    ["dry_run_required", "Test the rule on stored cases first"],
    ["warnings_not_confirmed", "Confirm the test warnings"],
    ["duplicate_builtin", "A built-in check already does this"],
    ["duplicate_custom", "A custom rule already does this"],
    ["no_change", "Nothing changed"],
    ["name_required", "Name required"],
    ["rule_limit", "Too many active rules"],
    ["rule_retired", "This rule was deleted"],
    ["already_public", "Already public"],
    ["withdrawn_by_admin", "Taken back by an administrator"],
    ["not_public", "Not public"],
    ["own_rule", "This is your own rule"],
    ["already_adopted", "Already adopted"],
    ["not_adopted", "Not adopted"],
    ["draft_refused", "A refused request is not sent on"],
    ["site_budget_used", "The model budget for the whole site is used up for today"],
    ["authoring_unavailable", "The rule assistant is not available"],
  ];
  it.each(table)("%s -> %s", (code, title) => {
    expect(ex(code).title).toBe(title);
  });

  it("returns an unknown code's message verbatim", () => {
    expect(ex("brand_new_code", "Quota exceeded for tenant").title).toBe("Quota exceeded for tenant");
  });

  it("uses details for field messages, kinds, versions and allowed verdicts", () => {
    expect(ex("validation_error", "x", { errors: [{ field: "reason", message: "too short" }] }).detail).toBe("reason: too short");
    expect(ex("not_found", "x", { kind: "flag" }).detail).toContain("flag");
    expect(ex("stale_version", "x", { current_version: 3 }).detail).toBe("Reload to see version 3.");
    expect(ex("verdict_not_allowed", "x", { allowed: ["approve", "refer_edd"] }).detail).toContain("approve, refer_edd");
  });

  it("tells an officer what to do about a built-in duplicate, with the rule and what it checks", () => {
    const d = ex("duplicate_builtin", "x", { rule: "rcpt_merchant", what: "A receipt must name its merchant.", action: "Change its severity in the check catalogue (rcpt_merchant)." }).detail;
    expect(d).toContain("A receipt must name its merchant.");
    expect(d).toContain("rcpt_merchant");
    expect(ex("duplicate_builtin", "x").detail).toMatch(/check catalogue/);
  });

  it("names the existing custom rule on a duplicate", () => {
    expect(ex("duplicate_custom", "x", { rule_id: "cus_0123456789", name: "Big invoices" }).detail).toContain('"Big invoices"');
    expect(ex("duplicate_custom", "x").detail).not.toContain('""');
  });

  it("offers the tech team when the assistant is out of reach, and marks only a busy draft as worth retrying", () => {
    expect(ex("authoring_unavailable").detail).toMatch(/tech team/);
    expect(ex("site_budget_used").detail).toMatch(/tech team/);
    expect(ex("draft_busy").retryable).toBe(true);
    expect(ex("authoring_unavailable").retryable).toBe(true);
    expect(ex("duplicate_builtin").retryable).toBe(false);
  });

  it("marks only transient errors retryable", () => {
    expect(ex("network_error").retryable).toBe(true);
    expect(ex("internal_error").retryable).toBe(true);
    expect(ex("reason_required").retryable).toBe(false);
  });
});

describe("ApiErrorException", () => {
  it("carries the error body, status and request id", () => {
    const e = new ApiErrorException({ code: "internal_error", message: "boom" }, 500, "req-1");
    expect(e).toBeInstanceOf(Error);
    expect(e.error.code).toBe("internal_error");
    expect(e.status).toBe(500);
    expect(e.requestId).toBe("req-1");
  });

  it("networkError has the network_error code", () => {
    expect(networkError(new TypeError("fetch failed")).error.code).toBe("network_error");
  });
});
