import { REVIEWER_STORAGE_KEY } from "./constants";

/** localStorage may be missing or throw (private window, blocked site data); every access is guarded. */
export function readReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveReviewer(name: string): void {
  try {
    window.localStorage.setItem(REVIEWER_STORAGE_KEY, name);
  } catch {
    /* works without it */
  }
}
