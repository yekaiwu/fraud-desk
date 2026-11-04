import { Ban, ListChecks } from "lucide-react";

/**
 * The two blocks of a partly supported request: what the saved rule WILL check and what it will NOT. Both lists are
 * written by the server from the rule itself, never from the model's wording.
 */
export function WillWillNot({ willCheck, willNotCheck }: { willCheck: string[]; willNotCheck: string[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <section aria-labelledby="will-check" className="rounded-lg border border-sev-low/30 border-l-[3px] border-l-sev-low bg-sev-low-tint p-4">
        <h4 id="will-check" className="flex items-center gap-2 text-[1rem] font-semibold text-ink">
          <ListChecks aria-hidden className="h-4 w-4 text-sev-low" />
          This rule WILL check
        </h4>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-[0.9375rem] text-ink marker:text-sev-low">
          {willCheck.map((t, i) => (
            <li key={i} className="break-words">
              {t}
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="will-not-check" className="rounded-lg border border-sev-medium/30 border-l-[3px] border-l-sev-medium bg-sev-medium-tint p-4">
        <h4 id="will-not-check" className="flex items-center gap-2 text-[1rem] font-semibold text-ink">
          <Ban aria-hidden className="h-4 w-4 text-sev-medium" />
          It will NOT check
        </h4>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-[0.9375rem] text-ink marker:text-sev-medium">
          {willNotCheck.map((t, i) => (
            <li key={i} className="break-words">
              {t}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
