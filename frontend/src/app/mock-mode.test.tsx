import "@/test/mockEnv";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { resetMockState, state } from "@/lib/mock/state";
import { nav, resetNav } from "@/test/navMock";
import { renderWithSWR } from "@/test/utils";
import CasePage from "./cases/[id]/page";
import EffectivenessPage from "./effectiveness/page";
import QueuePage from "./page";
import RulesPage from "./rules/page";
import UploadPage from "./upload/page";

vi.mock("next/navigation", async () => (await import("@/test/navMock")).navModule());

beforeEach(() => {
  resetNav();
  resetMockState();
  state.latencyMs = 0;
});
afterEach(cleanup);

describe("mock-data mode end to end", () => {
  it.each([
    ["queue", () => <QueuePage />, "/"],
    ["upload", () => <UploadPage />, "/upload"],
    ["effectiveness", () => <EffectivenessPage />, "/effectiveness"],
    ["rules", () => <RulesPage />, "/rules"],
    ["case", () => <CasePage />, "/cases/c_loan_fraud"],
  ])("renders the %s page in mock mode with no banner", async (_name, page, path) => {
    nav.pathname = path;
    nav.route = { id: "c_loan_fraud" };
    renderWithSWR(<AppShell>{page()}</AppShell>);
    expect(screen.queryByText(/MOCK DATA/)).not.toBeInTheDocument();
    await screen.findByRole("heading", { level: 1 });
  });

  it("renders the queue from fixtures", async () => {
    renderWithSWR(<QueuePage />);
    expect(await screen.findByRole("link", { name: /ACME-2026-014/ })).toHaveAttribute("href", "/cases/c_loan_fraud");
  });

  it("a created case advances through the analysis and becomes open", async () => {
    const form = new FormData();
    form.set("use_case", "loan");
    form.append("files", new File(["%PDF-1.7"], "a.pdf", { type: "application/pdf" }));
    const { case_id, job_id } = await api.createCase(form);
    expect((await api.getJob(job_id)).state).not.toBe("failed");
    expect((await api.getCase(case_id)).status).toBe("processing");
    state.readyAt.set(case_id, Date.now() - 1);
    const done = await api.getCase(case_id);
    expect(done.status).toBe("open");
    expect(done.findings.length).toBeGreaterThan(0);
  });

  it("serves the bundled page image and sample pack in mock mode", () => {
    expect(api.pageUrl("c_1", "f1", 1)).toBe("/mock/pages/statement.svg");
    expect(api.pageUrl("c_1", "f2", 1)).toBe("/mock/pages/invoice.svg");
    expect(api.pageUrl("c_1", "f3", 1)).toBe("/mock/pages/ownership.svg");
    expect(api.packUrl("c_1", "html")).toBe("/mock/sample-pack.html");
  });
});
