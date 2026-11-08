import { AlertTriangle, CheckCircle2, Clock, Flame, Lightbulb, Music, Palette, Rainbow, Square, Sun, Wind } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { HexColorPicker } from "react-colorful";
import { api } from "../api/client";
import type { Command, CommandResult, DeviceView, EffectName, MusicStyle } from "../api/types";
import { PRESETS, hexToRgb, kelvinToRgb, rgbToHex, stateColor } from "../lib/color";
import { reportError, sendCommand } from "../lib/commands";
import { locale, t, useT } from "../lib/i18n";
import { has, useDevices } from "../store/devices";
import { Card, Slider, StatusDot, Toggle, cx, swatchClass } from "./ui";

const COLOR_DEBOUNCE_MS = 350;

function pendingLabel(command: Command): string {
  switch (command.type) {
    case "set_power":
      return command.on ? t("card.turningOn") : t("card.turningOff");
    case "set_brightness":
      return t("card.brightnessPending", { value: command.brightness });
    case "set_color":
      return t("card.colorPending");
    case "set_color_temperature":
      return `${command.kelvin}K…`;
  }
}

function OutcomeBadge({ result }: { result: CommandResult | undefined }) {
  const tr = useT();
  if (!result) return null;
  const map = {
    confirmed: { icon: CheckCircle2, key: "outcome.confirmed", cls: "text-emerald-400" },
    unconfirmed: { icon: Clock, key: "outcome.unconfirmed", cls: "text-amber-300" },
    mismatch: { icon: AlertTriangle, key: "outcome.mismatch", cls: "text-amber-300" },
    failed: { icon: AlertTriangle, key: "outcome.failed", cls: "text-red-400" },
  } as const;
  const o = map[result.outcome];
  const Icon = o.icon;
  return (
    <span className={cx("inline-flex items-center gap-1 text-xs", o.cls)} title={result.error?.message}>
      <Icon className="size-3.5" aria-hidden />
      {tr(o.key)} · {result.duration_ms} ms
    </span>
  );
}

const EFFECTS: [EffectName, "fx.rainbow" | "fx.breathe" | "fx.candle", typeof Rainbow][] = [
  ["rainbow", "fx.rainbow", Rainbow],
  ["breathe", "fx.breathe", Wind],
  ["candle", "fx.candle", Flame],
];

const GRADIENTS: ["grad.sunset" | "grad.ocean" | "grad.dawn" | "grad.forest", string, string][] = [
  ["grad.sunset", "#ff5a00", "#8000ff"],
  ["grad.ocean", "#00e5ff", "#0030ff"],
  ["grad.dawn", "#ff2d87", "#ffc400"],
  ["grad.forest", "#00ff6a", "#ffe100"],
];

function Gradient({ deviceId, segments }: { deviceId: string; segments: number }) {
  const tr = useT();
  const [from, setFrom] = useState("#ff5a00");
  const [to, setTo] = useState("#8000ff");
  const [perBar, setPerBar] = useState(true);
  const [busy, setBusy] = useState(false);
  const twoBars = segments === 12;
  const apply = async (a: string, b: string) => {
    setBusy(true);
    try {
      await api.setSegments(deviceId, [hexToRgb(a), hexToRgb(b)], twoBars && perBar ? "per_bar" : "gradient");
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset className="space-y-2 rounded-xl border border-line p-3" disabled={busy}>
      <legend className="px-1 text-sm text-muted">{tr("grad.title", { n: segments })}</legend>
      <div className="flex flex-wrap gap-2">
        {GRADIENTS.map(([key, a, b]) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setFrom(a);
              setTo(b);
              void apply(a, b);
            }}
            className="inline-flex min-h-9 items-center gap-2 rounded-full border border-line px-3 text-sm text-gray-200 hover:border-gray-500 pointer-coarse:min-h-11"
          >
            <span className="h-3 w-6 rounded-full" style={{ background: `linear-gradient(90deg, ${a}, ${b})` }} aria-hidden />
            {tr(key)}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input type="color" aria-label={tr("grad.from")} value={from} onChange={(e) => setFrom(e.target.value)} className="h-9 w-12 cursor-pointer rounded-lg border border-line bg-transparent" />
        <span className="h-3 flex-1 rounded-full" style={{ background: `linear-gradient(90deg, ${from}, ${to})` }} aria-hidden />
        <input type="color" aria-label={tr("grad.to")} value={to} onChange={(e) => setTo(e.target.value)} className="h-9 w-12 cursor-pointer rounded-lg border border-line bg-transparent" />
        <button type="button" onClick={() => void apply(from, to)} className="min-h-9 rounded-full bg-accent px-4 text-sm font-medium text-black pointer-coarse:min-h-11">
          {tr("grad.apply")}
        </button>
      </div>
      {twoBars && (
        <label className="inline-flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" className="size-4 accent-amber-400" checked={perBar} onChange={(e) => setPerBar(e.target.checked)} />
          {tr("grad.perBar")}
        </label>
      )}
    </fieldset>
  );
}

const styleLabel = (s: string) =>
  s === "rainbowcircle" ? "Rainbow circle" : s.charAt(0).toUpperCase() + s.slice(1);

function MusicMode({ deviceId }: { deviceId: string }) {
  const tr = useT();
  const [styles, setStyles] = useState<MusicStyle[] | null>(null);
  const [style, setStyle] = useState<MusicStyle>("");
  const [on, setOn] = useState(false);
  // Any other command (from here, Claude, a scene…) ends music mode: re-check after each result.
  const lastResult = useDevices((s) => s.lastResult[deviceId]);
  useEffect(() => {
    api.musicStatus(deviceId).then((r) => setOn(r.on)).catch(() => undefined);
  }, [deviceId, lastResult]);
  const [sensitivity, setSensitivity] = useState(80);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api
      .musicStyles(deviceId)
      .then((list) => {
        setStyles(list);
        setStyle((cur) => cur || (list.includes("rhythm") ? "rhythm" : (list[0] ?? "")));
      })
      .catch(() => setStyles([]));
  }, [deviceId]);
  const toggle = async () => {
    setBusy(true);
    try {
      if (on) {
        await api.stopMusic(deviceId);
        setOn(false);
      } else {
        await api.setMusic(deviceId, style, sensitivity);
        setOn(true);
      }
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset className="space-y-2 rounded-xl border border-line p-3" disabled={busy}>
      <legend className="px-1 text-sm text-muted">{tr("music.title")}</legend>
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={tr("music.style")}
          value={style}
          onChange={(e) => setStyle(e.target.value as MusicStyle)}
          className="min-h-9 rounded-lg border border-line bg-panel-2 px-2 text-sm focus:border-accent focus:outline-none"
        >
          {(styles ?? []).map((s) => (
            <option key={s} value={s}>
              {styleLabel(s)}
            </option>
          ))}
        </select>
        <button
          type="button"
          aria-pressed={on}
          onClick={() => void toggle()}
          className={cx(
            "inline-flex min-h-9 items-center gap-1.5 rounded-full px-4 text-sm font-medium pointer-coarse:min-h-11",
            on ? "border border-accent bg-accent/15 text-accent" : "bg-accent text-black",
          )}
        >
          {on ? <Square className="size-3.5" aria-hidden /> : <Music className="size-4" aria-hidden />}
          {tr(on ? "music.stop" : "music.start")}
        </button>
      </div>
      <Slider label={tr("music.sensitivity")} min={0} max={100} value={sensitivity} onCommit={setSensitivity} format={(v) => `${v}%`} />
      <p className="text-xs text-muted">{tr("music.hint")}</p>
    </fieldset>
  );
}

function Effects({ deviceId }: { deviceId: string }) {
  const tr = useT();
  const running = useDevices((s) => s.effects[deviceId]);
  const start = (effect: EffectName) =>
    api.startEffect(effect, { kind: "device", id: deviceId }).catch((err) => reportError(null, err));
  return (
    <div role="group" aria-label={tr("fx.title")} className="flex flex-wrap items-center gap-2">
      {EFFECTS.map(([key, label, Icon]) => (
        <button
          key={key}
          type="button"
          aria-pressed={running === key}
          onClick={() => void start(key)}
          className={cx(
            "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-sm transition-colors pointer-coarse:min-h-11",
            running === key ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:text-gray-200",
          )}
        >
          <Icon className="size-4" aria-hidden /> {tr(label)}
        </button>
      ))}
      {running && (
        <button
          type="button"
          onClick={() => void api.stopEffects([deviceId]).catch((err) => reportError(null, err))}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-line px-3 text-sm text-gray-200 pointer-coarse:min-h-11"
        >
          <Square className="size-3.5" aria-hidden /> {tr("fx.stop")}
        </button>
      )}
    </div>
  );
}

export function DeviceCard({ view }: { view: DeviceView }) {
  const tr = useT();
  const { device, runtime } = view;
  const { state } = runtime;
  const lastResult = useDevices((s) => s.lastResult[device.id]);
  const caps = device.capabilities;
  const ctRange = caps.color_temp_range;
  const [tab, setTab] = useState<"color" | "white">(state.mode === "white" ? "white" : "color");
  const [draftHex, setDraftHex] = useState<string | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(debounce.current), []);
  useEffect(() => {
    if (state.mode) setTab(state.mode === "white" ? "white" : "color");
  }, [state.mode]);

  const offline = runtime.availability === "offline";
  const isOn = state.power === true;
  const preview = stateColor(state);
  const send = (command: Command) => sendCommand(device.id, command);

  const onPick = (hex: string) => {
    setDraftHex(hex);
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      send({ type: "set_color", color: hexToRgb(hex) });
      setDraftHex(null);
    }, COLOR_DEBOUNCE_MS);
  };

  const pickerHex = draftHex ?? (state.color ? rgbToHex(state.color) : "#ffffff");
  const showColor = has(view, "color_rgb");
  const showWhite = has(view, "color_temp") && ctRange;
  const lastKnown = runtime.state_confirmed_at
    ? ` (${new Date(runtime.state_confirmed_at).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })})`
    : "";

  return (
    <Card className="relative overflow-hidden">
      {/* soft glow in the light's current colour */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-24 -right-24 size-56 rounded-full opacity-25 blur-3xl transition-colors duration-700"
        style={{ background: preview ?? "transparent" }}
      />

      <div className="relative flex items-start gap-3">
        <div
          className={cx(
            "flex size-12 shrink-0 items-center justify-center rounded-xl border border-line transition-colors",
            !preview && "bg-panel-2",
          )}
          style={preview ? { background: preview, boxShadow: `0 0 24px ${preview}55` } : undefined}
          aria-label={preview ? tr("card.currentColor", { color: preview }) : tr("card.off")}
        >
          <Lightbulb className={cx("size-6", preview ? "text-black/70" : "text-muted")} aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-semibold">{device.name}</h3>
          <p className="truncate text-xs text-muted">
            {device.manufacturer}
            {device.model ? ` · ${device.model}` : ""}
          </p>
          <div className="mt-1">
            <StatusDot availability={runtime.availability} />
          </div>
        </div>
        {has(view, "power") && (
          <Toggle
            label={tr("card.powerAria", { name: device.name })}
            checked={state.power}
            pending={runtime.pending?.command.type === "set_power"}
            onChange={(on) => send({ type: "set_power", on })}
          />
        )}
      </div>

      <div className="relative mt-3 flex min-h-5 flex-wrap items-center gap-x-3 text-xs">
        {runtime.pending ? (
          <span className="inline-flex items-center gap-1 text-sky-300">
            <Clock className="size-3.5 animate-pulse" aria-hidden />
            {pendingLabel(runtime.pending.command)}
          </span>
        ) : (
          <OutcomeBadge result={lastResult} />
        )}
        {offline && runtime.last_error && <span className="text-red-400">{runtime.last_error.message}</span>}
      </div>
      {offline && (
        <p className="relative mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {tr("card.offline", { time: lastKnown })}
        </p>
      )}

      <div className={cx("relative mt-4 space-y-5", (!isOn || offline) && "opacity-60")}>
        {has(view, "brightness") && (
          <Slider
            label={tr("card.brightness")}
            min={caps.brightness_min}
            max={100}
            value={state.brightness}
            format={(v) => `${v}%`}
            onCommit={(brightness) => send({ type: "set_brightness", brightness })}
          />
        )}

        {(showColor || showWhite) && (
          <div>
            {showColor && showWhite && (
              <div className="mb-3 grid grid-cols-2 gap-1 rounded-xl bg-panel-2 p-1 text-sm" role="tablist">
                {(
                  [
                    ["color", tr("card.color"), Palette],
                    ["white", tr("card.white"), Sun],
                  ] as const
                ).map(([key, text, Icon]) => (
                  <button
                    key={key}
                    role="tab"
                    aria-selected={tab === key}
                    onClick={() => setTab(key)}
                    className={cx(
                      "inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg transition-colors",
                      tab === key ? "bg-line text-white" : "text-muted hover:text-white",
                    )}
                  >
                    <Icon className="size-4" aria-hidden />
                    {text}
                  </button>
                ))}
              </div>
            )}

            {showColor && (tab === "color" || !showWhite) && (
              <div className="space-y-3">
                <div className="color-picker">
                  <HexColorPicker color={pickerHex} onChange={onPick} />
                </div>
                <div role="group" className="flex flex-wrap gap-2" aria-label={tr("card.presets")}>
                  {PRESETS.map((p) => (
                    <button
                      key={p.key}
                      title={tr(p.key)}
                      aria-label={tr(p.key)}
                      onClick={() => send({ type: "set_color", color: p.rgb })}
                      className={swatchClass}
                      style={{ background: rgbToHex(p.rgb) }}
                    />
                  ))}
                </div>
                {Number(device.metadata.segments) > 0 && <Gradient deviceId={device.id} segments={Number(device.metadata.segments)} />}
                <Effects deviceId={device.id} />
                {device.metadata.music === true && <MusicMode deviceId={device.id} />}
              </div>
            )}

            {showWhite && (tab === "white" || !showColor) && ctRange && (
              <Slider
                label={tr("card.colorTemp")}
                min={ctRange.min_k}
                max={ctRange.max_k}
                step={50}
                value={state.mode === "white" ? state.color_temp_k : null}
                format={(v) => `${v} K`}
                track={`linear-gradient(90deg, ${rgbToHex(kelvinToRgb(ctRange.min_k))}, #ffffff, ${rgbToHex(kelvinToRgb(ctRange.max_k))})`}
                onCommit={(kelvin) => send({ type: "set_color_temperature", kelvin })}
              />
            )}
          </div>
        )}

        {caps.supported.length === 0 && (
          <p className="text-sm text-amber-300">
            {tr("card.noControls")}
            {device.metadata.has_devicekey === false && tr("card.missingKey")}
          </p>
        )}
      </div>
    </Card>
  );
}
