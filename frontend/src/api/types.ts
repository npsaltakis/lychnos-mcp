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
  room_id: string | null;
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
  gradient?: RGB[] | null;
  gradient_per_bar?: boolean;
  effect?: EffectName | null;
  music_style?: string | null;
  music_sensitivity?: number;
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

export interface Room {
  id: string;
  name: string;
  icon: string;
  sort_order: number;
  created_at: string;
}

export type Trigger =
  | { type: "time"; time: string; days: number[] }
  | { type: "sun"; event: "sunrise" | "sunset"; offset_min: number; days: number[]; cloudy_earlier_min?: number };

export interface Target {
  kind: "all" | "room" | "device";
  id: string | null;
}

export type AutomationAction =
  | { type: "scene"; scene_id: string }
  | { type: "command"; target: Target; command: Command }
  | { type: "fade"; target: Target; to_brightness: number; duration_min: number; color_temp_k: number | null }
  | { type: "fade_out"; target: Target; duration_min: number };

export interface AutomationInput {
  name: string;
  enabled: boolean;
  trigger: Trigger;
  action: AutomationAction;
}

export interface Automation extends AutomationInput {
  id: string;
  last_run_at: string | null;
  last_result: { outcome: string; message: string; at: string } | null;
  next_run_at: string | null;
}

export interface PresenceSettings {
  enabled: boolean;
  people: { name: string; mac: string }[];
  away_after_min: number;
  on_leave: "nothing" | "all_off";
  on_arrive: "nothing" | "all_on" | "scene";
  arrive_scene_id: string | null;
  arrive_only_dark: boolean;
}

export interface PresenceStatus {
  settings: PresenceSettings;
  anyone_home: boolean | null;
  people: { name: string; mac: string; home: boolean; last_seen: string | null; since: string | null; ip: string | null }[];
}

export interface Neighbor {
  ip: string;
  mac: string;
  state: string;
}

export interface WeatherDay {
  date: string;
  weather_code: number;
  temp_max: number;
  temp_min: number;
  precipitation_probability: number | null;
  sunrise: string | null;
  sunset: string | null;
}

export interface WeatherReport {
  now: {
    temperature: number;
    apparent_temperature: number | null;
    weather_code: number;
    cloud_cover: number | null;
    is_day: boolean;
    wind_speed: number | null;
    humidity: number | null;
  };
  days: WeatherDay[];
  fetched_at: string;
  location_label: string;
}

export interface Weighing {
  id: number;
  ts: string;
  kg: number;
}

export interface ScaleView {
  address: string;
  name: string;
  room_id: string | null;
  model: string;
  model_label: string;
  last_seen: string | null;
  last_kg: number | null;
}

export interface ScaleCandidate {
  address: string;
  name: string;
  model: string;
  model_label: string;
  kg: number | null;
}

export interface ScaleStatus {
  scales: ScaleView[];
  bluetooth: "off" | "listening" | "unavailable";
  error: string | null;
  latest: Weighing | null;
}

export interface UsageReport {
  days: string[];
  devices: { device_id: string; name: string; hours: number[]; total_hours: number }[];
}

export interface BackupFile {
  name: string;
  size_bytes: number;
  created_at: string;
}

export interface VacationSettings {
  enabled: boolean;
  start: "sunset" | "time";
  start_time: string;
  end_time: string;
  device_ids: string[];
}

export interface VacationStatus {
  settings: VacationSettings;
  active: boolean;
  window_start: string | null;
  window_end: string | null;
  lights_on: string[];
}

export interface Location {
  latitude: number;
  longitude: number;
  label: string;
}

export interface ActivityEntry {
  id: number;
  ts: string;
  source: string;
  device_id: string | null;
  device_name: string | null;
  command: string;
  outcome: string;
  detail: string | null;
}

export interface NotificationSettings {
  ntfy: { enabled: boolean; server: string; topic: string };
  telegram: { enabled: boolean; chat_id: string; commands: boolean };
  email: {
    enabled: boolean;
    smtp_host: string;
    smtp_port: number;
    security: "starttls" | "ssl" | "none";
    username: string;
    sender: string;
    recipient: string;
  };
  webpush: { enabled: boolean };
  events: {
    device_offline: boolean;
    device_online: boolean;
    automation_failed: boolean;
    automation_ran: boolean;
    command_failed: boolean;
    weight: boolean;
  };
}

export interface NotificationView extends NotificationSettings {
  secrets_set: { ntfy_token: boolean; telegram_token: boolean; smtp_password: boolean };
  webpush_devices: number;
}

export type Channel = "ntfy" | "telegram" | "email" | "webpush";

export type LiveEvent =
  | { type: "snapshot"; data: { devices: DeviceView[] } }
  | { type: "device_runtime"; data: { device_id: string; runtime: DeviceRuntime }; ts: string }
  | { type: "device_updated"; data: { device: DeviceRecord }; ts: string }
  | { type: "device_removed"; data: { device_id: string }; ts: string }
  | { type: "command_result"; data: CommandResult; ts: string }
  | { type: "scenes_changed"; data: Record<string, never>; ts: string }
  | { type: "rooms_changed"; data: Record<string, never>; ts: string }
  | { type: "automations_changed"; data: Record<string, never>; ts: string }
  | { type: "activity_added"; data: Record<string, never>; ts: string }
  | { type: "notification"; data: { level: string; message: string }; ts: string }
  | { type: "effects_changed"; data: { running: RunningEffect[] }; ts: string }
  | { type: "weight_recorded"; data: Record<string, unknown>; ts: string }
  | { type: "resync_required"; data: Record<string, never>; ts: string };

export type EffectName = "rainbow" | "breathe" | "candle";
export type MusicStyle = string;

export interface RunningEffect {
  effect: EffectName;
  device_ids: string[];
  started_at: string;
}
