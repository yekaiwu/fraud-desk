import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { askCase } from "@/lib/mock/handlers";
import { GENERATED_NOTE } from "../explain/AnalysisTrail";
import type { AskCitation, AskRequest, AskResponse, Region } from "@/lib/types";
import { AskPanel, askTabShown } from "./AskPanel";

afterEach(cleanup);

const fraud = () => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);

const citation = (c: ReturnType<typeof fraud>): AskCitation => {
  const flag = c.flags.find((f) => f.region) ?? c.flags[0];
  const region = flag.region as Region;
  return {
    file_id: region.file_id,
    page: region.page,
    document_id: flag.document_ids[0] ?? "",
    doc_type: "bank_statement",
    quote: "1500.00",
    region,
  };
};

function answered(c: ReturnType<typeof fraud>): AskResponse {
  return {
    status: "answered",
    preamble: null,
    message: null,
    sentences: [{ text: "The credit is on the statement.", citations: [citation(c)] }],
    flags: [],
  };
}

describe("AskPanel", () => {
  it("shows the answer as prose, without citation boxes", async () => {
    const user = userEvent.setup();
    const c = fraud();
    const ask = vi.fn(async (_id: string, req: AskRequest) => {
      expect(req.question.length).toBeGreaterThan(0);
      return answered(c);
    });

    render(<AskPanel c={c} fileId={c.files[0].id} page={1} ask={ask} />);
    expect(screen.getByRole("button", { name: "Summarise this document" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(GENERATED_NOTE)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Summarise this document" }));
    expect(ask).toHaveBeenCalled();
    const answer = screen.getByText("The credit is on the statement.");
    expect(answer).toBeInTheDocument();
    expect(screen.getByText(GENERATED_NOTE)).toBeInTheDocument();
    const box = answer.closest("div");
    expect(box).not.toBeNull();
    expect(within(box!).queryByRole("button")).toBeNull();
    expect(screen.queryByRole("button", { name: /x_directors|demo3|· p\./ })).toBeNull();
  });

  it("mock mode returns a cited answer and the decision hand-back", async () => {
    const c = fraud();
    const cited = await askCase(c.id, { question: "Summarise this document", scope: "document", file_id: "f1", page: 1, case_version: c.version });
    expect(cited.status).toBe("answered");
    expect(cited.sentences[0].citations[0].file_id).toBeTruthy();
    expect(cited.sentences[0].citations[0].page).toBeGreaterThan(0);
    const decision = await askCase(c.id, { question: "Should I approve this?", scope: "case", case_version: c.version });
    expect(decision.status).toBe("decision_request");
    expect(decision.preamble).toMatch(/That decision is yours/);
    expect(decision.sentences.join(" ")).not.toMatch(/\b(approve|reject|escalate|clear)\b/i);
  });

  it("shows the tab only when /me says Ask is on, or in mock mode", () => {
    expect(askTabShown(null, false)).toBe(false);
    expect(askTabShown({ case_assistant: false }, false)).toBe(false);
    expect(askTabShown({}, false)).toBe(false);
    expect(askTabShown({ case_assistant: true }, false)).toBe(true);
    expect(askTabShown(null, true)).toBe(true);
  });

  it("mock mode abstains with the server's words", async () => {
    const c = fraud();
    const none = await askCase(c.id, { question: "I can't find the lease", scope: "case", case_version: c.version });
    expect(none.status).toBe("not_found");
    expect(none.message).toBe("I can't find that in this case or on the worklist.");
  });
});
