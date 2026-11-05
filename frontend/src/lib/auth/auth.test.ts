// @vitest-environment node
import { describe, expect, it } from "vitest";
import { maxBodyBytes, readAuthConfig } from "./config";
import { clientIp, decide, isCrossSite, isHttps, isPublicPath, isSignedIn, safeNext, sessionCookie, sessionUser, setupErrorResponse, signInRedirect } from "./gate";
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

  it("reserve takes the place at once, so a burst cannot pass the check together", () => {
    const l = new LoginLimiter(() => T0);
    const slots = Array.from({ length: 50 }, () => l.reserve("1.1.1.1|boss", true));
    expect(slots.filter((s) => s.wait === 0)).toHaveLength(5);
    expect(slots.filter((s) => s.wait > 0 && !s.reserved)).toHaveLength(45);
  });

  it("refunds the place of an attempt that turned out to be right", () => {
    const l = new LoginLimiter(() => T0);
    for (let i = 0; i < 20; i++) l.reserve("1.1.1.1|boss", true).refund();
    expect(l.retryAfterS("1.1.1.1|boss")).toBe(0);
    for (let i = 0; i < 5; i++) l.reserve("1.1.1.1|boss", true);
    expect(l.reserve("1.1.1.1|boss", true).wait).toBeGreaterThan(0);
  });

  it("refunds the right place even when the clock moves on every read", () => {
    let t = T0;
    const l = new LoginLimiter(() => t++);
    for (let i = 0; i < 40; i++) {
      const slot = l.reserve("1.1.1.1|boss", true);
      expect(slot.reserved, `login ${i}`).toBe(true);
      expect(slot.wait, `login ${i}`).toBe(0);
      slot.refund();
    }
    expect(l.retryAfterS("1.1.1.1|boss")).toBe(0);
    // and the budget is still the same afterwards: 5 real failures, the 6th is refused
    for (let i = 0; i < 5; i++) expect(l.reserve("1.1.1.1|boss", true).reserved).toBe(true);
    expect(l.reserve("1.1.1.1|boss", true).reserved).toBe(false);
  });

  it("refunds exactly what it reserved under a random mix of clients, jumpy clocks and outcomes", () => {
    let seed = 12345;
    const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
    let t = T0;
    const l = new LoginLimiter(() => (t += Math.floor(rnd() * 3)), 5, 60);
    const failures = new Map<string, number>();
    for (let i = 0; i < 20000; i++) {
      const c = `c${Math.floor(rnd() * 8)}|boss`;
      const slot = l.reserve(c, true);
      expect(slot.reserved, `attempt ${i}`).toBe((failures.get(c) ?? 0) < 5);
      if (!slot.reserved) continue;
      if (rnd() < 0.7) slot.refund();
      else failures.set(c, (failures.get(c) ?? 0) + 1);
      if (rnd() < 0.01) {
        t += 11 * 60 * 1000; // the window passes: every budget starts over
        failures.clear();
      }
    }
  });

  it("counts only failures for accounts that exist toward the global budget", () => {
    const l = new LoginLimiter(() => T0, 5, 10);
    for (let i = 0; i < 100; i++) l.reserve(`10.0.0.${i}|ghost${i}`, false);
    expect(l.reserve("10.9.9.9|boss", true).wait).toBe(0);
    for (let i = 0; i < 10; i++) l.reserve(`10.0.1.${i}|boss`, true);
    const blocked = l.reserve("10.9.9.9|boss", true);
    expect(blocked.wait).toBeGreaterThan(0);
    expect(blocked.reserved).toBe(true); // the caller may still let a correct password in
    blocked.refund();
    expect(l.reserve("10.9.9.9|boss", true).wait).toBeGreaterThan(0);
  });

  it("still applies the client budget to an attempt while the global budget is used up", () => {
    const l = new LoginLimiter(() => T0, 2, 3);
    for (let i = 0; i < 3; i++) l.reserve(`10.0.1.${i}|boss`, true);
    l.reserve("9.9.9.9|boss", true);
    l.reserve("9.9.9.9|boss", true);
    expect(l.reserve("9.9.9.9|boss", true).reserved).toBe(false);
  });
});

describe("auth config (fail closed)", () => {
  const good = { NODE_ENV: "production", GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD, GLDB_SESSION_SECRET: SECRET };

  it("enforces with valid values", () => {
    expect(readAuthConfig(good)).toEqual({ mode: "enforce", username: USERNAME, password: PASSWORD, secret: SECRET, users: { [USERNAME]: PASSWORD } });
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
    expect(text).toContain("GLDB_SITE_PASSWORD must be at least 10");
    expect(text).toContain("GLDB_SESSION_SECRET must be at least 32");
    expect(text).not.toContain("short-pw");
  });

  it("requires a username of at least 3 characters", () => {
    expect(readAuthConfig({ ...good, GLDB_SITE_USERNAME: "" }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SITE_USERNAME: "ab" }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SITE_USERNAME: "abc" }).mode).toBe("enforce");
  });

  it("whitespace-only does not count as set; 9 chars fails, 10 passes", () => {
    expect(readAuthConfig({ ...good, GLDB_SITE_PASSWORD: "a".repeat(9) }).mode).toBe("error");
    expect(readAuthConfig({ ...good, GLDB_SITE_PASSWORD: "a".repeat(10) }).mode).toBe("enforce");
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
    expect(safeNext("/cases/c_1?a=b&c=d#top")).toBe("/cases/c_1?a=b&c=d#top");
    expect(safeNext("/")).toBe("/");
    for (const bad of ["//evil.example", "https://evil.example", "/\\evil", "javascript:alert(1)", "/api/cases", "/login", undefined, 5]) {
      expect(safeNext(bad)).toBe("/");
    }
  });

  it("refuses tab, CR, LF and other control or whitespace characters that a browser would strip", () => {
    for (const bad of ["/\t/evil.example", "/\r/evil.example", "/\n/evil.example", "/\t\t/evil.example", "/ /evil.example", "/\u0000/evil", "/a\x7fb", "/\\/evil.example", "/\\evil.example", "//evil.example", "/a/..//evil.example", "/./login", "/a/../api/cases"]) {
      expect(safeNext(bad), JSON.stringify(bad)).toBe("/");
    }
  });

  it("keeps an encoded variant harmless: never protocol-relative, never another origin", () => {
    for (const odd of ["/%09/evil.example", "/%0d%0a/evil.example", "/%2F%2Fevil.example", "/%5Cevil.example", "/%2e%2e//evil.example"]) {
      const out = safeNext(odd);
      expect(out.startsWith("//"), odd).toBe(false);
      expect(new URL(out, "http://x").origin, odd).toBe("http://x");
    }
  });

  it("recognises a request sent from another site, and lets non-browser clients through", () => {
    const url = "http://127.0.0.1:3000/api/auth/login";
    expect(isCrossSite(new Headers(), url)).toBe(false);
    expect(isCrossSite(new Headers({ "sec-fetch-site": "same-origin" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ "sec-fetch-site": "cross-site", origin: "https://evil.example" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "https://evil.example", host: "desk.example" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "null" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "https://desk.example", host: "desk.example" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "https://desk.example", host: "127.0.0.1:3000", "x-forwarded-host": "desk.example" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "https://desk.example.evil.example", host: "desk.example" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ "sec-fetch-site": "same-site", origin: "https://other.desk.example", host: "desk.example" }), url)).toBe(true);
  });

  it("compares host and port with the scheme's default port dropped", () => {
    const url = "http://127.0.0.1:3000/api/auth/login";
    // Funnel / a proxy that rewrites Host to name:443 without X-Forwarded-Host, browser sent no Sec-Fetch-Site
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net", host: "box.ts.net:443" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "https://Box.TS.net", host: "box.ts.net" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net", host: "127.0.0.1:3000", "x-forwarded-host": "box.ts.net:443" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net:443", host: "box.ts.net" }), url)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "http://box.example", host: "box.example:80" }), url)).toBe(false);
    // a default port of the other scheme is a different origin
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net", host: "box.ts.net:80" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "http://box.example", host: "box.example:443" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net", host: "box.ts.net:8443" }), url)).toBe(true);
    // localhost development, plain http with a real port
    const dev = "http://localhost:3000/api/auth/login";
    expect(isCrossSite(new Headers({ origin: "http://localhost:3000", host: "localhost:3000" }), dev)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "http://localhost:3000" }), dev)).toBe(false); // host header missing: the request URL
    expect(isCrossSite(new Headers({ origin: "http://localhost:4000", host: "localhost:3000" }), dev)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "http://localhost", host: "localhost:3000" }), dev)).toBe(true);
    // IPv6 literals
    const v6 = "http://[::1]:3000/api/auth/login";
    expect(isCrossSite(new Headers({ origin: "http://[::1]:3000", host: "[::1]:3000" }), v6)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "https://[2001:db8::1]", host: "[2001:db8::1]:443" }), v6)).toBe(false);
    expect(isCrossSite(new Headers({ origin: "http://[::1]:3000", host: "[::2]:3000" }), "http://[::2]:3000/x")).toBe(true);
    // no Host and no forwarded host at all: only the request URL is left to compare with
    expect(isCrossSite(new Headers({ origin: "https://evil.example" }), url)).toBe(true);
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net" }), "https://box.ts.net/api/auth/login")).toBe(false);
    // the first X-Forwarded-Host entry is the public name
    expect(isCrossSite(new Headers({ origin: "https://box.ts.net", "x-forwarded-host": "box.ts.net, 10.0.0.2" }), url)).toBe(false);
  });

  it("caps a request body at the backend's upload limits plus 1 MB", () => {
    expect(maxBodyBytes({})).toBe(40 * 25 * 1024 * 1024 + 1024 * 1024);
    expect(maxBodyBytes({ GLDB_MAX_FILES_PER_CASE: "2", GLDB_MAX_UPLOAD_MB: "3" })).toBe(7 * 1024 * 1024);
    expect(maxBodyBytes({ GLDB_MAX_FILES_PER_CASE: "x", GLDB_MAX_UPLOAD_MB: "-1" })).toBe(40 * 25 * 1024 * 1024 + 1024 * 1024);
  });
});


describe("personal accounts", () => {
  const PW_A = "alice has a long password";
  const PW_B = "bob has another long one";
  const env = { NODE_ENV: "production", GLDB_SESSION_SECRET: SECRET, GLDB_SITE_USERS: JSON.stringify({ Alice: PW_A, bob: PW_B }) };

  it("reads a JSON list of accounts, lower-cased, with no single account needed", () => {
    const cfg = readAuthConfig(env);
    expect(cfg.mode).toBe("enforce");
    if (cfg.mode === "enforce") expect(cfg.users).toEqual({ alice: PW_A, bob: PW_B });
  });

  it("keeps the single account as one more account", () => {
    const cfg = readAuthConfig({ ...env, GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD });
    if (cfg.mode === "enforce") expect(Object.keys(cfg.users).sort()).toEqual(["alice", "bob", USERNAME]);
    else throw new Error("expected enforce");
  });

  it("rejects bad JSON, short passwords, odd names and duplicates without echoing them", () => {
    for (const raw of ["not json", "[1]", JSON.stringify({ ab: PW_A }), JSON.stringify({ alice: "short" }), JSON.stringify({ "a b c": PW_A }), JSON.stringify({ Alice: PW_A, alice: PW_B })]) {
      const cfg = readAuthConfig({ ...env, GLDB_SITE_USERS: raw });
      expect(cfg.mode).toBe("error");
      expect(JSON.stringify(cfg)).not.toContain(PW_A);
    }
  });

  it("tells who a session belongs to, and only after the signature checks out", async () => {
    const cfg = readAuthConfig(env);
    const alice = await signSession(SECRET, `alice\u0000${PW_A}`, T0, undefined, "alice");
    expect(await sessionUser(cfg, alice, T0 + 1000)).toBe("alice");
    // bob cannot present a token that says alice but was signed with bob's password
    const forged = await signSession(SECRET, `alice\u0000${PW_B}`, T0, undefined, "alice");
    expect(await sessionUser(cfg, forged, T0 + 1000)).toBeNull();
    // an unknown name, an expired token and no cookie are all nobody
    const ghost = await signSession(SECRET, `ghost\u0000${PW_A}`, T0, undefined, "ghost");
    expect(await sessionUser(cfg, ghost, T0 + 1000)).toBeNull();
    expect(await sessionUser(cfg, alice, T0 + 13 * 3600 * 1000)).toBeNull();
    expect(await sessionUser(cfg, undefined, T0)).toBeNull();
  });

  it("lets every signed-in account through the gate and nobody else", async () => {
    const cfg = readAuthConfig(env);
    const bob = await signSession(SECRET, `bob\u0000${PW_B}`, T0, undefined, "bob");
    expect((await decide(cfg, "/cases", bob, T0 + 1000)).kind).toBe("allow");
    expect((await decide(cfg, "/cases", "junk", T0 + 1000)).kind).toBe("login");
  });

  it("still honours a token from before personal accounts (no name in it) for the first account", async () => {
    const cfg = readAuthConfig({ NODE_ENV: "production", GLDB_SESSION_SECRET: SECRET, GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD });
    const legacy = await signSession(SECRET, BINDING, T0);
    expect(await sessionUser(cfg, legacy, T0 + 1000)).toBe(USERNAME);
  });
});

describe("two accounts, officer and boss, and the password length rule", () => {
  // made-up passwords: the real ones never appear in this repository
  const OFFICER_PW = "officer test password";
  const BOSS_PW = "boss test password!";
  const live = { NODE_ENV: "production", GLDB_SESSION_SECRET: SECRET, GLDB_SITE_USERNAME: "officer", GLDB_SITE_PASSWORD: OFFICER_PW, GLDB_SITE_USERS: JSON.stringify({ boss: BOSS_PW }) };

  it("officer from the single login plus boss from the list are two accounts", async () => {
    const cfg = readAuthConfig(live);
    expect(cfg.mode).toBe("enforce");
    if (cfg.mode !== "enforce") return;
    expect(Object.keys(cfg.users).sort()).toEqual(["boss", "officer"]);
    const officer = await signSession(SECRET, `officer\u0000${OFFICER_PW}`, T0, undefined, "officer");
    const boss = await signSession(SECRET, `boss\u0000${BOSS_PW}`, T0, undefined, "boss");
    expect(await sessionUser(cfg, officer, T0 + 1000)).toBe("officer");
    expect(await sessionUser(cfg, boss, T0 + 1000)).toBe("boss");
  });

  it("a password of 9 characters is a setup error that stops the site, for either account; 10 is fine", () => {
    const eleven = "ninechars"; // 9 characters
    const twelve = "tenchars10"; // 10 characters
    for (const cfg of [readAuthConfig({ ...live, GLDB_SITE_PASSWORD: eleven }), readAuthConfig({ ...live, GLDB_SITE_USERS: JSON.stringify({ boss: eleven }) })]) {
      expect(cfg.mode).toBe("error");
      expect(JSON.stringify(cfg)).not.toContain(eleven);
    }
    expect(readAuthConfig({ ...live, GLDB_SITE_PASSWORD: twelve, GLDB_SITE_USERS: JSON.stringify({ boss: twelve }) }).mode).toBe("enforce");
  });
});
