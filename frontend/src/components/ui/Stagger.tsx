import clsx from "clsx";
import type { CSSProperties, ElementType, ReactNode } from "react";

/**
 * The one entrance animation. Direct children fade and rise one after another (default 50 ms apart, the
 * first 10 only; later children share the last step). Use it once per page, around the main sections,
 * not on every list. prefers-reduced-motion switches it off (globals.css).
 *
 *   <Stagger className="space-y-6"><Panel .../><Panel .../></Stagger>
 *
 * For items that are not direct children, give them the `rise` class and `riseStyle(index)`.
 */
export function Stagger({
  as: Tag = "div",
  step = 50,
  className,
  style,
  children,
  ...rest
}: {
  as?: ElementType;
  /** Milliseconds between children, 40 to 60 reads best. */
  step?: number;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
} & Omit<React.HTMLAttributes<HTMLElement>, "style" | "className" | "children">) {
  return (
    <Tag className={clsx("stagger", className)} style={{ ...style, "--stagger-step": `${step}ms` } as CSSProperties} {...rest}>
      {children}
    </Tag>
  );
}

/** Inline style for a manually staggered item (pair with the `rise` class). Index is capped at 9. */
export function riseStyle(index: number): CSSProperties {
  return { "--i": Math.min(Math.max(0, Math.floor(index)), 9) } as CSSProperties;
}
