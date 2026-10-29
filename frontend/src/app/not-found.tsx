import { ArrowLeft, Plus } from "lucide-react";
import Link from "next/link";
import { ErrorScreen } from "@/components/ErrorScreen";
import { buttonClasses } from "@/components/ui/Button";

export const metadata = { title: "Page not found" };

// Plain links styled as buttons: this is a server component, and <Button href> hands an onClick to the client-side Link.
export default function NotFound() {
  return (
    <ErrorScreen
      eyebrow="Error 404"
      title="We can't find that page"
      actions={
        <>
          <Link href="/" className={buttonClasses({ size: "lg" })}>
            <ArrowLeft aria-hidden className="h-4 w-4" />
            Back to the worklist
          </Link>
          <Link href="/upload" className={buttonClasses({ size: "lg", variant: "secondary" })}>
            <Plus aria-hidden className="h-4 w-4" />
            Start a new case
          </Link>
        </>
      }
    >
      <p>There is no such case, version or page. The case may have been removed, or the link may be incomplete.</p>
      <p>Your worklist still has everything that is waiting for you.</p>
    </ErrorScreen>
  );
}
