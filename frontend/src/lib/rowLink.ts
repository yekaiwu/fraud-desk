import type { MouseEvent as ReactMouseEvent } from "react";

/**
 * Marks the one control of a table row that opens it (the link or button holding the row's name). The keyboard
 * shortcuts of the worklist (j, k) look for it, and `openRowLink` replays row clicks on it.
 */
export const ROW_LINK_ATTR = "data-row-link";

/** Anything inside a row that does its own thing on click: a click on it must not also open the row. */
const OWN_CLICK = "a, button, input, select, textarea, summary, details, label, [role='button'], [role='dialog'], [data-no-row-click]";

/**
 * Makes a whole table row clickable.
 *
 * Do not build this with an `absolute inset-0` overlay inside a `relative` `<tr>`: browsers disagree on whether a
 * `<tr>` is a containing block (Safari has not always treated it as one). Where it is not, every row's overlay grows
 * to the nearest positioned ancestor, they stack over the whole table and the last row takes every click.
 *
 * Instead the row gets this as its `onClick`. The click is replayed on the row's own `ROW_LINK_ATTR` control with the
 * same modifier keys, so Next's router, "open in a new tab" and a button's own handler all behave as for a direct
 * click. Clicks on other controls, on portalled popovers (not in the row's DOM subtree), with a non-primary button
 * or at the end of a text selection are left alone.
 */
export function openRowLink(e: ReactMouseEvent<HTMLElement>): void {
  if (e.defaultPrevented || e.button !== 0) return;
  const target = e.target as Node;
  if (!e.currentTarget.contains(target)) return;
  const el = target instanceof Element ? target : target.parentElement;
  if (el?.closest(OWN_CLICK)) return;
  if (window.getSelection()?.toString()) return;
  e.currentTarget.querySelector<HTMLElement>(`[${ROW_LINK_ATTR}]`)?.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey, altKey: e.altKey }),
  );
}
