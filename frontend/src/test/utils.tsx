import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { SWRConfig } from "swr";

/** Fresh SWR cache per render so tests never share fetched data. */
export function renderWithSWR(ui: ReactElement) {
  return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);
}

/** matchMedia that answers (min-width: Npx) for a given viewport width. Call before rendering. */
export function setViewport(width: number): void {
  window.matchMedia = ((query: string) => {
    const m = /min-width:\s*(\d+)px/.exec(query);
    const matches = m ? width >= Number(m[1]) : false;
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList;
  }) as typeof window.matchMedia;
}

export function clearViewport(): void {
  // @ts-expect-error jsdom has no matchMedia; remove our stub
  delete window.matchMedia;
}
