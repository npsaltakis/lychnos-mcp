import { Scale, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ScaleStatus, Weighing } from "../api/types";
import { locale, useT } from "../lib/i18n";
import { reportError } from "../lib/commands";
import { formatAgo } from "../lib/time";
import { useDevices } from "../store/devices";
import { Button, Card, cx } from "./ui";

const RANGES = [30, 90, 365] as const;
const W = 600;
const H = 140;
const PAD = { top: 10, right: 8, bottom: 20, left: 36 };

export const fmtKg = (kg: number) =>
  kg.toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 });

const fmtDate = (iso: string) => new Date(iso).toLocaleDateString(locale(), { day: "numeric", month: "short" });

/** Change since the last weighing at least `days` ago (null when there is none). */
function changeSince(rows: Weighing[], days: number): number | null {
  if (rows.length < 2) return null;
  const last = rows[rows.length - 1];
  const cutoff = new Date(last.ts).getTime() - days * 86_400_000;
  const base = [...rows].reverse().find((r) => new Date(r.ts).getTime() <= cutoff);
  return base ? last.kg - base.kg : null;
}

function Delta({ value, label }: { value: number | null; label: string }) {
  if (value === null) return null;
  const sign = value > 0.05 ? "+" : value < -0.05 ? "−" : "±";
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="tabular-nums">
        {sign}
        {fmtKg(Math.abs(value))} kg
      </dd>
    </div>
  );
}

/** Weight over time: one series, so no legend; hover shows the exact weighing. */
function WeightChart({ rows }: { rows: Weighing[] }) {
  const tr = useT();
  const [hover, setHover] = useState<number | null>(null);
  const pts = useMemo(() => {
    const t = rows.map((r) => new Date(r.ts).getTime());
    const t0 = Math.min(...t);
    const t1 = Math.max(...t);
    const kMin = Math.floor(Math.min(...rows.map((r) => r.kg)) - 0.5);
    const kMax = Math.ceil(Math.max(...rows.map((r) => r.kg)) + 0.5);
    const x = (ts: number) => PAD.left + (t1 === t0 ? (W - PAD.left - PAD.right) / 2 : ((ts - t0) / (t1 - t0)) * (W - PAD.left - PAD.right));
    const y = (kg: number) => PAD.top + (1 - (kg - kMin) / (kMax - kMin)) * (H - PAD.top - PAD.bottom);
    return { list: rows.map((r, i) => ({ x: x(t[i]), y: y(r.kg), r })), kMin, kMax, y };
  }, [rows]);

  const path = pts.list.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const active = hover !== null ? pts.list[hover] : null;

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    pts.list.forEach((p, i) => {
      if (Math.abs(p.x - px) < Math.abs(pts.list[best].x - px)) best = i;
    });
    setHover(best);
  };

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-36 w-full touch-none overflow-visible"
        role="img"
        aria-label={tr("scale.chartLabel", { n: rows.length })}
        onPointerMove={onMove}
        onPointerDown={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {[pts.kMin, (pts.kMin + pts.kMax) / 2, pts.kMax].map((k) => (
          <g key={k}>
            <line x1={PAD.left} x2={W - PAD.right} y1={pts.y(k)} y2={pts.y(k)} className="stroke-line" strokeWidth={1} />
            <text x={PAD.left - 6} y={pts.y(k) + 4} textAnchor="end" className="fill-muted text-[11px] tabular-nums">
              {Math.round(k)}
            </text>
          </g>
        ))}
        <text x={PAD.left} y={H - 4} className="fill-muted text-[11px]">
          {fmtDate(rows[0].ts)}
        </text>
        <text x={W - PAD.right} y={H - 4} textAnchor="end" className="fill-muted text-[11px]">
          {fmtDate(rows[rows.length - 1].ts)}
        </text>
        <path d={path} fill="none" className="stroke-accent" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {active && (
          <>
            <line x1={active.x} x2={active.x} y1={PAD.top} y2={H - PAD.bottom} className="stroke-muted" strokeWidth={1} strokeDasharray="3 3" />
            <circle cx={active.x} cy={active.y} r={5} className="fill-accent stroke-panel" strokeWidth={2} />
          </>
        )}
        {!active && rows.length > 0 && (
          <circle cx={pts.list[pts.list.length - 1].x} cy={pts.list[pts.list.length - 1].y} r={4} className="fill-accent stroke-panel" strokeWidth={2} />
        )}
      </svg>
      {active && (
        <div
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-lg border border-line bg-panel-2 px-2 py-1 text-xs shadow"
          style={{ left: `${(active.x / W) * 100}%` }}
        >
          <span className="font-medium tabular-nums">{fmtKg(active.r.kg)} kg</span>
          <span className="text-muted"> · {new Date(active.r.ts).toLocaleString(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      )}
    </div>
  );
}

export function WeightCard() {
  const tr = useT();
  const version = useDevices((s) => s.weightVersion);
  const [status, setStatus] = useState<ScaleStatus | null>(null);
  const [rows, setRows] = useState<Weighing[]>([]);
  const [days, setDays] = useState<(typeof RANGES)[number]>(90);

  useEffect(() => {
    api.scale().then(setStatus).catch(() => setStatus(null));
  }, [version]);
  useEffect(() => {
    api.weighings(days).then(setRows).catch(() => setRows([]));
  }, [days, version]);

  if (!status?.scales.length || !status.latest) return null;
  const latest = status.latest;

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Scale className="size-8 text-accent" aria-hidden />
          <div>
            <h2 className="text-sm text-muted">{tr("scale.title")}</h2>
            <p className="text-3xl font-semibold tabular-nums leading-tight">
              {fmtKg(latest.kg)} <span className="text-base font-normal text-muted">kg</span>
            </p>
            <p className="text-xs text-muted">{formatAgo(latest.ts)}</p>
          </div>
        </div>
        <dl className="flex gap-5 text-sm">
          <Delta value={changeSince(rows, 7)} label={tr("scale.week")} />
          <Delta value={changeSince(rows, 30)} label={tr("scale.month")} />
        </dl>
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
      {rows.length >= 2 ? (
        <div className="mt-3">
          <WeightChart rows={rows} />
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted">{tr("scale.needMore")}</p>
      )}
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-muted">{tr("scale.recent")}</summary>
        <ul className="mt-2 divide-y divide-line">
          {[...rows]
            .reverse()
            .slice(0, 10)
            .map((w) => (
              <li key={w.id} className="flex items-center justify-between gap-3 py-1">
                <span className="tabular-nums">
                  <span className="font-medium">{fmtKg(w.kg)} kg</span>
                  <span className="text-muted">
                    {" · "}
                    {new Date(w.ts).toLocaleString(locale(), { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                  </span>
                </span>
                <Button
                  variant="ghost"
                  className="size-10 p-0"
                  aria-label={tr("common.delete")}
                  onClick={() => api.deleteWeighing(w.id).catch((err) => reportError(null, err))}
                >
                  <Trash2 className="size-4" aria-hidden />
                </Button>
              </li>
            ))}
        </ul>
        <p className="mt-1 text-xs text-muted">{tr("scale.deleteHint")}</p>
      </details>
    </Card>
  );
}
