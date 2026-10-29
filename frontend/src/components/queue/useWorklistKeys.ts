"use client";

import { useEffect, useRef } from "react";
import { ROW_LINK_ATTR } from "./QueueRow";
import { SEARCH_ID } from "./QueueFilters";

const TYPING = "input, textarea, select, [contenteditable=''], [contenteditable='true']";

/**
 * Worklist shortcuts: j / k (or the arrow keys while a row is focused) move the row focus, Enter opens the
 * focused row (it is a real link), "/" jumps to search and "?" opens the shortcut list. Nothing fires while
 * the officer is typing in a field, holding a modifier key, or has a dialog open.
 */
export function useWorklistKeys(onHelp: () => void): void {
  const help = useRef(onHelp);
  useEffect(() => {
    help.current = onHelp;
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest(TYPING)) return;
      if (document.querySelector("dialog[open]")) return;

      const links = Array.from(document.querySelectorAll<HTMLElement>(`[${ROW_LINK_ATTR}]`));
      const index = links.findIndex((l) => l === document.activeElement);

      if (e.key === "/") {
        const input = document.getElementById(SEARCH_ID);
        if (input instanceof HTMLInputElement) {
          e.preventDefault();
          input.focus();
          input.select();
        }
        return;
      }
      if (e.key === "?") {
        e.preventDefault();
        help.current();
        return;
      }

      const down = e.key === "j" || (e.key === "ArrowDown" && index >= 0);
      const up = e.key === "k" || (e.key === "ArrowUp" && index >= 0);
      if ((!down && !up) || links.length === 0) return;
      e.preventDefault();
      const next = index < 0 ? 0 : down ? Math.min(links.length - 1, index + 1) : Math.max(0, index - 1);
      links[next].focus();
      links[next].scrollIntoView?.({ block: "nearest" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
