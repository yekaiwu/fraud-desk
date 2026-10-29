import { describe, expect, it } from "vitest";
import type { QueueItem } from "@/lib/types";
import { item } from "./testItem";
import { bandCounts, formatWaitLong, isLongWait, laneBadgeFor, laneCounts, needsAttention, pickNext, splitTopFinding, summarise, summaryText, waitingText } from "./worklist";

const risky = (id: string, over: Partial<QueueItem> = {}) => item({ case_id: id, score: 75, band: "high", lane: "aml", high_count: 2, ...over });

describe("needsAttention", () => {
  it("is false for a routine open case", () => expect(needsAttention(item())).toBe(false));
  it("is true for a medium band, a partial score, a high concern, thin coverage or a pending second approval", () => {
    expect(needsAttention(item({ band: "medium", score: 40 }))).toBe(true);
    expect(needsAttention(item({ partial: true }))).toBe(true);
    expect(needsAttention(item({ high_count: 1 }))).toBe(true);
    expect(needsAttention(item({ pending_second_approvals: 1 }))).toBe(true);
    expect(needsAttention(item({ coverage: { applicable: 20, ran: 10, ratio: 0.5, skipped: [], pages_total: 1, pages_unchecked: 0 } }))).toBe(true);
  });
  it("is false for cases that are not open yet", () => {
    expect(needsAttention(risky("a", { status: "processing" }))).toBe(false);
    expect(needsAttention(risky("a", { status: "closed" }))).toBe(false);
  });
});

describe("summary sentence", () => {
  const items = [risky("a", { age_seconds: 2 * 86400 + 100 }), risky("b"), risky("c"), ...Array.from({ length: 7 }, (_, i) => item({ case_id: `r${i}` }))];

  it("counts open cases, attention and routine, and the longest wait", () => {
    expect(summaryText(summarise(items))).toBe("10 cases open: 3 need your attention, 7 look routine. Oldest has waited 2 days.");
  });

  it("uses singular verbs", () => {
    expect(summaryText(summarise([risky("a", { age_seconds: 3 * 3600 }), item()]))).toBe("2 cases open: 1 needs your attention, 1 looks routine. Oldest has waited 3 hours.");
  });

  it("says when everything looks routine or everything needs attention", () => {
    expect(summaryText(summarise([item(), item()]))).toContain("all look routine");
    expect(summaryText(summarise([risky("a"), risky("b")]))).toContain("all 2 need your attention");
  });

  it("says nothing needs attention when nothing is open, and mentions cases still being prepared", () => {
    expect(summaryText(summarise([]))).toBe("Nothing needs attention right now.");
    expect(summaryText(summarise([item({ status: "processing", score: null, band: null, lane: null }), item({ status: "awaiting_documents" })]))).toBe(
      "Nothing needs attention right now. Also 1 being analysed and 1 waiting for documents.",
    );
  });

  it("does not claim nothing needs attention when a search simply matched nothing", () => {
    expect(summaryText(summarise([], 0, false, true))).toBe("No cases match your search or filters.");
    expect(summaryText(summarise([], 0, false, false))).toBe("Nothing needs attention right now.");
  });

  it("marks the numbers as a floor when the sample was cut off", () => {
    expect(summaryText(summarise(items, 0, true))).toMatch(/^At least 10 cases open/);
  });

  it("lets the waiting time keep growing between refreshes", () => {
    expect(summaryText(summarise([item({ age_seconds: 600 })], 1200))).toContain("Oldest has waited 30 minutes.");
  });
});

describe("formatWaitLong", () => {
  it("reads as words", () => {
    expect(formatWaitLong(10)).toBe("under a minute");
    expect(formatWaitLong(60)).toBe("1 minute");
    expect(formatWaitLong(3 * 3600 + 5)).toBe("3 hours");
    expect(formatWaitLong(86400)).toBe("1 day");
  });
});

describe("lane and band counts", () => {
  const items = [
    risky("a"),
    risky("b", { lane: "edd" }),
    item({ case_id: "c", lane: "fast" }),
    item({ case_id: "d", lane: "fast" }),
    item({ case_id: "e", status: "processing", score: null, band: null, lane: null }),
  ];
  it("counts every lane, with All including cases not yet placed in a lane", () => {
    expect(laneCounts(items)).toEqual({ all: 5, loan: 0, edd: 1, aml: 1, fast: 2 });
  });
  it("honours the band filter in lane counts and the lane filter in band counts", () => {
    expect(laneCounts(items, ["high"])).toEqual({ all: 2, loan: 0, edd: 1, aml: 1, fast: 0 });
    expect(bandCounts(items)).toEqual({ high: 2, medium: 0, low: 2 });
    expect(bandCounts(items, ["fast"])).toEqual({ high: 0, medium: 0, low: 2 });
  });
});

describe("pickNext", () => {
  const items = [item({ case_id: "p", status: "processing" }), item({ case_id: "w", status: "awaiting_documents" }), risky("first"), risky("second", { lane: "edd" }), item({ case_id: "fast1" })];
  it("returns the first open case in priority order", () => expect(pickNext(items)?.case_id).toBe("first"));
  it("respects the lane and band in view", () => {
    expect(pickNext(items, { lane: ["edd"] })?.case_id).toBe("second");
    expect(pickNext(items, { band: ["low"] })?.case_id).toBe("fast1");
  });
  it("is null when nothing can be opened", () => expect(pickNext([items[0], items[1]])).toBeNull());
});

describe("splitTopFinding", () => {
  it("splits the (+N more) suffix", () => {
    expect(splitTopFinding("Running balance does not add up (+5 more)")).toEqual({ text: "Running balance does not add up", more: 5 });
    expect(splitTopFinding("Plain reason")).toEqual({ text: "Plain reason", more: 0 });
    expect(splitTopFinding(null)).toBeNull();
  });
});

describe("waiting", () => {
  it("is worded as Waiting and coloured only when long", () => {
    expect(waitingText(7300)).toBe("Waiting 2 h");
    expect(waitingText(3 * 86400)).toBe("Waiting 3 d");
    expect(isLongWait(86399)).toBe(false);
    expect(isLongWait(86400)).toBe(true);
  });
});

describe("plain wording for clean cases", () => {
  it("turns the API's 'No flags raised' sentence into plain English and drops the informational-notes tail", () => {
    expect(splitTopFinding("No flags raised; 41 of 54 checks ran (8 informational notes)")).toEqual({ text: "No concerns found. 41 of 54 checks completed.", more: 0 });
    expect(splitTopFinding("No flags raised; 47 of 53 checks ran")?.text).toBe("No concerns found. 47 of 53 checks completed.");
  });

  it("leaves real findings alone", () => {
    expect(splitTopFinding("The printed Gross profit does not equal the sum of its lines (+5 more)")).toEqual({ text: "The printed Gross profit does not equal the sum of its lines", more: 5 });
  });

  it("calls a low-risk case without a high concern 'Routine review', not by its escalation queue", () => {
    const routine = laneBadgeFor({ lane: "aml", band: "low", high_count: 0 });
    expect(routine?.label).toBe("Routine review");
    expect(routine?.variant).toBe("neutral");
    expect(routine?.title).toMatch(/AML investigation queue/);
  });

  it("keeps the real lane for risky cases and the fast lane badge for fast-lane cases", () => {
    expect(laneBadgeFor({ lane: "aml", band: "high", high_count: 2 })?.label).toBe("AML investigation");
    expect(laneBadgeFor({ lane: "edd", band: "medium", high_count: 0 })?.label).toBe("Enhanced due diligence");
    expect(laneBadgeFor({ lane: "aml", band: "low", high_count: 1 })?.label).toBe("AML investigation");
    expect(laneBadgeFor({ lane: "fast", band: "low", high_count: 0 })).toMatchObject({ label: "Fast lane", variant: "fast" });
    expect(laneBadgeFor({ lane: null, band: "low", high_count: 0 })).toBeNull();
  });
});
