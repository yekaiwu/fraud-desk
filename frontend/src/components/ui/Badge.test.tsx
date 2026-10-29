import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CaseStatus } from "@/lib/types";
import { Badge, ConcernBadge, LaneBadge, RiskBadge, StatusBadge } from "./Badge";

describe("Badge", () => {
  it("always renders its text and gives risk variants an icon (never colour alone)", () => {
    for (const variant of ["high", "medium", "low", "info"] as const) {
      const { container, unmount } = render(<Badge variant={variant}>{variant} text</Badge>);
      expect(screen.getByText(`${variant} text`)).toBeInTheDocument();
      expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      unmount();
    }
  });

  it("can drop the icon and carries the variant as data", () => {
    const { container } = render(
      <Badge variant="neutral" icon={null}>
        Plain
      </Badge>,
    );
    expect(container.querySelector("svg")).toBeNull();
    expect(container.firstElementChild).toHaveAttribute("data-variant", "neutral");
  });

  it("RiskBadge, ConcernBadge, LaneBadge use the plain-language labels", () => {
    render(
      <>
        <RiskBadge band="high" />
        <RiskBadge band="low" partial />
        <ConcernBadge severity="info" />
        <LaneBadge lane="edd" />
        <LaneBadge lane="fast" />
      </>,
    );
    expect(screen.getByText("High risk")).toBeInTheDocument();
    expect(screen.getByText("Low risk (partial score)")).toBeInTheDocument();
    expect(screen.getByText("For information")).toBeInTheDocument();
    expect(screen.getByText("Enhanced due diligence")).toHaveAttribute("data-variant", "lane");
    expect(screen.getByText("Fast lane")).toHaveAttribute("data-variant", "fast");
  });

  it("StatusBadge labels every status", () => {
    const all: CaseStatus[] = ["processing", "open", "awaiting_documents", "closed", "error"];
    render(
      <>
        {all.map((s) => (
          <StatusBadge key={s} status={s} />
        ))}
      </>,
    );
    for (const text of ["Being analysed", "Ready for review", "Waiting for documents", "Closed", "Could not be analysed"]) expect(screen.getByText(text)).toBeInTheDocument();
  });
});
