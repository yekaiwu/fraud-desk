import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveEntityGraph } from "./graph";
import { numberFlags, pageCounts } from "./flags";
import { clampRegion } from "./regions";
import type { CaseDetail } from "./types";

// Real API responses written by the backend tests (api.md section 12, CR-51). Skipped when the folder is absent.
const dir = join(__dirname, "..", "..", "..", "backend", "tests", "_out", "api_fixtures");
const have = existsSync(dir);
const load = (name: string) => JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8"));
const hasKeys = (o: object, keys: string[]) => keys.forEach((k) => expect(o, `missing key ${k}`).toHaveProperty(k));

describe.skipIf(!have)("API fixtures match lib/types.ts", () => {
  it("queue page", () => {
    const q = load("queue_page");
    hasKeys(q, ["items", "total", "page", "page_size", "has_next"]);
    for (const i of q.items) {
      hasKeys(i, ["case_id", "reference", "use_case", "status", "applicant_name", "version", "created_at", "updated_at", "age_seconds", "score", "band", "coverage", "lane", "top_finding", "findings_count", "high_count", "partial", "decision", "pending_second_approvals", "file_count"]);
      if (i.coverage) hasKeys(i.coverage, ["applicable", "ran", "ratio", "skipped", "pages_total", "pages_unchecked"]);
    }
  });

  it("case detail, and the UI helpers cope with it", () => {
    const c = load("case_detail") as CaseDetail;
    hasKeys(c, ["id", "use_case", "status", "version", "today", "applicant", "files", "documents", "flags", "findings", "check_runs", "partial", "partial_reasons", "stages", "costs", "review", "pending_second_approvals", "allowed_verdicts", "not_checked_documents", "cost_summary"]);
    for (const f of c.flags) {
      hasKeys(f, ["id", "rule", "stage", "severity", "document_ids", "extra_regions", "evidence", "explanation", "disposition"]);
      for (const r of [f.region, ...f.extra_regions]) if (r) expect(clampRegion(r), `flag ${f.id} region`).not.toBeNull();
    }
    for (const fnd of c.findings) hasKeys(fnd, ["id", "group_key", "title", "severity", "flag_ids", "document_ids", "rules", "weight", "summary", "hard_stop"]);
    expect(() => numberFlags(c.findings, c.flags)).not.toThrow();
    expect(() => deriveEntityGraph(c)).not.toThrow();
    expect(() => pageCounts(c)).not.toThrow();
  });

  it("review response, job, effectiveness, error", () => {
    const r = load("review_response");
    hasKeys(r.entry, ["id", "kind", "reviewer", "decision", "reason", "flag_ids", "status", "case_version", "created_at"]);
    hasKeys(r.case, ["id", "status", "decision", "escalated_at", "version", "pending_second_approvals"]);
    hasKeys(load("job"), ["id", "case_id", "kind", "state", "created_at", "started_at", "finished_at", "progress", "stages", "error"]);
    const e = load("effectiveness");
    hasKeys(e, ["generated_for", "cases_total", "cases_decided", "median_seconds_to_verdict", "rules"]);
    for (const rule of e.rules) hasKeys(rule, ["rule", "stage", "default_severity", "description", "cases_fired", "times_fired", "confirmed", "dismissed", "pending", "open", "dismissal_rate", "median_seconds_to_decide", "cases_ran", "cases_skipped"]);
    hasKeys(load("error_response").error, ["code", "message"]);
  });
});

describe.skipIf(have)("API fixtures", () => {
  it.skip("are not present; run the backend API tests to write backend/tests/_out/api_fixtures", () => {});
});
