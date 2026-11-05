import { act, cleanup, render, screen } from "@testing-library/react";
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

describe("ProcessingStepper polling", () => {
  const jobBody = (state: string) => ({
    id: "j_1", case_id: "c_1", kind: "analyse", state, created_at: "2026-10-27T10:00:00Z", started_at: null, finished_at: null,
    progress: { stage_index: 1, stage_count: 7, current_stage: "ingest" }, stages: [], error: null,
  });
  const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("one failed poll does not freeze the stepper: it recovers and reports the finish", async () => {
    vi.useFakeTimers();
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      n += 1;
      return n === 1 ? res({ error: { code: "internal", message: "bad gateway" } }, 502) : res(jobBody(n >= 3 ? "succeeded" : "running"));
    }));
    const onFinished = vi.fn();
    render(<ProcessingStepper jobId="j_1" onFinished={onFinished} onRerun={vi.fn()} />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(onFinished).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the error only after five failures in a row, and leaves no timer when it unmounts", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => res({ error: { code: "internal", message: "bad gateway" } }, 502));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<ProcessingStepper jobId="j_1" onFinished={vi.fn()} onRerun={vi.fn()} />);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    unmount();

    fetchMock.mockClear();
    const again = render(<ProcessingStepper jobId="j_2" onFinished={vi.fn()} onRerun={vi.fn()} />);
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    const seen = fetchMock.mock.calls.length;
    again.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(fetchMock.mock.calls.length).toBe(seen);
  });
});
