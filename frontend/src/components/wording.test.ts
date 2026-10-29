import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|css)$/.test(f) && !/\.test\./.test(f) ? [p] : [];
  });
}
const root = join(__dirname, "..");
// lib/wording.ts holds the only sentences that may negate these words; lib/mock holds fictitious fixtures.
const files = [...walk(join(root, "components")), ...walk(join(root, "app"))];

describe("source hygiene (frontend.md sections 3, 5.3, 13)", () => {
  it("never calls a document or signature authentic, verified or genuine", () => {
    for (const f of files) {
      const src = readFileSync(f, "utf8");
      // field names such as Cell.verified are not display text; none of the components reference them
      expect(src, f).not.toMatch(/\b(authentic|genuine|verified)\b/i);
    }
  });

  it("has no dangerouslySetInnerHTML anywhere", () => {
    for (const f of [...files, ...walk(join(root, "lib"))]) expect(readFileSync(f, "utf8"), f).not.toContain("dangerouslySetInnerHTML");
  });

  it("never runs Number() on an amount", () => {
    for (const f of [join(root, "lib", "graph.ts"), join(root, "lib", "decimal.ts"), ...walk(join(root, "components"))]) {
      const src = readFileSync(f, "utf8");
      expect(src, f).not.toMatch(/Number\([^)]*(amount|cents|total|credit|debit|balance)/i);
      expect(src, f).not.toMatch(/parseFloat\(/);
    }
  });

  it("has no secrets or API keys in the sources", () => {
    for (const f of [...files, ...walk(join(root, "lib"))]) expect(readFileSync(f, "utf8"), f).not.toMatch(/sk-[A-Za-z0-9]{20,}|api[_-]?key\s*[:=]\s*["'][A-Za-z0-9]{16,}/i);
  });
});
