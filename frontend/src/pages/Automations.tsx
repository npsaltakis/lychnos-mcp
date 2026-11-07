import { CalendarClock, Pencil, Play, Plus, Sunrise, Sunset, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Automation, AutomationAction, AutomationInput, Command, Location, Target, Trigger } from "../api/types";
import { Button, Card, Empty, PageHeader, Toggle, cx } from "../components/ui";
import { useAutomations, useRooms, useScenes } from "../hooks/useData";
import { hexToRgb, rgbToHex } from "../lib/color";
import { reportError } from "../lib/commands";
import { dayShort, describeDays, formatAgo, formatUpcoming } from "../lib/time";
import { sortedViews, useDevices } from "../store/devices";
import { notify } from "../store/toasts";

type When = "time" | "sunrise" | "sunset";
type Kind = "scene" | "on" | "off" | "color" | "brightness" | "white" | "fade";

interface Form {
  name: string;
  when: When;
  time: string;
  offset: number;
  days: number[];
  kind: Kind;
  target: string; // "all" | "room:<id>" | "device:<id>"
  sceneId: string;
  hex: string;
  brightness: number;
  kelvin: number;
  fadeMinutes: number;
  fadeWhite: boolean;
}

const KIND_LABEL: Record<Kind, string> = {
  scene: "Ενεργοποίηση σκηνής",
  on: "Άναμμα",
  off: "Σβήσιμο",
  color: "Χρώμα",
  brightness: "Φωτεινότητα",
  white: "Λευκό (θερμοκρασία)",
  fade: "Ξύπνημα με φως (σταδιακά)",
};

const EMPTY: Form = {
  name: "",
  when: "time",
  time: "21:00",
  offset: 0,
  days: [0, 1, 2, 3, 4, 5, 6],
  kind: "on",
  target: "all",
  sceneId: "",
  hex: "#ffb46b",
  brightness: 60,
  kelvin: 2700,
  fadeMinutes: 15,
  fadeWhite: true,
};

function targetToString(t: Target): string {
  return t.kind === "all" ? "all" : `${t.kind}:${t.id}`;
}

function stringToTarget(s: string): Target {
  if (s === "all") return { kind: "all", id: null };
  const [kind, id] = s.split(/:(.*)/s);
  return { kind: kind as Target["kind"], id };
}

function fromAutomation(a: Automation): Form {
  const f: Form = { ...EMPTY, name: a.name, days: a.trigger.days };
  if (a.trigger.type === "time") f.time = a.trigger.time;
  else {
    f.when = a.trigger.event;
    f.offset = a.trigger.offset_min;
  }
  const act = a.action;
  if (act.type === "scene") {
    f.kind = "scene";
    f.sceneId = act.scene_id;
  } else if (act.type === "fade") {
    f.kind = "fade";
    f.target = targetToString(act.target);
    f.brightness = act.to_brightness;
    f.fadeMinutes = act.duration_min;
    f.fadeWhite = act.color_temp_k !== null;
    if (act.color_temp_k) f.kelvin = act.color_temp_k;
  } else {
    f.target = targetToString(act.target);
    const c = act.command;
    if (c.type === "set_power") f.kind = c.on ? "on" : "off";
    else if (c.type === "set_color") {
      f.kind = "color";
      f.hex = rgbToHex(c.color);
    } else if (c.type === "set_brightness") {
      f.kind = "brightness";
      f.brightness = c.brightness;
    } else {
      f.kind = "white";
      f.kelvin = c.kelvin;
    }
  }
  return f;
}

function toInput(f: Form, enabled: boolean): AutomationInput {
  const trigger: Trigger =
    f.when === "time"
      ? { type: "time", time: f.time, days: f.days }
      : { type: "sun", event: f.when, offset_min: f.offset, days: f.days };
  let action: AutomationAction;
  const target = stringToTarget(f.target);
  if (f.kind === "scene") action = { type: "scene", scene_id: f.sceneId };
  else if (f.kind === "fade")
    action = {
      type: "fade",
      target,
      to_brightness: f.brightness,
      duration_min: f.fadeMinutes,
      color_temp_k: f.fadeWhite ? f.kelvin : null,
    };
  else {
    const command: Command =
      f.kind === "on" || f.kind === "off"
        ? { type: "set_power", on: f.kind === "on" }
        : f.kind === "color"
          ? { type: "set_color", color: hexToRgb(f.hex) }
          : f.kind === "brightness"
            ? { type: "set_brightness", brightness: f.brightness }
            : { type: "set_color_temperature", kelvin: f.kelvin };
    action = { type: "command", target, command };
  }
  return { name: f.name.trim(), enabled, trigger, action };
}

function Editor({
  initial,
  location,
  onDone,
}: {
  initial: Automation | null;
  location: Location | null;
  onDone: () => void;
}) {
  const [f, setF] = useState<Form>(initial ? fromAutomation(initial) : EMPTY);
  const [saving, setSaving] = useState(false);
  const rooms = useRooms();
  const scenes = useScenes();
  const devices = sortedViews(useDevices((s) => s.devices)).filter((v) => v.device.registered);
  const set = (patch: Partial<Form>) => setF((cur) => ({ ...cur, ...patch }));
  const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";
  const needsLocation = f.when !== "time" && !location;

  const save = async () => {
    if (!f.name.trim()) return notify("warning", "Δώσε όνομα στον αυτοματισμό");
    if (!f.days.length) return notify("warning", "Επίλεξε τουλάχιστον μία ημέρα");
    if (f.kind === "scene" && !f.sceneId) return notify("warning", "Επίλεξε σκηνή");
    setSaving(true);
    try {
      const body = toInput(f, initial?.enabled ?? true);
      if (initial) await api.updateAutomation(initial.id, body);
      else await api.createAutomation(body);
      notify("success", "Ο αυτοματισμός αποθηκεύτηκε", body.name);
      onDone();
    } catch (err) {
      reportError(null, err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="space-y-5">
      <label className="block text-sm">
        <span className="mb-1 block text-muted">Όνομα</span>
        <input id="auto-name" className={input} maxLength={64} value={f.name} placeholder="π.χ. Βραδινό φως" onChange={(e) => set({ name: e.target.value })} />
      </label>

      <fieldset className="space-y-3">
        <legend className="mb-1 text-sm text-muted">Πότε</legend>
        <div className="grid grid-cols-3 gap-1 rounded-xl bg-panel-2 p-1 text-sm">
          {(
            [
              ["time", "Ώρα", CalendarClock],
              ["sunrise", "Ανατολή", Sunrise],
              ["sunset", "Δύση", Sunset],
            ] as const
          ).map(([key, label, Icon]) => (
            <button
              key={key}
              type="button"
              aria-pressed={f.when === key}
              onClick={() => set({ when: key })}
              className={cx(
                "inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg",
                f.when === key ? "bg-line text-white" : "text-muted hover:text-white",
              )}
            >
              <Icon className="size-4" aria-hidden /> {label}
            </button>
          ))}
        </div>
        {f.when === "time" ? (
          <input id="auto-time" type="time" className={cx(input, "w-40")} value={f.time} onChange={(e) => set({ time: e.target.value })} aria-label="Ώρα" />
        ) : (
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <input
              id="auto-offset"
              type="number"
              min={-240}
              max={240}
              step={5}
              className={cx(input, "w-28")}
              value={f.offset}
              onChange={(e) => set({ offset: Number(e.target.value) })}
            />
            <span className="text-muted">
              λεπτά {f.offset < 0 ? "πριν" : "μετά"} τη{f.when === "sunrise" ? "ν ανατολή" : " δύση"} (αρνητικό = πριν)
            </span>
          </label>
        )}
        {needsLocation && (
          <p className="text-sm text-amber-300">
            Για ανατολή/δύση όρισε πρώτα την τοποθεσία σου στις <Link to="/settings" className="underline">Ρυθμίσεις</Link>.
          </p>
        )}
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Ημέρες">
          {[0, 1, 2, 3, 4, 5, 6].map((d) => {
            const on = f.days.includes(d);
            return (
              <button
                key={d}
                type="button"
                aria-pressed={on}
                onClick={() => set({ days: on ? f.days.filter((x) => x !== d) : [...f.days, d].sort() })}
                className={cx(
                  "min-h-9 w-12 rounded-lg border text-sm",
                  on ? "border-accent bg-accent/15 text-white" : "border-line text-muted hover:text-white",
                )}
              >
                {dayShort(d)}
              </button>
            );
          })}
          <span className="ml-1 self-center text-xs text-muted">{describeDays(f.days)}</span>
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="mb-1 text-sm text-muted">Τι θα γίνει</legend>
        <select id="auto-kind" className={input} value={f.kind} onChange={(e) => set({ kind: e.target.value as Kind })} aria-label="Ενέργεια">
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>

        {f.kind === "scene" ? (
          <select id="auto-scene" className={input} value={f.sceneId} onChange={(e) => set({ sceneId: e.target.value })} aria-label="Σκηνή">
            <option value="">Επίλεξε σκηνή…</option>
            {scenes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        ) : (
          <select id="auto-target" className={input} value={f.target} onChange={(e) => set({ target: e.target.value })} aria-label="Σε ποια φώτα">
            <option value="all">Όλα τα φώτα</option>
            {rooms.length > 0 && (
              <optgroup label="Δωμάτια">
                {rooms.map((r) => (
                  <option key={r.id} value={`room:${r.id}`}>
                    {r.name}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="Συσκευές">
              {devices.map((v) => (
                <option key={v.device.id} value={`device:${v.device.id}`}>
                  {v.device.name}
                </option>
              ))}
            </optgroup>
          </select>
        )}

        {f.kind === "color" && (
          <input type="color" aria-label="Χρώμα" value={f.hex} onChange={(e) => set({ hex: e.target.value })} className="h-10 w-20 cursor-pointer rounded-lg border border-line bg-transparent" />
        )}
        {(f.kind === "brightness" || f.kind === "fade") && (
          <label className="block text-sm">
            <span className="mb-1 flex justify-between text-muted">
              {f.kind === "fade" ? "Τελική φωτεινότητα" : "Φωτεινότητα"}
              <span className="tabular-nums text-gray-200">{f.brightness}%</span>
            </span>
            <input type="range" className="slider" min={f.kind === "fade" ? 2 : 1} max={100} value={f.brightness} onChange={(e) => set({ brightness: Number(e.target.value) })} />
          </label>
        )}
        {f.kind === "fade" && (
          <>
            <label className="block text-sm">
              <span className="mb-1 flex justify-between text-muted">
                Διάρκεια <span className="tabular-nums text-gray-200">{f.fadeMinutes} λεπτά</span>
              </span>
              <input type="range" className="slider" min={1} max={60} value={f.fadeMinutes} onChange={(e) => set({ fadeMinutes: Number(e.target.value) })} />
            </label>
            <label className="inline-flex items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-amber-400" checked={f.fadeWhite} onChange={(e) => set({ fadeWhite: e.target.checked })} />
              Σε λευκό φως {f.fadeWhite && `${f.kelvin} K`}
            </label>
          </>
        )}
        {(f.kind === "white" || (f.kind === "fade" && f.fadeWhite)) && (
          <label className="block text-sm">
            <span className="mb-1 flex justify-between text-muted">
              Θερμοκρασία <span className="tabular-nums text-gray-200">{f.kelvin} K</span>
            </span>
            <input type="range" className="slider" min={2000} max={6500} step={50} value={f.kelvin} onChange={(e) => set({ kelvin: Number(e.target.value) })} />
          </label>
        )}
      </fieldset>

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>
          Ακύρωση
        </Button>
        <Button variant="primary" busy={saving} disabled={needsLocation} onClick={save}>
          Αποθήκευση
        </Button>
      </div>
    </Card>
  );
}

function describe(a: Automation, names: Record<string, string>, rooms: Record<string, string>, scenes: Record<string, string>): string {
  const when =
    a.trigger.type === "time"
      ? a.trigger.time
      : `${a.trigger.event === "sunrise" ? "Ανατολή" : "Δύση"}${a.trigger.offset_min ? ` ${a.trigger.offset_min > 0 ? "+" : ""}${a.trigger.offset_min}′` : ""}`;
  const act = a.action;
  let what: string;
  if (act.type === "scene") what = `σκηνή «${scenes[act.scene_id] ?? "?"}»`;
  else {
    const t = act.target;
    const target = t.kind === "all" ? "όλα τα φώτα" : t.kind === "room" ? (rooms[t.id!] ?? "δωμάτιο") : (names[t.id!] ?? "συσκευή");
    if (act.type === "fade") what = `ξύπνημα με φως έως ${act.to_brightness}% σε ${act.duration_min}′ · ${target}`;
    else {
      const c = act.command;
      const verb =
        c.type === "set_power" ? (c.on ? "άναμμα" : "σβήσιμο")
        : c.type === "set_color" ? `χρώμα ${rgbToHex(c.color)}`
        : c.type === "set_brightness" ? `φωτεινότητα ${c.brightness}%`
        : `λευκό ${c.kelvin} K`;
      what = `${verb} · ${target}`;
    }
  }
  return `${when} · ${describeDays(a.trigger.days)} · ${what}`;
}

export function Automations() {
  const automations = useAutomations();
  const rooms = useRooms();
  const scenes = useScenes();
  const devices = useDevices((s) => s.devices);
  const [editing, setEditing] = useState<Automation | "new" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [location, setLocation] = useState<Location | null>(null);

  useEffect(() => {
    api.location().then(setLocation).catch(() => undefined);
  }, [editing]);

  const names = Object.fromEntries(Object.values(devices).map((v) => [v.device.id, v.device.name]));
  const roomNames = Object.fromEntries(rooms.map((r) => [r.id, r.name]));
  const sceneNames = Object.fromEntries(scenes.map((s) => [s.id, s.name]));

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Αυτοματισμοί"
        subtitle="Προγράμματα με ώρα ή ανατολή/δύση. Τρέχουν στην ώρα του υπολογιστή όπου είναι το backend."
        actions={
          editing === null && (
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Plus className="size-4" aria-hidden /> Νέος αυτοματισμός
            </Button>
          )
        }
      />
      {editing !== null ? (
        <Editor key={editing === "new" ? "new" : editing.id} initial={editing === "new" ? null : editing} location={location} onDone={() => setEditing(null)} />
      ) : automations.length === 0 ? (
        <Empty icon={<CalendarClock className="size-8" />} title="Κανένας αυτοματισμός ακόμα">
          Π.χ. «άναψε το σαλόνι στη δύση», «σβήσε όλα στις 00:30» ή «ξύπνημα με φως στις 07:00 τις καθημερινές».
          Μπορείς να τους φτιάξεις και ζητώντας το από το Claude.
        </Empty>
      ) : (
        <div className="space-y-3">
          {automations.map((a) => {
            const result = a.last_result;
            const ok = result?.outcome === "all_confirmed";
            return (
              <Card key={a.id} className={cx(!a.enabled && "opacity-60")}>
                <div className="flex flex-wrap items-start gap-4">
                  <div className="min-w-0 flex-1">
                    <h3 className="font-semibold">{a.name}</h3>
                    <p className="mt-1 text-sm text-muted">{describe(a, names, roomNames, sceneNames)}</p>
                    <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                      <span>
                        Επόμενη: <span className="text-gray-200">{a.enabled ? formatUpcoming(a.next_run_at) : "σε παύση"}</span>
                      </span>
                      {result && (
                        <span className={ok ? "text-emerald-400" : result.outcome === "failed" ? "text-red-400" : "text-amber-300"} title={result.message}>
                          Τελευταία: {formatAgo(a.last_run_at)} · {result.message}
                        </span>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Toggle label={`${a.name} ενεργός`} checked={a.enabled} pending={busy === `en-${a.id}`} onChange={(on) => act(`en-${a.id}`, () => api.setAutomationEnabled(a.id, on))} />
                    <Button variant="ghost" aria-label="Εκτέλεση τώρα" title="Εκτέλεση τώρα" busy={busy === `run-${a.id}`} onClick={() => act(`run-${a.id}`, () => api.runAutomation(a.id))}>
                      <Play className="size-4" />
                    </Button>
                    <Button variant="ghost" aria-label="Επεξεργασία" onClick={() => setEditing(a)}>
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      variant="danger"
                      aria-label="Διαγραφή"
                      busy={busy === `del-${a.id}`}
                      onClick={() => {
                        if (confirm(`Διαγραφή του αυτοματισμού «${a.name}»;`)) act(`del-${a.id}`, () => api.deleteAutomation(a.id));
                      }}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
