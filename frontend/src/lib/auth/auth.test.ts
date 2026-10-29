// @vitest-environment node
import { describe, expect, it } from "vitest";
import { readAuthConfig } from "./config";
import { clientIp, decide, isHttps, isPublicPath, isSignedIn, safeNext, sessionCookie, setupErrorResponse, signInRedirect } from "./gate";
import { LoginLimiter } from "./ratelimit";
import { safeEqual, signSession, verifySession } from "./session";

const SECRET = "s".repeat(32) + "-secret";
const USERNAME = "officer";
const PASSWORD = "correct horse battery";
const BINDING = `${USERNAME}\u0000${PASSWORD}`; // what an enforcing config signs sessions with
const T0 = 1_800_000_000_000;

describe("session cookie", () => {
  it("round-trips", async () => {
    const t = await signSession(SECRET, PASSWORD, T0);
    expect(t).toMatch(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
    expect(await verifySession(t, SECRET, PASSWORD, T0 + 1000)).toBe(true);
  });

  it("expires after 12 hours", async () => {
    const t = await signSession(SECRET, PASSWORD, T0);
    expect(await verifySession(t, SECRET, PASSWORD, T0 + 12 * 3600 * 1000 - 1000)).toBe(true);
    expect(await verifySession(t, SECRET, PASSWORD, T0 + 12 * 3600 * 1000 + 1000)).toBe(false);
  });

  it("rejects a tampered payload or signature", async () => {
    const t = await signSession(SECRET, PASSWORD, T0);
    const [p, s] = t.split(".");
    const longer = Buffer.from(JSON.stringify({ iat: T0 / 1000, exp: T0 / 1000 + 10 * 86400 })).toString("base64url");
    expect(await verifySession(`${longer}.${s}`, SECRET, PASSWORD, T0)).toBe(false);
    const flipped = s.slice(0, -2) + (s.endsWith("AA") ? "BB" : "AA");
    expect(await verifySession(`${p}.${flipped}`, SECRET, PASSWORD, T0)).toBe(false);
    expect(await verifySession(`${p}.`, SECRET, PASSWORD, T0)).toBe(false);
    expect(await verifySession(`${p}.${s}.x`, SECRET, PASSWORD, T0)).toBe(false);
    expect(await verifySession(`${p}.not base64!`, SECRET, PASSWORD, T0)).toBe(false);
  });

  it("rejects empty, junk and oversized tokens", async () => {
    for (const bad of [undefined, null, "", "abc", ".", "a.b", "x".repeat(600)]) {
      expect(await verifySession(bad as string, SECRET, PASSWORD, T0)).toBe(false);
    }
  });

  it("is bound to both the secret and the password", async () => {
    const t = await signSession(SECRET, PASSWORD, T0);
    expect(await verifySession(t, SECRET + "x", PASSWORD, T0)).toBe(false);
    expect(await verifySession(t, SECRET, PASSWORD + "x", T0)).toBe(false);
  });

  it("rejects a token issued in the future", async () => {
    const t = await signSession(SECRET, PASSWORD, T0 + 3600 * 1000);
    expect(await verifySession(t, SECRET, PASSWORD, T0)).toBe(false);
  });

  it("safeEqual compares whole strings", async () => {
    expect(await safeEqual("abc", "abc")).toBe(true);
    expect(await safeEqual("abc", "abd")).toBe(false);
    expect(await safeEqual("abc", "abcd")).toBe(false);
    expect(await safeEqual("", "")).toBe(true);
    expect(await safeEqual("é", "e")).toBe(false);
  });
});

describe("login limiter", () => {
  it("blocks the 6th failure from one client, not others, and recovers after 10 minutes", () => {
    let now = T0;
    const l = new LoginLimiter(() => now);
    for (let i = 0; i < 5; i++) {
      expect(l.retryAfterS("1.1.1.1")).toBe(0);
      l.recordFailure("1.1.1.1");
      now += 1000;
    }
    const wait = l.retryAfterS("1.1.1.1");
    expect(wait).toBeGreaterThan(0);
    expect(wait).toBeLessThanOrEqual(600);
    expect(l.retryAfterS("2.2.2.2")).toBe(0);
    now += 600 * 1000;
    expect(l.retryAfterS("1.1.1.1")).toBe(0);
  });

  it("has a global backstop when addresses are forged", () => {
    let now = T0;
    const l = new LoginLimiter(() => now, 5, 10);
    for (let i = 0; i < 10; i++) l.recordFailure(`10.0.0.${i}`);
    expect(l.retryAfterS("10.9.9.9")).toBeGreaterThan(0);
    now += 601 * 1000;
    expect(l.retryAfterS("10.9.9.9")).toBe(0);
  });
});

describe("auth config (fail closed)", () => {
  const good = { NODE_ENV: "production", GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD, GLDB_SESSION_SECRET: SECRET };

  it("enforces with valid values", () => {
    expect(readAuthConfig(good)).toEqual({ mode: "enforce", username: USERNAME, password: PASSWORD, secret: SECRET });
  });

  it("production with nothing set is a setup error, not an open site", () => {
    const c = readAuthConfig({ NODE_ENV: "production" });
    expect(c.mode).toBe("error");
    expect(c.mode === "error" && c.problems).toHaveLength(3);
  });

  it("rejects short values and names the variable without echoing it", () => {
    const c = readAuthConfig({ ...good, GLDB_SITE_PASSWORD: "short-pw", GLDB_SESSION_SECRET: "tiny" });
    expect(c.mode).toBe("error");
    const text = c.mode === "error" ? c.problems.join(" ") : "";
    expect(text).toContain("GLDB_SITE_PASSWORD must be at least 12");
    expect(text).toContain("GLDB_SESSION_SECRET must be at least 32");
    expect(text).not.toContain("short-pw");
  });

  it("requires a username of at least 3 characters", () => {
    expect(readAuthConfig({ ...good, GLDB_SITE_USERNAME: "" }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SITE_USERNAME: "ab" }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SITE_USERNAME: "abc" }).mode).toBe("enforce");
  });

  it("whitespace-only does not count as set; 11 chars fails, 12 passes", () => {
    expect(readAuthConfig({ ...good, GLDB_SITE_PASSWORD: "a".repeat(11) }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SITE_PASSWORD: "a".repeat(12) }).mode).toBe("enforce");
    expect(readAuthConfig({ ...good, GLDB_SESSION_SECRET: "a".repeat(31) }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SESSION_SECRET: "a".repeat(32) }).mode).toBe("enforce");
  });

  it("development with both unset is open; with only one set it is an error", () => {
    expect(readAuthConfig({ NODE_ENV: "development" }).mode).toBe("open");
    expect(readAuthConfig({ NODE_ENV: "development", GLDB_SITE_PASSWORD: PASSWORD }).mode).toBe("error");
  });
});

describe("gate decision", () => {
  const cfg = readAuthConfig({ NODE_ENV: "production", GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD, GLDB_SESSION_SECRET: SECRET });

  it("lets only the public paths through without a cookie", async () => {
    for (const p of ["/welcome", "/login", "/api/auth/login", "/robots.txt"]) expect((await decide(cfg, p, undefined)).kind).toBe("allow");
    for (const p of ["/", "/upload", "/cases/c_1", "/api/cases", "/api/health", "/api/auth/login/x", "/loginx", "/mock/sample-pack.html", "/welcome/x", "/welcomex", "/api/welcome", "/Welcome"]) {
      expect((await decide(cfg, p, undefined)).kind, p).toBe("login");
    }
  });

  it("allows everything with a valid cookie and nothing with a bad one", async () => {
    const token = await signSession(SECRET, BINDING);
    expect((await decide(cfg, "/api/cases", token)).kind).toBe("allow");
    expect((await decide(cfg, "/api/cases", token + "x")).kind).toBe("login");
  });

  it("serves a setup error for every path when misconfigured, even the public ones", async () => {
    const bad = readAuthConfig({ NODE_ENV: "production" });
    for (const p of ["/", "/welcome", "/login", "/api/cases", "/robots.txt"]) expect((await decide(bad, p, undefined)).kind).toBe("setup-error");
  });

  it("builds a 503 page or JSON without leaking values", async () => {
    const html = setupErrorResponse(["GLDB_SITE_PASSWORD is not set."], false);
    expect(html.status).toBe(503);
    expect(html.headers.get("content-type")).toContain("text/html");
    expect(await html.text()).toContain("GLDB_SITE_PASSWORD is not set.");
    const json = setupErrorResponse(["x"], true);
    expect(json.status).toBe(503);
    expect((await json.json()).error.code).toBe("setup_error");
  });
});

describe("welcome page access", () => {
  const cfg = readAuthConfig({ NODE_ENV: "production", GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD, GLDB_SESSION_SECRET: SECRET });

  it("is public, with or without a trailing slash, and only that exact path", () => {
    expect(isPublicPath("/welcome")).toBe(true);
    expect(isPublicPath("/welcome/")).toBe(true);
    expect(isPublicPath("/welcome/x")).toBe(false);
    expect(isPublicPath("/welcome.html")).toBe(false);
  });

  it("sends the front door to /welcome and every other page to /login with the target", () => {
    expect(signInRedirect("/", "")).toBe("/welcome");
    expect(signInRedirect("/", "?q=1")).toBe("/welcome");
    expect(signInRedirect("/upload", "")).toBe("/login?next=%2Fupload");
    expect(signInRedirect("/cases/c_1", "?tab=x")).toBe("/login?next=%2Fcases%2Fc_1%3Ftab%3Dx");
    expect(signInRedirect("/effectiveness", "")).toBe("/login?next=%2Feffectiveness");
  });

  it("tells a signed-in visitor from an anonymous one without granting anything", async () => {
    const token = await signSession(SECRET, BINDING, T0);
    expect(await isSignedIn(cfg, token, T0 + 1000)).toBe(true);
    expect(await isSignedIn(cfg, token + "x", T0 + 1000)).toBe(false);
    expect(await isSignedIn(cfg, undefined, T0)).toBe(false);
    expect(await isSignedIn(cfg, token, T0 + 13 * 3600 * 1000)).toBe(false);
  });

  it("treats open mode (development, no gate) as signed in and a misconfigured server as signed out", async () => {
    expect(await isSignedIn(readAuthConfig({ NODE_ENV: "development" }), undefined)).toBe(true);
    expect(await isSignedIn(readAuthConfig({ NODE_ENV: "production" }), "anything")).toBe(false);
  });
});

describe("helpers", () => {
  it("isPublicPath ignores a trailing slash only", () => {
    expect(isPublicPath("/login/")).toBe(true);
    expect(isPublicPath("/login/x")).toBe(false);
  });

  it("takes the first X-Forwarded-For hop", () => {
    expect(clientIp(new Headers({ "x-forwarded-for": "198.51.100.7, 10.0.0.1" }))).toBe("198.51.100.7");
    expect(clientIp(new Headers())).toBe("local");
  });

  it("detects https from X-Forwarded-Proto, falling back to the URL", () => {
    expect(isHttps(new Headers({ "x-forwarded-proto": "https" }), "http://127.0.0.1:3000/")).toBe(true);
    expect(isHttps(new Headers({ "x-forwarded-proto": "http" }), "https://x/")).toBe(false);
    expect(isHttps(new Headers(), "http://127.0.0.1:3000/")).toBe(false);
  });

  it("builds a Lax httpOnly cookie, Secure only over https", () => {
    const plain = sessionCookie("tok", false, 43200);
    expect(plain).toContain("HttpOnly");
    expect(plain).toContain("SameSite=Lax");
    expect(plain).toContain("Max-Age=43200");
    expect(plain).not.toContain("Secure");
    expect(sessionCookie("tok", true, 43200)).toContain("Secure");
  });

  it("only accepts same-site relative redirect targets", () => {
    expect(safeNext("/cases/c_1?x=1")).toBe("/cases/c_1?x=1");
    for (const bad of ["//evil.example", "https://evil.example", "/\\evil", "javascript:alert(1)", "/api/cases", "/login", undefined, 5]) {
      expect(safeNext(bad)).toBe("/");
    }
  });
});
