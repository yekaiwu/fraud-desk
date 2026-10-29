import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { TabPanel, Tabs } from "./Tabs";

function Demo({ keepMounted = true }: { keepMounted?: boolean }) {
  const [v, setV] = useState("a");
  return (
    <>
      <Tabs group="t" label="Sections" value={v} onValueChange={setV} tabs={[{ id: "a", label: "Alpha", count: 2 }, { id: "b", label: "Beta" }, { id: "c", label: "Gamma" }]} />
      <TabPanel group="t" id="a" value={v} keepMounted={keepMounted}>A body</TabPanel>
      <TabPanel group="t" id="b" value={v} keepMounted={keepMounted}>B body</TabPanel>
    </>
  );
}

describe("Tabs", () => {
  it("is a tablist with one tab stop and linked panels", () => {
    render(<Demo />);
    expect(screen.getByRole("tablist", { name: "Sections" })).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
    expect(tabs[0]).toHaveAttribute("aria-controls", "t-panel-a");
    expect(screen.getByRole("tabpanel", { name: /Alpha/ })).toHaveTextContent("A body");
    expect(screen.queryByText("B body")).not.toBeVisible();
  });

  it("moves and selects with the arrow keys, wraps, and supports Home/End", async () => {
    render(<Demo />);
    screen.getByRole("tab", { name: /Alpha/ }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /Alpha/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Gamma" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: /Alpha/ })).toHaveFocus();
  });

  it("selects on click and can unmount inactive panels", async () => {
    render(<Demo keepMounted={false} />);
    expect(screen.queryByText("B body")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Beta" }));
    expect(screen.getByText("B body")).toBeInTheDocument();
    expect(screen.queryByText("A body")).not.toBeInTheDocument();
  });

  it("passes a tooltip through and keeps the first tab reachable when none is selected", () => {
    render(<Tabs group="t" label="Sections" value="" onValueChange={() => {}} tabs={[{ id: "a", label: "Alpha", title: "Alpha means first" }, { id: "b", label: "Beta" }]} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveAttribute("title", "Alpha means first");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1"]);
  });
});
