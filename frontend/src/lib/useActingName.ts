"use client";

import { REVIEWER_MAX, REVIEWER_MIN } from "./reviewValidation";
import { useReviewer } from "./useReviewer";
import { useSignedInReviewer } from "./useSignedInReviewer";

/**
 * Who is doing it, the way the review flow handles it: the signed-in account when there is one (the server records
 * that name and ignores a typed one), otherwise the name typed once and remembered in this browser.
 */
export function useActingName() {
  const signedIn = useSignedInReviewer();
  const [typed, setTyped, remember] = useReviewer();
  const who = (signedIn ?? typed).trim();
  return { signedIn, typed, setTyped, remember, who, valid: who.length >= REVIEWER_MIN && who.length <= REVIEWER_MAX };
}
