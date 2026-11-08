import { useAuth } from "../store/auth";
import type {
  ActivityEntry,
  Channel,
  NotificationSettings,
  NotificationView,
  Automation,
  AutomationInput,
  Location,
  Room,
  BatchResult,
  Command,
  CommandResult,
  DeviceRuntime,
  DeviceView,
  Scene,
  SceneInput,
  SystemInfo,
  BackupFile,
  UsageReport,
  WeatherReport,
  Neighbor,
  EffectName,
  MusicStyle,
  RunningEffect,
  Target,
  PresenceSettings,
  PresenceStatus,
  VacationSettings,
  VacationStatus,
} from "./types";

/** Error returned by the backend: {"error": {code, message, details}} */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const err = body?.error ?? {};
    return new ApiError(res.status, err.code ?? "http_error", err.message ?? res.statusText, err.details ?? {});
  } catch {
    return new ApiError(res.status, "http_error", res.statusText || "Network error");
  }
}

// The dashboard authenticates with an HttpOnly session cookie that JavaScript cannot read.
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network_error", "Network error");
  }
  if (!res.ok) {
    const error = await toError(res);
    if (res.status === 401 && !path.startsWith("/auth/")) useAuth.getState().set("signed_out");
    throw error;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  authStatus: () => request<{ authenticated: boolean; password_set: boolean }>("GET", "/auth/status"),
  login: (password: string) => request<{ authenticated: boolean }>("POST", "/auth/login", { password }),
  logout: () => request<{ authenticated: boolean }>("POST", "/auth/logout"),
  systemInfo: () => request<SystemInfo>("GET", "/system/info"),
  devices: () => request<DeviceView[]>("GET", "/devices"),
  scan: (timeout_s?: number) => request<DeviceView[]>("POST", "/discovery/scan", { timeout_s }),
  updateDevice: (id: string, patch: { name?: string; registered?: boolean }) =>
    request<DeviceView>("PATCH", `/devices/${encodeURIComponent(id)}`, patch),
  deleteDevice: (id: string) => request<void>("DELETE", `/devices/${encodeURIComponent(id)}`),
  refreshState: (id: string) =>
    request<DeviceRuntime>("GET", `/devices/${encodeURIComponent(id)}/state?refresh=true`),
  command: (id: string, command: Command) =>
    request<CommandResult>("POST", `/devices/${encodeURIComponent(id)}/commands`, command),
  groupCommand: (command: Command) => request<BatchResult>("POST", "/groups/all/commands", command),
  scenes: () => request<Scene[]>("GET", "/scenes"),
  createScene: (scene: SceneInput) => request<Scene>("POST", "/scenes", scene),
  updateScene: (id: string, scene: SceneInput) => request<Scene>("PUT", `/scenes/${id}`, scene),
  deleteScene: (id: string) => request<void>("DELETE", `/scenes/${id}`),
  activateScene: (id: string) => request<BatchResult>("POST", `/scenes/${id}/activate`),
  rooms: () => request<Room[]>("GET", "/rooms"),
  createRoom: (name: string, icon = "home") => request<Room>("POST", "/rooms", { name, icon }),
  updateRoom: (id: string, name: string, icon = "home") => request<Room>("PUT", `/rooms/${id}`, { name, icon }),
  deleteRoom: (id: string) => request<void>("DELETE", `/rooms/${id}`),
  roomCommand: (id: string, command: Command) => request<BatchResult>("POST", `/rooms/${id}/commands`, command),
  setDeviceRoom: (deviceId: string, roomId: string | null) =>
    request<DeviceView>("PATCH", `/devices/${encodeURIComponent(deviceId)}`, { room_id: roomId }),
  automations: () => request<Automation[]>("GET", "/automations"),
  createAutomation: (body: AutomationInput) => request<Automation>("POST", "/automations", body),
  updateAutomation: (id: string, body: AutomationInput) => request<Automation>("PUT", `/automations/${id}`, body),
  setAutomationEnabled: (id: string, enabled: boolean) =>
    request<Automation>("PATCH", `/automations/${id}`, { enabled }),
  deleteAutomation: (id: string) => request<void>("DELETE", `/automations/${id}`),
  runAutomation: (id: string) => request<Automation>("POST", `/automations/${id}/run`),
  location: () => request<Location | null>("GET", "/settings/location"),
  setLocation: (loc: Location) => request<Location>("PUT", "/settings/location", loc),
  musicStatus: (id: string) => request<{ on: boolean }>("GET", `/devices/${encodeURIComponent(id)}/music`),
  stopMusic: (id: string) => request<DeviceRuntime>("POST", `/devices/${encodeURIComponent(id)}/music/stop`),
  musicStyles: (id: string) => request<string[]>("GET", `/devices/${encodeURIComponent(id)}/music/styles`),
  setMusic: (id: string, style: MusicStyle, sensitivity: number) =>
    request<DeviceRuntime>("POST", `/devices/${encodeURIComponent(id)}/music`, { style, sensitivity }),
  setSegments: (id: string, colors: { r: number; g: number; b: number }[], mode: "gradient" | "per_bar" | "exact") =>
    request<DeviceRuntime>("POST", `/devices/${encodeURIComponent(id)}/segments`, { colors, mode }),
  effects: () => request<RunningEffect[]>("GET", "/effects"),
  startEffect: (effect: EffectName, target: Target) => request<RunningEffect>("POST", "/effects", { effect, target }),
  stopEffects: (device_ids?: string[]) => request<RunningEffect[]>("POST", "/effects/stop", { device_ids: device_ids ?? null }),
  presence: () => request<PresenceStatus>("GET", "/settings/presence"),
  setPresence: (body: PresenceSettings) => request<PresenceStatus>("PUT", "/settings/presence", body),
  presenceNetwork: () => request<Neighbor[]>("GET", "/presence/network"),
  weather: () => request<WeatherReport | null>("GET", "/weather"),
  usage: (days: number) => request<UsageReport>("GET", `/stats/usage?days=${days}`),
  backups: () => request<{ folder: string; backups: BackupFile[] }>("GET", "/system/backups"),
  createBackup: () => request<BackupFile>("POST", "/system/backups"),
  vacation: () => request<VacationStatus>("GET", "/settings/vacation"),
  setVacation: (body: VacationSettings) => request<VacationStatus>("PUT", "/settings/vacation", body),
  activity: (limit = 100) => request<ActivityEntry[]>("GET", `/activity?limit=${limit}`),
  activityQuery: (query: string) => request<ActivityEntry[]>("GET", `/activity?${query}`),
  notifications: () => request<NotificationView>("GET", "/notifications"),
  saveNotifications: (body: NotificationSettings & { secrets: Record<string, string | null> }) =>
    request<NotificationView>("PUT", "/notifications", body),
  testNotification: (channel?: Channel) =>
    request<Record<string, string>>("POST", "/notifications/test", channel ? { channel } : {}),
  telegramChats: (token?: string) =>
    request<{ id: string; name: string }[]>("POST", "/notifications/telegram/chats", { token: token || null }),
  webpushKey: () => request<{ public_key: string }>("GET", "/notifications/webpush/key"),
  webpushSubscribe: (sub: PushSubscriptionJSON) =>
    request<NotificationView>("POST", "/notifications/webpush/subscriptions", sub),
  webpushUnsubscribe: (endpoint: string) =>
    request<NotificationView>("POST", "/notifications/webpush/unsubscribe", { endpoint }),
};

/** Ask the backend whether this browser has a valid session. */
export async function checkAuth(): Promise<void> {
  const set = useAuth.getState().set;
  try {
    const status = await api.authStatus();
    set(status.authenticated ? "signed_in" : status.password_set ? "signed_out" : "no_password");
  } catch {
    set("signed_out");
  }
}
