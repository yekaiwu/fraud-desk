import clsx from "clsx";
import { useId, type ElementType, type ReactNode } from "react";
import type { HeadingLevel } from "./SectionHeading";

/**
 * Raised sheet of paper: surface fill, hairline border, soft ink shadow. With a `title` it gets a ruled
 * header (hairline over a thicker line, in the soft tone) and becomes a labelled region.
 *
 *   <Panel title="Findings" eyebrow="3 to check" actions={<Button size="sm" variant="ghost">Expand all</Button>}>...</Panel>
 *   <Panel tone="attention" padding="none">...table...</Panel>
 */
export function Panel({
  title,
  eyebrow,
  actions,
  footer,
  tone = "default",
  padding = "md",
  headingLevel = 2,
  as: Tag = "section",
  className,
  bodyClassName,
  children,
}: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  /** "attention" adds a brass edge ("needs your attention"); "sunken" is a quieter paper well. */
  tone?: "default" | "attention" | "sunken";
  padding?: "md" | "sm" | "none";
  headingLevel?: HeadingLevel;
  as?: ElementType;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}) {
  const titleId = useId();
  const Heading = `h${headingLevel}` as ElementType;
  const hasHeader = Boolean(title || actions);
  const pad =
    padding === "none" ? "" : padding === "sm" ? (hasHeader ? "px-3 pb-3 pt-3" : "p-3") : hasHeader ? "px-4 pb-4 pt-3.5 sm:px-5 sm:pb-5" : "p-4 sm:p-5";
  return (
    <Tag
      aria-labelledby={title ? titleId : undefined}
      className={clsx(
        "rounded-lg border",
        tone === "sunken" ? "border-rule bg-paper-2/60" : "border-rule bg-surface shadow-paper",
        tone === "attention" && "border-l-[3px] border-l-brass",
        className,
      )}
    >
      {hasHeader ? (
        <div className="px-4 pt-3.5 sm:px-5 sm:pt-4">
          <div className="rule-statement rule-statement-soft flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
            <div className="min-w-0">
              {eyebrow ? <p className="label-caps mb-0.5">{eyebrow}</p> : null}
              {title ? (
                <Heading id={titleId} className="text-[1.0625rem] leading-snug">
                  {title}
                </Heading>
              ) : null}
            </div>
            {actions ? <div className="flex flex-wrap items-center gap-2 pb-0.5">{actions}</div> : null}
          </div>
        </div>
      ) : null}
      <div className={clsx(pad, bodyClassName)}>{children}</div>
      {footer ? <div className="rounded-b-lg border-t border-rule bg-paper-2/50 px-4 py-3 text-sm text-ink-2 sm:px-5">{footer}</div> : null}
    </Tag>
  );
}
