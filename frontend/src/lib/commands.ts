import { ApiError, api } from "../api/client";
import type { Command, CommandResult } from "../api/types";
import { useDevices } from "../store/devices";
import { notify } from "../store/toasts";

const OUTCOME_TEXT = {
  unconfirmed: "Στάλθηκε, αλλά η συσκευή δεν μπορεί να επιβεβαιώσει",
  mismatch: "Η συσκευή αναφέρει διαφορετική κατάσταση",
} as const;

// Per device: at most one request in flight; while busy only the newest command is kept.
const inFlight = new Set<string>();
const queued = new Map<string, Command>();

/**
 * Send a command with "latest wins" coalescing, so dragging a slider or colour picker
 * never floods the device. Results are reported as toasts when not confirmed.
 */
export function sendCommand(deviceId: string, command: Command): void {
  if (inFlight.has(deviceId)) {
    queued.set(deviceId, command);
    return;
  }
  void run(deviceId, command);
}

async function run(deviceId: string, command: Command): Promise<void> {
  inFlight.add(deviceId);
  try {
    const result = await api.command(deviceId, command);
    useDevices.getState().setResult(result);
    reportOutcome(result);
  } catch (err) {
    reportError(deviceId, err);
  } finally {
    inFlight.delete(deviceId);
    const next = queued.get(deviceId);
    if (next) {
      queued.delete(deviceId);
      void run(deviceId, next);
    }
  }
}

function deviceName(id: string): string {
  return useDevices.getState().devices[id]?.device.name ?? id;
}

export function reportOutcome(result: CommandResult): void {
  if (result.outcome === "unconfirmed" || result.outcome === "mismatch") {
    notify("warning", deviceName(result.device_id), OUTCOME_TEXT[result.outcome]);
  }
}

export function reportError(deviceId: string | null, err: unknown): void {
  const title = deviceId ? deviceName(deviceId) : "Σφάλμα";
  if (err instanceof ApiError) notify("error", title, err.message);
  else notify("error", title, String(err));
}
