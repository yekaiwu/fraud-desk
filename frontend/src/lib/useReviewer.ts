"use client";

import { useState } from "react";
import { readReviewer, saveReviewer } from "./storage";

/**
 * Reviewer name, prefilled from localStorage (a blocked storage just means an empty field). Only used by
 * components that render after client-side data has loaded, so reading storage in the initialiser is safe.
 */
export function useReviewer(): [string, (v: string) => void, (v: string) => void] {
  const [name, setName] = useState(readReviewer);
  return [name, setName, saveReviewer];
}
