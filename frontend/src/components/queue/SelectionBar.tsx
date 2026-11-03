"use client";

import { CheckSquare, Trash2 } from "lucide-react";
import { Button } from "../ui";

/**
 * The control of "Select and delete": a button that turns tick boxes on, and while they are on, how many cases
 * are ticked with the actions that apply to them.
 */
export function SelectionBar({ selecting, count, pageCount, onStart, onDone, onSelectPage, onClear, onDelete }: { selecting: boolean; count: number; pageCount: number; onStart: () => void; onDone: () => void; onSelectPage: () => void; onClear: () => void; onDelete: () => void }) {
  if (!selecting) {
    return (
      <Button size="sm" variant="secondary" onClick={onStart} icon={<CheckSquare aria-hidden className="h-4 w-4" />}>
        Select and delete
      </Button>
    );
  }
  return (
    <div role="region" aria-label="Delete selected cases" className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-rule bg-paper-2 px-4 py-2.5">
      <p className="text-sm text-ink-2" aria-live="polite">
        <span className="font-semibold text-ink tabular">{count}</span> selected
      </p>
      <Button size="sm" variant="secondary" onClick={onSelectPage} disabled={pageCount === 0}>
        Select all on this page
      </Button>
      <Button size="sm" variant="secondary" onClick={onClear} disabled={count === 0}>
        Clear
      </Button>
      <span className="ml-auto flex items-center gap-2">
        <Button size="sm" variant="danger" onClick={onDelete} disabled={count === 0} icon={<Trash2 aria-hidden className="h-4 w-4" />}>
          Delete {count > 0 ? count : ""} selected
        </Button>
        <Button size="sm" variant="secondary" onClick={onDone}>
          Done
        </Button>
      </span>
    </div>
  );
}
