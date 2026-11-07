import { Loader2 } from "lucide-react";
import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { Availability } from "../api/types";
import { useLang, useT, type MessageKey } from "../lib/i18n";

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
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-3.5 text-sm font-medium select-none",
        "transition-[color,background-color,border-color,transform] duration-150 active:scale-[0.97] pointer-coarse:min-h-11",
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
        "relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition-colors duration-200",
        // invisible 48px hit area around the 32px track
        "after:absolute after:-inset-2 after:content-['']",
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

const AVAILABILITY: Record<Availability, { key: MessageKey; dot: string }> = {
  online: { key: "avail.online", dot: "bg-emerald-400" },
  offline: { key: "avail.offline", dot: "bg-red-400" },
  unknown: { key: "avail.unknown", dot: "bg-gray-500" },
};

export function StatusDot({ availability }: { availability: Availability }) {
  const t = useT();
  const a = AVAILABILITY[availability];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className={cx("size-2 rounded-full", a.dot)} aria-hidden />
      {t(a.key)}
    </span>
  );
}

/** EL / EN switch. */
export function LanguageSwitch({ className }: { className?: string }) {
  const lang = useLang((s) => s.lang);
  const setLang = useLang((s) => s.setLang);
  const t = useT();
  return (
    <div role="group" aria-label={t("lang.label")} className={cx("inline-flex rounded-lg border border-line p-0.5 text-xs", className)}>
      {(["el", "en"] as const).map((l) => (
        <button
          key={l}
          type="button"
          aria-pressed={lang === l}
          onClick={() => setLang(l)}
          className={cx(
            "min-h-8 min-w-10 rounded-md px-2 font-medium uppercase transition-colors",
            lang === l ? "bg-panel-2 text-white" : "text-muted hover:text-white",
          )}
        >
          {l}
        </button>
      ))}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · Lychnos`;
  }, [title]);
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

/** Round colour preset button; 44px on touch screens. */
export const swatchClass =
  "size-9 shrink-0 rounded-full border-2 border-white/10 transition-transform duration-150 hover:scale-110 active:scale-95 disabled:opacity-40 disabled:hover:scale-100 pointer-coarse:size-11";
