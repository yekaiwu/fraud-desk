import type { Lane, Verdict } from "./types";

/** Fallbacks used until /health settings are known (api.md section 11). */
export const CLASSIFIER_LOW_PROB = 0.7;
export const MAX_FILES_PER_CASE = 40;
export const MAX_UPLOAD_MB = 25;
export const PAGE_SIZE = 25;

export const NOISY_MIN_DECIDED = 5;
export const NOISY_RATE = 0.5;

export const LOW_COVERAGE_RATIO = 0.7;
export const APPROVE_MIN_COVERAGE = 0.8;

export const REVIEWER_STORAGE_KEY = "gldb.reviewer";

/** Local development talks to the backend directly. */
export const DEV_API_BASE_URL = "http://localhost:8000";
/** A production build is served by the Next server, which proxies same-origin /api/* to the backend (docs/deploy.md). */
export const PROD_API_BASE_URL = "/api";
export const DEFAULT_API_BASE_URL = process.env.NODE_ENV === "production" ? PROD_API_BASE_URL : DEV_API_BASE_URL;

/** Read once at module load; the value is inlined at build time by Next. */
export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "1";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

export const LANE_LABELS: Record<Lane, string> = {
  edd: "EDD review",
  aml: "AML investigation",
  loan: "Loan decision",
  fast: "Fast lane",
};

export const VERDICT_LABELS: Record<Verdict, string> = {
  approve: "Approve",
  request_documents: "Request documents",
  refer_edd: "Refer to EDD",
  escalate_aml: "Escalate to AML investigation",
  reject_or_escalate: "Reject or escalate",
};

export const DISCLAIMER =
  "Decision support only; a human decides; not legal advice. Our mapping to MAS and STR obligations is a student reading.";
