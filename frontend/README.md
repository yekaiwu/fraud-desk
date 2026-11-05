# Fraud Desk (frontend)

The analyst workbench: a Next.js app that talks to the backend API. Specs: `../docs/frontend.md` and `../docs/api.md`. Look and wording: `DESIGN.md`. All data is fictitious. Decision support only.

## Stack

Next.js 16 (App Router), React 19, Tailwind CSS 4, swr, vitest with Testing Library. Needs Node 22.12 or newer.

Notes for page authors:

- Dynamic route params are async. Pages that fetch are client components and read them with `useParams()`.
- `cacheComponents` and `partialPrefetching` are off in `next.config.ts`. Do not turn them on without a reason.
- `useSearchParams()` in a statically prerendered page needs a `<Suspense>` boundary (see `src/app/page.tsx`).
- The path alias is `@/` for `src/`.
- Any new route under `src/app/api/` must call the gate (`decide()` in `src/lib/auth/gate.ts`).

## Scripts

| Script | What |
|---|---|
| `npm run dev` | dev server on :3000, talks to the API at `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`) |
| `npm run dev:mock` | dev server with `NEXT_PUBLIC_USE_MOCK=1`: no backend needed |
| `npm run build` / `npm run build:mock` / `npm start` | production build / start |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | `vitest run` (jsdom, no network) |

`package.json` and `package-lock.json` are written by npm; do not hand-edit them.

## Layout

```
src/app/            routes: / (worklist), /upload, /cases/[id], /notifications, /effectiveness,
                    /rules (check catalogue) and /rules/custom (your own checks), /spending (admins),
                    /login, /welcome, /kit (UI kit gallery), plus error and not-found pages
src/app/api/        server routes: the sign-in gate and the proxy to the backend
src/components/     shared pieces, one folder per area (case, queue, upload, catalogue, ...)
src/lib/            typed API client (api.ts, types.ts), helpers, auth gate (lib/auth)
src/lib/mock/       the stateful mock API and its fixture cases
public/mock/        page images and a sample pack for mock mode
```

## Mock-data mode

`NEXT_PUBLIC_USE_MOCK=1` (or `npm run dev:mock`) serves everything from `src/lib/mock`, and a banner on every page says so. The fixture cases (about a dozen, in `src/lib/mock/fixtures/index.ts`) cover a high-score loan, a KYC case waiting for a second approver, a closed AML case, a clean fast-lane case, and partial, processing, error, awaiting-documents and hard-stop cases. A normal build contains no mock code.

## Env

See `.env.example`.
