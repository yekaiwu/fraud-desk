/**
 * Signed session cookie and constant-time comparison, on Web Crypto only so the same code runs in
 * the proxy (middleware) and in route handlers.
 *
 * Token = base64url(JSON {iat, exp}) + "." + base64url(HMAC-SHA256(key, first part)).
 * The key is derived from GLDB_SESSION_SECRET and the account (username and password), so rotating any of them
 * signs everybody out.
 */
import { SESSION_MAX_AGE_S } from "./config";

const enc = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromB64url(s: string): Uint8Array | null {
  if (!/^[A-Za-z0-9_-]*$/.test(s)) return null;
  try {
    const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
}

async function hmacKey(secret: string, password: string, usage: "sign" | "verify"): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", enc.encode(`gldb-session-v1\0${secret}\0${password}`), { name: "HMAC", hash: "SHA-256" }, false, [usage]);
}

export async function signSession(secret: string, password: string, nowMs: number = Date.now(), maxAgeS: number = SESSION_MAX_AGE_S): Promise<string> {
  const iat = Math.floor(nowMs / 1000);
  const payload = b64url(enc.encode(JSON.stringify({ iat, exp: iat + maxAgeS })));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret, password, "sign"), enc.encode(payload));
  return `${payload}.${b64url(new Uint8Array(sig))}`;
}

/** True only for an untampered, unexpired token signed with this secret and password. */
export async function verifySession(token: string | undefined | null, secret: string, password: string, nowMs: number = Date.now()): Promise<boolean> {
  if (!token || token.length > 512) return false;
  const parts = token.split(".");
  if (parts.length !== 2) return false;
  const [payload, sig] = parts;
  const sigBytes = fromB64url(sig);
  if (!sigBytes) return false;
  // crypto.subtle.verify compares in constant time.
  const ok = await crypto.subtle.verify("HMAC", await hmacKey(secret, password, "verify"), sigBytes as BufferSource, enc.encode(payload));
  if (!ok) return false;
  const raw = fromB64url(payload);
  if (!raw) return false;
  try {
    const { exp, iat } = JSON.parse(new TextDecoder().decode(raw)) as { exp?: unknown; iat?: unknown };
    if (typeof exp !== "number" || typeof iat !== "number") return false;
    const now = Math.floor(nowMs / 1000);
    return exp > now && iat <= now + 60;
  } catch {
    return false;
  }
}

/** Constant-time string equality: hash both sides to a fixed length, then OR the byte differences. */
export async function safeEqual(a: string, b: string): Promise<boolean> {
  const [da, db] = await Promise.all([crypto.subtle.digest("SHA-256", enc.encode(a)), crypto.subtle.digest("SHA-256", enc.encode(b))]);
  const x = new Uint8Array(da);
  const y = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}
