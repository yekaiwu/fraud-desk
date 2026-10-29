import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ErrorPage from "./error";
import NotFound from "./not-found";

afterEach(cleanup);

describe("not-found and error screens", () => {
  it("not-found has one h1, a clear way back to the worklist and a second step", () => {
    render(<NotFound />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("We can't find that page");
    expect(screen.getByText(/no such case, version or page/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to the worklist/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Start a new case/ })).toHaveAttribute("href", "/upload");
  });

  it("error screen retries, links back, and quotes the reference when there is one", async () => {
    const reset = vi.fn();
    render(<ErrorPage error={Object.assign(new Error("boom <b>x</b>"), { digest: "d-123" })} reset={reset} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("This page could not be shown");
    expect(screen.getByText("Reference: d-123")).toBeInTheDocument();
    expect(screen.queryByText(/boom/)).toBeNull(); // the raw message is never shown to the officer
    await userEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(reset).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: /Back to the worklist/ })).toHaveAttribute("href", "/");
  });

  it("error screen omits the reference line without a digest", () => {
    render(<ErrorPage error={new Error("x")} reset={() => {}} />);
    expect(screen.queryByText(/Reference:/)).toBeNull();
  });
});
