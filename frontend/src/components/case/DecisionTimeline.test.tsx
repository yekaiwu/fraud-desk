import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ReviewEntry } from "@/lib/types";
import { DecisionTimeline, entryAction, newestFirst } from "./DecisionTimeline";

afterEach(cleanup);

const entry = (over: Partial<ReviewEntry>): ReviewEntry => ({
  id: "r1", kind: "verdict", reviewer: "a.tan", decision: "approve", reason: "ok", flag_ids: [], finding_id: null, target_entry_id: null,
  status: "effective", case_version: 1, created_at: "2026-10-27T10:00:00Z", ...over,
});

describe("entryAction", () => {
  it("speaks plainly about each kind of entry", () => {
    expect(entryAction(entry({}))).toBe("Decided: Approve");
    expect(entryAction(entry({ kind: "flag_decision", decision: "dismiss", flag_ids: ["a", "b"] }))).toBe("Mark as not a concern: 2 flags");
    expect(entryAction(entry({ kind: "flag_decision", decision: "confirm", finding_id: "fnd_1" }))).toBe("Mark as a real concern: a finding");
    expect(entryAction(entry({ kind: "second_approval", decision: "approve", target_entry_id: "r2" }))).toBe("Approved the dismissal (entry r2)");
    expect(entryAction(entry({ kind: "second_approval", decision: "decline", target_entry_id: "r2" }))).toBe("Declined the dismissal (entry r2)");
  });
});

describe("DecisionTimeline", () => {
  it("lists newest first", () => {
    const sorted = newestFirst([entry({ id: "r1" }), entry({ id: "r2", created_at: "2026-10-27T11:00:00Z" }), entry({ id: "r3", created_at: "2026-10-27T11:00:00Z" })]);
    expect(sorted.map((e) => e.id)).toEqual(["r3", "r2", "r1"]);
  });

  it("shows who, what, when and why, and marks entries waiting for a second reviewer", () => {
    render(
      <DecisionTimeline
        currentVersion={1}
        entries={[entry({ id: "r1", reviewer: "a.tan", kind: "flag_decision", decision: "dismiss", flag_ids: ["x"], reason: "Old draft chart.", status: "pending_second_approval" }), entry({ id: "r2", reviewer: "b.lim", reason: "All good.", created_at: "2026-10-27T12:00:00Z" })]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("b.lim");
    expect(items[0]).toHaveTextContent("Decided: Approve");
    expect(items[0]).toHaveTextContent("2026-10-27 12:00 UTC");
    expect(items[0]).toHaveTextContent("All good.");
    expect(within(items[1]).getByText("Needs a second reviewer")).toBeInTheDocument();
  });

  it("groups by version when the case was re-run and renders reasons as literal text", () => {
    render(<DecisionTimeline currentVersion={2} entries={[entry({ id: "r1", reason: "<b>x</b>", case_version: 1 }), entry({ id: "r2", case_version: 2, created_at: "2026-10-27T12:00:00Z" })]} />);
    expect(screen.getByText("<b>x</b>")).toBeInTheDocument();
    expect(document.querySelector("li b")).toBeNull();
    const headings = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(headings).toEqual(["Version 2 (current)", "Version 1"]);
  });

  it("says so when there are no decisions", () => {
    render(<DecisionTimeline entries={[]} currentVersion={1} />);
    expect(screen.getByText(/No decisions yet/)).toBeInTheDocument();
  });
});
