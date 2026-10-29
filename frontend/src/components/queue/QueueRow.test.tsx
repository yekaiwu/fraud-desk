import { fireEvent, render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { describe, expect, it, vi } from "vitest";
import { item } from "./testItem";
import { QueueCard, QueueTableRow } from "./QueueRow";

// A plain anchor stands in for next/link: these tests are about which link a click on the row reaches.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function renderRows() {
  const a = item({ case_id: "c_a", applicant_name: "Alpha Pte. Ltd.", top_finding: "Alpha finding" });
  const b = item({ case_id: "c_b", applicant_name: "Bravo Pte. Ltd.", top_finding: "Bravo finding" });
  render(
    <table>
      <tbody>
        <QueueTableRow item={a} rank={1} ageNow={0} />
        <QueueTableRow item={b} rank={2} ageNow={0} />
      </tbody>
    </table>,
  );
  // Record which link receives a click, and stop jsdom from trying to navigate.
  const opened: { href: string | null; meta: boolean }[] = [];
  for (const link of screen.getAllByRole("link")) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      opened.push({ href: link.getAttribute("href"), meta: (e as MouseEvent).metaKey });
    });
  }
  return opened;
}

describe("worklist table row", () => {
  it("does not rely on the row being a containing block for a stretched link", () => {
    renderRows();
    // Safari and others do not always anchor an absolutely positioned child to a <tr>: with an overlay in every row, the
    // overlays stack up over the whole table and the last row takes every click.
    for (const row of screen.getAllByRole("row")) expect(row.className).not.toMatch(/(^|\s)relative(\s|$)/);
    for (const link of screen.getAllByRole("link")) expect(link.className).not.toContain("after:absolute");
  });

  it("opens the clicked row's own case when the click lands anywhere on the row", () => {
    const opened = renderRows();
    fireEvent.click(screen.getByText("Bravo finding"));
    expect(opened).toEqual([{ href: "/cases/c_b", meta: false }]);
    fireEvent.click(screen.getByText("Alpha finding"));
    expect(opened.map((o) => o.href)).toEqual(["/cases/c_b", "/cases/c_a"]);
  });

  it("opens the case exactly once when the link itself is clicked", () => {
    const opened = renderRows();
    fireEvent.click(screen.getByText("Alpha Pte. Ltd."));
    expect(opened).toEqual([{ href: "/cases/c_a", meta: false }]);
  });

  it("passes the modifier keys on so a meta-click can still open a new tab", () => {
    const opened = renderRows();
    fireEvent.click(screen.getByText("Bravo finding"), { metaKey: true });
    expect(opened).toEqual([{ href: "/cases/c_b", meta: true }]);
  });

  it("leaves clicks on other controls and non-primary buttons alone", () => {
    const opened = renderRows();
    const row = screen.getByText("Alpha finding").closest("tr") as HTMLElement;
    const button = document.createElement("button");
    button.textContent = "Explain";
    row.querySelector("td")?.appendChild(button);
    fireEvent.click(button);
    fireEvent.click(screen.getByText("Alpha finding"), { button: 1 });
    expect(opened).toEqual([]);
  });

  it("does not open the case when the click ends a text selection", () => {
    const opened = renderRows();
    const text = screen.getByText("Alpha finding");
    const range = document.createRange();
    range.selectNodeContents(text);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    fireEvent.click(text);
    window.getSelection()?.removeAllRanges();
    expect(opened).toEqual([]);
  });
});

describe("worklist card", () => {
  it("keeps the stretched link: a card is a plain positioned element, so it anchors correctly", () => {
    render(
      <ul>
        <QueueCard item={item({ case_id: "c_a", applicant_name: "Alpha Pte. Ltd." })} rank={1} ageNow={0} />
      </ul>,
    );
    expect(screen.getByRole("link").className).toContain("after:absolute");
    expect(screen.getByRole("listitem").className).toMatch(/(^|\s)relative(\s|$)/);
  });
});
