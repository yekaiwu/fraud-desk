import { vi } from "vitest";

/** Shared, mutable stand-in for next/navigation. Tests set `nav.params` / `nav.route` before rendering. */
export const nav = {
  pathname: "/",
  params: new URLSearchParams(),
  route: {} as Record<string, string>,
  router: { push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn(), refresh: vi.fn() },
};

export function resetNav(): void {
  nav.pathname = "/";
  nav.params = new URLSearchParams();
  nav.route = {};
  for (const fn of Object.values(nav.router)) fn.mockReset();
}

export function navModule() {
  return {
    usePathname: () => nav.pathname,
    useSearchParams: () => nav.params,
    useParams: () => nav.route,
    useRouter: () => nav.router,
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  };
}
