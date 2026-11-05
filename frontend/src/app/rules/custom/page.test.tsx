import { describe, expect, it, vi } from "vitest";

const redirect = vi.fn((to: string): never => {
  throw new Error(`NEXT_REDIRECT ${to}`);
});
vi.mock("next/navigation", () => ({ redirect: (to: string) => redirect(to) }));

import CustomRulesRedirect from "./page";

describe("the old /rules/custom route", () => {
  it("sends bookmarks to the check catalogue instead of a 404", () => {
    expect(() => CustomRulesRedirect()).toThrow("NEXT_REDIRECT /rules");
    expect(redirect).toHaveBeenCalledWith("/rules");
  });
});
