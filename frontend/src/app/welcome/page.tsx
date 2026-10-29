import type { Metadata } from "next";
import { cookies } from "next/headers";
import { readAuthConfig } from "@/lib/auth/config";
import { SESSION_COOKIE, isSignedIn } from "@/lib/auth/gate";
import { WelcomeView } from "@/components/welcome/WelcomeView";
import "./welcome.css";

// Public page (see lib/auth/gate.ts). Static copy and fictitious sample data only; no API calls.
export const metadata: Metadata = {
  title: { absolute: "How it works | Green Link Fraud Desk" },
  description:
    "How Green Link Digital Bank's Fraud Desk works: documents are read and checked, risk is scored with a transparent formula, and an officer decides with a recorded reason. Decision support only; fictitious demo data.",
  robots: { index: false, follow: false },
};

export default async function WelcomePage() {
  // Only tailors the entrance button ("Open the worklist" for a signed-in officer). It grants nothing: the content is the same for everyone.
  const signedIn = await isSignedIn(readAuthConfig(), (await cookies()).get(SESSION_COOKIE)?.value);
  return <WelcomeView signedIn={signedIn} />;
}
