import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProcessingStepper, STAGE_LABEL, STAGE_ORDER } from "./ProcessingStepper";

afterEach(cleanup);

describe("ProcessingStepper", () => {
  it("lists the seven stages by plain name, without a job id", () => {
    render(<ProcessingStepper jobId={null} onFinished={vi.fn()} onRerun={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Analysing the documents" })).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(7);
    STAGE_ORDER.forEach((stage, i) => expect(items[i]).toHaveTextContent(STAGE_LABEL[stage]));
    expect(screen.getByRole("status")).toHaveTextContent(/Analysis running/);
    expect(screen.getByText(/s elapsed/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/crossdoc|forensics/);
  });
});
