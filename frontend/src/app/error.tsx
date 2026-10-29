"use client";

import { ArrowLeft, RotateCw } from "lucide-react";
import { ErrorScreen } from "@/components/ErrorScreen";
import { Button } from "@/components/ui/Button";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <ErrorScreen
      eyebrow="Something went wrong"
      title="This page could not be shown"
      reference={error.digest}
      actions={
        <>
          <Button size="lg" onClick={reset} icon={<RotateCw aria-hidden className="h-4 w-4" />}>
            Try again
          </Button>
          <Button href="/" size="lg" variant="secondary" icon={<ArrowLeft aria-hidden className="h-4 w-4" />}>
            Back to the worklist
          </Button>
        </>
      }
    >
      <p>Nothing you entered has been lost or sent anywhere. Trying again often clears it.</p>
      <p>{error.digest ? "If it keeps happening, tell the team and quote the reference below." : "If it keeps happening, tell the team."}</p>
    </ErrorScreen>
  );
}
