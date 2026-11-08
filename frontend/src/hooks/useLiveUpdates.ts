import { useEffect } from "react";
import { api, checkAuth } from "../api/client";
import type { LiveEvent } from "../api/types";
import { useDevices } from "../store/devices";
import { notify } from "../store/toasts";

const MAX_BACKOFF_MS = 15000;

/**
 * Keeps the device store in sync with the backend over a WebSocket (with reconnect).
 * Only mounted while signed in; the session cookie authenticates the socket.
 */
export function useLiveUpdates(): void {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const store = useDevices.getState;

    const handle = (event: LiveEvent) => {
      const s = store();
      switch (event.type) {
        case "snapshot":
          s.setSnapshot(event.data.devices);
          api.effects().then(s.setEffects).catch(() => undefined);
          break;
        case "effects_changed":
          s.setEffects(event.data.running);
          break;
        case "device_runtime":
          s.setRuntime(event.data.device_id, event.data.runtime);
          break;
        case "device_updated":
          s.upsertRecord(event.data.device);
          break;
        case "device_removed":
          s.remove(event.data.device_id);
          break;
        case "command_result":
          s.setResult(event.data);
          break;
        case "scenes_changed":
          s.bumpScenes();
          break;
        case "rooms_changed":
          s.bump("roomsVersion");
          break;
        case "automations_changed":
          s.bump("automationsVersion");
          break;
        case "activity_added":
          s.bump("activityVersion");
          break;
        case "notification":
          notify(event.data.level === "warning" ? "warning" : "info", event.data.message);
          break;
        case "resync_required":
          api.devices().then(s.setSnapshot).catch(() => undefined);
          break;
      }
    };

    const connect = () => {
      store().setConnection("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      let opened = false;
      socket = new WebSocket(`${proto}://${location.host}/api/ws`, ["shc.v1"]);
      socket.onopen = () => {
        opened = true;
        attempt = 0;
        store().setConnection("online");
      };
      socket.onmessage = (msg) => {
        try {
          handle(JSON.parse(msg.data as string) as LiveEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        store().setConnection("offline");
        // Rejected before opening: maybe the session expired.
        if (!opened) void checkAuth();
        const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt++);
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, []);
}
