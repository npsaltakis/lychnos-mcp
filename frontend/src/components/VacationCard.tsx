import { Plane } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Location, VacationSettings, VacationStatus } from "../api/types";
import { reportError } from "../lib/commands";
import { t, useT } from "../lib/i18n";
import { sortedViews, useDevices } from "../store/devices";
import { notify } from "../store/toasts";
import { Button, Card, Toggle, cx } from "./ui";

const hhmm = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";

export function VacationCard() {
  const tr = useT();
  const [status, setStatus] = useState<VacationStatus | null>(null);
  const [form, setForm] = useState<VacationSettings | null>(null);
  const [location, setLocation] = useState<Location | null>(null);
  const [saving, setSaving] = useState(false);
  const devices = sortedViews(useDevices((s) => s.devices)).filter((v) => v.device.registered);
  const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

  useEffect(() => {
    api
      .vacation()
      .then((s) => {
        setStatus(s);
        setForm(s.settings);
      })
      .catch((err) => reportError(null, err));
    api.location().then(setLocation).catch(() => undefined);
  }, []);

  if (!form) return null;
  const set = (patch: Partial<VacationSettings>) => setForm({ ...form, ...patch });

  const save = async (next: VacationSettings) => {
    setSaving(true);
    try {
      const s = await api.setVacation(next);
      setStatus(s);
      setForm(s.settings);
      notify("success", t("vac.saved"));
    } catch (err) {
      reportError(null, err);
    } finally {
      setSaving(false);
    }
  };

  const toggleDevice = (id: string, on: boolean) =>
    set({ device_ids: on ? [...form.device_ids, id] : form.device_ids.filter((d) => d !== id) });

  const state = !status?.settings.enabled
    ? tr("vac.off")
    : status.active
      ? tr("vac.activeNow", { end: hhmm(status.window_end) })
      : tr("vac.next", { start: hhmm(status.window_start), end: hhmm(status.window_end) });

  return (
    <Card className="lg:col-span-2">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 font-semibold">
          <Plane className={cx("size-5", status?.active ? "text-accent" : "text-muted")} aria-hidden />
          {tr("vac.title")}
        </h2>
        <Toggle checked={form.enabled} label={tr("vac.enabled")} disabled={saving} onChange={(on) => void save({ ...form, enabled: on })} />
      </div>
      <p className="mb-1 text-sm text-muted">{tr("vac.text")}</p>
      <p className={cx("mb-3 text-sm", status?.active ? "text-accent" : "text-muted")} aria-live="polite">
        {state}
      </p>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("vac.from")}</span>
          <select className={input} value={form.start} onChange={(e) => set({ start: e.target.value as VacationSettings["start"] })}>
            <option value="sunset">{tr("vac.fromSunset")}</option>
            <option value="time">{tr("vac.fromTime")}</option>
          </select>
        </label>
        {(form.start === "time" || !location) && (
          <label className="text-sm">
            <span className="mb-1 block text-muted">{tr("vac.fromTime")}</span>
            <input type="time" className={input} value={form.start_time} onChange={(e) => set({ start_time: e.target.value })} />
          </label>
        )}
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("vac.until")}</span>
          <input type="time" className={input} value={form.end_time} onChange={(e) => set({ end_time: e.target.value })} />
        </label>
      </div>
      {form.start === "sunset" && !location && <p className="mt-2 text-sm text-muted">{tr("vac.noLocation")}</p>}

      <fieldset className="mt-3">
        <legend className="mb-1 text-sm text-muted">{tr("vac.lights")}</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          <label className="inline-flex min-h-8 items-center gap-2 text-sm">
            <input type="checkbox" className="size-4 accent-amber-400" checked={form.device_ids.length === 0} onChange={(e) => e.target.checked && set({ device_ids: [] })} />
            {tr("vac.allLights")}
          </label>
          {devices.map((v) => (
            <label key={v.device.id} className="inline-flex min-h-8 items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-amber-400"
                checked={form.device_ids.includes(v.device.id)}
                onChange={(e) => toggleDevice(v.device.id, e.target.checked)}
              />
              {v.device.name}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mt-3">
        <Button variant="primary" busy={saving} onClick={() => void save(form)}>
          {tr("common.save")}
        </Button>
      </div>
    </Card>
  );
}
