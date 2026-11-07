import { Check, Pencil, RefreshCw, Search, Trash2, TriangleAlert, Wifi, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Capability, DeviceView } from "../api/types";
import { Button, Card, Empty, PageHeader, StatusDot, cx } from "../components/ui";
import { reportError } from "../lib/commands";
import { sortedViews, useDevices } from "../store/devices";
import { notify } from "../store/toasts";

const CAP_LABEL: Record<Capability, string> = {
  power: "ON/OFF",
  brightness: "Φωτεινότητα",
  color_rgb: "RGB",
  color_temp: "Θερμοκρασία",
};

function DeviceRow({ view }: { view: DeviceView }) {
  const { device, runtime } = view;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(device.name);
  const [busy, setBusy] = useState<string | null>(null);
  const setup =
    device.metadata.has_devicekey === false
      ? "Λείπει το κλειδί eWeLink: τρέξε το setup (δες Ρυθμίσεις)"
      : device.metadata.setup_required
        ? "Αυτός ο τύπος συσκευής δεν υποστηρίζεται ακόμα"
        : undefined;
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
              <input
                autoFocus
                value={name}
                maxLength={64}
                onChange={(e) => setName(e.target.value)}
                aria-label="Όνομα συσκευής"
                className="min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none"
              />
              <Button type="submit" variant="primary" busy={busy === "name"} aria-label="Αποθήκευση">
                <Check className="size-4" />
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(false)} aria-label="Ακύρωση">
                <X className="size-4" />
              </Button>
            </form>
          ) : (
            <div className="flex items-center gap-2">
              <h3 className="truncate font-semibold">{device.name}</h3>
              <button onClick={() => setEditing(true)} className="text-muted hover:text-white" aria-label="Μετονομασία">
                <Pencil className="size-4" />
              </button>
            </div>
          )}
          <p className="mt-1 text-xs text-muted">
            {device.manufacturer} {device.model ?? ""} · {device.address ?? "χωρίς IP"} · {device.provider}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusDot availability={runtime.availability} />
            {device.capabilities.supported.map((c) => (
              <span key={c} className="rounded-md bg-panel-2 px-2 py-0.5 text-xs text-gray-300">
                {CAP_LABEL[c]}
                {c === "color_temp" && ct ? ` ${ct.min_k}–${ct.max_k}K` : ""}
              </span>
            ))}
            {device.capabilities.supports_push && (
              <span className="rounded-md bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300">Push ενημερώσεις</span>
            )}
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
            Έλεγχος από το dashboard
          </label>
          <Button
            variant="ghost"
            title="Ανάγνωση κατάστασης από τη συσκευή"
            aria-label="Ανανέωση κατάστασης"
            disabled={!device.registered || !device.capabilities.supports_state_query}
            busy={busy === "refresh"}
            onClick={() => act("refresh", () => api.refreshState(device.id))}
          >
            <RefreshCw className="size-4" />
          </Button>
          <Button
            variant="danger"
            aria-label="Διαγραφή"
            busy={busy === "del"}
            onClick={() => {
              if (confirm(`Να αφαιρεθεί η «${device.name}» από τη λίστα; (Θα ξαναβρεθεί με νέα αναζήτηση.)`))
                act("del", () => api.deleteDevice(device.id));
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
  const devices = useDevices((s) => s.devices);
  const [scanning, setScanning] = useState(false);
  const views = sortedViews(devices);

  const scan = async () => {
    setScanning(true);
    try {
      const found = await api.scan(4);
      notify("success", "Αναζήτηση ολοκληρώθηκε", `Βρέθηκαν ${found.length} συσκευές`);
    } catch (err) {
      reportError(null, err);
    } finally {
      setScanning(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Συσκευές"
        subtitle="Μόνο οι καταχωρημένες συσκευές δέχονται εντολές από το dashboard και το Claude."
        actions={
          <Button variant="primary" busy={scanning} onClick={scan}>
            <Search className="size-4" aria-hidden /> Αναζήτηση στο δίκτυο
          </Button>
        }
      />
      {views.length === 0 ? (
        <Empty icon={<Wifi className="size-8" />} title="Καμία συσκευή ακόμα">
          Πάτα «Αναζήτηση στο δίκτυο». Η Govee χρειάζεται ενεργό «LAN Control» στην εφαρμογή Govee Home.
        </Empty>
      ) : (
        <div className={cx("space-y-3", scanning && "opacity-70")}>
          {views.map((v) => (
            <DeviceRow key={v.device.id} view={v} />
          ))}
        </div>
      )}
    </>
  );
}
