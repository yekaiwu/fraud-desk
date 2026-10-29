/**
 * Same-origin proxy: /api/<path> -> GLDB_BACKEND_URL/<path> (default http://127.0.0.1:8000).
 *
 * - Gated here (not in src/proxy.ts, see the note there): no valid session cookie -> 401 JSON.
 * - Streams request and response bodies, so a multi-file 25 MB upload is never buffered or truncated.
 * - Adds X-Gldb-Proxy-Secret (GLDB_PROXY_SECRET) and a single X-Forwarded-For (the first hop that
 *   reached us); drops the session cookie and any client-supplied forwarding/secret headers.
 * Routes next to this file (/api/auth/login, /api/auth/logout) win over the catch-all.
 */
import type { NextRequest } from "next/server";
import { backendUrl, readAuthConfig } from "@/lib/auth/config";
import { clientIp, decide, SESSION_COOKIE, setupErrorResponse, unauthorizedJson } from "@/lib/auth/gate";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const HOP_BY_HOP = ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"];
const DROP_REQUEST = [...HOP_BY_HOP, "host", "cookie", "expect", "accept-encoding", "x-gldb-proxy-secret", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "forwarded", "x-real-ip"];
const DROP_RESPONSE = [...HOP_BY_HOP, "content-encoding", "set-cookie"];

async function handle(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const cfg = readAuthConfig();
  const { path } = await ctx.params;
  const pathname = `/api/${path.join("/")}`;
  const decision = await decide(cfg, pathname, request.cookies.get(SESSION_COOKIE)?.value);
  if (decision.kind === "setup-error") return setupErrorResponse(decision.problems, true);
  if (decision.kind === "login") return unauthorizedJson();

  const target = `${backendUrl()}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  for (const h of DROP_REQUEST) headers.delete(h);
  headers.set("accept-encoding", "identity");
  headers.set("x-forwarded-for", clientIp(request.headers));
  const proxySecret = process.env.GLDB_PROXY_SECRET?.trim();
  if (proxySecret) headers.set("x-gldb-proxy-secret", proxySecret);

  const hasBody = request.method !== "GET" && request.method !== "HEAD" && request.body !== null;
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
      cache: "no-store",
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch (e) {
    const cause = (e as { cause?: { code?: string; message?: string } }).cause;
    console.error(`api proxy: ${request.method} ${pathname} failed:`, cause?.code ?? cause?.message ?? (e as Error).message);
    return Response.json({ error: { code: "network_error", message: "The analysis service is not reachable.", details: {} } }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }

  const out = new Headers(upstream.headers);
  for (const h of DROP_RESPONSE) out.delete(h);
  const location = out.get("location");
  if (location) out.set("location", location.startsWith("/") ? `/api${location}` : location.replace(backendUrl(), "/api"));
  return new Response(upstream.status === 204 || upstream.status === 304 ? null : upstream.body, { status: upstream.status, headers: out });
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE, handle as HEAD };
