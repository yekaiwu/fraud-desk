import clsx from "clsx";

/** A keyboard key, for shortcut hints: `Press <Kbd>j</Kbd> for the next concern`. */
export function Kbd({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <kbd className={clsx("inline-flex min-w-[1.5rem] items-center justify-center rounded-[5px] border border-rule-strong border-b-2 bg-surface px-1.5 py-px font-mono text-[0.75rem] font-medium leading-5 text-ink-2", className)}>
      {children}
    </kbd>
  );
}
