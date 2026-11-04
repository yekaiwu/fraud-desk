import "@/test/mockEnv";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ApiErrorException } from "./errors";

describe("custom rules API client in mock mode", () => {
  it("has no custom rules: every call says the feature is off and nothing is fetched", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const calls: (() => Promise<unknown>)[] = [
      () => api.getCustomCapabilities(),
      () => api.startCustomDraft("flag big invoices"),
      () => api.getCustomDraft("crd_5b2e9f0a1c3d"),
      () => api.sendCustomTurn("crd_5b2e9f0a1c3d", { text: "more" }),
      () => api.dryRunCustomDraft("crd_5b2e9f0a1c3d"),
      () => api.saveCustomRule({ draft_id: "crd_5b2e9f0a1c3d" }),
      () => api.listCustomRules(),
      () => api.listSharedCustomRules(),
      () => api.getCustomRule("cus_3f9a1c2b7d"),
      () => api.setCustomRuleEnabled("cus_3f9a1c2b7d", true, "reason"),
      () => api.publishCustomRule("cus_3f9a1c2b7d", "reason"),
      () => api.unpublishCustomRule("cus_3f9a1c2b7d", "reason"),
      () => api.adoptCustomRule("cus_3f9a1c2b7d"),
      () => api.unadoptCustomRule("cus_3f9a1c2b7d"),
      () => api.retireCustomRule("cus_3f9a1c2b7d", "reason"),
      () => api.getCustomRuleChanges(),
      () => api.listCustomTickets(),
      () => api.submitCustomTicket({ text: "an idea" }),
    ];
    for (const f of calls) {
      const err = await f().catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiErrorException);
      expect((err as ApiErrorException).error.code).toBe("feature_off");
    }
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
