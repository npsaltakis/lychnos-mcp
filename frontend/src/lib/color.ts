import type { DeviceState, RGB } from "../api/types";

export const rgbToHex = ({ r, g, b }: RGB) =>
  `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;

export function hexToRgb(hex: string): RGB {
  const v = hex.replace("#", "");
  return { r: parseInt(v.slice(0, 2), 16), g: parseInt(v.slice(2, 4), 16), b: parseInt(v.slice(4, 6), 16) };
}

/** Approximate RGB of a colour temperature (Tanner Helland's algorithm), for previews only. */
export function kelvinToRgb(kelvin: number): RGB {
  const t = kelvin / 100;
  const clamp = (x: number) => Math.max(0, Math.min(255, Math.round(x)));
  const r = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
  const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return { r: clamp(r), g: clamp(g), b: clamp(b) };
}

/** CSS colour that represents what the light currently shows. */
export function stateColor(state: DeviceState): string | null {
  if (state.power === false) return null;
  if (state.mode === "white" && state.color_temp_k) return rgbToHex(kelvinToRgb(state.color_temp_k));
  if (state.color) return rgbToHex(state.color);
  return null;
}

export const PRESETS: { name: string; rgb: RGB }[] = [
  { name: "Κόκκινο", rgb: { r: 255, g: 0, b: 0 } },
  { name: "Πορτοκαλί", rgb: { r: 255, g: 127, b: 0 } },
  { name: "Κίτρινο", rgb: { r: 255, g: 210, b: 0 } },
  { name: "Πράσινο", rgb: { r: 0, g: 255, b: 64 } },
  { name: "Κυανό", rgb: { r: 0, g: 220, b: 255 } },
  { name: "Μπλε", rgb: { r: 0, g: 60, b: 255 } },
  { name: "Μωβ", rgb: { r: 140, g: 0, b: 255 } },
  { name: "Ροζ", rgb: { r: 255, g: 0, b: 150 } },
];
