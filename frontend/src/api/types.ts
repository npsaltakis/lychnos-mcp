// Mirrors backend/app/domain/models.py

export type Capability = "power" | "brightness" | "color_rgb" | "color_temp";
export type Availability = "unknown" | "online" | "offline";
export type LightMode = "color" | "white";
export type CommandOutcome = "confirmed" | "unconfirmed" | "mismatch" | "failed";
export type BatchOutcome = "all_confirmed" | "partial" | "failed" | "nothing_to_do";

export interface RGB {
  r: number;
  g: number;
  b: number;
}

export interface Capabilities {
  supported: Capability[];
  brightness_min: number;
  color_temp_range: { min_k: number; max_k: number } | null;
  supports_state_query: boolean;
  supports_push: boolean;
}

export interface DeviceState {
  power: boolean | null;
  brightness: number | null;
  color: RGB | null;
  color_temp_k: number | null;
  mode: LightMode | null;
}

export type Command =
  | { type: "set_power"; on: boolean }
  | { type: "set_brightness"; brightness: number }
  | { type: "set_color"; color: RGB }
  | { type: "set_color_temperature"; kelvin: number };

export interface ErrorInfo {
  code: string;
  message: string;
  at: string;
}

export interface DeviceRecord {
  id: string;
  provider: string;
  native_id: string;
  name: string;
  manufacturer: string;
  model: string | null;
  address: string | null;
  registered: boolean;
  capabilities: Capabilities;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DeviceRuntime {
  availability: Availability;
  state: DeviceState;
  state_confirmed_at: string | null;
  pending: { command_id: string; command: Command; issued_at: string } | null;
  last_error: ErrorInfo | null;
  consecutive_failures: number;
  last_seen_at: string | null;
}

export interface DeviceView {
  device: DeviceRecord;
  runtime: DeviceRuntime;
}

export interface CommandResult {
  command_id: string;
  device_id: string;
  command: Command;
  outcome: CommandOutcome;
  state: DeviceState;
  availability: Availability;
  error: ErrorInfo | null;
  duration_ms: number;
}

export interface BatchResult {
  outcome: BatchOutcome;
  results: CommandResult[];
  skipped: { device_id: string; reason: string }[];
}

export interface SceneAction {
  device_id: string;
  power?: boolean | null;
  brightness?: number | null;
  color?: RGB | null;
  color_temp_k?: number | null;
}

export interface SceneInput {
  name: string;
  description: string;
  actions: SceneAction[];
}

export interface Scene extends SceneInput {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface SystemInfo {
  version: string;
  providers: { key: string; manufacturer: string }[];
  devices_total: number;
  devices_registered: number;
  poll_interval_s: number;
  remote_access: boolean;
}

export type LiveEvent =
  | { type: "snapshot"; data: { devices: DeviceView[] } }
  | { type: "device_runtime"; data: { device_id: string; runtime: DeviceRuntime }; ts: string }
  | { type: "device_updated"; data: { device: DeviceRecord }; ts: string }
  | { type: "device_removed"; data: { device_id: string }; ts: string }
  | { type: "command_result"; data: CommandResult; ts: string }
  | { type: "scenes_changed"; data: Record<string, never>; ts: string }
  | { type: "notification"; data: { level: string; message: string }; ts: string }
  | { type: "resync_required"; data: Record<string, never>; ts: string };
