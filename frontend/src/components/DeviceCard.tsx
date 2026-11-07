import { AlertTriangle, CheckCircle2, Clock, Lightbulb, Palette, Sun } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { HexColorPicker } from "react-colorful";
import type { Command, CommandResult, DeviceView } from "../api/types";
import { PRESETS, hexToRgb, kelvinToRgb, rgbToHex, stateColor } from "../lib/color";
import { sendCommand } from "../lib/commands";
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
