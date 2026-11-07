import { Loader2 } from "lucide-react";
import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { Availability } from "../api/types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("rounded-2xl border border-line bg-panel p-4 sm:p-5", className)}>{children}</div>
  );
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  busy,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; busy?: boolean }) {
  const styles: Record<ButtonVariant, string> = {
    primary: "bg-accent text-black hover:bg-amber-300",
    secondary: "bg-panel-2 text-gray-100 border border-line hover:border-gray-500",
    ghost: "text-muted hover:text-white hover:bg-panel-2",
    danger: "bg-red-500/10 text-red-300 border border-red-500/30 hover:bg-red-500/20",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-3.5 text-sm font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-50",
        styles[variant],
        className,
      )}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
  pending,
}: {
  checked: boolean | null;
  onChange: (on: boolean) => void;
  disabled?: boolean;
  label: string;
  pending?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked ?? false}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-40",
        checked ? "bg-accent" : "bg-line",
      )}
    >
      <span
        className={cx(
          "inline-flex size-6 items-center justify-center rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-7" : "translate-x-1",
        )}
      >
        {pending && <Loader2 className="size-3.5 animate-spin text-gray-500" aria-hidden />}
      </span>
    </button>
  );
}

/**
 * Range slider that shows the live drag value but only commits on release
 * (pointer up / keyboard), so the device receives one command per gesture.
 */
export function Slider({
  value,
  min,
  max,
  step = 1,
  onCommit,
  disabled,
  label,
  format,
  track,
}: {
  value: number | null;
  min: number;
  max: number;
  step?: number;
  onCommit: (value: number) => void;
  disabled?: boolean;
  label: string;
  format: (v: number) => string;
  track?: string;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const shown = draft ?? value ?? min;
  useEffect(() => setDraft(null), [value]);

  const commit = () => {
    if (draft !== null && draft !== value) onCommit(draft);
  };

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between text-sm">
        <span className="text-muted">{label}</span>
        <span className="tabular-nums text-gray-200">{value === null && draft === null ? "—" : format(shown)}</span>
      </div>
      <input
        type="range"
        className="slider"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={shown}
        disabled={disabled}
        style={track ? ({ "--track": track } as React.CSSProperties) : undefined}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
    </div>
  );
}

const AVAILABILITY: Record<Availability, { text: string; dot: string }> = {
  online: { text: "Online", dot: "bg-emerald-400" },
  offline: { text: "Offline", dot: "bg-red-400" },
  unknown: { text: "Άγνωστο", dot: "bg-gray-500" },
};

export function StatusDot({ availability }: { availability: Availability }) {
  const a = AVAILABILITY[availability];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className={cx("size-2 rounded-full", a.dot)} aria-hidden />
      {a.text}
    </span>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-2xl border border-dashed border-line px-6 py-12 text-center">
      <div className="mb-3 text-muted">{icon}</div>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-2 max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}
