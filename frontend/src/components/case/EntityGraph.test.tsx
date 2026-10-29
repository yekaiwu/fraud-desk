import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deriveEntityGraph } from "@/lib/graph";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { clearViewport, setViewport } from "@/test/utils";
import { EntityGraph } from "./EntityGraph";

afterEach(() => {
  cleanup();
  clearViewport();
});

const graph = () => deriveEntityGraph(buildCase(SPECS.find((s) => s.id === "c_aml_graph")!));

describe("EntityGraph", () => {
  it("draws the graph on a wide screen with a key and keeps every relationship in a table", () => {
    setViewport(1280);
    const g = graph();
    render(<EntityGraph graph={g} onSelectEdge={vi.fn()} />);
    expect(screen.getByRole("img", { name: /Entity graph, \d+ parties, \d+ relationships, \d+ flagged/ })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Key to the graph" })).toHaveTextContent("The applicant");
    const table = screen.getByRole("table", { name: /Relationships between parties/, hidden: true });
    expect(within(table).getAllByRole("row", { hidden: true })).toHaveLength(g.edges.length + 1);
  });

  it("shows the table by default on a phone and switches to the graph", async () => {
    setViewport(390);
    const user = userEvent.setup();
    render(<EntityGraph graph={graph()} onSelectEdge={vi.fn()} />);
    expect(screen.getByRole("table", { name: /Relationships between parties/ })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Entity graph/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Graph" }));
    expect(screen.getByRole("img", { name: /Entity graph, \d+ parties/ })).toBeInTheDocument();
  });

  it("selects an edge when its row is chosen in the table", async () => {
    setViewport(390);
    const user = userEvent.setup();
    const onSelectEdge = vi.fn();
    render(<EntityGraph graph={graph()} onSelectEdge={onSelectEdge} />);
    await user.click(screen.getAllByRole("button", { name: /show on document/i })[0]);
    expect(onSelectEdge).toHaveBeenCalledTimes(1);
  });
});
