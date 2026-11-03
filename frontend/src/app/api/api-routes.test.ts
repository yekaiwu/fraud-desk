// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { signSession } from "@/lib/auth/session";
import { loginLimiter, LoginLimiter } from "@/lib/auth/ratelimit";
import { POST as login } from "./auth/login/route";
import { POST as logout } from "./auth/logout/route";
import { GET, POST } from "./[...path]/route";
import { proxy } from "@/proxy";

const USERNAME = "officer";
const PASSWORD = "correct horse battery";
const BINDING = `${USERNAME}\u0000${PASSWORD}`;
const SECRET = "x".repeat(40);

function env(vars: Record<string, string | undefined>) {
  for (const [k, v] of Object.entries(vars)) {
    if (v === undefined) vi.stubEnv(k, "");
    else vi.stubEnv(k, v);
  }
}

beforeEach(() => {
  env({ NODE_ENV: "production", GLDB_SITE_USERNAME: USERNAME, GLDB_SITE_PASSWORD: PASSWORD, GLDB_SESSION_SECRET: SECRET, GLDB_PROXY_SECRET: "proxy-secret-proxy-secret", GLDB_BACKEND_URL: "http://127.0.0.1:9" });
  (globalThis as Record<string, unknown>).__gldbLoginLimiter = new LoginLimiter();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const post = (body: unknown, headers: Record<string, string> = {}) =>
  new Request("http://127.0.0.1:3000/api/auth/login", { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });

describe("POST /api/auth/login", () => {
  it("wrong password or wrong username is 401 and sets no cookie", async () => {
    const r = await login(post({ username: USERNAME, password: "nope" }));
    expect(r.status).toBe(401);
    expect(r.headers.get("set-cookie")).toBeNull();
    expect((await r.json()).error.message).toBe("Wrong username or password.");
    const wrongUser = await login(post({ username: "intruder", password: PASSWORD }, { "x-forwarded-for": "203.0.113.9" }));
    expect(wrongUser.status).toBe(401);
    expect(wrongUser.headers.get("set-cookie")).toBeNull();
  });

  it("the username is not case-sensitive and surrounding spaces are ignored", async () => {
    expect((await login(post({ username: "  Officer ", password: PASSWORD }, { "x-forwarded-for": "203.0.113.10" }))).status).toBe(200);
  });

  it("correct password sets a signed httpOnly Lax cookie; Secure behind https", async () => {
    const r = await login(post({ username: USERNAME, password: PASSWORD, next: "/upload" }, { "x-forwarded-proto": "https" }));
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ ok: true, next: "/upload" });
    const c = r.headers.get("set-cookie")!;
    expect(c).toMatch(/^gldb_session=[\w-]+\.[\w-]+;/);
    expect(c).toContain("HttpOnly");
    expect(c).toContain("SameSite=Lax");
    expect(c).toContain("Secure");
    expect(c).toContain("Max-Age=43200");
    const plain = await login(post({ username: USERNAME, password: PASSWORD }));
    expect(plain.headers.get("set-cookie")).not.toContain("Secure");
  });

  it("does not follow an off-site next target", async () => {
    const r = await login(post({ username: USERNAME, password: PASSWORD, next: "//evil.example" }));
    expect((await r.json()).next).toBe("/");
  });

  it("the 6th failure from one address is 429 with Retry-After, even for the right password", async () => {
    const h = { "x-forwarded-for": "203.0.113.5" };
    for (let i = 0; i < 5; i++) expect((await login(post({ username: USERNAME, password: "bad" + i }, h))).status).toBe(401);
    const r = await login(post({ username: USERNAME, password: "bad" }, h));
    expect(r.status).toBe(429);
    expect(Number(r.headers.get("retry-after"))).toBeGreaterThan(0);
    expect((await login(post({ username: USERNAME, password: PASSWORD }, h))).status).toBe(429);
    expect((await login(post({ username: USERNAME, password: PASSWORD }, { "x-forwarded-for": "203.0.113.6" }))).status).toBe(200);
    expect(loginLimiter()).toBeInstanceOf(LoginLimiter);
  });

  it("accepts a form post", async () => {
    const body = new URLSearchParams({ username: USERNAME, password: PASSWORD });
    const r = await login(new Request("http://x/api/auth/login", { method: "POST", body }));
    expect(r.status).toBe(200);
  });

  it("is a 503 setup error when the server is not configured", async () => {
    env({ GLDB_SITE_USERNAME: "", GLDB_SITE_PASSWORD: "", GLDB_SESSION_SECRET: "" });
    const r = await login(post({ username: USERNAME, password: PASSWORD }));
    expect(r.status).toBe(503);
  });
});

describe("POST /api/auth/logout", () => {
  it("clears the cookie and redirects to /login", async () => {
    const r = await logout(new Request("http://x/api/auth/logout", { method: "POST" }));
    expect(r.status).toBe(303);
    expect(r.headers.get("location")).toBe("/login");
    expect(r.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});

describe("page proxy", () => {
  const page = (path: string, cookie?: string) =>
    new NextRequest(`http://127.0.0.1:3000${path}`, { headers: cookie ? { cookie: `gldb_session=${cookie}` } : {} });

  it("redirects an anonymous visitor to /login with the target", async () => {
    const r = await proxy(page("/cases/c_1?tab=x"));
    expect(r.status).toBe(303);
    expect(r.headers.get("location")).toBe("http://localhost:3000/login?next=%2Fcases%2Fc_1%3Ftab%3Dx");
  });

  it("redirects to the public host and scheme the browser used", async () => {
    const r = await proxy(new NextRequest("http://localhost:3000/upload", { headers: { host: "app.example.com", "x-forwarded-proto": "https" } }));
    expect(r.headers.get("location")).toBe("https://app.example.com/login?next=%2Fupload");
    const evil = await proxy(new NextRequest("http://localhost:3000/upload", { headers: { host: "a.b/evil" } }));
    expect(evil.headers.get("location")).toBe("http://localhost:3000/login?next=%2Fupload");
  });

  it("sends an anonymous visit to the front door to the public product tour, not to /login", async () => {
    const r = await proxy(page("/"));
    expect(r.status).toBe(303);
    expect(r.headers.get("location")).toBe("http://localhost:3000/welcome");
    expect((await proxy(page("/?x=1"))).headers.get("location")).toBe("http://localhost:3000/welcome");
    const behindFunnel = await proxy(new NextRequest("http://localhost:3000/", { headers: { host: "app.example.com", "x-forwarded-proto": "https" } }));
    expect(behindFunnel.headers.get("location")).toBe("https://app.example.com/welcome");
  });

  it("serves /welcome without a session and still sends every other page to /login", async () => {
    expect((await proxy(page("/welcome"))).status).toBe(200);
    expect((await proxy(page("/welcome"))).headers.get("location")).toBeNull();
    for (const p of ["/upload", "/effectiveness", "/rules", "/cases/c_1", "/welcome/x", "/kit"]) {
      const r = await proxy(page(p));
      expect(r.status, p).toBe(303);
      expect(r.headers.get("location"), p).toBe(`http://localhost:3000/login?next=${encodeURIComponent(p)}`);
    }
  });

  it("serves /login and passes a signed-in visitor", async () => {
    expect((await proxy(page("/login"))).status).toBe(200);
    const token = await signSession(SECRET, BINDING);
    const r = await proxy(page("/", token));
    expect(r.headers.get("location")).toBeNull();
  });

  it("answers 503 for everything when unconfigured", async () => {
    env({ GLDB_SITE_USERNAME: "", GLDB_SITE_PASSWORD: "", GLDB_SESSION_SECRET: "" });
    for (const p of ["/", "/login", "/welcome"]) expect((await proxy(page(p))).status).toBe(503);
  });
});

describe("/api catch-all proxy", () => {
  const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
  const api = (path: string, init: { method?: string; body?: string; headers?: Record<string, string>; cookie?: string } = {}) => {
    const { cookie, headers, ...rest } = init;
    return new NextRequest(`http://127.0.0.1:3000/api/${path}`, { ...rest, headers: { ...headers, ...(cookie ? { cookie: `gldb_session=${cookie}` } : {}) } });
  };

  it("401s without a session and never calls the backend", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const r = await GET(api("cases"), ctx("cases"));
    expect(r.status).toBe(401);
    expect((await r.json()).error.code).toBe("unauthorized");
    expect(f).not.toHaveBeenCalled();
  });

  it("503s when unconfigured", async () => {
    env({ GLDB_SITE_USERNAME: "", GLDB_SITE_PASSWORD: "", GLDB_SESSION_SECRET: "" });
    expect((await GET(api("cases"), ctx("cases"))).status).toBe(503);
  });

  it("forwards with the proxy secret, one X-Forwarded-For, no cookie, and streams the body", async () => {
    const token = await signSession(SECRET, BINDING);
    let seen: { url: string; init: RequestInit & { duplex?: string } } | undefined;
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen = { url, init };
      return new Response('{"ok":true}', { status: 202, headers: { "content-type": "application/json", location: "/jobs/j_1", "set-cookie": "x=1" } });
    });
    const r = await POST(
      api("cases?a=1&b=2", { method: "POST", body: "payload", cookie: token, headers: { "x-forwarded-for": "198.51.100.7, 10.0.0.1", "x-gldb-proxy-secret": "forged", "expect": "100-continue", "content-type": "text/plain" } }),
      ctx("cases"),
    );
    expect(r.status).toBe(202);
    expect(r.headers.get("location")).toBe("/api/jobs/j_1");
    expect(r.headers.get("set-cookie")).toBeNull();
    expect(seen!.url).toBe("http://127.0.0.1:9/cases?a=1&b=2");
    const h = seen!.init.headers as Headers;
    expect(h.get("x-gldb-proxy-secret")).toBe("proxy-secret-proxy-secret");
    expect(h.get("x-forwarded-for")).toBe("198.51.100.7");
    expect(h.get("cookie")).toBeNull();
    expect(h.get("expect")).toBeNull(); // undici rejects Expect: 100-continue (curl sends it for big uploads)
    expect(h.get("content-type")).toBe("text/plain");
    expect(seen!.init.duplex).toBe("half");
    expect(seen!.init.body).toBeInstanceOf(ReadableStream);
  });

  it("maps an unreachable backend to a 502 error envelope", async () => {
    const token = await signSession(SECRET, BINDING);
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    const r = await GET(api("cases", { cookie: token }), ctx("cases"));
    expect(r.status).toBe(502);
    expect((await r.json()).error.code).toBe("network_error");
  });
});

describe("two accounts, officer and boss (the shape of the demo)", () => {
  // made-up values; the real passwords never appear in this repository
  const OFFICER_PW = "officer test password";
  const BOSS_PW = "boss test password!";
  const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });

  beforeEach(() => {
    // the single login is officer, one more account comes from GLDB_SITE_USERS
    env({ GLDB_SITE_USERNAME: "officer", GLDB_SITE_PASSWORD: OFFICER_PW, GLDB_SITE_USERS: JSON.stringify({ boss: BOSS_PW }) });
  });

  const cookieOf = async (username: string, password: string, ip: string) => {
    const r = await login(post({ username, password }, { "x-forwarded-for": ip }));
    expect(r.status).toBe(200);
    return /gldb_session=([^;]+)/.exec(r.headers.get("set-cookie")!)![1];
  };
  const reviewerSeenByTheBackend = async (cookie: string, forged?: string) => {
    let seen: Headers | undefined;
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      seen = init.headers as Headers;
      return new Response('{"reviewer":null}', { status: 200, headers: { "content-type": "application/json" } });
    });
    const req = new NextRequest("http://127.0.0.1:3000/api/me", { headers: { cookie: `gldb_session=${cookie}`, ...(forged ? { "x-gldb-reviewer": forged } : {}) } });
    expect((await GET(req, ctx("me"))).status).toBe(200);
    return seen!.get("x-gldb-reviewer");
  };

  it("both accounts can sign in, and a wrong password never can", async () => {
    await cookieOf("officer", OFFICER_PW, "203.0.113.21");
    await cookieOf("boss", BOSS_PW, "203.0.113.22");
    await cookieOf("  BOSS ", BOSS_PW, "203.0.113.23"); // the name is not case-sensitive
    expect((await login(post({ username: "boss", password: OFFICER_PW }, { "x-forwarded-for": "203.0.113.24" }))).status).toBe(401);
    expect((await login(post({ username: "officer", password: "wrong" }, { "x-forwarded-for": "203.0.113.25" }))).status).toBe(401);
  });

  it("each session tells the backend who is signed in, so officer and boss are two different reviewers", async () => {
    const officer = await cookieOf("officer", OFFICER_PW, "203.0.113.31");
    const boss = await cookieOf("boss", BOSS_PW, "203.0.113.32");
    expect(await reviewerSeenByTheBackend(officer)).toBe("officer");
    expect(await reviewerSeenByTheBackend(boss)).toBe("boss");
  });

  it("a reviewer name sent by the browser is thrown away: officer cannot pose as boss", async () => {
    const officer = await cookieOf("officer", OFFICER_PW, "203.0.113.41");
    expect(await reviewerSeenByTheBackend(officer, "boss")).toBe("officer");
  });

  it("a session cookie signed for one account's name with the other's password does not work", async () => {
    const forged = await signSession(SECRET, `boss\u0000${OFFICER_PW}`, Date.now(), undefined, "boss");
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const r = await GET(new NextRequest("http://127.0.0.1:3000/api/me", { headers: { cookie: `gldb_session=${forged}` } }), ctx("me"));
    expect(r.status).toBe(401);
    expect(f).not.toHaveBeenCalled();
  });
});
