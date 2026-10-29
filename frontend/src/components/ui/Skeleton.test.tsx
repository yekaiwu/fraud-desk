import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Skeleton, SkeletonText } from "./Skeleton";

describe("Skeleton", () => {
  it("is decorative", () => {
    const { container } = render(<Skeleton className="h-6 w-40" />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
    expect(container.firstElementChild).toHaveClass("skeleton", "w-40");
  });

  it("SkeletonText renders the requested number of lines, the last one shorter", () => {
    const { container } = render(<SkeletonText lines={4} />);
    const lines = container.querySelectorAll(".skeleton");
    expect(lines).toHaveLength(4);
    expect(lines[3]).toHaveClass("w-2/3");
  });
});
