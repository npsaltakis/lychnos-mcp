import { MapPin } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SystemInfo } from "../api/types";
import { NotificationsCard } from "../components/NotificationsCard";
import { Button, Card, PageHeader } from "../components/ui";
import { reportError } from "../lib/commands";
import { SECRET_STORE, VENV_PY } from "../lib/platform";
import { useDevices } from "../store/devices";
import { notify } from "../store/toasts";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 border-b border-line py-3 text-sm last:border-0">
      <span className="text-muted">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

function LocationCard() {
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

  useEffect(() => {
    api
      .location()
      .then((loc) => {
        if (!loc) return;
        setLat(String(loc.latitude));
        setLon(String(loc.longitude));
        setLabel(loc.label);
      })
      .catch(() => undefined);
  }, []);

  const useBrowser = () => {
    if (!navigator.geolocation) return notify("warning", "Ο browser δεν δίνει τοποθεσία");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(pos.coords.latitude.toFixed(4));
        setLon(pos.coords.longitude.toFixed(4));
      },
      () => notify("warning", "Δεν δόθηκε τοποθεσία", "Γράψε τις συντεταγμένες με το χέρι (π.χ. από Google Maps)."),
    );
  };

  const save = async () => {
    const latitude = Number(lat.replace(",", "."));
    const longitude = Number(lon.replace(",", "."));
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180)
      return notify("warning", "Μη έγκυρες συντεταγμένες", "Πλάτος −90…90, μήκος −180…180.");
    setSaving(true);
    try {
      await api.setLocation({ latitude, longitude, label: label.trim() });
      notify("success", "Η τοποθεσία αποθηκεύτηκε", "Οι αυτοματισμοί ανατολής/δύσης θα τη χρησιμοποιούν.");
    } catch (err) {
      reportError(null, err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="lg:col-span-2">
      <h2 className="mb-1 font-semibold">Τοποθεσία για ανατολή/δύση</h2>
      <p className="mb-3 text-sm text-muted">
        Χρειάζεται μόνο για αυτοματισμούς με ανατολή ή δύση ηλίου. Αρκεί η πόλη, όχι ακριβής διεύθυνση. Αποθηκεύεται
        τοπικά.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="mb-1 block text-muted">Γεωγραφικό πλάτος</span>
          <input id="loc-lat" className={input} inputMode="decimal" placeholder="37.98" value={lat} onChange={(e) => setLat(e.target.value)} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">Γεωγραφικό μήκος</span>
          <input id="loc-lon" className={input} inputMode="decimal" placeholder="23.73" value={lon} onChange={(e) => setLon(e.target.value)} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">Όνομα (προαιρετικό)</span>
          <input id="loc-label" className={input} maxLength={80} placeholder="Αθήνα" value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" busy={saving} onClick={save}>
          Αποθήκευση
        </Button>
        <Button variant="ghost" className="border border-line" onClick={useBrowser}>
          <MapPin className="size-4" aria-hidden /> Από τον browser
        </Button>
      </div>
    </Card>
  );
}

export function Settings() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const connection = useDevices((s) => s.connection);

  useEffect(() => {
    api.systemInfo().then(setInfo).catch((err) => reportError(null, err));
  }, []);

  return (
    <>
      <PageHeader title="Ρυθμίσεις" subtitle="Κατάσταση συστήματος και πληροφορίες" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-2 font-semibold">Σύστημα</h2>
          <Row label="Έκδοση backend">{info?.version ?? "—"}</Row>
          <Row label="Ζωντανή σύνδεση">{connection === "online" ? "Ενεργή" : connection === "connecting" ? "Σύνδεση…" : "Ανενεργή"}</Row>
          <Row label="Συσκευές">
            {info ? `${info.devices_registered} καταχωρημένες / ${info.devices_total} συνολικά` : "—"}
          </Row>
          <Row label="Ανανέωση κατάστασης (Govee)">{info ? `κάθε ${info.poll_interval_s} s, μόνο όταν είναι ανοιχτό το dashboard` : "—"}</Row>
          <Row label="Απομακρυσμένη πρόσβαση">{info?.remote_access ? "Ενεργή" : "Μόνο από αυτό το PC"}</Row>
        </Card>

        <Card>
          <h2 className="mb-2 font-semibold">Ενσωματώσεις</h2>
          {info?.providers.length ? (
            info.providers.map((p) => (
              <Row key={p.key} label={p.manufacturer}>
                <code className="text-xs text-muted">{p.key}</code>
              </Row>
            ))
          ) : (
            <p className="py-3 text-sm text-muted">
              Δεν έχουν φορτωθεί ενσωματώσεις. Όρισε <code>SHC_ENABLED_PROVIDERS</code> στο <code>backend/.env</code>.
            </p>
          )}
        </Card>

        <NotificationsCard />

        <LocationCard />

        <Card className="lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold">Ιστορικό εντολών</h2>
            <Link to="/history" className="text-sm text-accent underline">
              Άνοιγμα ιστορικού
            </Link>
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <h2 className="mb-2 font-semibold">Ασφάλεια</h2>
          <p className="text-sm text-muted">
            Αλλαγή κωδικού (λήγουν και όλες οι ενεργές συνδέσεις) ή αποσύνδεση όλων των συσκευών, από τον φάκελο{" "}
            <code>backend</code>:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-panel-2 p-3 text-xs">
            {`${VENV_PY} -m app.auth_cli set-password\n${VENV_PY} -m app.auth_cli logout-all`}
          </pre>
        </Card>

        <Card className="lg:col-span-2">
          <h2 className="mb-2 font-semibold">Sonoff: κλειδιά eWeLink</h2>
          <p className="text-sm text-muted">
            Οι εντολές LAN της Sonoff κρυπτογραφούνται με κλειδί που δίνει μία φορά το eWeLink. Τρέξε από τον φάκελο{" "}
            <code>backend</code>:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-panel-2 p-3 text-xs">
            {`${VENV_PY} -m app.integrations.ewelink_setup`}
          </pre>
          <p className="mt-3 text-sm text-muted">
            Τα κλειδιά αποθηκεύονται κρυπτογραφημένα στο {SECRET_STORE}, όχι σε αρχείο <code>.env</code>. Τα
            δεδομένα και τα logs βρίσκονται στο <code>%USERPROFILE%\.smarthome-control</code> (Windows) ή{" "}
            <code>~/.local/share/SmartHomeControl</code> (Linux).
          </p>
        </Card>
      </div>
    </>
  );
}
