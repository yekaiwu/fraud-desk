import { Skeleton } from "../ui";

/** Skeleton rows with a status message: the skeleton itself is decorative. */
export function LoadingBlock({ label, rows = 1, className = "h-24" }: { label: string; rows?: number; className?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`${className} rounded-md`} />
      ))}
    </div>
  );
}
