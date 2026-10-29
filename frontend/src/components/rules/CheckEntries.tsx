import { Pencil } from "lucide-react";
import { documentTypeLabel, effectiveDescription, effectiveName, isSwitchedOff, stageLabel } from "@/lib/checkCatalogue";
import { CheckStateBadges, EffectiveConcern } from "../catalogue/CheckStatus";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import type { RuleInfo } from "@/lib/types";

const MAX_CHIPS = 3;

/** Document types a check applies to, as chips. Long lists collapse to "+n more" with the full list on hover and for screen readers. */
export function AppliesTo({ types }: { types: string[] }) {
  if (types.length === 0) return <span className="text-sm text-muted">-</span>;
  const labels = types.map(documentTypeLabel);
  const shown = labels.length > MAX_CHIPS + 1 ? labels.slice(0, MAX_CHIPS) : labels;
  const hidden = labels.slice(shown.length);
  return (
    <ul aria-label="Applies to" className="flex flex-wrap gap-1.5">
      {shown.map((l) => (
        <li key={l}>
          <Badge variant="neutral">{l}</Badge>
        </li>
      ))}
      {hidden.length > 0 ? (
        <li>
          <Badge variant="status" title={hidden.join(", ")}>
            +{hidden.length} more
            <span className="sr-only">: {hidden.join(", ")}</span>
          </Badge>
        </li>
      ) : null}
    </ul>
  );
}

function IdLine({ r }: { r: RuleInfo }) {
  return (
    <span className="num text-xs text-muted">
      Check: <code className="font-mono">{r.rule}</code> <span aria-hidden>·</span> v{r.version}
    </span>
  );
}

const NO_TAMPER = "Not tied to one edit";

/** The check's name. With `onEdit` it is a button that opens the editor (the Edit button does the same). */
function NameLine({ r, onEdit, className }: { r: RuleInfo; onEdit?: (rule: string) => void; className: string }) {
  const name = effectiveName(r.rule, r.override);
  if (!onEdit) return <span className={className}>{name}</span>;
  return (
    <button type="button" onClick={() => onEdit(r.rule)} className={`${className} -mx-1 rounded px-1 text-left hover:text-brand-2 hover:underline hover:underline-offset-2`}>
      {name}
    </button>
  );
}

function Note({ r }: { r: RuleInfo }) {
  const note = r.override?.note?.trim();
  if (!note) return null;
  return (
    <span className="mt-1 block max-w-prose break-words text-sm text-ink-2">
      <span className="label-caps mr-1.5">Internal note</span>
      {note}
    </span>
  );
}

function EditButton({ r, onEdit }: { r: RuleInfo; onEdit?: (rule: string) => void }) {
  if (!onEdit) return null;
  return (
    <Button size="md" variant="secondary" icon={<Pencil aria-hidden className="h-3.5 w-3.5" />} onClick={() => onEdit(r.rule)} aria-label={`Edit ${effectiveName(r.rule, r.override)}`}>
      Edit
    </Button>
  );
}

/** Desktop: a compact table, one row per check. */
export function CheckTable({ rules, onEdit }: { rules: RuleInfo[]; onEdit?: (rule: string) => void }) {
  return (
    <div className="hidden overflow-x-auto md:block" role="region" aria-label="Checks" tabIndex={0}>
      <table className="w-full min-w-[56rem] text-left text-sm">
        <caption className="sr-only">Checks the system can run</caption>
        <thead className="bg-paper-2">
          <tr className="label-caps">
            <th scope="col" className="w-[36%] px-4 py-2.5">Check</th>
            <th scope="col" className="px-3 py-2.5">Tampering it catches</th>
            <th scope="col" className="px-3 py-2.5">Applies to</th>
            <th scope="col" className="px-3 py-2.5">Stage</th>
            <th scope="col" className="px-3 py-2.5">Concern</th>
            {onEdit ? (
              <th scope="col" className="px-3 py-2.5">
                <span className="sr-only">Edit</span>
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.rule} className={`border-t border-rule align-top hover:bg-paper-2/40 ${isSwitchedOff(r) ? "bg-paper-2/50" : ""}`}>
              <th scope="row" className="px-4 py-3 text-left font-normal">
                <NameLine r={r} onEdit={onEdit} className="block text-[0.9375rem] font-semibold leading-snug text-ink" />
                <span className="mt-1 block empty:hidden">
                  <CheckStateBadges r={r} />
                </span>
                <span className="mt-1 block max-w-prose break-words text-sm leading-snug text-ink-2">{effectiveDescription(r)}</span>
                <Note r={r} />
                <span className="mt-1 block">
                  <IdLine r={r} />
                </span>
              </th>
              <td className="px-3 py-3 text-ink-2">{r.tamper ?? <span className="text-muted">{NO_TAMPER}</span>}</td>
              <td className="px-3 py-3">
                <AppliesTo types={r.applies_to} />
              </td>
              <td className="px-3 py-3 text-ink-2">{stageLabel(r.stage)}</td>
              <td className="px-3 py-3">
                <EffectiveConcern r={r} />
              </td>
              {onEdit ? (
                <td className="px-3 py-3 text-right">
                  <EditButton r={r} onEdit={onEdit} />
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Phone: one card per check. */
export function CheckCards({ rules, onEdit }: { rules: RuleInfo[]; onEdit?: (rule: string) => void }) {
  return (
    <ul aria-label="Checks" className="space-y-3 p-3 md:hidden">
      {rules.map((r) => (
        <li key={r.rule} className={`rounded-lg border border-rule p-3.5 ${isSwitchedOff(r) ? "bg-paper-2/50" : "bg-surface"}`}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="min-w-0 break-words">
              <NameLine r={r} onEdit={onEdit} className="text-[1rem] font-semibold leading-snug text-ink" />
            </p>
            <EffectiveConcern r={r} />
          </div>
          <div className="mt-1.5 empty:hidden">
            <CheckStateBadges r={r} />
          </div>
          <p className="mt-1 break-words text-sm text-ink-2">{effectiveDescription(r)}</p>
          <Note r={r} />
          <dl className="mt-3 space-y-2 text-sm">
            {r.tamper ? (
              <div>
                <dt className="label-caps">Tampering it catches</dt>
                <dd className="text-ink">{r.tamper}</dd>
              </div>
            ) : null}
            <div>
              <dt className="label-caps mb-1">Applies to</dt>
              <dd>
                <AppliesTo types={r.applies_to} />
              </dd>
            </div>
            <div>
              <dt className="label-caps">Stage</dt>
              <dd className="text-ink">{stageLabel(r.stage)}</dd>
            </div>
          </dl>
          <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-rule pt-2">
            <IdLine r={r} />
            <EditButton r={r} onEdit={onEdit} />
          </div>
        </li>
      ))}
    </ul>
  );
}
