import { describe, expect, it } from "vitest";
import { DEFAULT_QUEUE_STATUS, SCOPE_PAGE_SIZE, queueQueryToSearchParams, scopeQuery, searchParamsToQueueQuery } from "./queueQuery";
import type { QueueQuery } from "./types";

describe("queueQuery", () => {
  it("round-trips arrays, partial=false and min_score=0 losslessly", () => {
    const q: QueueQuery = {
      use_case: ["kyc", "loan"], status: ["open", "closed"], lane: ["edd", "fast"], band: ["high"],
      min_score: 0, q: "acme", partial: false, sort: "score", order: "asc", page: 3,
    };
    const params = queueQueryToSearchParams(q);
    expect(params.get("partial")).toBe("false");
    expect(params.get("min_score")).toBe("0");
    expect(searchParamsToQueueQuery(new URLSearchParams(params.toString()))).toEqual(q);
  });

  it("does not serialise the default status, sort or page", () => {
    const params = queueQueryToSearchParams({ status: [...DEFAULT_QUEUE_STATUS].reverse(), sort: "priority", page: 1, page_size: 25 });
    expect(params.toString()).toBe("");
  });

  it("ignores unknown values", () => {
    const q = searchParamsToQueueQuery(new URLSearchParams("use_case=kyc,nope&sort=bogus&page=0&min_score=abc&lane=zzz"));
    expect(q).toEqual({ use_case: ["kyc"] });
  });
});

describe("scopeQuery", () => {
  it("drops lane, band, sort, order and page but keeps what defines the set of cases", () => {
    const scope = scopeQuery({ use_case: ["kyc"], status: ["open", "closed"], q: "acme", lane: ["fast"], band: ["low"], sort: "age", order: "asc", page: 3, page_size: 25 });
    expect(scope).toEqual({ use_case: ["kyc"], status: ["open", "closed"], q: "acme", page_size: SCOPE_PAGE_SIZE });
  });

  it("is just a big first page for an untouched query", () => {
    expect(scopeQuery({})).toEqual({ page_size: SCOPE_PAGE_SIZE });
  });
});
