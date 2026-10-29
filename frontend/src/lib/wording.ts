/**
 * Fixed wording for silence and forensic results (plan 4.5, forensics.md 8). The UI never calls a
 * document or signature "authentic", "verified" or "genuine"; the only places those ideas appear are
 * the negations below. wording.test.ts greps the components for the banned words.
 */
export const NO_SIGNAL = "No signal found (not a guarantee of authenticity)";
export const NO_AI_HINT = "No AI-generator provenance hint found. This does not mean the document is authentic.";
export const NO_FLAGS_NOTE = "No flags is not proof of authenticity; see coverage.";
export const NOT_EXAMINED = "These pages were not examined by any check.";
