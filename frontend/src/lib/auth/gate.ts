/**
 * The access decision, shared by the page proxy (src/proxy.ts) and the /api route handlers so
 * there is exactly one definition of "who may see what".
 *
 * Public: /welcome (the product tour: static copy and fictitious sample data, no API calls), /login,
 * /api/auth/login, /api/auth/logout, /robots.txt and the static build output (the proxy matcher
 * skips /_next/static and the favicon). Everything else needs a valid session cookie.
 */
import { SESSION_COOKIE, sessionBinding, type AuthConfig } from "./config";
import { verifySession } from "./session";

export const WELCOME_PATH = "/welcome";

export const PUBLIC_PATHS = new Set([WELCOME_PATH, "/login", "/api/auth/login", "/api/auth/logout", "/robots.txt"]);

export type GateDecision =
  | { kind: "allow" }
  | { kind: "setup-error"; problems: string[] }
  | { kind: "login" } // not signed in
  ;

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname.replace(/\/+$/, "") || "/");
}

export async function decide(cfg: AuthConfig, pathname: string, cookie: string | undefined, nowMs: number = Date.now()): Promise<GateDecision> {
  if (cfg.mode === "error") return { kind: "setup-error", problems: cfg.problems };
  if (cfg.mode === "open") return { kind: "allow" };
  if (isPublicPath(pathname)) return { kind: "allow" };
  return (await verifySession(cookie, cfg.secret, sessionBinding(cfg), nowMs)) ? { kind: "allow" } : { kind: "login" };
}

/**
 * Where an unauthenticated page request is sent. The front door ("/") goes to the public product tour;
 * every other page goes to the sign-in form and remembers where the visitor was heading.
 */
export function signInRedirect(pathname: string, search: string): string {
  if ((pathname.replace(/\/+$/, "") || "/") === "/") return WELCOME_PATH;
  return `/login?next=${encodeURIComponent(pathname + search)}`;
}

/**
 * Whether this cookie is a valid session, for public pages that tailor a button ("Open the worklist").
 * It grants nothing: public pages show the same content to everyone. Open mode (local development,
 * no gate) counts as signed in because every page is open there.
 */
export async function isSignedIn(cfg: AuthConfig, cookie: string | undefined, nowMs: number = Date.now()): Promise<boolean> {
  if (cfg.mode === "open") return true;
  if (cfg.mode === "error") return false;
  return verifySession(cookie, cfg.secret, sessionBinding(cfg), nowMs);
}

export { SESSION_COOKIE };

/** Client address for rate limiting: first hop of X-Forwarded-For (set by Tailscale Funnel). */
export function clientIp(headers: Headers): string {
  const xff = headers.get("x-forwarded-for");
  const first = xff ? xff.split(",")[0].trim() : "";
  return (first || "local").slice(0, 64);
}

export function isHttps(headers: Headers, url: string): boolean {
  const proto = headers.get("x-forwarded-proto")?.split(",")[0].trim().toLowerCase();
  return proto ? proto === "https" : url.startsWith("https:");
}

/** Only same-site relative paths (never //host, never /api/...) are valid post-login targets. */
export function safeNext(next: unknown): string {
  if (typeof next !== "string" || !next.startsWith("/") || next.startsWith("//") || next.includes("\\") || next.startsWith("/api") || next.startsWith("/login")) return "/";
  return next;
}

export function sessionCookie(token: string, secure: boolean, maxAgeS: number): string {
  return [`${SESSION_COOKIE}=${token}`, "Path=/", `Max-Age=${maxAgeS}`, "HttpOnly", "SameSite=Lax", ...(secure ? ["Secure"] : [])].join("; ");
}

export function clearedCookie(secure: boolean): string {
  return sessionCookie("", secure, 0);
}

export function htmlEscape(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** What every request gets when the server is not configured: an explanation and nothing else. */
export function setupErrorResponse(problems: string[], asJson: boolean): Response {
  const headers = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "X-Content-Type-Options": "nosniff" };
  if (asJson) {
    return Response.json({ error: { code: "setup_error", message: "The server is not configured.", details: { problems } } }, { status: 503, headers });
  }
  const items = problems.map((p) => `<li>${htmlEscape(p)}</li>`).join("");
  const body = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>Setup required</title>
<style>body{font:16px/1.5 system-ui,sans-serif;background:#f4f1ea;color:#101828;margin:0;padding:24px}main{max-width:36rem;margin:10vh auto;background:#fff;border:1px solid #d9d3c4;border-radius:6px;padding:24px}h1{font-size:1.25rem;margin:0 0 12px}code{background:#faf8f3;padding:1px 4px;border-radius:3px}</style></head>
<body><main><h1>Setup required</h1><p>This server refuses to serve anything until access is configured.</p><ul>${items}</ul>
<p>Set the missing variables in <code>deploy/.env.production</code> and restart the frontend. See <code>docs/deploy.md</code>.</p></main></body></html>`;
  return new Response(body, { status: 503, headers: { ...headers, "Content-Type": "text/html; charset=utf-8" } });
}

export function unauthorizedJson(): Response {
  return Response.json({ error: { code: "unauthorized", message: "Sign in required.", details: {} } }, { status: 401, headers: { "Cache-Control": "no-store" } });
}
