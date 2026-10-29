import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiErrorException, networkError } from "@/lib/errors";
import { Empty, ErrorState, Loading } from "./StateViews";

describe("StateViews", () => {
  it("Loading is a busy status with text", () => {
    render(<Loading label="Loading the queue" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Loading the queue...")).toBeInTheDocument();
  });

  it("Empty shows the title and an optional link", () => {
    render(<Empty title="No cases yet." href="/upload" linkLabel="Upload a bundle" />);
    expect(screen.getByText("No cases yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a bundle" })).toHaveAttribute("href", "/upload");
  });

  it("ErrorState is an alert with a retry only when retryable", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorState error={networkError()} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the API");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(<ErrorState error={new ApiErrorException({ code: "reason_required", message: "m" }, 422)} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Reason required");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("ErrorState shows the request reference for server errors and renders text literally", () => {
    const e = new ApiErrorException({ code: "internal_error", message: "<img src=x onerror=alert(1)>" }, 500, "req-42");
    const { container } = render(<ErrorState error={e} />);
    expect(screen.getByText("Reference: req-42")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("ErrorState prints an unknown code's message verbatim", () => {
    render(<ErrorState error={new ApiErrorException({ code: "weird", message: "Quota exceeded" }, 429)} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Quota exceeded");
  });

  it("Empty can carry an icon and no link", () => {
    render(<Empty title="Nothing here" icon={<svg data-testid="ic" />}>Try again later.</Empty>);
    expect(screen.getByTestId("ic")).toBeInTheDocument();
    expect(screen.getByText("Try again later.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
