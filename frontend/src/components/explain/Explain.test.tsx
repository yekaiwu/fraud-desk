import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { clearViewport, setViewport } from "@/test/utils";
import { Explain } from "./Explain";
import { ExplainSection, ExplainTable, TechnicalDetail } from "./parts";

afterEach(() => {
  clearViewport();
  cleanup();
});

function Sample({ label = "How the risk score is calculated" }: { label?: string }) {
  return (
    <Explain label={label} title="Risk score">
      <p>Plain first paragraph.</p>
      <button type="button">Inside the panel</button>
    </Explain>
  );
}

describe("Explain", () => {
  it("is a small button named by its label, closed at first, and builds nothing until it opens", () => {
    render(<Sample />);
    const b = screen.getByRole("button", { name: "How the risk score is calculated" });
    expect(b).toHaveAttribute("aria-expanded", "false");
    expect(b).toHaveAttribute("aria-controls");
    expect(b).toHaveClass("h-5", "w-5", "rounded-full");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("Plain first paragraph.")).toBeNull();
  });

  it("opens a labelled panel, moves focus into it and links the button to it", async () => {
    const user = userEvent.setup();
    render(<Sample />);
    const b = screen.getByRole("button", { name: "How the risk score is calculated" });
    await user.click(b);
    const panel = screen.getByRole("dialog", { name: "Risk score" });
    expect(b).toHaveAttribute("aria-expanded", "true");
    expect(b.getAttribute("aria-controls")).toBe(panel.id);
    expect(panel).toHaveFocus();
    expect(within(panel).getByText("Plain first paragraph.")).toBeInTheDocument();
    expect(panel).not.toHaveAttribute("aria-modal");
  });

  it("closes on Escape and gives focus back to the button", async () => {
    const user = userEvent.setup();
    render(<Sample />);
    const b = screen.getByRole("button", { name: "How the risk score is calculated" });
    await user.click(b);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(b).toHaveFocus();
    expect(b).toHaveAttribute("aria-expanded", "false");
  });

  it("closes with its close button and returns focus", async () => {
    const user = userEvent.setup();
    render(<Sample />);
    const b = screen.getByRole("button", { name: "How the risk score is calculated" });
    await user.click(b);
    await user.click(screen.getByRole("button", { name: "Close: How the risk score is calculated" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(b).toHaveFocus();
  });

  it("closes on a click outside, without taking focus from what was clicked", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <Sample />
        <button type="button">Elsewhere</button>
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Elsewhere" })).toHaveFocus();
  });

  it("stays open when the click is inside the panel, and toggles shut from the button", async () => {
    const user = userEvent.setup();
    render(<Sample />);
    const b = screen.getByRole("button", { name: "How the risk score is calculated" });
    await user.click(b);
    await user.click(screen.getByRole("button", { name: "Inside the panel" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(b);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps Tab inside the open panel", async () => {
    const user = userEvent.setup();
    render(<Sample />);
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    await user.tab();
    expect(screen.getByRole("button", { name: "Close: How the risk score is calculated" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Inside the panel" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Close: How the risk score is calculated" })).toHaveFocus();
  });

  it("only one explainer is open at a time", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Explain label="First one" title="First">
          one
        </Explain>
        <Explain label="Second one" title="Second">
          two
        </Explain>
      </>,
    );
    await user.click(screen.getByRole("button", { name: "First one" }));
    await user.click(screen.getByRole("button", { name: "Second one" }));
    expect(screen.queryByRole("dialog", { name: "First" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Second" })).toBeInTheDocument();
  });

  it("is a popover on a wide screen and a bottom sheet with a scrim on a phone", async () => {
    const user = userEvent.setup();
    setViewport(1280);
    const { unmount } = render(<Sample />);
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    let panel = screen.getByRole("dialog");
    expect(panel).not.toHaveClass("bottom-0");
    expect(panel.style.position).toBe("fixed");
    expect(panel.style.width).not.toBe("");
    unmount();

    setViewport(390);
    render(<Sample />);
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    panel = screen.getByRole("dialog");
    expect(panel).toHaveClass("inset-x-0", "bottom-0", "rounded-t-xl");
    expect(document.querySelector(".bg-ink\\/40")).not.toBeNull();
    // tapping the scrim closes it
    await user.click(document.querySelector(".bg-ink\\/40") as HTMLElement);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("is not clipped by an overflow container: the panel lives outside it", async () => {
    const user = userEvent.setup();
    render(
      <div data-testid="clip" style={{ overflow: "hidden" }}>
        <table>
          <thead>
            <tr>
              <th>
                Risk <Sample />
              </th>
            </tr>
          </thead>
        </table>
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    expect(within(screen.getByTestId("clip")).queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("dialog").parentElement).toBe(document.body);
  });

  it("opens inside an open modal dialog when its button is in one", async () => {
    const user = userEvent.setup();
    render(
      <dialog open data-testid="host">
        <Sample />
      </dialog>,
    );
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    expect(screen.getByTestId("host")).toContainElement(screen.getByRole("dialog", { name: "Risk score" }));
  });

  it("does not trigger what it sits inside (a link or a row)", async () => {
    const user = userEvent.setup();
    let clicks = 0;
    render(
      <div onClick={() => clicks++}>
        <Sample />
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "How the risk score is calculated" }));
    expect(clicks).toBe(0);
  });
});

describe("explainer layout helpers", () => {
  it("renders a labelled table with row headers and a total row", () => {
    render(
      <ExplainTable
        caption="Points"
        columns={[{ label: "Concern" }, { label: "Points", numeric: true }]}
        rows={[{ key: "a", cells: ["High concern", 30] }]}
        foot={[{ key: "t", cells: ["Total", 30] }]}
      />,
    );
    const table = screen.getByRole("table", { name: "Points" });
    expect(within(table).getByRole("rowheader", { name: "High concern" })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "Total" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Points" })).toBeInTheDocument();
  });

  it("keeps technical detail collapsed until it is opened", async () => {
    const user = userEvent.setup();
    render(
      <ExplainSection title="Section">
        <TechnicalDetail>
          <p>Rule id</p>
        </TechnicalDetail>
      </ExplainSection>,
    );
    const details = document.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    await user.click(screen.getByText("Technical detail"));
    expect(details.open).toBe(true);
  });
});
