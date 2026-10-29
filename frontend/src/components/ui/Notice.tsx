import clsx from "clsx";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Inline banner for the outcome of an action or a page-level warning. Errors are announced at once
 * (role="alert"); the others politely (role="status"). Put the next step in `action`.
 *
 *   <Notice variant="success" title="Decision recorded">Saved to the review log.</Notice>
 *   <Notice variant="error" title="Could not save" action={<Button size="sm" variant="secondary">Try again</Button>}>Check your connection.</Notice>
 */
export type NoticeVariant = "success" | "warning" | "error" | "info";

const STYLE: Record<NoticeVariant, { box: string; icon: string; Icon: LucideIcon }> = {
  success: { box: "border-sev-low/30 border-l-sev-low bg-sev-low-tint", icon: "text-sev-low", Icon: CheckCircle2 },
  warning: { box: "border-sev-medium/30 border-l-sev-medium bg-sev-medium-tint", icon: "text-sev-medium", Icon: AlertTriangle },
  error: { box: "border-sev-high/30 border-l-sev-high bg-sev-high-tint", icon: "text-sev-high", Icon: AlertOctagon },
  info: { box: "border-sev-info/30 border-l-sev-info bg-sev-info-tint", icon: "text-sev-info", Icon: Info },
};

export function Notice({
  variant = "info",
  title,
  children,
  action,
  onDismiss,
  role,
  className,
}: {
  variant?: NoticeVariant;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
  role?: "status" | "alert" | "note";
  className?: string;
}) {
  const { box, icon, Icon } = STYLE[variant];
  return (
    <div role={role ?? (variant === "error" ? "alert" : "status")} data-variant={variant} className={clsx("flex gap-3 rounded-md border border-l-[3px] px-3.5 py-3 text-ink", box, className)}>
      <Icon aria-hidden className={clsx("mt-0.5 h-5 w-5 shrink-0", icon)} />
      <div className="min-w-0 flex-1 text-[0.9375rem]">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={clsx("text-ink-2", title && "mt-0.5")}>{children}</div> : null}
        {action ? <div className="mt-2.5">{action}</div> : null}
      </div>
      {onDismiss ? (
        <button type="button" onClick={onDismiss} aria-label="Close message" className="-m-2 grid h-11 w-11 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-ink/5">
          <X aria-hidden className="h-4 w-4" />
        </button>
      ) : null}
    </div>
  );
}
