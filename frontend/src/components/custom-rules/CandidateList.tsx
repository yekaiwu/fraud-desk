import type { CandidateView } from "@/lib/types";
import { Button } from "../ui/Button";

/** An ambiguous request: each reading in plain words (built by code from the spec), one button per reading. */
export function CandidateList({ candidates, busy, onChoose }: { candidates: CandidateView[]; busy: boolean; onChoose: (index: number) => void }) {
  return (
    <ul aria-label="Possible readings" className="space-y-2.5">
      {candidates.map((c, i) => (
        <li key={c.index} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rule bg-surface p-4 shadow-paper">
          <p className="min-w-0 flex-1 break-words text-[0.9375rem] text-ink">
            <span className="label-caps mr-2">Reading {i + 1}</span>
            {c.readback}
          </p>
          <Button variant="secondary" disabled={busy} onClick={() => onChoose(c.index)}>
            Use this one{" "}<span className="sr-only">(reading {i + 1})</span>
          </Button>
        </li>
      ))}
    </ul>
  );
}
