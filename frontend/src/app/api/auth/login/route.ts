import { readAuthConfig, SESSION_MAX_AGE_S, sessionBinding } from "@/lib/auth/config";
import { clientIp, isHttps, safeNext, sessionCookie, setupErrorResponse } from "@/lib/auth/gate";
import { loginLimiter } from "@/lib/auth/ratelimit";
import { safeEqual, signSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };
const err = (status: number, code: string, message: string, extra: Record<string, string> = {}) =>
  Response.json({ error: { code, message, details: {} } }, { status, headers: { ...NO_STORE, ...extra } });

/** POST {username, password, next?} as JSON or as a form. 200 sets the session cookie; 401 wrong username or password; 429 too many failures. */
export async function POST(request: Request) {
  const cfg = readAuthConfig();
  if (cfg.mode === "error") return setupErrorResponse(cfg.problems, true);
  if (cfg.mode === "open") return err(404, "not_found", "Sign-in is not enabled on this server.");

  let username = "";
  let password = "";
  let next: unknown;
  try {
    if ((request.headers.get("content-type") ?? "").includes("application/json")) {
      const body = (await request.json()) as { username?: unknown; password?: unknown; next?: unknown };
      username = typeof body.username === "string" ? body.username : "";
      password = typeof body.password === "string" ? body.password : "";
      next = body.next;
    } else {
      const form = await request.formData();
      username = String(form.get("username") ?? "");
      password = String(form.get("password") ?? "");
      next = form.get("next");
    }
  } catch {
    return err(400, "bad_request", "Send the username and password as JSON or as a form.");
  }
  if (username.length > 256 || password.length > 1024) return err(400, "bad_request", "Username or password too long.");

  const client = clientIp(request.headers);
  const limiter = loginLimiter();
  const wait = limiter.retryAfterS(client);
  if (wait > 0) return err(429, "rate_limited", `Too many failed attempts. Try again in ${Math.ceil(wait / 60)} minute(s).`, { "Retry-After": String(wait) });

  // Both comparisons always run, so the response time does not reveal whether the username was right.
  const [userOk, passOk] = await Promise.all([safeEqual(username.trim().toLowerCase(), cfg.username.toLowerCase()), safeEqual(password, cfg.password)]);
  if (!(userOk && passOk)) {
    limiter.recordFailure(client);
    return err(401, "invalid_credentials", "Wrong username or password.");
  }
  const token = await signSession(cfg.secret, sessionBinding(cfg));
  return Response.json({ ok: true, next: safeNext(next) }, { headers: { ...NO_STORE, "Set-Cookie": sessionCookie(token, isHttps(request.headers, request.url), SESSION_MAX_AGE_S) } });
}
