import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { CoverageSkip } from "@/lib/types";
import { CoveragePanel, groupSkippedChecks, plainSkipReason } from "./CoveragePanel";

afterEach(cleanup);
const fraud = () => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);

describe("groupSkippedChecks", () => {
  const skip = (rule: string, reason: string, status: CoverageSkip["status"] = "skipped"): CoverageSkip => ({ rule, status, reason });

  it("groups by reason in plain English and says why it matters", () => {
    const g = groupSkippedChecks([
      skip("fs_balance", "no usable document of type financial_statement"),
      skip("fs_subtotals", "no usable document of type financial_statement"),
      skip("copy_move", "no raster source"),
      skip("ela_region", "no jpeg raster"),
      skip("x_odd", "something unusual"),
    ]);
    expect(g.map((x) => x.title)).toEqual(["No usable financial statement was found", "No photo or scan to examine", "Something unusual"]);
    expect(g[0].checks.map((c) => c.rule)).toEqual(["fs_balance", "fs_subtotals"]);
    expect(g[0].why).toMatch(/tested against it/);
    expect(g[1].checks).toHaveLength(2);
  });

  it("puts checks that errored first and never calls them clear", () => {
    const g = groupSkippedChecks([skip("a", "no raster source"), skip("b", "timeout", "error")]);
    expect(g[0].errored).toBe(true);
    expect(g[0].title).toBe("A check stopped with an error");
    expect(g[0].why).toMatch(/has not been looked at/);
  });

  it("words a check that was switched off in the catalogue and keeps it out of 'no document'", () => {
    const g = groupSkippedChecks([skip("a", "requires invoice"), skip("bs_running_balance", "switched_off"), skip("b", "timeout", "error")]);
    expect(g.map((x) => x.key)).toEqual(["error:timeout", "switched_off", "needs:invoice"]);
    expect(g[1].title).toBe("Switched off in the check catalogue");
    expect(g[1].why).toMatch(/never a clearance/);
    expect(plainSkipReason("switched_off")).toBe("Switched off in the check catalogue");
    // the real engine sends a machine code next to a long human reason
    const real = groupSkippedChecks([{ rule: "a", status: "skipped", code: "switched_off", reason: "Switched off in the check catalogue by j.lim on 2026-10-28: Not used" }]);
    expect(real[0].key).toBe("switched_off");
    expect(groupSkippedChecks([{ rule: "a", status: "skipped", code: "switched_off", reason: "anything" }])[0].key).toBe("switched_off");
  });

  it("reads the older 'requires' wording too", () => {
    expect(groupSkippedChecks([skip("x_directors", "requires company_profile")])[0].title).toBe("No usable company profile was found");
    expect(plainSkipReason("requires ownership_chart")).toBe("Needs a ownership chart document, and none was found.");
  });
});

describe("CoveragePanel", () => {
  it("shows checks completed as words and a meter, pages we could not review, and that silence is not a clearance", () => {
    render(<CoveragePanel c={fraud()} />);
    expect(screen.getByRole("progressbar", { name: "Checks completed" })).toHaveAttribute("aria-valuetext", "18 of 21 checks completed");
    expect(screen.getByText("1 page we could not review (of 4)")).toBeInTheDocument();
    expect(screen.getByText("A check that did not run is not a clearance.")).toBeInTheDocument();
  });

  it("opens the details with the checks that did not run, grouped, without check ids as text", async () => {
    const user = userEvent.setup();
    render(<CoveragePanel c={fraud()} defaultOpen={false} />);
    expect(screen.queryByText("Checks that did not run")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Coverage details/ }));
    const region = screen.getByRole("region", { name: "Checks that did not run" });
    expect(within(region).getByText("No usable financial statement was found")).toBeInTheDocument();
    expect(region.textContent).not.toMatch(/fs_arith/);
    expect(within(region).getByTitle("fs_arith")).toHaveTextContent("Financial statement: arith");
  });

  it("says so when every check ran and keeps the forensic wording", async () => {
    const user = userEvent.setup();
    const c = buildCase(SPECS.find((s) => s.id === "c_loan_clean")!);
    render(<CoveragePanel c={c} defaultOpen={false} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuetext", "21 of 21 checks completed");
    expect(screen.queryByText("A check that did not run is not a clearance.")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Coverage details/ }));
    expect(screen.getByText("Every applicable check ran.")).toBeInTheDocument();
    expect(screen.getByText(/No signal found \(not a guarantee of authenticity\)/)).toBeInTheDocument();
    expect(screen.getByText(/No AI-generator provenance hint found\./)).toBeInTheDocument();
  });

  it("renders nothing without coverage", () => {
    const { container } = render(<CoveragePanel c={{ ...fraud(), coverage: null }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
