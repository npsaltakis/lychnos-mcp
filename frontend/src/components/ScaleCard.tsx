import { Bluetooth, Scale, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ScaleStatus, Weighing } from "../api/types";
import { reportError } from "../lib/commands";
import { locale, t, useT } from "../lib/i18n";
import { formatAgo } from "../lib/time";
import { useDevices } from "../store/devices";
import { notify } from "../store/toasts";
import { Button, Card, Toggle, cx } from "./ui";
import { fmtKg } from "./WeightCard";

/** Settings: turn the Bluetooth scale on, see which scale was found, delete wrong weighings. */
export function ScaleCard() {
  const tr = useT();
  const version = useDevices((s) => s.weightVersion);
  const [status, setStatus] = useState<ScaleStatus | null>(null);
  const [rows, setRows] = useState<Weighing[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const load = () => api.scale().then(setStatus).catch((err) => reportError(null, err));
    void load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, [version]);
  useEffect(() => {
    api.weighings(60).then((r) => setRows(r.slice(-8).reverse())).catch(() => setRows([]));
  }, [version]);

  if (!status) return null;
  const s = status.settings;

  const save = async (next: ScaleStatus["settings"]) => {
    setBusy(true);
    try {
      setStatus(await api.setScale(next));
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (w: Weighing) => {
    try {
      await api.deleteWeighing(w.id);
      setRows(rows.filter((r) => r.id !== w.id));
      notify("success", t("scale.deleted"), `${fmtKg(w.kg)} kg`);
    } catch (err) {
      reportError(null, err);
    }
  };

  return (
    <Card className="lg:col-span-2">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 font-semibold">
          <Scale className={cx("size-5", s.enabled ? "text-accent" : "text-muted")} aria-hidden />
          {tr("scale.settingsTitle")}
        </h2>
        <Toggle checked={s.enabled} label={tr("scale.enabled")} disabled={busy} onChange={(on) => void save({ ...s, enabled: on })} />
      </div>
      <p className="mb-3 text-sm text-muted">{tr("scale.text")}</p>

      {s.enabled && (
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm" aria-live="polite">
          {status.bluetooth === "unavailable" ? (
            <span className="text-amber-400">{tr("scale.unavailable", { error: status.error ?? "" })}</span>
          ) : s.address ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <Bluetooth className="size-4 text-accent" aria-hidden />
                {tr("scale.found", { name: s.name || s.address })}
              </span>
              {status.last_seen && <span className="text-muted">{tr("scale.lastSeen", { when: formatAgo(status.last_seen) })}</span>}
              <Button variant="ghost" className="border border-line" disabled={busy} onClick={() => void save({ ...s, address: "", name: "" })}>
                {tr("scale.forget")}
              </Button>
            </>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-accent">
              <Bluetooth className="size-4 animate-pulse" aria-hidden />
              {tr("scale.waiting")}
            </span>
          )}
        </div>
      )}

      {s.enabled && (
        <div>
          <h3 className="mb-1 text-sm text-muted">{tr("scale.recent")}</h3>
          {rows.length === 0 ? (
            <p className="text-sm text-muted">{tr("scale.none")}</p>
          ) : (
            <>
              <ul className="divide-y divide-line text-sm">
                {rows.map((w) => (
                  <li key={w.id} className="flex items-center justify-between gap-3 py-1">
                    <span className="tabular-nums">
                      <span className="font-medium">{fmtKg(w.kg)} kg</span>
                      <span className="text-muted">
                        {" · "}
                        {new Date(w.ts).toLocaleString(locale(), { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </span>
                    <Button variant="ghost" className="size-10 p-0" aria-label={tr("common.delete")} onClick={() => void remove(w)}>
                      <Trash2 className="size-4" aria-hidden />
                    </Button>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-muted">{tr("scale.deleteHint")}</p>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
