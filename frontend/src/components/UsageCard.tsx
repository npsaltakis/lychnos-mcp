import { BarChart3 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { UsageReport } from "../api/types";
import { useT } from "../lib/i18n";
import { useDevices } from "../store/devices";
import { Card, cx } from "./ui";

const RANGES = [7, 30] as const;

const fmtHours = (h: number) => (h >= 10 ? h.toFixed(0) : h.toFixed(1));

/** Hours on per light per day: one small bar chart per light (one series each, same scale). */
export function UsageCard() {
  const tr = useT();
  const version = useDevices((s) => s.activityVersion);
  const [days, setDays] = useState<(typeof RANGES)[number]>(7);
  const [report, setReport] = useState<UsageReport | null>(null);

  useEffect(() => {
    api.usage(days).then(setReport).catch(() => undefined);
  }, [days, version]);

  const max = Math.max(1, ...(report?.devices.flatMap((d) => d.hours) ?? [0]));
  const dayLabel = (iso: string, long = false) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString([], long ? { weekday: "short", day: "numeric", month: "short" } : { weekday: "narrow" });

  return (
    <Card className="mb-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold">
          <BarChart3 className="size-5 text-muted" aria-hidden /> {tr("use.title")}
        </h2>
        <div className="flex gap-1" role="group" aria-label={tr("use.range")}>
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              aria-pressed={days === r}
              onClick={() => setDays(r)}
              className={cx(
                "min-h-8 rounded-full border px-3 text-sm",
                days === r ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:text-gray-200",
              )}
            >
              {tr("use.days", { n: r })}
            </button>
          ))}
        </div>
      </div>

      {!report || report.devices.length === 0 ? (
        <p className="text-sm text-muted">{tr("use.empty")}</p>
      ) : (
        <ul className="space-y-4">
          {report.devices.map((d) => (
            <li key={d.device_id}>
              <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                <span className="truncate">{d.name}</span>
                <span className="tabular-nums text-muted">
                  {tr("use.total", { h: fmtHours(d.total_hours) })}
                </span>
              </div>
              <div className="flex h-16 items-end gap-0.5 border-b border-line" aria-hidden>
                {d.hours.map((h, i) => (
                  <div key={report.days[i]} className="group relative flex h-full flex-1 items-end">
                    <div
                      className="w-full rounded-t bg-accent/80 transition-colors group-hover:bg-accent"
                      style={{ height: h > 0 ? `max(2px, ${(h / max) * 100}%)` : 0 }}
                    />
                    <span className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap rounded-lg border border-line bg-panel-2 px-2 py-1 text-xs text-gray-200 shadow group-hover:block">
                      {dayLabel(report.days[i], true)} · {tr("use.hours", { h: fmtHours(h) })}
                    </span>
                  </div>
                ))}
              </div>
              {days === 7 && (
                <div className="mt-1 flex gap-0.5 text-center text-xs text-muted" aria-hidden>
                  {report.days.map((day) => (
                    <span key={day} className="flex-1">
                      {dayLabel(day)}
                    </span>
                  ))}
                </div>
              )}
              <table className="sr-only">
                <caption>{d.name}</caption>
                <tbody>
                  {report.days.map((day, i) => (
                    <tr key={day}>
                      <th scope="row">{dayLabel(day, true)}</th>
                      <td>{tr("use.hours", { h: fmtHours(d.hours[i]) })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">{tr("use.note")}</p>
    </Card>
  );
}
