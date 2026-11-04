import { Copy } from "lucide-react";
import Link from "next/link";
import type { DuplicateNote } from "@/lib/types";
import { Notice } from "../ui/Notice";

/**
 * Rules that already check this. An identical built-in sends the officer to its severity setting in the catalogue; an
 * identical custom rule is named and blocks saving; a similar one is only a warning.
 */
export function DuplicateNotes({ notes }: { notes: DuplicateNote[] }) {
  if (notes.length === 0) return null;
  return (
    <div className="space-y-2.5" aria-label="Rules that already check something like this" role="group">
      {notes.map((n, i) => {
        const identical = n.level === "identical";
        return (
          <Notice
            key={`${n.rule}-${i}`}
            variant={identical ? "error" : "warning"}
            title={
              identical
                ? n.kind === "builtin"
                  ? "A built-in check already does exactly this"
                  : "You already have this rule"
                : n.kind === "builtin"
                  ? "A built-in check looks at something close"
                  : "A custom rule looks at something close"
            }
          >
            <p className="break-words">
              <Copy aria-hidden className="mr-1.5 inline h-3.5 w-3.5 text-muted" />
              {n.name ? <span className="font-medium text-ink">{n.name}. </span> : null}
              {n.what}
            </p>
            <p className="mt-1 break-words">{n.action}</p>
            {n.kind === "builtin" ? (
              <p className="mt-1.5">
                <Link href={`/rules?check=${encodeURIComponent(n.rule)}`} className="font-medium text-brand-2 underline underline-offset-2">
                  Open {n.rule} in the check catalogue
                </Link>
              </p>
            ) : (
              <p className="num mt-1 text-xs text-muted">Rule: {n.rule}</p>
            )}
          </Notice>
        );
      })}
    </div>
  );
}
