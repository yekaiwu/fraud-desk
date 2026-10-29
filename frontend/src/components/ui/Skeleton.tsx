import clsx from "clsx";

/**
 * Loading placeholder. Decorative (aria-hidden): pair a group of them with a visible or sr-only
 * `role="status"` message such as "Loading cases...".
 *
 *   <Skeleton className="h-6 w-40" />     <SkeletonText lines={3} />
 */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("skeleton h-4 w-full", className)} />;
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div aria-hidden className={clsx("space-y-2.5", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={clsx("h-3.5", i === lines - 1 && lines > 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}
