/**
 * The access decision, shared by the page proxy (src/proxy.ts) and the /api route handlers so
 * there is exactly one definition of "who may see what".
 *
 * Public: /welcome (the product tour: static copy and fictitious sample data, no API calls), /login,
 * /api/auth/login, /api/auth/logout, /robots.txt and the static build output (the proxy matcher
 * skips /_next/static and the favicon). Everything else needs a valid session cookie.
 */
import { SESSION_COOKIE, sessionBinding, type AuthConfig } from "./config";
import { claimedSubject, verifySession } from "./session";

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

/**
 * Who this cookie belongs to, or null (not signed in, or open mode where nobody is). A token carrying an
 * account name is verified against that account's password; an older token without one belongs to the
 * first account. The name is trusted only after the signature checks out.
 */
export async function sessionUser(cfg: AuthConfig, cookie: string | undefined | null, nowMs: number = Date.now()): Promise<string | null> {
  if (cfg.mode !== "enforce") return null;
  const sub = claimedSubject(cookie);
  if (sub) {
    const password = cfg.users[sub];
    if (password === undefined) return null;
    return (await verifySession(cookie, cfg.secret, `${sub}\u0000${password}`, nowMs)) ? sub : null;
  }
  return (await verifySession(cookie, cfg.secret, sessionBinding(cfg), nowMs)) ? cfg.username.trim().toLowerCase() : null;
}

export async function decide(cfg: AuthConfig, pathname: string, cookie: string | undefined, nowMs: number = Date.now()): Promise<GateDecision> {
  if (cfg.mode === "error") return { kind: "setup-error", problems: cfg.problems };
  if (cfg.mode === "open") return { kind: "allow" };
  if (isPublicPath(pathname)) return { kind: "allow" };
  return (await sessionUser(cfg, cookie, nowMs)) ? { kind: "allow" } : { kind: "login" };
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
  return (await sessionUser(cfg, cookie, nowMs)) !== null;
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

/**
 * Only same-site relative paths (never //host, never /api/...) are valid post-login targets. Browsers
 * drop tab, CR and LF inside a URL, so "/\t/evil.example" would become "//evil.example": any control or
 * whitespace character is refused, and the result is re-read as a URL against a dummy origin so only a
 * path on that origin survives.
 */
export function safeNext(next: unknown): string {
  if (typeof next !== "string" || !next.startsWith("/") || next.startsWith("//") || next.includes("\\") || /[\x00-\x20\x7f]/.test(next)) return "/";
  let url: URL;
  try {
    url = new URL(next, "http://x");
  } catch {
    return "/";
  }
  if (url.origin !== "http://x") return "/";
  const out = url.pathname + url.search + url.hash;
  if (!out.startsWith("/") || out.startsWith("//") || out.startsWith("/api") || out.startsWith("/login")) return "/";
  return out;
}

/**
 * Whether a state-changing request was sent by a page on another site (login / logout CSRF). A browser
 * always sends Sec-Fetch-Site and, on a POST, Origin; a command-line client or a test sends neither,
 * and is let through (it has no cookie of a victim to ride on). "same-origin" is the browser's own
 * word, so it wins; otherwise the Origin host must be the host the request was addressed to (the
 * forwarded one first: behind the tunnel that is the public name). Host and port are compared with the
 * scheme's default port dropped, so a proxy that rewrites ``Host`` to ``box.ts.net:443`` still matches
 * ``Origin: https://box.ts.net``.
 */
export function isCrossSite(headers: Headers, requestUrl: string): boolean {
  const fetchSite = headers.get("sec-fetch-site")?.trim().toLowerCase();
  if (fetchSite === "same-origin") return false;
  if (fetchSite === "cross-site") return true;
  const origin = headers.get("origin");
  if (origin === null) return false;
  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return true; // "null" and anything unparseable
  }
  const want = hostKey(parsed.host, parsed.protocol);
  const hosts = [headers.get("x-forwarded-host")?.split(",")[0], headers.get("host"), new URL(requestUrl).host];
  return !hosts.some((h) => h && hostKey(h, parsed.protocol) === want);
}

/** ``host[:port]`` lower-cased, with the default port of the scheme ("http:" 80, "https:" 443) dropped. */
function hostKey(host: string, protocol: string): string {
  let h = host.trim().toLowerCase();
  const dflt = protocol === "https:" ? ":443" : protocol === "http:" ? ":80" : "";
  if (dflt && h.endsWith(dflt)) h = h.slice(0, -dflt.length);
  return h;
}

export function crossSiteJson(): Response {
  return Response.json({ error: { code: "forbidden", message: "This request came from another site.", details: {} } }, { status: 403, headers: { "Cache-Control": "no-store" } });
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
