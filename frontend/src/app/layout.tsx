import type { Metadata, Viewport } from "next";
import { AppShell } from "@/components/AppShell";
// Fonts are the OS system stacks defined in globals.css: nothing to load, CSP-safe.
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Fraud Desk | Green Link Digital Bank", template: "%s | Fraud Desk" },
  description: "Document-risk worklist for Green Link Digital Bank: cases ranked by risk, concerns shown on the page, decisions recorded with a reason. Decision support only.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
