import clsx from "clsx";
import { CheckCircle2, Circle } from "lucide-react";
import { plural } from "@/lib/labels";
import { caseTypeOf, type Recognition } from "@/lib/uploadChecklist";
import type { UseCase } from "@/lib/types";

/**
 * "Documents usually needed" for the chosen case type. Ticks itself as files are recognised by file name
 * and type only; a missing item is a prompt, never a block.
 */
export function DocumentChecklist({ useCase, recognition, fileCount }: { useCase: UseCase; recognition: Recognition; fileCount: number }) {
  const total = recognition.rows.length;
  const missing = recognition.missing.length;
  const needsAttention = fileCount > 0 && missing > 0;
  return (
    <section
      aria-labelledby="checklist-title"
      className={clsx("rounded-lg border p-4", needsAttention ? "border-rule border-l-[3px] border-l-brass bg-surface" : "border-rule bg-paper-2/60")}
    >
      <p className="label-caps">{caseTypeOf(useCase).title}</p>
      <h3 id="checklist-title" className="font-display text-[1.0625rem] font-semibold">
        Documents usually needed
      </h3>
      <p className="mt-1 text-sm text-muted">Based on file names; the system reads the content afterwards. Nothing here stops you from starting.</p>
      <p role="status" className={clsx("mt-3 text-[0.9375rem] font-semibold", needsAttention ? "text-brass-deep" : "text-ink-2")}>
        {fileCount === 0
          ? `${total} kinds of document are usually needed.`
          : missing === 0
            ? `All ${total} usual kinds of document look covered.`
            : `${recognition.foundCount} of ${total} usual kinds added; ${missing} not yet added.`}
      </p>
      <ul className="mt-2 divide-y divide-rule">
        {recognition.rows.map((r) => (
          <li key={r.id} className="flex items-start gap-3 py-2.5" data-found={r.found}>
            {r.found ? <CheckCircle2 aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-sev-low" /> : <Circle aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-rule-strong" strokeDasharray="3 3" />}
            <div className="min-w-0 flex-1">
              <p className="text-[0.9375rem] font-semibold leading-snug text-ink">{r.label}</p>
              <p className="text-sm text-muted">{r.found ? `${r.files.length} ${plural(r.files.length, "file")}: ${r.files.slice(0, 2).join(", ")}${r.files.length > 2 ? `, and ${r.files.length - 2} more` : ""}` : r.hint}</p>
            </div>
            <span className={clsx("shrink-0 pt-0.5 text-sm font-semibold", r.found ? "text-sev-low" : "text-ink-2")}>{r.found ? "Added" : "Not yet added"}</span>
          </li>
        ))}
      </ul>
      {recognition.unplaced.length > 0 ? (
        <p className="mt-2 border-t border-rule pt-2.5 text-sm text-ink-2">
          {recognition.unplaced.length} {plural(recognition.unplaced.length, "file")} could not be matched by name. {recognition.unplaced.length === 1 ? "It is" : "They are"} still uploaded and read.
        </p>
      ) : null}
    </section>
  );
}
