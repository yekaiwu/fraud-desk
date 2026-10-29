import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/welcome" }));

import { AppShell } from "@/components/AppShell";
import { DISCLAIMER } from "@/lib/constants";
import { ScoreCalculator } from "./ScoreCalculator";
import { ScoreExplainer } from "./ScoreExplainer";
import { SpotTheEdit } from "./SpotTheEdit";
import { WelcomeView } from "./WelcomeView";
import { entrance } from "./TopBar";

// Role queries over the whole page are slow in jsdom, especially when the full suite runs in parallel.
vi.setConfig({ testTimeout: 30_000 });

describe("entrance", () => {
  it("says Staff sign-in and goes to /login for visitors, Open the worklist and / for signed-in staff", () => {
    expect(entrance(false)).toEqual({ href: "/login", label: "Staff sign-in" });
    expect(entrance(true)).toEqual({ href: "/", label: "Open the worklist" });
  });
});

describe("WelcomeView", () => {
  it("has the landmarks, one h1, a labelled region per section and the skip target", () => {
    render(<WelcomeView signedIn={false} />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("contentinfo")).toHaveTextContent(DISCLAIMER);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Every document read.Every decision explained.");
    const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2).toEqual([
      "From upload to decision, in five steps.",
      "Spot the edit.",
      "A score you can add up yourself.",
      "An empty result is never a clearance.",
      "Same desk, three different questions.",
      "The desk recommends. A person decides.",
      "If it can’t be explained on the page, it isn’t a flag.",
      "Honest limits, stated up front.",
      "See the evidence. Make the call.",
    ]);
    for (const region of screen.getAllByRole("region")) expect(region).toHaveAccessibleName();
  });

  it("offers Staff sign-in (to /login) and the scroll link when signed out, with no worklist link", () => {
    render(<WelcomeView signedIn={false} />);
    const signIn = screen.getAllByRole("link", { name: "Staff sign-in" });
    expect(signIn.length).toBeGreaterThanOrEqual(3); // top bar, hero, closing band
    for (const a of signIn) expect(a).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "See how it works" })).toHaveAttribute("href", "#flow");
    expect(screen.queryByRole("link", { name: "Open the worklist" })).not.toBeInTheDocument();
  });

  it("reads Open the worklist and links to / when signed in", () => {
    render(<WelcomeView signedIn />);
    const open = screen.getAllByRole("link", { name: "Open the worklist" });
    expect(open.length).toBeGreaterThanOrEqual(3);
    for (const a of open) expect(a).toHaveAttribute("href", "/");
    expect(screen.queryByRole("link", { name: "Staff sign-in" })).not.toBeInTheDocument();
  });

  it("covers the five steps, the three case types, the limits and the fictitious-data line", () => {
    render(<WelcomeView signedIn={false} />);
    const flow = within(screen.getByRole("region", { name: "From upload to decision, in five steps." }));
    expect(flow.getAllByRole("listitem").length).toBeGreaterThanOrEqual(5);
    for (const t of ["Upload", "Read", "Check", "Score and rank", "Decide"]) expect(flow.getByRole("heading", { name: new RegExp(t) })).toBeInTheDocument();
    for (const t of ["Loan application", "Corporate onboarding (KYC)", "Source of funds (AML)"]) expect(screen.getByRole("heading", { name: t })).toBeInTheDocument();
    const limits = within(screen.getByRole("region", { name: "Honest limits, stated up front." }));
    expect(limits.getByText(/does not decide for you/)).toBeInTheDocument();
    expect(limits.getByText(/cannot prove a document is real/)).toBeInTheDocument();
    expect(limits.getByText(/fully AI-generated/)).toBeInTheDocument();
    expect(limits.getByText(/suspicious transaction reports/)).toBeInTheDocument();
    expect(limits.getByText(/synthetic data/)).toBeInTheDocument();
    expect(screen.getAllByText(/All data in this tool is fictitious/).length).toBeGreaterThan(0);
  });

  it("shows coverage and the unreviewed page in the silence section", () => {
    render(<WelcomeView signedIn={false} />);
    const silence = within(screen.getByRole("region", { name: "An empty result is never a clearance." }));
    expect(silence.getAllByText("41 of 54 checks completed").length).toBeGreaterThan(0);
    expect(silence.getByText("12 of 54 checks completed")).toBeInTheDocument();
    expect(silence.getByText(/1 page we could not review/)).toBeInTheDocument();
  });

  it("makes no network request and links to nothing external", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { container } = render(<WelcomeView signedIn={false} />);
    expect(fetchSpy).not.toHaveBeenCalled();
    for (const a of container.querySelectorAll("a")) expect(a.getAttribute("href")).toMatch(/^(#|\/)/);
    expect(container.querySelector("img, iframe, script, link")).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("AppShell on /welcome", () => {
  it("is bare: skip link and the page's own landmarks, no staff nav or logout", () => {
    render(
      <AppShell>
        <main id="main">tour</main>
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log out" })).not.toBeInTheDocument();
    expect(screen.queryByRole("contentinfo")).not.toBeInTheDocument();
  });
});

describe("SpotTheEdit", () => {
  it("shows nothing flagged until the checks run, then flags the edited row with its evidence and a score of 30", () => {
    render(<SpotTheEdit />);
    expect(screen.getByText("Nothing has been checked yet")).toBeInTheDocument();
    expect(screen.queryByText("Running balance does not add up from this row")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run the checks" }));
    const row = screen.getByText("Receipt INV-2211").closest("tr")!;
    expect(row).toHaveAttribute("data-failed", "true");
    expect(document.querySelectorAll("tr[data-failed]")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Running balance does not add up from this row" })).toBeInTheDocument();
    const finding = within(screen.getByRole("article", { name: "Finding" }));
    expect(finding.getByText("Blocks the fast lane")).toBeInTheDocument();
    expect(finding.getByText("11,574.50")).toBeInTheDocument();
    expect(finding.getByText("+ 14,000.00")).toBeInTheDocument();
    expect(finding.getByText("25,574.50")).toBeInTheDocument();
    expect(finding.getByText("15,574.50")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Risk score 30 of 100, medium" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Checks complete\. One finding.*Risk score 30 of 100, medium/);
  });

  it("scores the clean version 0 with the no-concerns line and the coverage", () => {
    render(<SpotTheEdit />);
    fireEvent.click(screen.getByRole("button", { name: "Clean version" }));
    expect(screen.getByRole("button", { name: "Clean version" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("+4,000.00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run the checks" }));
    expect(document.querySelectorAll("tr[data-failed]")).toHaveLength(0);
    expect(screen.getByText("No concerns found. 41 of 54 checks completed.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Risk score 0 of 100, low" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("No concerns found. 41 of 54 checks completed.");
  });

  it("clears the result when the version changes, so a result is never shown for the wrong statement", () => {
    render(<SpotTheEdit />);
    fireEvent.click(screen.getByRole("button", { name: "Run the checks" }));
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clean version" }));
    expect(screen.getByText("Nothing has been checked yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run the checks" })).toBeInTheDocument();
  });
});

describe("ScoreCalculator", () => {
  const box = (name: RegExp) => screen.getByRole("checkbox", { name });
  const score = () => screen.getByRole("status").textContent ?? "";

  it("starts with one high finding: 30, medium", () => {
    render(<ScoreCalculator />);
    expect(score()).toMatch(/Risk score 30 of 100, medium/);
  });

  it("adds weights, picks the band and caps at 100", () => {
    render(<ScoreCalculator />);
    fireEvent.click(box(/director named/)); // 60
    expect(score()).toMatch(/Risk score 60 of 100, high/);
    fireEvent.click(box(/director named/)); // back to 30
    fireEvent.click(box(/printed gross profit/)); // 0
    expect(score()).toMatch(/Risk score 0 of 100, low/);
    fireEvent.click(screen.getByRole("button", { name: "Tick all" }));
    expect(score()).toMatch(/Risk score 100 of 100, high.*115 points, capped at 100/);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(score()).toMatch(/Risk score 0 of 100, low/);
  });

  it("is keyboard operable: every finding is a native checkbox", () => {
    render(<ScoreCalculator />);
    expect(screen.getAllByRole("checkbox")).toHaveLength(8);
  });

  it("states the documented defaults and that each finding counts once", () => {
    render(<ScoreCalculator />);
    expect(screen.getByText(/documented defaults/)).toBeInTheDocument();
    expect(screen.getByText(/counts once/)).toBeInTheDocument();
    expect(screen.getByText("Under 30")).toBeInTheDocument();
    expect(screen.getByText("30 to 59")).toBeInTheDocument();
    expect(screen.getByText("60 and over")).toBeInTheDocument();
  });
});

describe("ScoreExplainer", () => {
  it("opens the formula from the question mark and closes on Escape, returning focus", () => {
    render(<ScoreExplainer />);
    const q = screen.getByRole("button", { name: "How the risk score is calculated" });
    expect(q).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(q);
    expect(q).toHaveAttribute("aria-expanded", "true");
    const group = within(screen.getByRole("group", { name: "How the risk score is calculated" }));
    expect(group.getByText("High concern")).toBeInTheDocument();
    expect(group.getByText(/This case:/)).toHaveTextContent(/= 45, which is Medium/);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(q).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("group", { name: "How the risk score is calculated" })).not.toBeInTheDocument();
    expect(q).toHaveFocus();
  });

  it("closes on a click outside", () => {
    render(<ScoreExplainer />);
    fireEvent.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("group", { name: "How the risk score is calculated" })).not.toBeInTheDocument();
  });
});
