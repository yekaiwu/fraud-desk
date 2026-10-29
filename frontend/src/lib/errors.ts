import { API_BASE_URL } from "./constants";
import type { ApiError } from "./types";

export type ApiErrorBody = ApiError["error"];

/** Error thrown by every api.ts function. Carries the API's single error body. */
export class ApiErrorException extends Error {
  readonly error: ApiErrorBody;
  readonly status: number;
  /** `X-Request-Id` response header, when the server sent one (shown for 5xx). */
  readonly requestId: string | null;

  constructor(error: ApiErrorBody, status = 0, requestId: string | null = null) {
    super(error.message);
    this.name = "ApiErrorException";
    this.error = error;
    this.status = status;
    this.requestId = requestId;
  }
}

export function networkError(cause?: unknown): ApiErrorException {
  const detail = cause instanceof Error ? cause.message : undefined;
  return new ApiErrorException(
    {
      code: "network_error",
      message: `Cannot reach the API at ${API_BASE_URL}.`,
      details: detail ? { cause: detail } : undefined,
    },
    0,
  );
}

export function isApiError(e: unknown): e is ApiErrorException {
  return e instanceof ApiErrorException;
}

export type ExplainedError = { title: string; detail: string; retryable: boolean };

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function fieldMessages(details: ApiErrorBody["details"]): string {
  const errs = details?.errors;
  if (Array.isArray(errs)) {
    const parts = errs
      .map((e) => {
        if (typeof e === "string") return e;
        if (e && typeof e === "object") {
          const o = e as Record<string, unknown>;
          const field = Array.isArray(o.loc) ? o.loc.join(".") : typeof o.field === "string" ? o.field : "";
          const msg = typeof o.msg === "string" ? o.msg : typeof o.message === "string" ? o.message : "";
          return [field, msg].filter(Boolean).join(": ");
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return "";
}

/** Table in frontend.md section 9.4. Unknown codes: the server message verbatim. */
export function explainError(e: ApiErrorBody): ExplainedError {
  const d = e.details;
  switch (e.code) {
    case "network_error":
      return { title: "Cannot reach the API", detail: `Is the backend running at ${API_BASE_URL}?`, retryable: true };
    case "bad_request":
    case "validation_error":
      return { title: "Check the form", detail: fieldMessages(d) || e.message, retryable: false };
    case "not_found": {
      const kind = typeof d?.kind === "string" ? d.kind : "item";
      return { title: "Not found", detail: `No such ${kind}.`, retryable: false };
    }
    case "case_processing":
      return { title: "Analysis still running", detail: "Try again when it finishes.", retryable: true };
    case "case_not_open":
      return { title: "Case is not open", detail: "Decisions are only possible while a case is open.", retryable: false };
    case "high_flags_open":
      return {
        title: "High flags still open",
        detail: "Confirm or dismiss every high flag (a pending dismissal does not count).",
        retryable: false,
      };
    case "second_approver_must_differ":
      return { title: "A different reviewer is needed", detail: "A different reviewer must countersign.", retryable: false };
    case "entry_not_pending":
      return { title: "Nothing to approve", detail: "This entry was already decided.", retryable: false };
    case "flag_decision_pending":
      return {
        title: "Decision pending",
        detail: "A dismissal of this flag is waiting for a second reviewer.",
        retryable: false,
      };
    case "stale_version": {
      const v = typeof d?.current_version === "number" ? d.current_version : typeof d?.version === "number" ? d.version : null;
      return {
        title: "Case changed",
        detail: v === null ? "Reload to see the latest version." : `Reload to see version ${v}.`,
        retryable: false,
      };
    }
    case "file_too_large":
      return { title: "File too large", detail: "Each file may be at most 25 MB.", retryable: false };
    case "unsupported_media_type":
      return { title: "Unsupported file", detail: "Only PDF, JPEG and PNG.", retryable: false };
    case "too_many_files":
    case "no_files":
      return { title: "Wrong number of files", detail: "Upload 1 to 40 files.", retryable: false };
    case "reason_required":
      return { title: "Reason required", detail: "Enter at least 5 characters.", retryable: false };
    case "reviewer_required":
      return { title: "Reviewer name required", detail: "2 to 64 characters.", retryable: false };
    case "verdict_not_allowed": {
      const allowed = strList(d?.allowed);
      return {
        title: "Verdict not allowed here",
        detail: allowed.length ? `Allowed: ${allowed.join(", ")}.` : e.message,
        retryable: false,
      };
    }
    case "unauthorized":
      return { title: "Signed out", detail: "Your session has ended. Sign in again.", retryable: false };
    case "rate_limited": {
      const s = typeof d?.retry_after_s === "number" ? d.retry_after_s : null;
      return { title: "Slow down", detail: s === null ? e.message : `Too many requests. Try again in ${s < 90 ? `${s} seconds` : `${Math.ceil(s / 60)} minutes`}.`, retryable: false };
    }
    case "busy":
      return { title: "Server is busy", detail: "Too many analyses are running. Try again in a minute.", retryable: true };
    case "internal_error":
      return { title: "Something went wrong", detail: e.message, retryable: true };
    default:
      return { title: e.message, detail: "", retryable: false };
  }
}
