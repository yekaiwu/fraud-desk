import type { NextConfig } from "next";
import { securityHeaders } from "./src/lib/securityHeaders";

// Notes for frontend-app (Next 16):
// - Cache Components / partial prefetching (the create-next-app default for 16.4) are switched OFF.
//   Every page here is a client component that fetches with SWR, which does not need them, and
//   Cache Components would force Suspense around useSearchParams and keep hidden routes mounted
//   (their polling effects would stay alive). Remove only with a reason.
// - Dynamic route params are async in Next 16: server components get `params: Promise<...>`;
//   client components should use `useParams()`.
const nextConfig: NextConfig = {
  // No floating Next.js dev badge: it covers buttons on small screens.
  devIndicators: false,
  // Lets several dev servers share this folder, each with its own NEXT_DIST_DIR (e.g. .next-shell). Default is unchanged.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Always define the public flags, even when unset: Next only inlines NEXT_PUBLIC_* variables that
  // exist at build time, and an inlined "" is what lets the bundler drop the mock-data code
  // (lib/api.ts) from a normal build.
  env: {
    NEXT_PUBLIC_USE_MOCK: process.env.NEXT_PUBLIC_USE_MOCK ?? "",
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
  },
  // Security headers on every response (pages, /api proxy, errors); CSP only in production builds because
  // `next dev` needs eval. See docs/deploy.md.
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders(process.env.NODE_ENV === "production") }];
  },
  // Tailwind v4 is processed by the @tailwindcss/turbopack loader declared below.
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
