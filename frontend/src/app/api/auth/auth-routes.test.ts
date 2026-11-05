// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { POST as login } from "./login/route";
import { POST as logout } from "./logout/route";

const ENV = { GLDB_SITE_USERS: JSON.stringify({ officer: "correct horse battery", boss: "another long password" }), GLDB_SESSION_SECRET: "s".repeat(40) };
const saved: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const k of [...Object.keys(ENV), "GLDB_SITE_USERNAME", "GLDB_SITE_PASSWORD", "GLDB_LOGIN_MAX_FAILURES", "GLDB_LOGIN_GLOBAL_MAX_FAILURES"]) {
    saved[k] = process.env[k];
    delete process.env[k];
  }
  Object.assign(process.env, ENV);
  delete (globalThis as Record<string, unknown>).__gldbLoginLimiter; // a fresh limiter per test
});

afterEach(() => {
  for (const [k, v] of Object.entries(saved)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  delete (globalThis as Record<string, unknown>).__gldbLoginLimiter;
});

function attempt(username: string, password: string, headers: Record<string, string> = {}, ip = "198.51.100.7") {
  return login(
    new Request("http://127.0.0.1:3000/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json", "x-forwarded-for": ip, ...headers },
      body: JSON.stringify({ username, password }),
    }),
  );
}

async function statuses(responses: Promise<Response>[]): Promise<number[]> {
  return (await Promise.all(responses)).map((r) => r.status);
}

describe("login route: limiter", () => {
  it("lets at most 5 of 50 parallel wrong guesses get a 401 and refuses the rest with 429", async () => {
    const codes = await statuses(Array.from({ length: 50 }, (_, i) => attempt("officer", `wrong password ${i}`)));
    expect(codes.filter((c) => c === 401)).toHaveLength(5);
    expect(codes.filter((c) => c === 429)).toHaveLength(45);
    // and a correct password from that client is refused too until the window passes
    expect((await attempt("officer", "correct horse battery")).status).toBe(429);
  });

  it("does not let made-up usernames from rotating addresses lock everybody out", async () => {
    const codes = await statuses(Array.from({ length: 200 }, (_, i) => attempt(`ghost${i}`, "whatever it is", {}, `203.0.113.${i % 250}`)));
    expect(new Set(codes)).toEqual(new Set([401]));
    const ok = await attempt("officer", "correct horse battery", {}, "192.0.2.1");
    expect(ok.status).toBe(200);
    expect((await attempt("boss", "not the password", {}, "192.0.2.2")).status).toBe(401);
  });

  it("while the site-wide budget is used up a correct login still works and a wrong one is refused", async () => {
    process.env.GLDB_LOGIN_GLOBAL_MAX_FAILURES = "3";
    for (let i = 0; i < 3; i++) expect((await attempt("boss", "wrong password", {}, `203.0.113.${i}`)).status).toBe(401);
    expect((await attempt("boss", "wrong password", {}, "203.0.113.99")).status).toBe(429);
    expect((await attempt("ghost", "wrong password", {}, "203.0.113.98")).status).toBe(429); // no hint that the name is made up
    const ok = await attempt("boss", "another long password", {}, "203.0.113.97");
    expect(ok.status).toBe(200);
    expect(ok.headers.get("set-cookie")).toContain("gldb_session=");
  });

  it("a correct login does not use up the client's budget", async () => {
    for (let i = 0; i < 12; i++) expect((await attempt("officer", "correct horse battery")).status).toBe(200);
    expect((await attempt("officer", "wrong password")).status).toBe(401);
  });
});

describe("login and logout routes: cross-site requests", () => {
  it("refuses a login from another site, whatever the credentials", async () => {
    const r = await attempt("officer", "correct horse battery", { origin: "https://evil.example", "sec-fetch-site": "cross-site" });
    expect(r.status).toBe(403);
    expect(r.headers.get("set-cookie")).toBeNull();
    expect((await attempt("officer", "correct horse battery", { origin: "https://evil.example" })).status).toBe(403);
  });

  it("accepts a login from the same origin and from a client that sends neither header", async () => {
    expect((await attempt("officer", "correct horse battery", { origin: "http://127.0.0.1:3000", "sec-fetch-site": "same-origin" })).status).toBe(200);
    expect((await attempt("officer", "correct horse battery")).status).toBe(200);
  });

  it("refuses a logout from another site and allows one from the same origin", async () => {
    const make = (headers: Record<string, string>) => logout(new Request("http://127.0.0.1:3000/api/auth/logout", { method: "POST", headers }));
    expect((await make({ origin: "https://evil.example", "sec-fetch-site": "cross-site" })).status).toBe(403);
    expect((await make({ origin: "null" })).status).toBe(403);
    const ok = await make({ origin: "http://127.0.0.1:3000", "sec-fetch-site": "same-origin" });
    expect(ok.status).toBe(303);
    expect(ok.headers.get("set-cookie")).toContain("Max-Age=0");
    expect((await make({})).status).toBe(303);
  });
});

describe("login route: redirect target", () => {
  it("never echoes a target a browser would read as another site", async () => {
    const r = await login(
      new Request("http://127.0.0.1:3000/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: "officer", password: "correct horse battery", next: "/\t/evil.example" }),
      }),
    );
    expect((await r.json()).next).toBe("/");
  });
});
