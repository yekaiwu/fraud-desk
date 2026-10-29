/**
 * Access configuration (docs/deploy.md). Read from the server environment at request time, never
 * from NEXT_PUBLIC_* (those are baked into the browser bundle).
 *
 * Fail closed: in production a missing or too-short GLDB_SITE_USERNAME / GLDB_SITE_PASSWORD / GLDB_SESSION_SECRET is a
 * setup error and the server serves nothing but a 503 explanation. Outside production (npm run dev,
 * vitest) leaving both unset turns the gate off so local development stays frictionless; setting
 * only one of them is a setup error everywhere.
 */

export const MIN_USERNAME_CHARS = 3;
export const MIN_PASSWORD_CHARS = 12;
export const MIN_SECRET_CHARS = 32;
export const SESSION_COOKIE = "gldb_session";
export const SESSION_MAX_AGE_S = 12 * 60 * 60;

export type AuthConfig =
  | { mode: "open" }
  | { mode: "enforce"; username: string; password: string; secret: string }
  | { mode: "error"; problems: string[] };

type Env = Record<string, string | undefined>;

export function readAuthConfig(env: Env = process.env): AuthConfig {
  const username = env.GLDB_SITE_USERNAME ?? "";
  const password = env.GLDB_SITE_PASSWORD ?? "";
  const secret = env.GLDB_SESSION_SECRET ?? "";
  const production = env.NODE_ENV === "production";
  if (!production && !username && !password && !secret) return { mode: "open" };

  const problems: string[] = [];
  if (!username) problems.push("GLDB_SITE_USERNAME is not set.");
  else if (username.length < MIN_USERNAME_CHARS) problems.push(`GLDB_SITE_USERNAME must be at least ${MIN_USERNAME_CHARS} characters.`);
  if (!password) problems.push("GLDB_SITE_PASSWORD is not set.");
  else if (password.length < MIN_PASSWORD_CHARS) problems.push(`GLDB_SITE_PASSWORD must be at least ${MIN_PASSWORD_CHARS} characters.`);
  if (!secret) problems.push("GLDB_SESSION_SECRET is not set.");
  else if (secret.length < MIN_SECRET_CHARS) problems.push(`GLDB_SESSION_SECRET must be at least ${MIN_SECRET_CHARS} characters.`);
  if (problems.length) return { mode: "error", problems };
  return { mode: "enforce", username, password, secret };
}

/** What the session signature is bound to: changing the username or the password signs everybody out. */
export function sessionBinding(cfg: { username: string; password: string }): string {
  return `${cfg.username}\u0000${cfg.password}`;
}

/** Where /api/* is forwarded to (server side only). */
export function backendUrl(env: Env = process.env): string {
  return (env.GLDB_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}
