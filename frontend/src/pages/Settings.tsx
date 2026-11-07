import { ChevronRight, Cpu, MapPin } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SystemInfo } from "../api/types";
import { NotificationsCard } from "../components/NotificationsCard";
import { Button, Card, LanguageSwitch, PageHeader } from "../components/ui";
import { reportError } from "../lib/commands";
import { t, useT } from "../lib/i18n";
import { secretStoreName, VENV_PY } from "../lib/platform";
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
  const tr = useT();
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
    if (!navigator.geolocation) return notify("warning", t("loc.noBrowser"));
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(pos.coords.latitude.toFixed(4));
        setLon(pos.coords.longitude.toFixed(4));
      },
      () => notify("warning", t("loc.denied"), t("loc.deniedText")),
    );
  };

  const save = async () => {
    const latitude = Number(lat.replace(",", "."));
    const longitude = Number(lon.replace(",", "."));
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180)
      return notify("warning", t("loc.invalid"), t("loc.invalidText"));
    setSaving(true);
    try {
      await api.setLocation({ latitude, longitude, label: label.trim() });
      notify("success", t("loc.saved"), t("loc.savedText"));
    } catch (err) {
      reportError(null, err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="lg:col-span-2">
      <h2 className="mb-1 font-semibold">{tr("loc.title")}</h2>
      <p className="mb-3 text-sm text-muted">{tr("loc.text")}</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("loc.lat")}</span>
          <input id="loc-lat" className={input} inputMode="decimal" placeholder="37.98" value={lat} onChange={(e) => setLat(e.target.value)} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("loc.lon")}</span>
          <input id="loc-lon" className={input} inputMode="decimal" placeholder="23.73" value={lon} onChange={(e) => setLon(e.target.value)} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-muted">{tr("loc.label")}</span>
          <input id="loc-label" className={input} maxLength={80} placeholder={tr("loc.labelPlaceholder")} value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" busy={saving} onClick={save}>
          {tr("common.save")}
        </Button>
        <Button variant="ghost" className="border border-line" onClick={useBrowser}>
          <MapPin className="size-4" aria-hidden /> {tr("loc.fromBrowser")}
        </Button>
      </div>
    </Card>
  );
}

export function Settings() {
  const tr = useT();
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const connection = useDevices((s) => s.connection);

  useEffect(() => {
    api.systemInfo().then(setInfo).catch((err) => reportError(null, err));
  }, []);

  return (
    <>
      <PageHeader title={tr("set.title")} subtitle={tr("set.subtitle")} />
      <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        {/* Devices is not in the phone's bottom bar */}
        <Link
          to="/devices"
          className="flex items-center gap-3 rounded-2xl border border-line bg-panel p-4 transition-colors hover:border-gray-500 md:hidden"
        >
          <Cpu className="size-5 text-accent" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="block font-semibold">{tr("set.devicesLink")}</span>
            <span className="block text-sm text-muted">{tr("set.devicesLinkHint")}</span>
          </span>
          <ChevronRight className="size-5 text-muted" aria-hidden />
        </Link>

        <Card>
          <h2 className="mb-2 font-semibold">{tr("set.system")}</h2>
          <Row label={tr("set.version")}>{info?.version ?? "—"}</Row>
          <Row label={tr("set.live")}>
            {connection === "online" ? tr("set.liveOn") : connection === "connecting" ? tr("set.liveConnecting") : tr("set.liveOff")}
          </Row>
          <Row label={tr("set.devices")}>
            {info ? tr("set.devicesValue", { reg: info.devices_registered, total: info.devices_total }) : "—"}
          </Row>
          <Row label={tr("set.refresh")}>{info ? tr("set.refreshValue", { s: info.poll_interval_s }) : "—"}</Row>
          <Row label={tr("set.remote")}>{info?.remote_access ? tr("set.remoteOn") : tr("set.remoteOff")}</Row>
        </Card>

        <Card>
          <h2 className="mb-2 font-semibold">{tr("set.integrations")}</h2>
          {info?.providers.length ? (
            info.providers.map((p) => (
              <Row key={p.key} label={p.manufacturer}>
                <code className="text-xs text-muted">{p.key}</code>
              </Row>
            ))
          ) : (
            <p className="py-3 text-sm text-muted">{tr("set.noIntegrations")}</p>
          )}
        </Card>

        <Card>
          <h2 className="mb-1 font-semibold">{tr("set.language")}</h2>
          <p className="mb-3 text-sm text-muted">{tr("set.languageHint")}</p>
          <LanguageSwitch />
        </Card>

        <Card>
          <h2 className="mb-1 font-semibold">{tr("set.logTitle")}</h2>
          <p className="mb-3 text-sm text-muted">{tr("log.subtitle")}</p>
          <Link to="/log" className="text-sm text-accent underline">
            {tr("set.logOpen")}
          </Link>
        </Card>

        <NotificationsCard />

        <LocationCard />

        <Card className="lg:col-span-2">
          <h2 className="mb-2 font-semibold">{tr("set.security")}</h2>
          <p className="text-sm text-muted">{tr("set.securityText")}</p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-panel-2 p-3 text-xs">
            {`${VENV_PY} -m app.auth_cli set-password\n${VENV_PY} -m app.auth_cli logout-all`}
          </pre>
        </Card>

        <Card className="lg:col-span-2">
          <h2 className="mb-2 font-semibold">{tr("set.sonoff")}</h2>
          <p className="text-sm text-muted">{tr("set.sonoffText")}</p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-panel-2 p-3 text-xs">{`${VENV_PY} -m app.integrations.ewelink_setup`}</pre>
          <p className="mt-3 text-sm text-muted">{tr("set.sonoffStorage", { store: secretStoreName() })}</p>
        </Card>
      </div>
    </>
  );
}
