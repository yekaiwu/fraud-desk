import { describe, expect, it } from "vitest";
import { sameReviewer, validateReview } from "./reviewValidation";

describe("validateReview", () => {
  it("requires 5 trimmed characters of reason", () => {
    expect(validateReview({ reviewer: "a.tan", reason: "abcd" }).reason).toBeTruthy();
    expect(validateReview({ reviewer: "a.tan", reason: "    " }).reason).toBeTruthy();
    expect(validateReview({ reviewer: "a.tan", reason: "  abcde " })).toEqual({});
    expect(validateReview({ reviewer: "a.tan", reason: "x".repeat(1001) }).reason).toBeTruthy();
  });
  it("requires a reviewer of 2 to 64 characters", () => {
    expect(validateReview({ reviewer: "a", reason: "long enough" }).reviewer).toBeTruthy();
    expect(validateReview({ reviewer: " a ", reason: "long enough" }).reviewer).toBeTruthy();
    expect(validateReview({ reviewer: "x".repeat(65), reason: "long enough" }).reviewer).toBeTruthy();
    expect(validateReview({ reviewer: "ab", reason: "long enough" })).toEqual({});
  });
  it("compares reviewers case-insensitively with whitespace trimmed", () => {
    expect(sameReviewer("A.Tan", "  a.tan ")).toBe(true);
    expect(sameReviewer("a.tan", "b.lim")).toBe(false);
    expect(sameReviewer("", " ")).toBe(false);
  });
});
