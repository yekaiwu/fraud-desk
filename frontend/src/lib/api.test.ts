import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createCase, deleteCase, getCase, getQueue, getJob, getRuleChanges, packUrl, pageUrl, pollJob, postReview, resetRuleOverride, updateRuleOverride } from "./api";
import { ApiErrorException } from "./errors";
import type { Job, ReviewRequest } from "./types";

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" }, ...init });

function job(state: Job["state"]): Job {
  return {
    id: "j_1", case_id: "c_1", kind: "analyse", state, created_at: "2026-10-27T10:00:00Z", started_at: null, finished_at: null,
    progress: { stage_index: 1, stage_count: 7, current_stage: "ingest" }, stages: [], error: null,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("request mapping", () => {
  it("serialises the queue query into the URL", async () => {
    fetchMock.mockResolvedValue(json({ items: [], total: 0, page: 1, page_size: 25, has_next: false }));
    await getQueue({ use_case: ["loan"], band: ["high"], min_score: 0 });
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/cases");
    expect(url.searchParams.get("use_case")).toBe("loan");
    expect(url.searchParams.get("min_score")).toBe("0");
  });

  it("passes the case version and encodes the id", async () => {
    fetchMock.mockResolvedValue(json({ id: "c 1" }));
    await getCase("c 1", 2);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/cases/c%201");
    expect(url).toContain("version=2");
  });

  it("sends review requests as JSON", async () => {
    fetchMock.mockResolvedValue(json({ entry: {}, case: {} }, { status: 201 }));
    const req: ReviewRequest = { kind: "verdict", decision: "approve", reviewer: "a.tan", reason: "Checked all flags.", case_version: 1 };
    await postReview("c_1", req);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual(req);
  });

  it("does not set Content-Type on multipart uploads", async () => {
    fetchMock.mockResolvedValue(json({ case_id: "c_1", job_id: "j_1", status_url: "", case_url: "" }, { status: 202 }));
    const form = new FormData();
    form.set("use_case", "loan");
    await createCase(form);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toBe(form);
    expect(init.headers).toBeUndefined();
  });

  it("handles 204 on delete and sends reviewer and reason as query", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(deleteCase("c_1", "a.tan", "Test data cleanup")).resolves.toBeUndefined();
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    expect(url.searchParams.get("reviewer")).toBe("a.tan");
  });

  it("builds page and pack URLs", () => {
    const u = new URL(pageUrl("c_1", "f1", 2, 800));
    expect(u.pathname).toBe("/cases/c_1/pages/2");
    expect(u.searchParams.get("file_id")).toBe("f1");
    expect(u.searchParams.get("w")).toBe("800");
    expect(new URL(packUrl("c_1", "html")).searchParams.get("format")).toBe("html");
  });
});

describe("check catalogue calls", () => {
  it("PUTs the changed settings with the reviewer and reason as JSON to the encoded rule path", async () => {
    fetchMock.mockResolvedValue(json({ rule: "bs_running_balance", enabled: false }));
    await updateRuleOverride("bs_running_balance", { enabled: false, severity: null, reviewer: "j.lim", reason: "Because we agreed" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(new URL(String(url)).pathname).toBe("/rules/bs_running_balance/override");
    expect(init.method).toBe("PUT");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ enabled: false, severity: null, reviewer: "j.lim", reason: "Because we agreed" });
  });

  it("DELETEs the override with a JSON body", async () => {
    fetchMock.mockResolvedValue(json({ rule: "x" }));
    await resetRuleOverride("x y", { reviewer: "j.lim", reason: "Back to normal" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/rules/x%20y/override");
    expect(init.method).toBe("DELETE");
    expect(JSON.parse(init.body)).toEqual({ reviewer: "j.lim", reason: "Back to normal" });
  });

  it("reads the change log with rule and limit, as {items} or as a bare array", async () => {
    const row = { id: 1, rule: "a", at: "2026-10-27T10:00:00Z", reviewer: "j", reason: "r", action: "update", before: null, after: null };
    fetchMock.mockResolvedValueOnce(json({ changes: [row] }));
    expect(await getRuleChanges({ rule: "a", limit: 20 })).toEqual([row]);
    fetchMock.mockResolvedValueOnce(json({ items: [row] }));
    expect(await getRuleChanges()).toEqual([row]);
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/rules/changes");
    expect(url.searchParams.get("rule")).toBe("a");
    expect(url.searchParams.get("limit")).toBe("20");
    fetchMock.mockResolvedValueOnce(json([row]));
    expect(await getRuleChanges()).toEqual([row]);
    fetchMock.mockResolvedValueOnce(json({}));
    expect(await getRuleChanges()).toEqual([]);
  });

  it("surfaces the error envelope codes the editor handles", async () => {
    for (const [status, code] of [[404, "unknown_rule"], [422, "no_change"], [429, "rate_limited"]] as const) {
      fetchMock.mockResolvedValueOnce(json({ error: { code, message: code } }, { status }));
      const e = await updateRuleOverride("a", { enabled: false, reviewer: "j.lim", reason: "Because" }).catch((x) => x);
      expect(e).toBeInstanceOf(ApiErrorException);
      expect(e.error.code).toBe(code);
      expect(e.status).toBe(status);
    }
  });
});

describe("errors", () => {
  it("throws the API error body with status and request id", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "reason_required", message: "A reason is required.", details: { field: "reason" } } }, { status: 422, headers: { "X-Request-Id": "abc" } }),
    );
    const e = await getCase("c_1").catch((x) => x);
    expect(e).toBeInstanceOf(ApiErrorException);
    expect(e.error.code).toBe("reason_required");
    expect(e.status).toBe(422);
    expect(e.requestId).toBe("abc");
  });

  it("maps a failed fetch to network_error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const e = await getQueue().catch((x) => x);
    expect(e).toBeInstanceOf(ApiErrorException);
    expect(e.error.code).toBe("network_error");
  });

  it("maps a non-JSON 5xx to internal_error", async () => {
    fetchMock.mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 }));
    const e = await getJob("j_1").catch((x) => x);
    expect(e.error.code).toBe("internal_error");
    expect(e.status).toBe(502);
  });
});

describe("pollJob", () => {
  it("polls every second, then every 3 s after 30 s, and stops when the job ends", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-27T10:00:00Z"));
    let calls = 0;
    fetchMock.mockImplementation(async () => {
      calls += 1;
      return json(job(calls >= 36 ? "succeeded" : "running"));
    });
    const seen: string[] = [];
    const p = pollJob("j_1", (j) => seen.push(j.state), new AbortController().signal);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(28_000); // t = 29 s: still polling every second
    expect(calls).toBe(30);
    await vi.advanceTimersByTimeAsync(1000); // t = 30 s: next sleep is 3 s
    const atThirty = calls;
    await vi.advanceTimersByTimeAsync(2900);
    expect(calls).toBe(atThirty);
    await vi.advanceTimersByTimeAsync(100);
    expect(calls).toBe(atThirty + 1);
    await vi.advanceTimersByTimeAsync(60_000);
    const final = await p;
    expect(final.state).toBe("succeeded");
    expect(seen.at(-1)).toBe("succeeded");
  });

  it("rejects with AbortError when aborted", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(async () => json(job("running")));
    const ac = new AbortController();
    const p = pollJob("j_1", () => undefined, ac.signal);
    const assertion = expect(p).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(10);
    ac.abort();
    await assertion;
  });
});
