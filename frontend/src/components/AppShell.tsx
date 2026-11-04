"use client";

import clsx from "clsx";
import { Bell, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { DISCLAIMER, USE_MOCK } from "@/lib/constants";
import { useMe } from "@/lib/useMe";
import { useNotificationBadge } from "@/lib/useNotificationSummary";
import { Guilloche } from "./ui/Guilloche";
import { Logo, LogoMark } from "./ui/Logo";

/** Routes stay fixed; only the visible labels were made friendlier (Worklist = the case queue). */
export const NAV_ITEMS = [
  { href: "/", label: "Worklist" },
  { href: "/upload", label: "New case" },
  { href: "/effectiveness", label: "Control performance" },
  { href: "/rules", label: "Check catalogue" },
] as const;

/** Shown only to the administrator accounts (GLDB_ADMIN_USERS). */
const ADMIN_NAV_ITEMS = [{ href: "/spending", label: "Spending" }] as const;

/** Shown only when the server says custom rules are on and an account is signed in (/me custom_rules). */
const CUSTOM_RULES_NAV = { href: "/rules/custom", label: "Custom rules" } as const;

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/" || pathname.startsWith("/cases");
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The tab for this page: the longest matching href, so /rules/custom lights "Custom rules" and not "Check catalogue" too. */
function currentHref(pathname: string, hrefs: readonly string[]): string | null {
  const hits = hrefs.filter((h) => isActive(pathname, h));
  return hits.length ? hits.reduce((a, b) => (b.length > a.length ? b : a)) : null;
}

function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2.5 focus:text-sm focus:font-semibold focus:text-brand focus:shadow-lift focus:outline-2 focus:outline-offset-2 focus:outline-brass"
    >
      Skip to main content
    </a>
  );
}

/** The count on the notifications bell. Nothing is drawn at zero; the link's name carries the number otherwise. */
export function badgeLabel(count: number): string {
  return count > 99 ? "99+" : String(count);
}

/**
 * The notifications button in the top right of the header: a bell that opens /notifications, with the count of
 * things to look at on it. Its accessible name is "Notifications", or "Notifications, 3 to look at".
 */
function NotificationsButton({ pathname }: { pathname: string }) {
  const count = useNotificationBadge(pathname);
  const active = isActive(pathname, "/notifications");
  return (
    <Link
      href="/notifications"
      aria-current={active ? "page" : undefined}
      className={clsx(
        "relative inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border px-3 text-on-brand-muted transition-colors hover:border-on-brand/60 hover:bg-on-brand/10 hover:text-on-brand",
        active ? "border-brass bg-on-brand/10 text-on-brand" : "border-on-brand/25",
      )}
    >
      <Bell aria-hidden className="h-5 w-5" />
      <span className="sr-only">
        Notifications{count > 0 ? `, ${count} to look at` : ""}
      </span>
      {count > 0 ? (
        <span data-testid="notifications-badge" aria-hidden className="tabular absolute -right-1.5 -top-1.5 inline-flex min-w-[1.375rem] items-center justify-center rounded-full border border-brass/60 bg-brass-tint px-1.5 text-xs font-semibold leading-5 text-brass-deep">
          {badgeLabel(count)}
        </span>
      ) : null}
    </Link>
  );
}

export const CONTAINER = "mx-auto w-full max-w-[1320px] px-4 sm:px-6 lg:px-8";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const activeRef = useRef<HTMLAnchorElement | null>(null);
  const me = useMe(pathname !== "/welcome" && pathname !== "/login");
  const navItems = [...NAV_ITEMS, ...(me?.custom_rules ? [CUSTOM_RULES_NAV] : []), ...(me?.admin ? ADMIN_NAV_ITEMS : [])];
  const current = currentHref(pathname, navItems.map((i) => i.href));

  // On a phone the tab row scrolls sideways; make sure the current page's tab is in view.
  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [pathname]);

  // The public product tour (/welcome) brings its own header, main and footer: no staff navigation, no logout.
  if (pathname === "/welcome") {
    return (
      <>
        <SkipLink />
        {children}
      </>
    );
  }

  // The sign-in page is full-bleed and has no navigation to a section the visitor cannot open yet.
  if (pathname === "/login") {
    return (
      <>
        <SkipLink />
        <main id="main" tabIndex={-1} className="flex min-h-dvh flex-1 flex-col focus:outline-none">
          {children}
        </main>
      </>
    );
  }

  return (
    <>
      <SkipLink />
      <header className="band-dark relative overflow-hidden border-b-[3px] border-brass bg-brand">
        <Guilloche variant="band" opacity={0.2} className="absolute inset-0 h-full w-full text-brass-light" />
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-brand/0 via-brand/10 to-brand/55" />
        <div className={clsx(CONTAINER, "relative")}>
          <div className="flex min-h-16 items-center justify-between gap-4 py-3">
            <Link href="/" aria-label="Green Link Digital Bank, Fraud Desk: go to the worklist" className="rounded-md">
              <Logo tone="light" />
            </Link>
            <div className="flex items-center gap-2">
              <NotificationsButton pathname={pathname} />
              {USE_MOCK ? null : (
                <form method="post" action="/api/auth/logout">
                  <button
                    type="submit"
                    className="inline-flex min-h-11 items-center gap-2 rounded-md border border-on-brand/25 px-3.5 text-sm font-semibold text-on-brand-muted transition-colors hover:border-on-brand/60 hover:bg-on-brand/10 hover:text-on-brand"
                  >
                    <LogOut aria-hidden className="h-4 w-4" />
                    Log out
                  </button>
                </form>
              )}
            </div>
          </div>
          <nav aria-label="Main" className="-mb-[3px] mt-1">
            <ul className="flex gap-1 overflow-x-auto pb-[3px] pt-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {navItems.map((item) => {
                const active = item.href === current;
                return (
                  <li key={item.href} className="shrink-0">
                    <Link
                      href={item.href}
                      ref={active ? activeRef : undefined}
                      aria-current={active ? "page" : undefined}
                      className={clsx(
                        "relative inline-flex min-h-11 items-center rounded-t-lg border-t-[3px] px-3.5 text-sm font-semibold transition-colors sm:px-5",
                        active
                          ? "border-t-brass bg-paper text-brand"
                          : "border-t-transparent text-on-brand-muted hover:bg-on-brand/10 hover:text-on-brand",
                      )}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>
      </header>
      <main id="main" tabIndex={-1} className={clsx(CONTAINER, "flex-1 py-6 focus:outline-none sm:py-8")}>
        {children}
      </main>
      <footer className="border-t border-rule bg-paper-2/60">
        <div className={clsx(CONTAINER, "flex items-start gap-3 py-5 text-xs leading-relaxed text-muted")}>
          <LogoMark tone="dark" className="mt-0.5 h-5 w-5 opacity-60" />
          <div>
            <p>{DISCLAIMER}</p>
            <p className="mt-1">All data in this tool is fictitious.</p>
          </div>
        </div>
      </footer>
    </>
  );
}
