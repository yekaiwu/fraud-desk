/**
 * Page gate (Next 16 "proxy", formerly middleware): every page needs a valid session cookie, except
 * the public ones listed in lib/auth/gate.ts (/welcome, /login). An anonymous visit to "/" lands on
 * /welcome; any other page redirects to /login?next=...
 *
 * /api/* is deliberately NOT matched here. When a proxy matches a request Next clones and buffers
 * its body (10 MB by default, silently truncating anything larger), which would corrupt a 25 MB
 * multipart upload. The /api route handlers call the same decide() instead and stream the body.
 */
import { NextResponse, type NextRequest } from "next/server";
import { readAuthConfig } from "@/lib/auth/config";
import { decide, SESSION_COOKIE, setupErrorResponse, signInRedirect } from "@/lib/auth/gate";

/** Public origin as the browser addressed it (Funnel sets Host and X-Forwarded-Proto); next's own request.url says "localhost". */
function origin(request: NextRequest): string {
  const host = request.headers.get("x-forwarded-host")?.split(",")[0].trim() || request.headers.get("host") || "";
  const proto = request.headers.get("x-forwarded-proto")?.split(",")[0].trim() === "https" ? "https" : "http";
  return /^[A-Za-z0-9.\-]+(:\d{1,5})?$/.test(host) ? `${proto}://${host}` : request.nextUrl.origin;
}

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const decision = await decide(readAuthConfig(), pathname, request.cookies.get(SESSION_COOKIE)?.value);
  if (decision.kind === "setup-error") return setupErrorResponse(decision.problems, false);
  if (decision.kind === "login") {
    return new Response(null, { status: 303, headers: { Location: `${origin(request)}${signInRedirect(pathname, search)}`, "Cache-Control": "no-store" } });
  }
  return NextResponse.next();
}

export const config = {
  // Skip /api/ (see above), the immutable build output and the favicon.
  matcher: ["/((?!api/|_next/static/|favicon.ico$).*)"],
};
