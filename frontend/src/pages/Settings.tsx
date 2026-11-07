import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SystemInfo } from "../api/types";
import { Card, PageHeader } from "../components/ui";
import { reportError } from "../lib/commands";
import { SECRET_STORE, VENV_PY } from "../lib/platform";
import { useDevices } from "../store/devices";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 border-b border-line py-3 text-sm last:border-0">
      <span className="text-muted">{label}</span>
      <span className="text-right">{children}</span>
    </div>
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
