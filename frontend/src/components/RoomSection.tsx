import { Power, PowerOff } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Command, DeviceView, Room } from "../api/types";
import { reportError } from "../lib/commands";
import { useT } from "../lib/i18n";
import { summarizeBatch } from "./AllLights";
import { DeviceCard } from "./DeviceCard";
import { Button } from "./ui";

/** A room heading with room-wide controls, followed by the room's device cards. */
export function RoomSection({ room, views }: { room: Room | null; views: DeviceView[] }) {
  const t = useT();
  const [busy, setBusy] = useState<string | null>(null);
  const on = views.filter((v) => v.runtime.state.power === true).length;
  const title = room?.name ?? t("room.none");

  const run = async (key: string, label: string, command: Command) => {
    if (!room) return;
    setBusy(key);
    try {
      summarizeBatch(`${room.name}: ${label}`, await api.roomCommand(room.id, command));
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby={`room-${room?.id ?? "none"}`} className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id={`room-${room?.id ?? "none"}`} className="text-lg font-semibold">
            {title}
          </h2>
          <p className="text-sm text-muted">{t("all.onCount", { on, total: views.length })}</p>
        </div>
        {room && (
          <div className="flex gap-2">
            <Button busy={busy === "on"} disabled={busy !== null} onClick={() => run("on", "ON", { type: "set_power", on: true })}>
              <Power className="size-4" aria-hidden /> ON
            </Button>
            <Button busy={busy === "off"} disabled={busy !== null} onClick={() => run("off", "OFF", { type: "set_power", on: false })}>
              <PowerOff className="size-4" aria-hidden /> OFF
            </Button>
          </div>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {views.map((view) => (
          <DeviceCard key={view.device.id} view={view} />
        ))}
      </div>
    </section>
  );
}
