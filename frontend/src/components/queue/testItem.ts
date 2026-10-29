import type { QueueItem } from "@/lib/types";

/** Test helper: a routine open case; override fields to make it something else. */
export function item(over: Partial<QueueItem> = {}): QueueItem {
  return {
    case_id: "c_1", reference: null, use_case: "loan", status: "open", applicant_name: "Acme Pte. Ltd.", version: 1,
    created_at: "2026-10-28T00:00:00Z", updated_at: "2026-10-28T00:00:00Z", age_seconds: 7200,
    score: 0, band: "low", coverage: { applicable: 20, ran: 19, ratio: 0.95, skipped: [], pages_total: 10, pages_unchecked: 0 },
    lane: "fast", top_finding: "No flags raised", findings_count: 0, high_count: 0, partial: false,
    decision: null, pending_second_approvals: 0, file_count: 3, ...over,
  };
}
