import { Pencil, Play, Plus, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { DeviceView, Scene, SceneAction, SceneInput } from "../api/types";
import { summarizeBatch } from "../components/AllLights";
import { Button, Card, Empty, PageHeader, cx } from "../components/ui";
import { useScenes } from "../hooks/useData";
import { hexToRgb, rgbToHex } from "../lib/color";
import { reportError } from "../lib/commands";
import { t, useT, type MessageKey } from "../lib/i18n";
import { has, sortedViews, useDevices } from "../store/devices";
import { notify } from "../store/toasts";

type Kind = "skip" | "off" | "on" | "color" | "white";

interface Row {
  kind: Kind;
  brightness: number;
  hex: string;
  kelvin: number;
}

const TEMPLATES: { name: MessageKey; description: MessageKey; row: (v: DeviceView) => Row }[] = [
  {
    name: "tpl.movie",
    description: "tpl.movieDesc",
    row: (v) => (has(v, "color_rgb") ? row("color", 20, "#5a00b4") : row("off")),
  },
  {
    name: "tpl.relax",
    description: "tpl.relaxDesc",
    row: (v) => (has(v, "color_temp") ? row("white", 45, "#ffffff", 2700) : row("on")),
  },
  {
    name: "tpl.reading",
    description: "tpl.readingDesc",
    row: (v) => (has(v, "color_temp") ? row("white", 100, "#ffffff", 4500) : row("on")),
  },
  { name: "tpl.allOff", description: "tpl.allOffDesc", row: () => row("off") },
];

function row(kind: Kind, brightness = 100, hex = "#ffffff", kelvin = 4000): Row {
  return { kind, brightness, hex, kelvin };
}

function clampKelvin(v: DeviceView, k: number): number {
  const r = v.device.capabilities.color_temp_range;
  return r ? Math.min(r.max_k, Math.max(r.min_k, k)) : k;
}

function rowFromState(v: DeviceView): Row {
  const s = v.runtime.state;
  if (s.power === false) return row("off");
  if (s.mode === "white" && s.color_temp_k) return row("white", s.brightness ?? 100, "#ffffff", s.color_temp_k);
  if (s.color) return row("color", s.brightness ?? 100, rgbToHex(s.color));
  return row("on");
}

function rowFromAction(a: SceneAction | undefined): Row {
  if (!a) return row("skip");
  if (a.power === false) return row("off");
  if (a.color) return row("color", a.brightness ?? 100, rgbToHex(a.color));
  if (a.color_temp_k) return row("white", a.brightness ?? 100, "#ffffff", a.color_temp_k);
  return { ...row("on"), brightness: a.brightness ?? 100 };
}

function toAction(v: DeviceView, r: Row): SceneAction | null {
  const brightness = has(v, "brightness") ? r.brightness : null;
  switch (r.kind) {
    case "skip":
      return null;
    case "off":
      return { device_id: v.device.id, power: false };
    case "on":
      return { device_id: v.device.id, power: true, brightness };
    case "color":
      return { device_id: v.device.id, power: true, color: hexToRgb(r.hex), brightness };
    case "white":
      return { device_id: v.device.id, power: true, color_temp_k: clampKelvin(v, r.kelvin), brightness };
  }
}

function SceneEditor({ initial, devices, onDone }: { initial: Scene | null; devices: DeviceView[]; onDone: () => void }) {
  const tr = useT();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [rows, setRows] = useState<Record<string, Row>>(() =>
    Object.fromEntries(
      devices.map((v) => [v.device.id, initial ? rowFromAction(initial.actions.find((a) => a.device_id === v.device.id)) : rowFromState(v)]),
    ),
  );
  const [saving, setSaving] = useState(false);

  const update = (id: string, patch: Partial<Row>) => setRows((r) => ({ ...r, [id]: { ...r[id], ...patch } }));

  const applyTemplate = (tpl: (typeof TEMPLATES)[number]) => {
    if (!name) setName(t(tpl.name));
    if (!description) setDescription(t(tpl.description));
    setRows(Object.fromEntries(devices.map((v) => [v.device.id, tpl.row(v)])));
  };

  const save = async () => {
    const actions = devices.map((v) => toAction(v, rows[v.device.id])).filter((a): a is SceneAction => a !== null);
    if (!name.trim()) return notify("warning", t("scenes.needName"));
    if (!actions.length) return notify("warning", t("scenes.needDevice"));
    const body: SceneInput = { name: name.trim(), description: description.trim(), actions };
    setSaving(true);
    try {
      if (initial) await api.updateScene(initial.id, body);
      else await api.createScene(body);
      notify("success", t("scenes.saved"), body.name);
      onDone();
    } catch (err) {
      reportError(null, err);
    } finally {
      setSaving(false);
    }
  };

  const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

  return (
    <Card className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("scenes.name")}</span>
          <input id="scene-name" className={input} value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("scenes.description")}</span>
          <input id="scene-desc" className={input} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>

      {!initial && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted">{tr("scenes.templates")}</span>
          {TEMPLATES.map((tpl) => (
            <Button key={tpl.name} variant="ghost" className="border border-line" onClick={() => applyTemplate(tpl)}>
              {tr(tpl.name)}
            </Button>
          ))}
        </div>
      )}

      <div className="space-y-3">
        {devices.map((v) => {
          const r = rows[v.device.id];
          const kinds: [Kind, MessageKey][] = [
            ["skip", "scenes.kind.skip"],
            ["off", "scenes.kind.off"],
            ["on", "scenes.kind.on"],
            ...(has(v, "color_rgb") ? ([["color", "scenes.kind.color"]] as [Kind, MessageKey][]) : []),
            ...(has(v, "color_temp") ? ([["white", "scenes.kind.white"]] as [Kind, MessageKey][]) : []),
          ];
          const ct = v.device.capabilities.color_temp_range;
          return (
            <div key={v.device.id} className="rounded-xl border border-line p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{v.device.name}</span>
                <select
                  className={cx(input, "w-auto")}
                  value={r.kind}
                  aria-label={tr("scenes.actionFor", { name: v.device.name })}
                  onChange={(e) => update(v.device.id, { kind: e.target.value as Kind })}
                >
                  {kinds.map(([k, label]) => (
                    <option key={k} value={k}>
                      {tr(label)}
                    </option>
                  ))}
                </select>
              </div>
              {(r.kind === "on" || r.kind === "color" || r.kind === "white") && (
                <div className="mt-3 grid items-center gap-3 sm:grid-cols-[1fr_auto]">
                  {has(v, "brightness") && (
                    <label className="text-sm">
                      <span className="mb-1 flex justify-between text-muted">
                        {tr("card.brightness")} <span className="tabular-nums text-gray-200">{r.brightness}%</span>
                      </span>
                      <input
                        type="range"
                        className="slider"
                        min={v.device.capabilities.brightness_min}
                        max={100}
                        value={r.brightness}
                        onChange={(e) => update(v.device.id, { brightness: Number(e.target.value) })}
                      />
                    </label>
                  )}
                  {r.kind === "color" && (
                    <input
                      type="color"
                      aria-label={tr("card.color")}
                      value={r.hex}
                      onChange={(e) => update(v.device.id, { hex: e.target.value })}
                      className="h-10 w-16 cursor-pointer rounded-lg border border-line bg-transparent"
                    />
                  )}
                  {r.kind === "white" && ct && (
                    <label className="text-sm sm:w-56">
                      <span className="mb-1 flex justify-between text-muted">
                        {tr("scenes.temperature")} <span className="tabular-nums text-gray-200">{clampKelvin(v, r.kelvin)} K</span>
                      </span>
                      <input
                        type="range"
                        className="slider"
                        min={ct.min_k}
                        max={ct.max_k}
                        step={50}
                        value={clampKelvin(v, r.kelvin)}
                        onChange={(e) => update(v.device.id, { kelvin: Number(e.target.value) })}
                      />
                    </label>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>
          {tr("common.cancel")}
        </Button>
        <Button variant="primary" busy={saving} onClick={save}>
          {tr("common.save")}
        </Button>
      </div>
    </Card>
  );
}

export function Scenes() {
  const tr = useT();
  const devices = useDevices((s) => s.devices);
  const scenes = useScenes();
  const [editing, setEditing] = useState<Scene | "new" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const registered = sortedViews(devices).filter((v) => v.device.registered && v.device.capabilities.supported.length);
  const nameOf = (id: string) => devices[id]?.device.name ?? tr("scenes.unknownDevice");

  const activate = async (scene: Scene) => {
    setBusy(scene.id);
    try {
      summarizeBatch(scene.name, await api.activateScene(scene.id));
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (scene: Scene) => {
    if (!confirm(tr("scenes.confirmDelete", { name: scene.name }))) return;
    try {
      await api.deleteScene(scene.id);
    } catch (err) {
      reportError(null, err);
    }
  };

  return (
    <>
      <PageHeader
        title={tr("scenes.title")}
        subtitle={tr("scenes.subtitle")}
        actions={
          editing === null && (
            <Button variant="primary" disabled={!registered.length} onClick={() => setEditing("new")}>
              <Plus className="size-4" aria-hidden /> {tr("scenes.new")}
            </Button>
          )
        }
      />
      {editing !== null ? (
        <SceneEditor
          key={editing === "new" ? "new" : editing.id}
          initial={editing === "new" ? null : editing}
          devices={registered}
          onDone={() => setEditing(null)}
        />
      ) : scenes.length === 0 ? (
        <Empty icon={<Sparkles className="size-8" />} title={tr("scenes.emptyTitle")}>
          {tr("scenes.emptyText")}
        </Empty>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {scenes.map((scene) => (
            <Card key={scene.id} className="flex flex-col">
              <h3 className="font-semibold">{scene.name}</h3>
              {scene.description && <p className="mt-1 text-sm text-muted">{scene.description}</p>}
              <ul className="mt-3 flex-1 space-y-1 text-xs text-muted">
                {scene.actions.map((a) => (
                  <li key={a.device_id} className="flex items-center gap-2">
                    {a.color && <span className="size-3 rounded-full" style={{ background: rgbToHex(a.color) }} />}
                    <span>
                      {nameOf(a.device_id)}:{" "}
                      {a.power === false
                        ? tr("scenes.off")
                        : [a.color ? tr("scenes.colorWord") : a.color_temp_k ? `${a.color_temp_k}K` : tr("scenes.on"), a.brightness ? `${a.brightness}%` : ""]
                            .filter(Boolean)
                            .join(", ")}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="mt-4 flex gap-2">
                <Button variant="primary" className="flex-1" busy={busy === scene.id} onClick={() => activate(scene)}>
                  <Play className="size-4" aria-hidden /> {tr("scenes.activate")}
                </Button>
                <Button variant="ghost" aria-label={tr("common.edit")} onClick={() => setEditing(scene)}>
                  <Pencil className="size-4" />
                </Button>
                <Button variant="danger" aria-label={tr("common.delete")} onClick={() => remove(scene)}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
