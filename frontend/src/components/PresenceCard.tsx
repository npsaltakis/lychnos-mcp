import { House, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Neighbor, PresenceSettings, PresenceStatus } from "../api/types";
import { useScenes } from "../hooks/useData";
import { reportError } from "../lib/commands";
import { locale, t, useT } from "../lib/i18n";
import { notify } from "../store/toasts";
import { Button, Card, Toggle, cx } from "./ui";

function sinceLabel(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return time;
  return `${d.toLocaleDateString(locale(), { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}

/** Second hex digit 2/6/a/e = a "private" (random) address, which phones use on Wi-Fi. */
const isRandomMac = (mac: string) => /^.[26ae]/i.test(mac);

export function PresenceCard() {
  const tr = useT();
  const scenes = useScenes();
  const [status, setStatus] = useState<PresenceStatus | null>(null);
  const [form, setForm] = useState<PresenceSettings | null>(null);
  const [network, setNetwork] = useState<Neighbor[] | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "scan">("");
  const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

  useEffect(() => {
    api
      .presence()
      .then((s) => {
        setStatus(s);
        setForm(s.settings);
      })
      .catch((err) => reportError(null, err));
  }, []);

  // Keep the home / away labels current while the page is open
  useEffect(() => {
    const timer = setInterval(() => api.presence().then(setStatus).catch(() => undefined), 30_000);
    return () => clearInterval(timer);
  }, []);

  if (!form) return null;
  const set = (patch: Partial<PresenceSettings>) => setForm({ ...form, ...patch });
  const setPerson = (i: number, patch: Partial<PresenceSettings["people"][number]>) =>
    set({ people: form.people.map((p, j) => (j === i ? { ...p, ...patch } : p)) });

  const save = async (next: PresenceSettings) => {
    setBusy("save");
    try {
      const s = await api.setPresence(next);
      setStatus(s);
      setForm(s.settings);
      notify("success", t("pres.saved"));
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy("");
    }
  };

  const scan = async () => {
    setBusy("scan");
    try {
      setNetwork(await api.presenceNetwork());
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy("");
    }
  };

  const pick = (mac: string) => {
    const empty = form.people.findIndex((p) => !p.mac);
    if (empty >= 0) setPerson(empty, { mac });
    else set({ people: [...form.people, { name: t("pres.phone"), mac }] });
  };

  const summary =
    !status?.settings.enabled || status.anyone_home === null
      ? null
      : status.anyone_home
        ? tr("pres.someoneHome")
        : tr("pres.nobodyHome");

  return (
    <Card className="lg:col-span-2">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 font-semibold">
          <House className={cx("size-5", status?.anyone_home ? "text-accent" : "text-muted")} aria-hidden />
          {tr("pres.title")}
        </h2>
        <Toggle checked={form.enabled} label={tr("pres.enabled")} disabled={busy === "save"} onChange={(on) => void save({ ...form, enabled: on })} />
      </div>
      <p className="mb-1 text-sm text-muted">{tr("pres.text")}</p>
      {summary && (
        <p className="mb-2 text-sm text-accent" aria-live="polite">
          {summary}
        </p>
      )}

      <fieldset className="mt-3 space-y-2">
        <legend className="mb-1 text-sm text-muted">{tr("pres.people")}</legend>
        {form.people.map((p, i) => {
          const st = status?.people.find((x) => x.mac === p.mac.toLowerCase().replace(/-/g, ":"));
          return (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input className={cx(input, "w-36 flex-none")} aria-label={tr("pres.name")} value={p.name} maxLength={40} onChange={(e) => setPerson(i, { name: e.target.value })} />
              <input className={cx(input, "w-48 flex-1 font-mono")} aria-label="MAC" placeholder="aa:bb:cc:dd:ee:ff" value={p.mac} onChange={(e) => setPerson(i, { mac: e.target.value.trim() })} />
              {st && status?.anyone_home === null && form.enabled && <span className="text-xs text-muted">{tr("pres.checking")}</span>}
              {st && status?.anyone_home !== null && (
                <span className={cx("text-xs", st.home ? "text-emerald-400" : "text-muted")}>
                  {st.home ? tr("pres.home") : tr("pres.away")}
                  {st.since && ` · ${tr("pres.since", { when: sinceLabel(st.since) })}`}
                </span>
              )}
              <Button variant="ghost" className="size-10 p-0" aria-label={tr("common.delete")} onClick={() => set({ people: form.people.filter((_, j) => j !== i) })}>
                <Trash2 className="size-4" aria-hidden />
              </Button>
            </div>
          );
        })}
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" className="border border-line" onClick={() => set({ people: [...form.people, { name: "", mac: "" }] })}>
            <Plus className="size-4" aria-hidden /> {tr("pres.add")}
          </Button>
          <Button variant="ghost" className="border border-line" busy={busy === "scan"} onClick={scan}>
            <Search className="size-4" aria-hidden /> {tr("pres.scan")}
          </Button>
        </div>
      </fieldset>

      {network && (
        <div className="mt-3 rounded-xl border border-line p-3">
          <p className="mb-2 text-sm text-muted">{tr("pres.scanHint")}</p>
          <ul className="space-y-1 text-sm">
            {network.map((n) => (
              <li key={n.mac} className="flex flex-wrap items-center gap-x-3">
                <span className="w-28 tabular-nums text-muted">{n.ip}</span>
                <span className="font-mono">{n.mac}</span>
                {isRandomMac(n.mac) && <span className="text-xs text-accent">{tr("pres.likelyPhone")}</span>}
                <button type="button" className="ml-auto min-h-8 text-accent underline" onClick={() => pick(n.mac)}>
                  {tr("pres.use")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("pres.awayAfter")}</span>
          <select className={input} value={form.away_after_min} onChange={(e) => set({ away_after_min: Number(e.target.value) })}>
            {[5, 10, 15, 20, 30, 45, 60].map((m) => (
              <option key={m} value={m}>
                {tr("auto.minutes", { n: m })}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("pres.onLeave")}</span>
          <select className={input} value={form.on_leave} onChange={(e) => set({ on_leave: e.target.value as PresenceSettings["on_leave"] })}>
            <option value="all_off">{tr("pres.allOff")}</option>
            <option value="nothing">{tr("pres.nothing")}</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("pres.onArrive")}</span>
          <select className={input} value={form.on_arrive} onChange={(e) => set({ on_arrive: e.target.value as PresenceSettings["on_arrive"] })}>
            <option value="nothing">{tr("pres.nothing")}</option>
            <option value="all_on">{tr("pres.allOn")}</option>
            <option value="scene">{tr("pres.scene")}</option>
          </select>
        </label>
        {form.on_arrive === "scene" && (
          <label className="text-sm sm:col-start-3">
            <span className="mb-1 block text-muted">{tr("pres.whichScene")}</span>
            <select className={input} value={form.arrive_scene_id ?? ""} onChange={(e) => set({ arrive_scene_id: e.target.value || null })}>
              <option value="">—</option>
              {scenes.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {form.on_arrive !== "nothing" && (
        <label className="mt-3 inline-flex items-center gap-2 text-sm">
          <input type="checkbox" className="size-4 accent-amber-400" checked={form.arrive_only_dark} onChange={(e) => set({ arrive_only_dark: e.target.checked })} />
          {tr("pres.onlyDark")}
        </label>
      )}

      <div className="mt-3">
        <Button variant="primary" busy={busy === "save"} onClick={() => void save({ ...form, people: form.people.filter((p) => p.mac) })}>
          {tr("common.save")}
        </Button>
      </div>
    </Card>
  );
}
