"use client";

import { SlidersHorizontal } from "lucide-react";
import { plural, severityLabel } from "@/lib/labels";
import type { CatalogueProvenance } from "@/lib/types";
import { useCheckName } from "@/lib/useCheckNames";

/**
 * A quiet line for the analysis trail: this case was analysed with check-catalogue settings that differ from the
 * defaults. Renders nothing when `catalogue` is absent (the server only records it when overrides existed).
 */
export function CustomCatalogueNote({ catalogue }: { catalogue?: CatalogueProvenance | null }) {
  const nameOf = useCheckName();
  const list = catalogue?.overrides ?? [];
  if (list.length === 0) return null;
  return (
    <div className="text-sm text-ink-2" data-testid="custom-catalogue-note">
      <p className="flex items-start gap-2">
        <SlidersHorizontal aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
        <span className="tabular">
          Custom catalogue settings were used: {list.length} {plural(list.length, "check")} changed
        </span>
      </p>
      <details className="mt-1 pl-6">
        <summary className="cursor-pointer font-medium text-brand-2 underline underline-offset-2">Which checks</summary>
        <ul className="mt-1.5 space-y-1">
          {list.map((o) => (
            <li key={o.rule} title={o.rule} className="break-words">
              <span className="font-medium text-ink">{nameOf(o.rule)}</span>
              {": "}
              {[o.enabled ? null : "switched off", o.severity ? `severity set to ${severityLabel(o.severity).toLowerCase()}` : null].filter(Boolean).join(", ") || "other settings changed"}
              <span className="num text-xs text-muted"> (revision {o.revision})</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
