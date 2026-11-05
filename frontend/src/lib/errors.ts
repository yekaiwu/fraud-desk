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
      return {
        title: "File too large",
        detail: "This upload is too large. A file may be at most 25 MB and a case at most 40 files.",
        retryable: false,
      };
    case "unsupported_media_type":
      return { title: "Unsupported file", detail: "Only PDF, JPEG and PNG.", retryable: false };
    case "too_many_files":
    case "no_files":
      return { title: "Wrong number of files", detail: "Upload 1 to 40 files.", retryable: false };
    case "too_many_fields":
      return { title: "Too many form fields", detail: "The form has more fields than allowed. Reload the page and try again.", retryable: false };
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
    case "alert_not_open":
      return { title: "Already handled", detail: "This alert is no longer open. Reload to see its state.", retryable: false };
    case "account_required":
      return { title: "Sign in with your own account", detail: "A typed name is not accepted here.", retryable: false };
    case "forbidden":
      return { title: "Not available to this account", detail: e.message, retryable: false };
    case "user_budget_used":
      return { title: "Your budget for today is used up", detail: `${e.message} Open an existing case instead, or try again tomorrow.`, retryable: false };
    // ---- custom checks (api.md 9c)
    case "feature_off":
      return { title: "Custom checks are switched off", detail: "This deployment has not turned custom checks on. Ask the administrator.", retryable: false };
    case "bad_answer":
      return { title: "That answer cannot be used", detail: e.message, retryable: false };
    case "draft_closed":
      return { title: "This request is finished", detail: "It was already saved or closed. Start a new request.", retryable: false };
    case "draft_turn_limit":
      return { title: "Too many turns on one request", detail: "Start again with the whole idea in one message, or send it to the tech team.", retryable: false };
    case "draft_busy":
      return { title: "Another reply is being processed", detail: "Wait a moment and try again.", retryable: true };
    case "no_spec":
      return { title: "There is no rule to test yet", detail: "Answer the open questions first.", retryable: false };
    case "draft_not_ready":
      return { title: "This rule cannot be saved yet", detail: "It still has an open question, or the assistant could not turn it into a valid rule.", retryable: false };
    case "narrower_not_accepted":
      return { title: "Tick the box first", detail: "Saving a rule that checks less than you asked for needs your confirmation.", retryable: false };
    case "dry_run_required":
      return { title: "Test the rule on stored cases first", detail: "Run the test on stored cases for this exact rule, then save.", retryable: false };
    case "warnings_not_confirmed":
      return { title: "Confirm the test warnings", detail: "The test on stored cases gave a warning. Tick \"Save anyway\" to go on.", retryable: false };
    case "duplicate_builtin": {
      const rule = typeof d?.rule === "string" ? d.rule : null;
      const what = typeof d?.what === "string" ? d.what : "";
      const action = typeof d?.action === "string" ? d.action : "";
      return {
        title: "A built-in check already does this",
        detail: [what, action || (rule ? `Change its severity in the check catalogue (${rule}).` : "Change its severity in the check catalogue.")].filter(Boolean).join(" "),
        retryable: false,
      };
    }
    case "duplicate_custom": {
      const name = typeof d?.name === "string" && d.name ? ` "${d.name}"` : "";
      return { title: "A custom check already does this", detail: `The same check already exists${name}. Use that one, or change what this one checks.`, retryable: false };
    }
    case "no_change":
      return { title: "Nothing changed", detail: "The new version checks exactly the same thing as the current one.", retryable: false };
    case "name_required":
      return { title: "Name required", detail: "Give the rule a name of 3 to 80 characters.", retryable: false };
    case "rule_limit":
      return { title: "Too many active rules", detail: "Delete a rule you no longer use before adding another.", retryable: false };
    case "rule_retired":
      return { title: "This rule was deleted", detail: "A deleted rule cannot be changed, published or adopted.", retryable: false };
    case "already_public":
      return { title: "Already public", detail: "This version is already public.", retryable: false };
    case "withdrawn_by_admin":
      return { title: "Taken back by an administrator", detail: "An administrator took this rule back, and the same check cannot be made public again, even as a new rule. It still runs for you.", retryable: false };
    case "adoption_limit":
      return { title: "Too many public rules in use", detail: "You already use the most public rules allowed. Stop using one before adopting another.", retryable: false };
    case "not_public":
      return { title: "Not public", detail: "Only a public rule can be adopted or taken back.", retryable: false };
    case "own_rule":
      return { title: "This is your own rule", detail: "You cannot adopt your own rule; it already runs on your analyses when it is switched on.", retryable: false };
    case "already_adopted":
      return { title: "Already adopted", detail: "You already use this version.", retryable: false };
    case "not_adopted":
      return { title: "Not adopted", detail: "You have not adopted this rule.", retryable: false };
    case "draft_refused":
      return { title: "A refused request is not sent on", detail: "Requests that break the rules for custom checks are not passed to the tech team.", retryable: false };
    case "site_budget_used":
      return { title: "The model budget for the whole site is used up for today", detail: "The rule assistant is unavailable until tomorrow. You can still send the idea to the tech team.", retryable: false };
    case "authoring_unavailable":
      return { title: "The rule assistant is not available", detail: "You can still send the idea to the tech team.", retryable: true };
    case "busy":
      return { title: "Server is busy", detail: "Too many analyses are running. Try again in a minute.", retryable: true };
    case "internal_error":
      return { title: "Something went wrong", detail: e.message, retryable: true };
    default:
      return { title: e.message, detail: "", retryable: false };
  }
}
