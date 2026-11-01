"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "./constants";

const MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "1";
let cached: string | null | undefined;
let inflight: Promise<string | null> | null = null;

function load(): Promise<string | null> {
  if (cached !== undefined) return Promise.resolve(cached);
  inflight ??= fetch(`${API_BASE_URL}/me`, { cache: "no-store" })
    .then((r) => (r.ok ? (r.json() as Promise<{ reviewer?: unknown }>) : { reviewer: null }))
    .then((b) => (typeof b.reviewer === "string" && b.reviewer ? b.reviewer : null))
    .catch(() => null)
    .then((who) => {
      cached = who;
      return who;
    });
  return inflight;
}

/**
 * The account the server says is signed in, or null (shared login, open mode, mock mode, no answer). When an
 * account is signed in the backend records that name on every verdict, so the form shows it instead of asking.
 */
export function useSignedInReviewer(): string | null {
  const [who, setWho] = useState<string | null>(cached ?? null);
  useEffect(() => {
    if (MOCK || typeof fetch !== "function") return;
    let live = true;
    void load().then((w) => live && setWho(w));
    return () => {
      live = false;
    };
  }, []);
  return who;
}
