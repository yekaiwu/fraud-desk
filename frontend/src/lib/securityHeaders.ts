/** Response headers for every route (applied from next.config.ts). */
export function securityHeaders(production: boolean): { key: string; value: string }[] {
  const headers = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "no-referrer" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
    { key: "Strict-Transport-Security", value: "max-age=15552000" }, // browsers ignore it over plain http
  ];
  if (production) {
    // Next inlines its bootstrap and RSC payload scripts, hence 'unsafe-inline' for scripts.
    // Page images are same-origin PNGs from /api; blob:/data: cover the viewer and the mock SVGs.
    headers.push({
      key: "Content-Security-Policy",
      value: [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
      ].join("; "),
    });
  }
  return headers;
}
