import { Check, DoorOpen, Pencil, Plus, RefreshCw, Search, Trash2, TriangleAlert, Wifi, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { DeviceView, Room } from "../api/types";
import { Button, Card, Empty, PageHeader, StatusDot, cx } from "../components/ui";
import { useRooms } from "../hooks/useData";
import { reportError } from "../lib/commands";
import { t, useT, type MessageKey } from "../lib/i18n";
import { sortedViews, useDevices } from "../store/devices";
import { notify } from "../store/toasts";

const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

function RoomsManager({ rooms, views }: { rooms: Room[]; views: DeviceView[] }) {
  const tr = useT();
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (key: string, fn: () => Promise<unknown>, done?: () => void) => {
    setBusy(key);
    try {
      await fn();
      done?.();
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <div className="mb-3 flex items-center gap-2">
        <DoorOpen className="size-5 text-accent" aria-hidden />
        <h2 className="font-semibold">{tr("dev.rooms")}</h2>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const name = newName.trim();
          if (name) act("new", () => api.createRoom(name), () => setNewName(""));
        }}
      >
        <input
          id="new-room"
          className={input}
          placeholder={tr("dev.newRoom")}
          maxLength={40}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          aria-label={tr("dev.newRoomAria")}
        />
        <Button type="submit" variant="primary" busy={busy === "new"} disabled={!newName.trim()}>
          <Plus className="size-4" aria-hidden /> {tr("common.add")}
        </Button>
      </form>
      {rooms.length > 0 && (
        <ul className="mt-3 divide-y divide-line">
          {rooms.map((room) => {
            const count = views.filter((v) => v.device.room_id === room.id).length;
            return (
              <li key={room.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                {editing === room.id ? (
                  <form
                    className="flex flex-1 gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (editName.trim()) act(room.id, () => api.updateRoom(room.id, editName.trim(), room.icon), () => setEditing(null));
                    }}
                  >
                    <input autoFocus className={input} value={editName} maxLength={40} onChange={(e) => setEditName(e.target.value)} aria-label={tr("dev.renameRoomAria")} />
                    <Button type="submit" variant="primary" busy={busy === room.id} aria-label={tr("common.save")}>
                      <Check className="size-4" />
                    </Button>
                    <Button type="button" variant="ghost" onClick={() => setEditing(null)} aria-label={tr("common.cancel")}>
                      <X className="size-4" />
                    </Button>
                  </form>
                ) : (
                  <>
                    <span>
                      <span className="font-medium">{room.name}</span>
                      <span className="ml-2 text-sm text-muted">{count === 1 ? tr("dev.deviceCount1") : tr("dev.deviceCount", { n: count })}</span>
                    </span>
                    <span className="flex gap-1">
                      <Button
                        variant="ghost"
                        aria-label={`${tr("common.rename")} ${room.name}`}
                        onClick={() => {
                          setEditing(room.id);
                          setEditName(room.name);
                        }}
                      >
                        <Pencil className="size-4" />
                      </Button>
                      <Button
                        variant="danger"
                        aria-label={`${tr("common.delete")} ${room.name}`}
                        busy={busy === `del-${room.id}`}
                        onClick={() => {
                          if (confirm(tr("dev.confirmDeleteRoom", { name: room.name }))) act(`del-${room.id}`, () => api.deleteRoom(room.id));
                        }}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function DeviceRow({ view, rooms }: { view: DeviceView; rooms: Room[] }) {
  const tr = useT();
  const { device, runtime } = view;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(device.name);
  const [busy, setBusy] = useState<string | null>(null);
  const setup =
    device.metadata.has_devicekey === false ? tr("dev.missingKey") : device.metadata.setup_required ? tr("dev.unsupported") : undefined;
  const ct = device.capabilities.color_temp_range;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
    } catch (err) {
      reportError(device.id, err);
    } finally {
      setBusy(null);
    }
  };

  const save = () =>
    act("name", async () => {
      await api.updateDevice(device.id, { name: name.trim() });
      setEditing(false);
    });

  return (
    <Card>
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) save();
              }}
            >
              <input autoFocus value={name} maxLength={64} onChange={(e) => setName(e.target.value)} aria-label={tr("dev.nameAria")} className={input} />
              <Button type="submit" variant="primary" busy={busy === "name"} aria-label={tr("common.save")}>
                <Check className="size-4" />
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(false)} aria-label={tr("common.cancel")}>
                <X className="size-4" />
              </Button>
            </form>
          ) : (
            <div className="flex items-center gap-2">
              <h3 className="truncate font-semibold">{device.name}</h3>
              <button
                onClick={() => setEditing(true)}
                className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-panel-2 hover:text-white"
                aria-label={`${tr("common.rename")} ${device.name}`}
              >
                <Pencil className="size-4" />
              </button>
            </div>
          )}
          <p className="mt-1 text-xs text-muted">
            {device.manufacturer} {device.model ?? ""} · {device.address ?? tr("dev.noIp")} · {device.provider}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusDot availability={runtime.availability} />
            {device.capabilities.supported.map((c) => (
              <span key={c} className="rounded-md bg-panel-2 px-2 py-0.5 text-xs text-gray-300">
                {tr(`dev.cap.${c}` as MessageKey)}
                {c === "color_temp" && ct ? ` ${ct.min_k}–${ct.max_k}K` : ""}
              </span>
            ))}
            {device.capabilities.supports_push && <span className="rounded-md bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300">{tr("dev.push")}</span>}
          </div>
          {setup && (
            <p className="mt-2 inline-flex items-center gap-1.5 text-sm text-amber-300">
              <TriangleAlert className="size-4" aria-hidden /> {setup}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-xl border border-line px-3 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-amber-400"
              checked={device.registered}
              disabled={busy !== null}
              onChange={(e) => act("reg", () => api.updateDevice(device.id, { registered: e.target.checked }))}
            />
            {tr("dev.registered")}
          </label>
          <select
            aria-label={tr("dev.roomFor", { name: device.name })}
            value={device.room_id ?? ""}
            disabled={busy !== null}
            onChange={(e) => act("room", () => api.setDeviceRoom(device.id, e.target.value || null))}
            className="min-h-10 rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none"
          >
            <option value="">{tr("room.none")}</option>
            {rooms.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <Button
            variant="ghost"
            title={tr("dev.refreshTitle")}
            aria-label={tr("dev.refreshAria")}
            disabled={!device.registered || !device.capabilities.supports_state_query}
            busy={busy === "refresh"}
            onClick={() => act("refresh", () => api.refreshState(device.id))}
          >
            <RefreshCw className="size-4" />
          </Button>
          <Button
            variant="danger"
            aria-label={tr("common.delete")}
            busy={busy === "del"}
            onClick={() => {
              if (confirm(tr("dev.confirmDelete", { name: device.name }))) act("del", () => api.deleteDevice(device.id));
            }}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </div>
    </Card>
  );
}

export function Devices() {
  const tr = useT();
  const devices = useDevices((s) => s.devices);
  const rooms = useRooms();
  const [scanning, setScanning] = useState(false);
  const views = sortedViews(devices);

  const scan = async () => {
    setScanning(true);
    try {
      const found = await api.scan(4);
      notify("success", t("dev.scanDone"), t("dev.found", { n: found.length }));
    } catch (err) {
      reportError(null, err);
    } finally {
      setScanning(false);
    }
  };

  return (
    <>
      <PageHeader
        title={tr("dev.title")}
        subtitle={tr("dev.subtitle")}
        actions={
          <Button variant="primary" busy={scanning} onClick={scan}>
            <Search className="size-4" aria-hidden /> {tr("dev.scan")}
          </Button>
        }
      />
      {views.length === 0 ? (
        <Empty icon={<Wifi className="size-8" />} title={tr("dev.emptyTitle")}>
          {tr("dev.emptyText")}
        </Empty>
      ) : (
        <div className="space-y-6">
          <RoomsManager rooms={rooms} views={views} />
          <div className={cx("space-y-3", scanning && "opacity-70")}>
            {views.map((v) => (
              <DeviceRow key={v.device.id} view={v} rooms={rooms} />
            ))}
          </div>
        </div>
      )}
    </>
  );
}
