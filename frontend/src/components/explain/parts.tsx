import clsx from "clsx";
import { Check, Minus, X } from "lucide-react";
import type { ReactNode } from "react";

/** The plain-English first paragraph of an explainer. */
export function ExplainLead({ children }: { children: ReactNode }) {
  return <p className="text-ink">{children}</p>;
}

/** A titled block inside an explainer ("This case", "How it is decided"). */
export function ExplainSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="label-caps rule-statement rule-statement-soft font-sans !pb-1.5">{title}</h3>
      {children}
    </section>
  );
}

export type ExplainColumn = { label: string; numeric?: boolean };
export type ExplainRow = { key: string; cells: ReactNode[]; highlight?: boolean; strong?: boolean };

/** Small ruled table of the numbers behind a result. The first cell of each row is its header. */
export function ExplainTable({ columns, rows, foot, caption }: { columns: ExplainColumn[]; rows: ExplainRow[]; foot?: ExplainRow[]; caption: string }) {
  const body = (r: ExplainRow, section: "body" | "foot") => (
    <tr key={r.key} className={clsx(section === "body" && "border-t border-rule", r.highlight && "bg-brand-tint", (r.strong || section === "foot") && "font-semibold text-ink")}>
      {r.cells.map((cell, i) =>
        i === 0 ? (
          <th key={i} scope="row" className="px-2 py-1.5 text-left font-[inherit]">
            {cell}
          </th>
        ) : (
          <td key={i} className={clsx("px-2 py-1.5", columns[i]?.numeric && "tabular text-right")}>
            {cell}
          </td>
        ),
      )}
    </tr>
  );
  return (
    <div className="overflow-x-auto rounded-md border border-rule">
      <table className="w-full min-w-[17rem] border-collapse text-sm text-ink-2">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-paper-2">
          <tr>
            {columns.map((c, i) => (
              <th key={i} scope="col" className={clsx("label-caps px-2 py-1.5", c.numeric ? "text-right" : "text-left")}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.map((r) => body(r, "body"))}</tbody>
        {foot && foot.length > 0 ? <tfoot className="border-t-2 border-rule-strong">{foot.map((r) => body(r, "foot"))}</tfoot> : null}
      </table>
    </div>
  );
}

/** A caveat or reminder that should not be skimmed past, in a calm well. */
export function ExplainNote({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-rule bg-paper-2/70 px-3 py-2 text-sm text-ink">{children}</p>;
}

/** Collapsed by default; the exact method and identifiers for an auditor. */
export function TechnicalDetail({ children, label = "Technical detail" }: { children: ReactNode; label?: string }) {
  return (
    <details className="group rounded-md border border-rule bg-paper/60">
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1.5 rounded-md px-2.5 text-sm font-semibold text-brand-2 hover:bg-brand-tint pointer-coarse:min-h-11 [&::-webkit-details-marker]:hidden">
        <span aria-hidden className="inline-block w-3 text-xs transition-transform duration-150 group-open:rotate-90">
          ›
        </span>
        {label}
      </summary>
      <div className="space-y-2 border-t border-rule px-2.5 py-2.5 text-sm text-ink-2">{children}</div>
    </details>
  );
}

/** Label and value pairs, for identifiers and settings. */
export function ExplainFacts({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      {items.map((i) => (
        <div key={i.label} className="contents">
          <dt className="text-muted">{i.label}</dt>
          <dd className="min-w-0 break-words text-ink">{i.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Met, not met, or not known: an icon and words, never colour alone. */
export function Tick({ ok }: { ok: boolean | null }) {
  if (ok === null) {
    return (
      <span className="inline-flex shrink-0 items-center text-muted">
        <Minus aria-hidden className="h-4 w-4" />
        <span className="sr-only">Cannot be shown here</span>
      </span>
    );
  }
  return ok ? (
    <span className="inline-flex shrink-0 items-center text-sev-low">
      <Check aria-hidden className="h-4 w-4" />
      <span className="sr-only">Met</span>
    </span>
  ) : (
    <span className="inline-flex shrink-0 items-center text-sev-high">
      <X aria-hidden className="h-4 w-4" />
      <span className="sr-only">Not met</span>
    </span>
  );
}

/** Shown where the method response is needed but could not be fetched. The case's own numbers still appear around it. */
export function MethodUnavailable() {
  return (
    <p role="status" className="rounded-md border border-dashed border-rule-strong px-3 py-2 text-sm text-ink-2">
      The method details are not available right now.
    </p>
  );
}

export function Loading({ what = "Loading the method" }: { what?: string }) {
  return (
    <p role="status" className="text-sm text-muted">
      {what}...
    </p>
  );
}
