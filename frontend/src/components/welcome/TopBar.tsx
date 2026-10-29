import Link from "next/link";
import { ArrowRight, LogIn } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { WIDE } from "./parts";

export const SIGN_IN_LABEL = "Staff sign-in";
export const OPEN_WORKLIST_LABEL = "Open the worklist";

/** Where the entrance button goes and what it says: a signed-in officer goes straight to the worklist. */
export function entrance(signedIn: boolean): { href: string; label: string } {
  return signedIn ? { href: "/", label: OPEN_WORKLIST_LABEL } : { href: "/login", label: SIGN_IN_LABEL };
}

const ANCHORS = [
  ["#flow", "How a case flows"],
  ["#try", "Try it"],
  ["#limits", "Limits"],
] as const;

/** Slim sticky bar: logo, three anchors (wide screens only) and the entrance button. */
export function TopBar({ signedIn }: { signedIn: boolean }) {
  const { href, label } = entrance(signedIn);
  return (
    <header className="band-dark sticky top-0 z-40 border-b-[3px] border-brass bg-brand/95 backdrop-blur-[2px]">
      <div className={`${WIDE} flex min-h-14 items-center justify-between gap-4 py-1.5`}>
        <Link href="/welcome" aria-label="Green Link Digital Bank, Fraud Desk: top of this page" className="rounded-md">
          <Logo tone="light" />
        </Link>
        <nav aria-label="On this page" className="hidden md:block">
          <ul className="flex items-center gap-1">
            {ANCHORS.map(([to, text]) => (
              <li key={to}>
                <a href={to} className="inline-flex min-h-11 items-center rounded-md px-3 text-sm font-semibold text-on-brand-muted transition-colors hover:bg-on-brand/10 hover:text-on-brand">
                  {text}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <Link
          href={href}
          className="inline-flex min-h-11 items-center gap-2 rounded-md border border-surface bg-surface px-4 text-sm font-semibold text-brand transition-colors hover:border-brass-light hover:bg-brass-tint"
        >
          {signedIn ? <ArrowRight aria-hidden className="h-4 w-4" /> : <LogIn aria-hidden className="h-4 w-4" />}
          {label}
        </Link>
      </div>
    </header>
  );
}
