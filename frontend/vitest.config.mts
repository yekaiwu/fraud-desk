import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["**/node_modules/**", ".next/**", ".next-*/**"],
    css: false,
    // Rendering a whole page in jsdom takes a few seconds on a busy laptop; the 5 s default makes those tests flaky.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
});
