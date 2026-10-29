"use client";

import clsx from "clsx";
import { ChevronRight } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { usePrefersReducedMotion } from "@/lib/useMedia";

/**
 * Content that fades and slides in by a few pixels when it appears. Uses the Web Animations API so it
 * needs no extra CSS, does nothing where the API is missing (tests) and is skipped under reduced motion.
 */
export function Reveal({ children, className, id }: { children: ReactNode; className?: string; id?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || reduced || typeof el.animate !== "function") return;
    el.animate([{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "none" }], { duration: 160, easing: "ease-out" });
  }, [reduced]);
  return (
    <div ref={ref} id={id} className={className}>
      {children}
    </div>
  );
}

/**
 * A quiet disclosure: a text button with a turning chevron that shows or hides its children. Controlled when
 * `open` is given, otherwise it keeps its own state. Used for Evidence, instance lists and similar detail.
 */
export function Disclosure({
  label,
  open: controlled,
  defaultOpen = false,
  onOpenChange,
  meta,
  children,
  className,
  buttonClassName,
}: {
  label: ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Small text after the label, e.g. a count. */
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  buttonClassName?: string;
}) {
  const [own, setOwn] = useState(defaultOpen);
  const open = controlled ?? own;
  const bodyId = useId();
  const toggle = () => {
    const next = !open;
    if (controlled === undefined) setOwn(next);
    onOpenChange?.(next);
  };
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={toggle}
        className={clsx(
          "-mx-1 inline-flex min-h-9 items-center gap-1.5 rounded-md px-1 text-sm font-semibold text-brand-2 hover:bg-brand-tint pointer-coarse:min-h-11",
          buttonClassName,
        )}
      >
        <ChevronRight aria-hidden className={clsx("h-4 w-4 shrink-0 transition-transform duration-150", open && "rotate-90")} />
        {label}
        {meta ? <span className="font-normal text-muted">{meta}</span> : null}
      </button>
      {open ? (
        <Reveal id={bodyId} className="mt-1.5">
          {children}
        </Reveal>
      ) : null}
    </div>
  );
}
