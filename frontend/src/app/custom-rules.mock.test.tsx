import "@/test/mockEnv";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { renderWithSWR } from "@/test/utils";
import RulesPage from "./rules/page";

afterEach(cleanup);

describe("custom checks with mock data", () => {
  it("has no custom checks in mock mode: the catalogue shows no button and no section", async () => {
    renderWithSWR(<RulesPage />);
    await screen.findAllByText("bs_running_balance");
    expect(screen.queryByRole("button", { name: "Create custom check" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Custom checks" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/custom (check|rule)/i);
  });
});
