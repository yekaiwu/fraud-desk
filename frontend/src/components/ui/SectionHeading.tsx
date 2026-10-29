import clsx from "clsx";
import type { ElementType, ReactNode } from "react";

/**
 * Heading with a bank-statement rule (a hairline over a thicker line). One <h1> per page.
 *
 *   <SectionHeading level={1} size="page" eyebrow="Worklist" description="Highest risk first.">Cases to review</SectionHeading>
 *   <SectionHeading level={2}>Why this case is risky</SectionHeading>
 *
 * size "page": large serif page title; "section" (default): ruled section title; "minor": small caps label, no rule.
 */
export type HeadingLevel = 1 | 2 | 3 | 4;

const SIZE = {
  page: "text-[1.75rem] leading-tight sm:text-[2.25rem]",
  section: "text-[1.25rem] leading-snug sm:text-[1.375rem]",
  minor: "font-sans text-[0.72rem] font-semibold uppercase leading-snug tracking-[0.09em] text-muted",
} as const;

export function SectionHeading({
  level = 2,
  size = "section",
  eyebrow,
  description,
  actions,
  rule = size !== "minor",
  className,
  id,
  children,
}: {
  level?: HeadingLevel;
  size?: keyof typeof SIZE;
  eyebrow?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** Draw the double rule underneath. */
  rule?: boolean;
  className?: string;
  id?: string;
  children: ReactNode;
}) {
  const Tag = `h${level}` as ElementType;
  return (
    <div className={clsx("flex flex-wrap items-end justify-between gap-x-6 gap-y-3", rule && "rule-statement", className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="label-caps mb-1">{eyebrow}</p> : null}
        <Tag id={id} className={clsx(SIZE[size])}>
          {children}
        </Tag>
        {description ? <p className="mt-1 max-w-prose text-[0.9375rem] text-ink-2">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
