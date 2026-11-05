import { act, cleanup, render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiErrorException } from "./errors";

const getMe = vi.fn();
vi.mock("./api", () => ({ getMe: () => getMe() }));
vi.mock("./constants", () => ({ USE_MOCK: false }));

import { useMe } from "./useMe";

function Probe() {
  const me = useMe();
  return <p>{me ? (me.admin ? "admin" : "reviewer") : "unknown"}</p>;
}
const mount = () =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <Probe />
    </SWRConfig>,
  );
const blip = () => new ApiErrorException({ code: "internal", message: "bad gateway" }, 502);
const ME = { reviewer: "Ana", admin: true, custom_rules: true };

beforeEach(() => {
  vi.useFakeTimers();
  getMe.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useMe", () => {
  it("recovers from one failed /me and only then answers", async () => {
    getMe.mockRejectedValueOnce(blip()).mockResolvedValue(ME);
    mount();
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("unknown")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(2100));
    expect(getMe).toHaveBeenCalledTimes(2);
    expect(screen.getByText("admin")).toBeInTheDocument();
  });

  it("stops after three retries and never shows the admin view", async () => {
    getMe.mockRejectedValue(blip());
    mount();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(getMe).toHaveBeenCalledTimes(4);
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("does not retry a 401", async () => {
    getMe.mockRejectedValue(new ApiErrorException({ code: "unauthorized", message: "no" }, 401));
    mount();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(getMe).toHaveBeenCalledTimes(1);
  });
});
