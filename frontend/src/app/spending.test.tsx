import "@/test/mockEnv";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import type { Spending } from "@/lib/types";
import { renderWithSWR } from "@/test/utils";
import SpendingPage from "./spending/page";

const data: Spending = {
  viewer: "boss",
  day: "2026-11-05",
  site_cap_usd: "5.00",
  per_account_cap_usd: "1.00",
  site_today_usd: "0.850000",
  unattributed_today_usd: "0.100000",
  accounts: [
    { account: "boss", today_usd: "0.500000", week_usd: "0.600000" },
    { account: "user1", today_usd: "0.250000", week_usd: "0.250000" },
  ],
  history: [{ day: "2026-11-05", site_usd: "0.850000" }],
};

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("spending page", () => {
  it("lists every account with the viewer marked, and the site total against its cap", async () => {
    vi.spyOn(api, "getSpending").mockResolvedValue(data);
    renderWithSWR(<SpendingPage />);
    expect(await screen.findByText("user1")).toBeInTheDocument();
    expect(screen.getByText("(you)")).toBeInTheDocument();
    expect(screen.getAllByText("$0.2500").length).toBe(2); // today and last 7 days
    expect(screen.getAllByText(/of a \$5\.00 daily cap/).length).toBeGreaterThan(0);
    expect(screen.getByText("Each account may spend $1.00 a day")).toBeInTheDocument();
  });

  it("tells any other account that the page is not for them", async () => {
    vi.spyOn(api, "getSpending").mockRejectedValue(new ApiErrorException({ code: "forbidden", message: "Only an administrator account can see everybody's spending." }, 403, null));
    renderWithSWR(<SpendingPage />);
    expect(await screen.findByText("Only the administrator account can see this")).toBeInTheDocument();
  });
});
