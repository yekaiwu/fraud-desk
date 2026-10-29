import { describe, expect, it } from "vitest";

describe("test setup", () => {
  it("runs in jsdom with jest-dom matchers", () => {
    const el = document.createElement("div");
    el.textContent = "hello";
    document.body.appendChild(el);
    expect(el).toBeInTheDocument();
    expect(el).toHaveTextContent("hello");
  });

  it("resolves the @/ path alias", async () => {
    const mod = await import("@/lib/constants");
    expect(mod.DEFAULT_API_BASE_URL).toBe("http://localhost:8000");
  });
});
