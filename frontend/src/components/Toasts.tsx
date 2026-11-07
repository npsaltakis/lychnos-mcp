import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useToasts, type ToastLevel } from "../store/toasts";
import { cx } from "./ui";

const STYLE: Record<ToastLevel, { icon: typeof Info; cls: string }> = {
  success: { icon: CheckCircle2, cls: "text-emerald-400" },
  info: { icon: Info, cls: "text-sky-300" },
  warning: { icon: AlertTriangle, cls: "text-amber-300" },
  error: { icon: XCircle, cls: "text-red-400" },
};

export function Toasts() {
  const { toasts, dismiss } = useToasts();
  return (
    <div
      className="pointer-events-none fixed inset-x-4 bottom-20 z-50 flex flex-col items-end gap-2 md:bottom-6 md:left-auto md:right-6 md:w-96"
      aria-live="polite"
    >
      {toasts.map((t) => {
        const { icon: Icon, cls } = STYLE[t.level];
        return (
          <div
            key={t.id}
            role={t.level === "error" ? "alert" : "status"}
            className="pointer-events-auto flex w-full items-start gap-3 rounded-xl border border-line bg-panel-2/95 p-3 shadow-xl backdrop-blur"
          >
            <Icon className={cx("mt-0.5 size-5 shrink-0", cls)} aria-hidden />
            <div className="min-w-0 flex-1 text-sm">
              <p className="font-medium">{t.title}</p>
              {t.message && <p className="mt-0.5 text-muted">{t.message}</p>}
            </div>
            <button onClick={() => dismiss(t.id)} aria-label="Κλείσιμο" className="text-muted hover:text-white">
              <X className="size-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
