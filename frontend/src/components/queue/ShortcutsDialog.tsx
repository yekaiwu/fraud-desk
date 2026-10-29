"use client";

import { Kbd } from "../ui";
import { Modal } from "../Modal";

const SHORTCUTS: { keys: string[]; what: string }[] = [
  { keys: ["j", "↓"], what: "Move to the next case" },
  { keys: ["k", "↑"], what: "Move to the previous case" },
  { keys: ["Enter"], what: "Open the case you are on" },
  { keys: ["/"], what: "Search by applicant or reference" },
  { keys: ["?"], what: "Show this list" },
  { keys: ["Esc"], what: "Close this list" },
];

export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Keyboard shortcuts">
      <p className="mb-3 text-sm text-ink-2">The shortcuts work on the worklist whenever you are not typing in a box.</p>
      <dl className="divide-y divide-rule">
        {SHORTCUTS.map((s) => (
          <div key={s.what} className="flex items-center justify-between gap-4 py-2.5">
            <dt className="text-[0.9375rem] text-ink">{s.what}</dt>
            <dd className="flex shrink-0 items-center gap-1.5">
              {s.keys.map((k, i) => (
                <span key={k} className="flex items-center gap-1.5">
                  {i > 0 ? <span className="text-xs text-muted">or</span> : null}
                  <Kbd>{k}</Kbd>
                </span>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}
