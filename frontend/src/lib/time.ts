import { locale, t, type MessageKey } from "./i18n";

/** Short weekday name; Monday = 0, as in the backend. */
export const dayShort = (d: number) => t(`day.${d}` as MessageKey);

const hhmm = (d: Date) => d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", hour12: false });

/** "today 21:00", "tomorrow 07:00", "Mon 07:00" or a date for later. */
export function formatUpcoming(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const today = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(today)) / 86_400_000);
  if (days === 0) return t("time.today", { time: hhmm(d) });
  if (days === 1) return t("time.tomorrow", { time: hhmm(d) });
  if (days > 1 && days < 7) return `${dayShort((d.getDay() + 6) % 7)} ${hhmm(d)}`;
  return `${d.toLocaleDateString(locale(), { day: "numeric", month: "short" })} ${hhmm(d)}`;
}

/** "just now", "5 minutes ago", "2 hours ago", then date and time. */
export function formatAgo(iso: string | null): string {
  if (!iso) return "—";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return t("time.justNow");
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return minutes === 1 ? t("time.minuteAgo") : t("time.minutesAgo", { n: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? t("time.hourAgo") : t("time.hoursAgo", { n: hours });
  const d = new Date(iso);
  return `${d.toLocaleDateString(locale(), { day: "numeric", month: "short" })} ${hhmm(d)}`;
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(locale(), {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function describeDays(days: number[]): string {
  const set = [...days].sort();
  if (set.length === 7) return t("days.every");
  if (set.join() === "0,1,2,3,4") return t("days.weekdays");
  if (set.join() === "5,6") return t("days.weekend");
  return set.map(dayShort).join(", ");
}
