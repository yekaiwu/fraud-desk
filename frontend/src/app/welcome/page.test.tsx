import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { signSession } from "@/lib/auth/session";

let cookieValue: string | undefined;
vi.mock("next/headers", () => ({ cookies: async () => ({ get: (name: string) => (name === "gldb_session" && cookieValue ? { name, value: cookieValue } : undefined) }) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/welcome" }));

import WelcomePage, { metadata } from "./page";

const USERNAME = "officer";
const PASSWORD = "correct horse battery";
const SECRET = "x".repeat(40);

beforeEach(() => {
  cookieValue = undefined;
});
afterEach(() => vi.unstubAllEnvs());

// Role queries over the whole page are slow in jsdom, especially when the full suite runs in parallel.
vi.setConfig({ testTimeout: 30_000 });

describe("/welcome page", () => {
  it("has the agreed title and a description, and stays out of search engines", () => {
    expect(metadata.title).toEqual({ absolute: "How it works | Green Link Fraud Desk" });
    expect(metadata.description).toMatch(/Fraud Desk/);
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });

  it("shows Staff sign-in to an anonymous visitor when the gate is on", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("GLDB_SITE_USERNAME", USERNAME);
    vi.stubEnv("GLDB_SITE_PASSWORD", PASSWORD);
    vi.stubEnv("GLDB_SESSION_SECRET", SECRET);
    render(await WelcomePage());
    expect(screen.getAllByRole("link", { name: "Staff sign-in" })[0]).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: "Open the worklist" })).not.toBeInTheDocument();
  });

  it("shows Open the worklist to a visitor with a valid session, and not for a forged cookie", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("GLDB_SITE_USERNAME", USERNAME);
    vi.stubEnv("GLDB_SITE_PASSWORD", PASSWORD);
    vi.stubEnv("GLDB_SESSION_SECRET", SECRET);
    cookieValue = await signSession(SECRET, `${USERNAME}\u0000${PASSWORD}`);
    const { unmount } = render(await WelcomePage());
    expect(screen.getAllByRole("link", { name: "Open the worklist" })[0]).toHaveAttribute("href", "/");
    unmount();
    cookieValue = cookieValue + "x";
    render(await WelcomePage());
    expect(screen.getAllByRole("link", { name: "Staff sign-in" }).length).toBeGreaterThan(0);
  });

  it("treats a misconfigured production server as signed out", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("GLDB_SITE_USERNAME", "");
    vi.stubEnv("GLDB_SITE_PASSWORD", "");
    vi.stubEnv("GLDB_SESSION_SECRET", "");
    cookieValue = "anything";
    render(await WelcomePage());
    expect(screen.getAllByRole("link", { name: "Staff sign-in" }).length).toBeGreaterThan(0);
  });
});
