import { explainError, isApiError } from "@/lib/errors";

export type SaveError = { title: string; detail: string; field?: "reviewer" | "reason" | "name" | "description" | "note" | "severity"; reload?: boolean };

const FIELD_NAMES: Record<string, string> = { name: "Name", description: "Description", note: "Note", reason: "Reason", reviewer: "Reviewer name", severity: "Severity", enabled: "Switch" };

function problems(details: Record<string, unknown> | undefined): string[] {
  const errs = details?.errors;
  if (!Array.isArray(errs)) return [];
  return errs
    .map((e) => {
      if (typeof e === "string") return e;
      if (!e || typeof e !== "object") return "";
      const o = e as Record<string, unknown>;
      const key = typeof o.field === "string" ? o.field : Array.isArray(o.loc) ? String(o.loc[o.loc.length - 1]) : "";
      const msg = typeof o.message === "string" ? o.message : typeof o.msg === "string" ? o.msg : "";
      const label = FIELD_NAMES[key] ?? key;
      return [label, msg].filter(Boolean).join(": ");
    })
    .filter(Boolean);
}

/** One plain message for every way saving or resetting can fail. The draft is always kept; `reload` says the list is out of date. */
export function saveError(e: unknown, what: "save" | "reset" | "load" = "save"): SaveError {
  if (!isApiError(e)) return { title: `Could not ${what === "load" ? "load" : what}`, detail: "Something unexpected went wrong. Your changes are still here; try again." };
  const { code, message, details } = e.error;
  switch (code) {
    case "no_change":
      return { title: "Nothing to save", detail: "These settings are already in place, so there is nothing new to record. Someone else may have saved the same change a moment ago.", reload: true };
    case "unknown_rule":
      return { title: "This check is no longer in the catalogue", detail: "Close this panel and reload the catalogue. Nothing was saved.", reload: true };
    case "no_override":
      return { title: "Already at its defaults", detail: "This check has no custom settings left to reset. The list will refresh.", reload: true };
    case "rate_limited": {
      const s = typeof details?.retry_after_s === "number" ? details.retry_after_s : null;
      return { title: "Too many changes in a short time", detail: `Wait ${s === null ? "a moment" : s < 90 ? `${s} seconds` : `${Math.ceil(s / 60)} minutes`} and try again. Your changes are still here.` };
    }
    case "network_error":
      return {
        title: "Cannot reach the server",
        detail: what === "load" ? "Check your connection and try again." : `Nothing was ${what === "save" ? "saved" : "reset"}. Check your connection and try again; your changes are still here.`,
      };
    case "reason_required":
      return { title: "A reason is required", detail: "Write at least 5 characters about why you are changing this check.", field: "reason" };
    case "reviewer_required":
      return { title: "Your name is required", detail: "Enter your name, 2 to 64 characters.", field: "reviewer" };
    case "validation_error":
    case "bad_request": {
      const list = problems(details);
      const first = list[0]?.split(":")[0]?.toLowerCase();
      const field = (["name", "description", "note", "reason", "reviewer", "severity"] as const).find((f) => FIELD_NAMES[f].toLowerCase() === first);
      return { title: "Some settings are not valid", detail: list.length > 0 ? list.join(" ") : message, field };
    }
    case "unauthorized":
      return { title: "Signed out", detail: "Your session has ended. Sign in again, then repeat the change." };
    default: {
      const x = explainError(e.error);
      return { title: x.title, detail: x.detail || "Nothing was saved. Try again." };
    }
  }
}
