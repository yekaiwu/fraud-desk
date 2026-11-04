import { Lightbulb } from "lucide-react";
import Link from "next/link";
import type { Idea } from "@/lib/types";
import { Button } from "../ui/Button";

const KIND_TITLE: Record<Idea["kind"], string> = {
  weaker_rule: "A weaker rule you could save",
  related_builtin: "A built-in check that already looks at something close",
  template: "What a custom rule can do here",
};

/** What IS possible and close to the request. A weaker rule can be tried as a new request. */
export function IdeasList({ ideas, onTry }: { ideas: Idea[]; onTry?: (text: string) => void }) {
  if (ideas.length === 0) return null;
  return (
    <section aria-labelledby="ideas-title" className="rounded-lg border border-rule bg-paper-2/60 p-4">
      <h4 id="ideas-title" className="flex items-center gap-2 text-[1rem] font-semibold text-ink">
        <Lightbulb aria-hidden className="h-4 w-4 text-brass-deep" />
        What is possible instead
      </h4>
      <ul className="mt-2 space-y-2.5">
        {ideas.map((idea, i) => (
          <li key={i} className="text-[0.9375rem] text-ink">
            <p className="label-caps">{KIND_TITLE[idea.kind]}</p>
            <p className="break-words">{idea.text}</p>
            <div className="mt-1 flex flex-wrap gap-2">
              {idea.kind === "related_builtin" && idea.rule ? (
                <Link href={`/rules?check=${encodeURIComponent(idea.rule)}`} className="text-sm font-medium text-brand-2 underline underline-offset-2">
                  Open this check in the catalogue
                </Link>
              ) : null}
              {idea.kind === "weaker_rule" && onTry ? (
                <Button size="sm" variant="secondary" onClick={() => onTry(idea.text)}>
                  Use this as my request
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
