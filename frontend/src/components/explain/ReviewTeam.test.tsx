import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentReport } from "@/lib/types";
import { ReviewTeam } from "./ReviewTeam";

const base: Omit<AgentReport, "agent" | "role"> = { status: "ok", checks_run: 3, flag_ids: [], summary: "Ran 3 checks.", verdicts: [], next_steps: [], handoff: "", cost_usd: "0", seconds: 0 };

describe("ReviewTeam", () => {
  it("renders nothing when the team did not run", () => {
    const { container } = render(<ReviewTeam agents={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the lowered flag with its quote, and labels the officer's text as generated", () => {
    const agents: AgentReport[] = [
      { ...base, agent: "identity", role: "Checks who is named." },
      {
        ...base,
        agent: "challenger",
        role: "Argues the innocent reading.",
        summary: "Ruled on 1 flag(s); lowered 1, kept 0.",
        verdicts: [{ flag_id: "f3", verdict: "doubtful", action: "lowered", reason: "The statement prints the name correctly.", document_id: "d1", quote: "Tan Wei Ming" }],
      },
      { ...base, agent: "case_officer", role: "Briefs the reviewer.", summary: "One thing stands out.", next_steps: ["Ask for the ID (f1)"] },
    ];
    render(<ReviewTeam agents={agents} />);
    expect(screen.getByText("The review team")).toBeInTheDocument();
    expect(screen.getByText("Tan Wei Ming")).toBeInTheDocument();
    expect(screen.getByText("One thing stands out.")).toBeInTheDocument();
    expect(screen.getByText(/never changes the score/)).toBeInTheDocument();
    expect(screen.getByText("Ask for the ID (f1)")).toBeInTheDocument();
  });

  it("says when a member could not run", () => {
    render(<ReviewTeam agents={[{ ...base, agent: "challenger", status: "failed", role: "r", summary: "The challenger could not run; every flag stands." }]} />);
    expect(screen.getByText("Could not run")).toBeInTheDocument();
  });
});
