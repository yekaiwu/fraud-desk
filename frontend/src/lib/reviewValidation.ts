/** Client validation mirrors the server (api.md section 6); the server stays authoritative. */
export const REASON_MIN = 5;
export const REASON_MAX = 1000;
export const REVIEWER_MIN = 2;
export const REVIEWER_MAX = 64;

export type ReviewErrors = { reviewer?: string; reason?: string };

export function validateReview(input: { reviewer: string; reason: string }): ReviewErrors {
  const errors: ReviewErrors = {};
  const r = input.reviewer.trim();
  if (r.length < REVIEWER_MIN || r.length > REVIEWER_MAX) errors.reviewer = `Reviewer name must be ${REVIEWER_MIN} to ${REVIEWER_MAX} characters.`;
  const reason = input.reason.trim();
  if (reason.length < REASON_MIN) errors.reason = `Enter a reason of at least ${REASON_MIN} characters.`;
  else if (reason.length > REASON_MAX) errors.reason = `The reason can be at most ${REASON_MAX} characters.`;
  return errors;
}

/** Case-insensitive, trimmed comparison used to block countersigning under the same name. */
export const sameReviewer = (a: string, b: string): boolean => a.trim().toLowerCase() === b.trim().toLowerCase() && a.trim() !== "";
