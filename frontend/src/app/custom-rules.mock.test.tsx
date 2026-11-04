import "@/test/mockEnv";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { renderWithSWR } from "@/test/utils";
import CustomRulesPage from "./rules/custom/page";
import RulesPage from "./rules/page";

afterEach(cleanup);

describe("custom rules with mock data", () => {
  it("has no custom rules in mock mode: the page says so and the catalogue shows no section", async () => {
    renderWithSWR(<CustomRulesPage />);
    expect(await screen.findByText("Custom rules are not available")).toBeInTheDocument();
    cleanup();
    renderWithSWR(<RulesPage />);
    await screen.findAllByText("bs_running_balance");
    expect(screen.queryByRole("heading", { name: "Custom rules" })).toBeNull();
  });
});
