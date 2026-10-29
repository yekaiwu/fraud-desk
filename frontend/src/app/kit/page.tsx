import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { KitGallery } from "./KitGallery";

export const metadata: Metadata = { title: "UI kit", robots: { index: false, follow: false } };

/** Developer-only gallery of every ui/ primitive (see frontend/DESIGN.md). 404s in production builds. */
export default function KitPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <KitGallery />;
}
