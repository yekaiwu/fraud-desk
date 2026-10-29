# GLDB Fraud Desk (frontend)

Analyst workbench for the GLDB document-fraud analysis tool. Specs: `../docs/frontend.md` and `../docs/api.md`.
All data is fictitious. Decision support only.

## Installed stack (scaffold)

| Package | Version |
|---|---|
| Next.js (App Router, Turbopack) | **16.4.3** |
| React | 19.3.0 |
| Tailwind CSS | 4 (processed by the `@tailwindcss/turbopack` loader; no PostCSS config, no `tailwind.config`) |
| swr, clsx, lucide-react | latest at scaffold time |
| vitest 5, @testing-library/react, jest-dom, user-event, jsdom | tests |

Next 16 notes for page authors:

- **Dynamic route params are async.** Server components receive `params: Promise<{ id: string }>`; client components use `useParams()` (all pages that fetch are client components).
- `cacheComponents` / `partialPrefetching` (the create-next-app default) are **off** in `next.config.ts`. Do not turn them on without a reason: they force Suspense around `useSearchParams` and keep hidden routes mounted.
- `useSearchParams()` in a statically prerendered page still needs a `<Suspense>` boundary (see `src/app/page.tsx`).
- The path alias is `@/` -> `src/`.
- `next.config.ts` always defines `NEXT_PUBLIC_USE_MOCK` and `NEXT_PUBLIC_API_BASE_URL` (empty when unset) so the bundler can drop all mock code from a normal build. Verified: a normal `npm run build` contains no fixture text.

## Scripts

| Script | What |
|---|---|
| `npm run dev` | dev server on :3000, talks to the API at `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`) |
| `npm run dev:mock` | dev server with `NEXT_PUBLIC_USE_MOCK=1`: no backend needed |
| `npm run build` / `npm run build:mock` / `npm start` | production build / start |
| `npm run lint` | ESLint (`react/no-danger` is an error) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | `vitest run` (jsdom, no network) |

`package.json` and `package-lock.json` are created by npm; do not hand-edit them.

## Layout

```
src/app/            routes: / (queue), /upload, /cases/[id], /effectiveness, /rules, error.tsx, not-found.tsx
src/components/     AppShell, MockBanner, StateViews (Loading | Empty | ErrorState)
src/lib/types.ts    docs/api.md section 12, pasted verbatim
src/lib/api.ts      typed client; throws ApiErrorException; mock mode via dynamic import
src/lib/errors.ts   ApiErrorException, explainError (frontend.md 9.4)
src/lib/queueQuery.ts, constants.ts, storage.ts
src/lib/mock/       handlers.ts (stateful mock API), state.ts, fixtures/index.ts (7 cases, typed against types.ts)
public/mock/        pages/{statement,invoice,ownership}.svg, sample-pack.html
```

The pages under `src/app/` are working placeholders wired to the API client (they render in mock mode); the real
pages replace them.

## Mock-data mode

`NEXT_PUBLIC_USE_MOCK=1` (or `npm run dev:mock`) serves everything from `src/lib/mock`. A banner on every page says so.
Fixture file ids pick the page image: `f1` statement, `f2` invoice, `f3` ownership chart. Region helpers in
`fixtures/index.ts` (`stmtBalance`, `stmtCredit`, `invAmount`, `invTotal`, `ownHolder`) compute fractions from the same row
geometry the SVGs are drawn with. Mock cases: `c_loan_fraud` (score 75, high), `c_kyc_review`
(a high flag pending second approval), `c_aml_closed`, `c_loan_clean` (fast lane), `c_loan_partial`, `c_kyc_processing`
(its job starts the first time it is polled), `c_loan_error`.

## Env

See `.env.example`.
