const DAY_SHORT = ["Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"]; // Monday = 0, as in the backend

export const dayShort = (d: number) => DAY_SHORT[d] ?? String(d);

const hhmm = (d: Date) => d.toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hour12: false });

/** "σήμερα 21:00", "αύριο 07:00", "Δευ 07:00" or a date for later. */
export function formatUpcoming(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const today = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(today)) / 86_400_000);
  if (days === 0) return `σήμερα ${hhmm(d)}`;
  if (days === 1) return `αύριο ${hhmm(d)}`;
  if (days > 1 && days < 7) return `${dayShort((d.getDay() + 6) % 7)} ${hhmm(d)}`;
  return `${d.toLocaleDateString("el-GR", { day: "numeric", month: "short" })} ${hhmm(d)}`;
}

/** "μόλις τώρα", "πριν 5 λεπτά", "πριν 2 ώρες", then date and time. */
export function formatAgo(iso: string | null): string {
  if (!iso) return "—";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "μόλις τώρα";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `πριν ${minutes} λεπτ${minutes === 1 ? "ό" : "ά"}`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `πριν ${hours} ώρ${hours === 1 ? "α" : "ες"}`;
  const d = new Date(iso);
  return `${d.toLocaleDateString("el-GR", { day: "numeric", month: "short" })} ${hhmm(d)}`;
}

export function describeDays(days: number[]): string {
  const set = [...days].sort();
  if (set.length === 7) return "Κάθε μέρα";
  if (set.join() === "0,1,2,3,4") return "Καθημερινές";
  if (set.join() === "5,6") return "Σαββατοκύριακο";
  return set.map(dayShort).join(", ");
}
