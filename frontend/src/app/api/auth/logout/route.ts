import { readAuthConfig } from "@/lib/auth/config";
import { clearedCookie, isHttps, setupErrorResponse } from "@/lib/auth/gate";

export const dynamic = "force-dynamic";

/** Clears the session cookie and sends the browser to the login page. POST only (a GET link could be triggered cross-site). */
export async function POST(request: Request) {
  const cfg = readAuthConfig();
  if (cfg.mode === "error") return setupErrorResponse(cfg.problems, true);
  // A relative Location keeps the public host name the browser used (it is the only one we know it can reach).
  return new Response(null, { status: 303, headers: { Location: "/login", "Set-Cookie": clearedCookie(isHttps(request.headers, request.url)), "Cache-Control": "no-store" } });
}
