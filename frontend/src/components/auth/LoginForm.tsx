"use client";

import { AlertCircle, Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";

/** Username and password sign-in. Posts JSON to /api/auth/login, then navigates (full load) so the new cookie applies everywhere. */
export function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      const next = new URLSearchParams(window.location.search).get("next") ?? "/";
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password, next }),
        cache: "no-store",
      });
      const body = (await res.json().catch(() => null)) as { next?: string; error?: { code?: string; message?: string } } | null;
      if (res.ok && body?.next) {
        window.location.assign(body.next);
        return;
      }
      setError(
        res.status === 429
          ? (body?.error?.message ?? "Too many failed attempts. Try again later.")
          : res.status === 401
            ? "Wrong username or password."
            : (body?.error?.message ?? "Sign-in failed."),
      );
      setPassword("");
    } catch {
      setError("Cannot reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-lg border border-rule bg-surface p-6 shadow-lift sm:p-8" aria-labelledby="login-title">
      <div className="rule-statement">
        <h1 id="login-title" className="text-[1.75rem] leading-tight">
          Sign in
        </h1>
        <p className="mt-1 text-[0.9375rem] text-ink-2">Sign in to open the worklist.</p>
      </div>
      <Field
        label="Username"
        name="username"
        type="text"
        autoComplete="username"
        autoCapitalize="none"
        spellCheck={false}
        autoFocus
        required
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        className="mt-6"
      />
      <Field
        label="Password"
        name="password"
        type={show ? "text" : "password"}
        autoComplete="current-password"
        required
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        className="mt-4"
        aria-describedby={error ? "login-error" : undefined}
        trailing={
          <button
            type="button"
            onClick={() => setShow((v) => !v)}
            aria-label={show ? "Hide password" : "Show password"}
            className="grid h-11 w-11 place-items-center rounded-md text-ink-2 hover:bg-paper-2 hover:text-brand"
          >
            {show ? <EyeOff aria-hidden className="h-[18px] w-[18px]" /> : <Eye aria-hidden className="h-[18px] w-[18px]" />}
          </button>
        }
      />
      {error ? (
        <p id="login-error" role="alert" className="mt-3 flex items-start gap-2 rounded-md border border-sev-high/30 bg-sev-high-tint px-3 py-2.5 text-sm font-medium text-sev-high">
          <AlertCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}
      <Button type="submit" size="lg" fullWidth loading={busy} disabled={!username.trim() || !password} className="mt-6">
        Sign in
      </Button>
      <p className="mt-6 border-t border-rule pt-4 text-xs leading-relaxed text-muted">Demo with fictitious data only; reviewer names are self-declared.</p>
      <p className="mt-2 text-sm text-ink-2">
        New to Fraud Desk?{" "}
        <Link href="/welcome" className="inline-flex min-h-11 items-center font-semibold text-brand-2 underline">
          How it works
        </Link>
      </p>
    </form>
  );
}
