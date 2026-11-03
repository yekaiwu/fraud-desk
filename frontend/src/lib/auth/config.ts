/**
 * Access configuration (docs/deploy.md). Read from the server environment at request time, never
 * from NEXT_PUBLIC_* (those are baked into the browser bundle).
 *
 * Fail closed: in production a missing or too-short GLDB_SITE_USERNAME / GLDB_SITE_PASSWORD / GLDB_SESSION_SECRET is a
 * setup error and the server serves nothing but a 503 explanation. Outside production (npm run dev,
 * vitest) leaving both unset turns the gate off so local development stays frictionless; setting
 * only one of them is a setup error everywhere.
 *
 * Personal accounts: GLDB_SITE_USERS is a JSON object {"alice":"password", "bob":"password"}. Each person
 * signs in as themselves, the session records who, and the backend writes that name (not a typed one)
 * into every verdict. GLDB_SITE_USERNAME / GLDB_SITE_PASSWORD still work and become one more account.
 */

export const MIN_USERNAME_CHARS = 3;
export const MIN_PASSWORD_CHARS = 10;
export const MIN_SECRET_CHARS = 32;
export const SESSION_COOKIE = "gldb_session";
export const SESSION_MAX_AGE_S = 12 * 60 * 60;

export type AuthConfig =
  | { mode: "open" }
  | { mode: "enforce"; username: string; password: string; secret: string; users: Record<string, string> }
  | { mode: "error"; problems: string[] };

type Env = Record<string, string | undefined>;

export function readAuthConfig(env: Env = process.env): AuthConfig {
  const username = env.GLDB_SITE_USERNAME ?? "";
  const password = env.GLDB_SITE_PASSWORD ?? "";
  const secret = env.GLDB_SESSION_SECRET ?? "";
  const rawUsers = (env.GLDB_SITE_USERS ?? "").trim();
  const production = env.NODE_ENV === "production";
  if (!production && !username && !password && !secret && !rawUsers) return { mode: "open" };

  const problems: string[] = [];
  const users: Record<string, string> = {};
  let parsed: Record<string, unknown> = {};
  if (rawUsers) {
    try {
      const v: unknown = JSON.parse(rawUsers);
      if (v && typeof v === "object" && !Array.isArray(v)) parsed = v as Record<string, unknown>;
      else problems.push('GLDB_SITE_USERS must be a JSON object like {"alice":"a long password"}.');
    } catch {
      problems.push('GLDB_SITE_USERS must be valid JSON like {"alice":"a long password"}.');
    }
    for (const [name, pw] of Object.entries(parsed)) {
      const key = name.trim().toLowerCase();
      if (key.length < MIN_USERNAME_CHARS) problems.push(`A GLDB_SITE_USERS username must be at least ${MIN_USERNAME_CHARS} characters.`);
      else if (!/^[a-z0-9._@-]{3,64}$/.test(key)) problems.push("A GLDB_SITE_USERS username may use letters, digits and . _ @ - only.");
      else if (typeof pw !== "string" || pw.length < MIN_PASSWORD_CHARS) problems.push(`Every GLDB_SITE_USERS password must be at least ${MIN_PASSWORD_CHARS} characters.`);
      else if (key in users) problems.push("GLDB_SITE_USERS has the same username twice.");
      else users[key] = pw;
    }
  }
  const useSingle = Boolean(username || password) || !rawUsers;
  if (useSingle) {
    if (!username) problems.push("GLDB_SITE_USERNAME is not set.");
    else if (username.length < MIN_USERNAME_CHARS) problems.push(`GLDB_SITE_USERNAME must be at least ${MIN_USERNAME_CHARS} characters.`);
    if (!password) problems.push("GLDB_SITE_PASSWORD is not set.");
    else if (password.length < MIN_PASSWORD_CHARS) problems.push(`GLDB_SITE_PASSWORD must be at least ${MIN_PASSWORD_CHARS} characters.`);
    if (username && password && username.length >= MIN_USERNAME_CHARS && password.length >= MIN_PASSWORD_CHARS) {
      const key = username.trim().toLowerCase();
      if (key in users) problems.push("GLDB_SITE_USERNAME is also listed in GLDB_SITE_USERS.");
      else users[key] = password;
    }
  }
  if (!secret) problems.push("GLDB_SESSION_SECRET is not set.");
  else if (secret.length < MIN_SECRET_CHARS) problems.push(`GLDB_SESSION_SECRET must be at least ${MIN_SECRET_CHARS} characters.`);
  if (problems.length) return { mode: "error", problems };
  const [first, firstPw] = Object.entries(users)[0];
  return { mode: "enforce", username: useSingle ? username : first, password: useSingle ? password : firstPw, secret, users };
}

/** What the session signature is bound to: changing the username or the password signs everybody out. */
export function sessionBinding(cfg: { username: string; password: string }): string {
  return `${cfg.username}\u0000${cfg.password}`;
}

/** Where /api/* is forwarded to (server side only). */
export function backendUrl(env: Env = process.env): string {
  return (env.GLDB_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}
