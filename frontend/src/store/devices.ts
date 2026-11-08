import { create } from "zustand";
import type { CommandResult, DeviceRecord, DeviceRuntime, DeviceView, EffectName, RunningEffect } from "../api/types";

export type Connection = "connecting" | "online" | "offline";

interface DeviceStore {
  devices: Record<string, DeviceView>;
  lastResult: Record<string, CommandResult>;
  connection: Connection;
  scenesVersion: number;
  roomsVersion: number;
  automationsVersion: number;
  activityVersion: number;
  bump: (key: "roomsVersion" | "automationsVersion" | "activityVersion") => void;
  setSnapshot: (views: DeviceView[]) => void;
  upsertRecord: (record: DeviceRecord) => void;
  setRuntime: (id: string, runtime: DeviceRuntime) => void;
  remove: (id: string) => void;
  setResult: (result: CommandResult) => void;
  setConnection: (c: Connection) => void;
  bumpScenes: () => void;
  effects: Record<string, EffectName>;
  setEffects: (running: RunningEffect[]) => void;
}

const emptyRuntime: DeviceRuntime = {
  availability: "unknown",
  state: { power: null, brightness: null, color: null, color_temp_k: null, mode: null },
  state_confirmed_at: null,
  pending: null,
  last_error: null,
  consecutive_failures: 0,
  last_seen_at: null,
};

export const useDevices = create<DeviceStore>((set) => ({
  devices: {},
  lastResult: {},
  connection: "connecting",
  scenesVersion: 0,
  roomsVersion: 0,
  automationsVersion: 0,
  activityVersion: 0,
  bump: (key) => set((s) => ({ [key]: s[key] + 1 }) as Partial<DeviceStore>),
  setSnapshot: (views) => set({ devices: Object.fromEntries(views.map((v) => [v.device.id, v])) }),
  upsertRecord: (record) =>
    set((s) => ({
      devices: {
        ...s.devices,
        [record.id]: { device: record, runtime: s.devices[record.id]?.runtime ?? emptyRuntime },
      },
    })),
  setRuntime: (id, runtime) =>
    set((s) => (s.devices[id] ? { devices: { ...s.devices, [id]: { ...s.devices[id], runtime } } } : s)),
  remove: (id) =>
    set((s) => {
      const devices = { ...s.devices };
      delete devices[id];
      return { devices };
    }),
  setResult: (result) => set((s) => ({ lastResult: { ...s.lastResult, [result.device_id]: result } })),
  setConnection: (connection) => set({ connection }),
  effects: {},
  setEffects: (running) =>
    set({ effects: Object.fromEntries(running.flatMap((r) => r.device_ids.map((id) => [id, r.effect] as const))) }),
  bumpScenes: () => set((s) => ({ scenesVersion: s.scenesVersion + 1 })),
}));

export const sortedViews = (devices: Record<string, DeviceView>) =>
  Object.values(devices).sort((a, b) => a.device.name.localeCompare(b.device.name, "el"));

export const has = (view: DeviceView, cap: DeviceRecord["capabilities"]["supported"][number]) =>
  view.device.capabilities.supported.includes(cap);
