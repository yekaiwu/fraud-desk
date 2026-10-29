import clsx from "clsx";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, MouseEvent, ReactNode, Ref } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-md border font-semibold leading-none whitespace-nowrap select-none " +
  "transition-[background-color,border-color,color,box-shadow,transform] duration-150 active:translate-y-px " +
  "aria-disabled:cursor-not-allowed aria-disabled:opacity-55 disabled:cursor-not-allowed disabled:opacity-55";

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    "border-brand bg-brand text-on-brand shadow-[inset_0_1px_0_rgb(255_255_255/0.14),var(--elev-paper)] hover:bg-brand-2 hover:border-brand-2",
  secondary: "border-rule-strong bg-surface text-brand shadow-paper hover:border-brand-2 hover:bg-brand-tint",
  ghost: "border-transparent bg-transparent text-brand-2 hover:bg-brand-tint",
  danger:
    "border-sev-high bg-sev-high text-surface shadow-[inset_0_1px_0_rgb(255_255_255/0.14),var(--elev-paper)] hover:border-[#85190f] hover:bg-[#85190f]",
};

// 44 px touch target everywhere except `sm` on a mouse, which is 36 px and grows to 44 px on touch screens.
const SIZE: Record<ButtonSize, string> = {
  sm: "min-h-9 px-3 text-sm pointer-coarse:min-h-11",
  md: "min-h-11 px-4 text-[0.9375rem]",
  lg: "min-h-12 px-6 text-base",
};

/** Class string for anything that must look like a button (a <label>, a <summary>, a router link). */
export function buttonClasses({ variant = "primary", size = "md", fullWidth = false, className }: { variant?: ButtonVariant; size?: ButtonSize; fullWidth?: boolean; className?: string } = {}): string {
  return clsx(BASE, VARIANT[variant], SIZE[size], fullWidth && "w-full", className);
}

type Common = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, sets aria-busy and blocks clicks. Keep the label: it says what is happening. */
  loading?: boolean;
  /** Leading icon (a lucide element with aria-hidden). */
  icon?: ReactNode;
  iconRight?: ReactNode;
  fullWidth?: boolean;
  children?: ReactNode;
  className?: string;
};
type AsButton = Common & { href?: undefined; ref?: Ref<HTMLButtonElement> } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, keyof Common>;
type AsLink = Common & { href: string; ref?: Ref<HTMLAnchorElement> } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof Common | "href">;
export type ButtonProps = AsButton | AsLink;

/**
 * Button, or a link that looks like one when `href` is given (internal hrefs use next/link).
 *
 *   <Button onClick={save} loading={saving}>Record decision</Button>
 *   <Button href="/upload" variant="secondary" icon={<Plus aria-hidden className="h-4 w-4" />}>New case</Button>
 */
export function Button(props: ButtonProps) {
  const { variant, size, loading = false, icon, iconRight, fullWidth, className, children } = props;
  const classes = buttonClasses({ variant, size, fullWidth, className });
  const content = (
    <>
      {loading ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : icon}
      {children}
      {iconRight}
    </>
  );

  if (props.href !== undefined) {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { variant: _v, size: _s, loading: _l, icon: _i, iconRight: _r, fullWidth: _f, className: _c, children: _ch, href, ref, onClick, ...rest } = props;
    const disabled = loading;
    const external = /^(https?:|mailto:|tel:)/.test(href) || rest.download !== undefined;
    const common = {
      ...rest,
      className: classes,
      "aria-disabled": disabled || undefined,
      "aria-busy": loading || undefined,
      tabIndex: disabled ? -1 : rest.tabIndex,
      onClick: (e: MouseEvent<HTMLAnchorElement>) => {
        if (disabled) {
          e.preventDefault();
          return;
        }
        onClick?.(e);
      },
    };
    return external ? (
      <a ref={ref} href={href} {...common}>
        {content}
      </a>
    ) : (
      <Link ref={ref} href={href} {...common}>
        {content}
      </Link>
    );
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { variant: _v, size: _s, loading: _l, icon: _i, iconRight: _r, fullWidth: _f, className: _c, children: _ch, href: _h, ref, type, disabled, ...rest } = props;
  return (
    <button ref={ref} type={type ?? "button"} disabled={disabled || loading} aria-busy={loading || undefined} className={classes} {...rest}>
      {content}
    </button>
  );
}
