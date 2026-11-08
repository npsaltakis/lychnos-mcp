import { ChevronRight, Cpu, DatabaseBackup, MapPin, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { BackupFile, SystemInfo } from "../api/types";
import { NotificationsCard } from "../components/NotificationsCard";
import { VacationCard } from "../components/VacationCard";
import { PresenceCard } from "../components/PresenceCard";
import { ScaleCard } from "../components/ScaleCard";
import { Button, Card, LanguageSwitch, PageHeader } from "../components/ui";
import { reportError } from "../lib/commands";
import { t, useLang, useT } from "../lib/i18n";
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
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ name: string; region: string; latitude: number; longitude: number }[] | null>(null);
  const [searching, setSearching] = useState(false);
  const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

  const search = async () => {
    if (query.trim().length < 2) return;
    setSearching(true);
    try {
      const found = await api.places(query.trim(), useLang.getState().lang);
      if (found.length === 1) await choose(found[0]);
      else setResults(found);
    } catch (err) {
      reportError(null, err);
    } finally {
      setSearching(false);
    }
  };

  const choose = async (p: { name: string; latitude: number; longitude: number }) => {
    setLat(String(p.latitude));
    setLon(String(p.longitude));
    setLabel(p.name);
    setResults(null);
    setQuery("");
    setSaving(true);
    try {
      await api.setLocation({ latitude: p.latitude, longitude: p.longitude, label: p.name });
      notify("success", t("loc.saved"), p.name);
    } catch (err) {
      reportError(null, err);
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    api
      .location()
      .then((loc) => {
        if (!loc || (loc.latitude === 0 && loc.longitude === 0)) return;
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
      {label && lat && <p className="mb-3 text-sm">{tr("loc.current", { name: label })}</p>}
      <form
        className="mb-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <input
          className={input}
          placeholder={tr("loc.searchPlaceholder")}
          aria-label={tr("loc.search")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" variant="primary" busy={searching}>
          <Search className="size-4" aria-hidden /> {tr("loc.search")}
        </Button>
      </form>
      {results && results.length > 1 && <p className="mb-2 text-sm text-accent">{tr("loc.pick")}</p>}
      {results && (
        <ul className="mb-3 divide-y divide-line rounded-xl border border-line">
          {results.length === 0 && <li className="p-3 text-sm text-muted">{tr("loc.noResults")}</li>}
          {results.map((p) => (
            <li key={`${p.latitude},${p.longitude}`}>
              <button
                type="button"
                onClick={() => void choose(p)}
                className="flex min-h-11 w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-panel-2"
              >
                <span>
                  <span className="font-medium">{p.name}</span>
                  {p.region && <span className="text-muted"> · {p.region}</span>}
                </span>
                <MapPin className="size-4 flex-none text-muted" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted">{tr("loc.manual")}</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
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
      </details>
    </Card>
  );
}

function BackupCard() {
  const tr = useT();
  const [data, setData] = useState<{ folder: string; backups: BackupFile[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.backups().then(setData).catch((err) => reportError(null, err));
  useEffect(() => {
    void load();
  }, []);
  const latest = data?.backups[0];
  const now = async () => {
    setBusy(true);
    try {
      await api.createBackup();
      notify("success", t("bak.done"));
      await load();
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <h2 className="mb-1 flex items-center gap-2 font-semibold">
        <DatabaseBackup className="size-5 text-muted" aria-hidden /> {tr("bak.title")}
      </h2>
      <p className="mb-2 text-sm text-muted">{tr("bak.text")}</p>
      <Row label={tr("bak.latest")}>{latest ? new Date(latest.created_at).toLocaleString() : "—"}</Row>
      <Row label={tr("bak.count")}>{data ? data.backups.length : "—"}</Row>
      {data && <p className="mt-2 break-all text-xs text-muted">{data.folder}</p>}
      <Button className="mt-3 border border-line" variant="ghost" busy={busy} onClick={now}>
        {tr("bak.now")}
      </Button>
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

        <LocationCard />

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

        <BackupCard />

        <NotificationsCard />

        <PresenceCard />

        <ScaleCard />

        <VacationCard />

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
