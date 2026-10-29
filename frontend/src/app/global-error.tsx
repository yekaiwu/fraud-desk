"use client";

// Rendered instead of the root layout when the layout itself fails, so it brings its own <html>, fonts and styles.
import "@fontsource-variable/fraunces/opsz.css";
import "@fontsource-variable/hanken-grotesk/wght.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./globals.css";
import { RotateCw } from "lucide-react";
import { ErrorScreen } from "@/components/ErrorScreen";
import { Logo } from "@/components/ui/Logo";

export default function RootError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <header className="band-dark border-b-[3px] border-brass bg-brand">
          <div className="mx-auto flex min-h-16 w-full max-w-[1320px] items-center px-4 py-3 sm:px-6 lg:px-8">
            <Logo tone="light" />
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1320px] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
          <ErrorScreen
            eyebrow="Something went wrong"
            title="The tool could not start"
            reference={error.digest}
            actions={
              <button
                type="button"
                onClick={reset}
                className="inline-flex min-h-12 items-center justify-center gap-2 rounded-md border border-brand bg-brand px-6 font-semibold text-on-brand hover:bg-brand-2"
              >
                <RotateCw aria-hidden className="h-4 w-4" />
                Try again
              </button>
            }
          >
            <p>Nothing you entered has been lost or sent anywhere. Reload the page, or try again in a moment.</p>
            <p>{error.digest ? "If it keeps happening, tell the team and quote the reference below." : "If it keeps happening, tell the team."}</p>
          </ErrorScreen>
        </main>
      </body>
    </html>
  );
}
