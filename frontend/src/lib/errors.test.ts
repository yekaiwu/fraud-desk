import { describe, expect, it } from "vitest";
import { ApiErrorException, explainError, networkError } from "./errors";

const ex = (code: string, message = "server message", details?: Record<string, unknown>) => explainError({ code, message, details });

describe("explainError", () => {
  const table: [string, string][] = [
    ["network_error", "Cannot reach the API"],
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
