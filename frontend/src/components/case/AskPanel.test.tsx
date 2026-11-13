import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { askCase } from "@/lib/mock/handlers";
import { GENERATED_NOTE } from "../explain/AnalysisTrail";
import type { AskCitation, AskRequest, AskResponse, Region } from "@/lib/types";
import { AskPanel } from "./AskPanel";
import { PageViewer } from "./PageViewer";

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
  it("shows suggestion chips, the generated-text label, and opens the cited page", async () => {
    const user = userEvent.setup();
    const c = fraud();
    const ask = vi.fn(async (_id: string, req: AskRequest) => {
      expect(req.question.length).toBeGreaterThan(0);
      return answered(c);
    });

    function Harness() {
      const numbered = numberFlags(c.findings, c.flags);
      const [loc, setLoc] = useState({ fileId: c.files[0].id, page: 1 });
      const [mark, setMark] = useState<Region | null>(null);
      return (
        <>
          <AskPanel
            c={c}
            fileId={loc.fileId}
            page={loc.page}
            ask={ask}
            onCite={(cit) => {
              setMark(cit.region ?? null);
              setLoc({ fileId: cit.file_id, page: cit.page });
            }}
          />
          <PageViewer
            caseId={c.id}
            files={c.files}
            documents={c.documents}
            flags={numbered.all}
            selectedFlagId={null}
            onSelectFlag={() => {}}
            current={loc}
            onNavigate={setLoc}
            mark={mark}
          />
        </>
      );
    }

    render(<Harness />);
    expect(screen.getByRole("button", { name: "Summarise this document" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(GENERATED_NOTE)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Summarise this document" }));
    expect(ask).toHaveBeenCalled();
    expect(screen.getByText("The credit is on the statement.")).toBeInTheDocument();
    expect(screen.getByText(GENERATED_NOTE)).toBeInTheDocument();

    const chip = screen.getByRole("button", { name: /· p\.\d+/ });
    await user.click(chip);
    const box = screen.getByTestId("citation-box");
    const cited = citation(c);
    expect(box).toHaveAttribute("aria-label", "Cited passage");
    const viewer = screen.getByRole("region", { name: "Page viewer" });
    expect(within(viewer).getByRole("combobox", { name: "Document and page" })).toHaveValue(`${cited.file_id}:${cited.page}`);
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
});
