// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";

const KEYS = ["GLDB_SITE_USERNAME", "GLDB_SITE_PASSWORD", "GLDB_SESSION_SECRET", "GLDB_SITE_USERS", "GLDB_MAX_FILES_PER_CASE", "GLDB_MAX_UPLOAD_MB", "GLDB_PROXY_SECRET"];
const saved: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const k of KEYS) {
    saved[k] = process.env[k];
    delete process.env[k]; // no site login configured: the gate is open, so only the size cap is under test
  }
  process.env.GLDB_MAX_FILES_PER_CASE = "1";
  process.env.GLDB_MAX_UPLOAD_MB = "1"; // 1 MB of files + 1 MB of form = 2 MB
});

afterEach(() => {
  vi.unstubAllGlobals();
  for (const [k, v] of Object.entries(saved)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
});

const ctx = { params: Promise.resolve({ path: ["cases"] }) };
const MB = 1024 * 1024;

function stream(chunks: number): ReadableStream<Uint8Array> {
  let sent = 0;
  return new ReadableStream({
    pull(controller) {
      if (sent++ < chunks) controller.enqueue(new Uint8Array(MB));
      else controller.close();
    },
  });
}

describe("API proxy: request body size", () => {
  it("answers 413 from Content-Length without contacting the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const req = new NextRequest("http://127.0.0.1:3000/api/cases", { method: "POST", headers: { "content-length": String(2 * MB + 1) }, body: "x" });
    const res = await POST(req, ctx);
    expect(res.status).toBe(413);
    expect((await res.json()).error.code).toBe("file_too_large");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops a body that did not declare its size once it passes the cap", async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const reader = (init.body as ReadableStream<Uint8Array>).getReader();
      for (;;) if ((await reader.read()).done) break; // the backend reading what it is sent
      return new Response("{}", { status: 202 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const req = new NextRequest("http://127.0.0.1:3000/api/cases", { method: "POST", body: stream(5), duplex: "half" } as unknown as ConstructorParameters<typeof NextRequest>[1]);
    const res = await POST(req, ctx);
    expect(res.status).toBe(413);
  });

  it("forwards a body within the cap", async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const reader = (init.body as ReadableStream<Uint8Array>).getReader();
      let n = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        n += value.byteLength;
      }
      return Response.json({ n }, { status: 202 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const req = new NextRequest("http://127.0.0.1:3000/api/cases", { method: "POST", body: stream(2), duplex: "half" } as unknown as ConstructorParameters<typeof NextRequest>[1]);
    const res = await POST(req, ctx);
    expect(res.status).toBe(202);
    expect((await res.json()).n).toBe(2 * MB);
  });
});
