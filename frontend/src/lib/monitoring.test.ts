import { describe, expect, it } from "vitest";
import { formatMetricValue, metricLabel, schedulerView, sinkLabel, valueVersusLimit, whenText } from "./monitoring";

const NOW = Date.parse("2026-10-27T10:30:00Z");

describe("metric wording", () => {
  it("names the known metrics in plain words and falls back to the key", () => {
    expect(metricLabel("partial_rate")).toBe("Cases only partly analysed");
    expect(metricLabel("monitor_stale")).toBe("The monitor went quiet");
    expect(metricLabel("something_new")).toBe("something_new");
  });

  it("shows rates as percentages, drift as points and silence as minutes", () => {
    expect(formatMetricValue("partial_rate", 0.42)).toBe("42%");
    expect(formatMetricValue("check_error_rate", 0.0625)).toBe("6.3%");
    expect(formatMetricValue("daily_spend_ratio", 0.8)).toBe("80%");
    expect(formatMetricValue("rule_drift", 0.31)).toBe("31 points");
    expect(formatMetricValue("monitor_stale", 45.5)).toBe("45.5 min");
    expect(formatMetricValue("partial_rate", null)).toBe("-");
    expect(valueVersusLimit("partial_rate", 0.42, 0.3)).toBe("42% against a limit of 30%");
  });

  it("writes a time as UTC with how long ago, or 'never'", () => {
    expect(whenText("2026-10-27T10:26:00.000000Z", NOW)).toBe("2026-10-27 10:26 UTC (4 min ago)");
    expect(whenText(null)).toBe("never");
  });

  it("names the page as a place", () => {
    expect(sinkLabel("in-app")).toBe("the Notifications page");
    expect(sinkLabel("webhook")).toBe("webhook");
  });
});

describe("schedulerView", () => {
  it("says off plainly, never healthy", () => {
    const v = schedulerView({ scheduler: "off", interval_minutes: null, last_evaluated_at: null, monitor_stale: false });
    expect(v).toMatchObject({ tone: "off", badge: "Off" });
    expect(v.line).toMatch(/Nothing is checking/);
    expect(v.line).toMatch(/does not mean all is well/);
  });

  it("says running with the last evaluation, in process or external", () => {
    const a = schedulerView({ scheduler: "in_process", interval_minutes: 15, last_evaluated_at: "2026-10-27T10:26:00Z", monitor_stale: false }, NOW);
    expect(a.tone).toBe("running");
    expect(a.line).toBe("Checks run every 15 min inside this service. Last checked 2026-10-27 10:26 UTC (4 min ago).");
    const b = schedulerView({ scheduler: "external", interval_minutes: 30, last_evaluated_at: null, monitor_stale: false }, NOW);
    expect(b.line).toBe("Checks run every 30 min by the bank's scheduler. Not checked yet.");
  });

  it("says stale when the monitor has gone quiet", () => {
    const v = schedulerView({ scheduler: "in_process", interval_minutes: 15, last_evaluated_at: "2026-10-27T08:00:00Z", monitor_stale: true }, NOW);
    expect(v).toMatchObject({ tone: "stale", badge: "Stale" });
    expect(v.line).toMatch(/more than two intervals/);
    expect(v.line).toMatch(/Alerts may have been missed/);
  });
});
