import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ActivityEntry, Automation, Room, Scene } from "../api/types";
import { useDevices } from "../store/devices";

/** Loads a list from the API and reloads it whenever the related live event arrives. */
function useLiveList<T>(load: () => Promise<T[]>, version: number): T[] {
  const [items, setItems] = useState<T[]>([]);
  useEffect(() => {
    let cancelled = false;
    load()
      .then((data) => !cancelled && setItems(data))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);
  return items;
}

export const useRooms = (): Room[] => useLiveList(api.rooms, useDevices((s) => s.roomsVersion));
export const useScenes = (): Scene[] => useLiveList(api.scenes, useDevices((s) => s.scenesVersion));
export const useAutomations = (): Automation[] =>
  useLiveList(api.automations, useDevices((s) => s.automationsVersion));
export const useActivity = (limit = 100): ActivityEntry[] =>
  useLiveList(() => api.activity(limit), useDevices((s) => s.activityVersion));
