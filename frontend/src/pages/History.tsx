import { Download, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ActivityEntry } from "../api/types";
import { Button, Card, Empty, PageHeader, cx } from "../components/ui";
import { useT, type MessageKey } from "../lib/i18n";
import { changeLabel, sourceLabel } from "../lib/labels";
import { formatDateTime } from "../lib/time";
import { sortedViews, useDevices } from "../store/devices";

type Kind = "all" | "commands" | "changes" | "availability";
const KINDS: Kind[] = ["all", "commands", "changes", "availability"];
const LIMIT = 500;

const OUTCOME: Record<string, { key: MessageKey; cls: string }> = {
  confirmed: { key: "outcome.confirmed", cls: "text-emerald-400" },
  unconfirmed: { key: "outcome.unconfirmed", cls: "text-amber-300" },
  mismatch: { key: "outcome.mismatch", cls: "text-amber-300" },
  failed: { key: "outcome.failed", cls: "text-red-400" },
  observed: { key: "log.observed", cls: "text-sky-300" },
  offline: { key: "log.offline", cls: "text-red-400" },
  online: { key: "log.online", cls: "text-emerald-400" },
};

function csvCell(value: string): string {
  return /[",\n;]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function History() {
  const t = useT();
  const version = useDevices((s) => s.activityVersion);
  const devices = sortedViews(useDevices((s) => s.devices));
  const [kind, setKind] = useState<Kind>("all");
  const [deviceId, setDeviceId] = useState("");
  const [entries, setEntries] = useState<ActivityEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ limit: String(LIMIT) });
    if (kind !== "all") params.set("kind", kind);
    if (deviceId) params.set("device_id", deviceId);
    api
      .activityQuery(params.toString())
      .then((data) => !cancelled && setEntries(data))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [kind, deviceId, version]);

  const exportCsv = () => {
    const header = [
      t("log.when"),
      t("log.device"),
      t("log.change"),
      t("log.source"),
      t("log.result"),
    ];
    const lines = entries.map((e) =>
      [
        new Date(e.ts).toISOString(),
        e.device_name ?? "",
        changeLabel(e.command),
        sourceLabel(e.source),
        OUTCOME[e.outcome] ? t(OUTCOME[e.outcome].key) : e.outcome,
      ]
        .map(csvCell)
        .join(","),
    );
    // BOM so Excel opens Greek text correctly
    const blob = new Blob(
      ["﻿" + [header.map(csvCell).join(","), ...lines].join("\r\n")],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `lychnos-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const select =
    "min-h-10 rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

  return (
    <>
      <PageHeader
        title={t("log.title")}
        subtitle={t("log.subtitle")}
        actions={
          <Button onClick={exportCsv} disabled={!entries.length}>
            <Download className="size-4" aria-hidden /> {t("log.export")}
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div
          role="group"
          aria-label={t("log.change")}
          className="-mx-4 flex max-w-[calc(100%+2rem)] gap-1 overflow-x-auto px-4 text-sm [scrollbar-width:none] sm:mx-0 sm:max-w-full sm:rounded-xl sm:bg-panel-2 sm:p-1 sm:px-1"
        >
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={kind === k}
              onClick={() => setKind(k)}
              className={cx(
                "min-h-10 shrink-0 whitespace-nowrap rounded-lg px-3 transition-colors",
                kind === k
                  ? "bg-line font-medium text-white"
                  : "bg-panel-2 text-muted hover:text-white sm:bg-transparent",
              )}
            >
              {t(`log.kind.${k}` as MessageKey)}
            </button>
          ))}
        </div>
        <select
          id="log-device"
          className={select}
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
          aria-label={t("log.device")}
        >
          <option value="">{t("log.allDevices")}</option>
          {devices.map((v) => (
            <option key={v.device.id} value={v.device.id}>
              {v.device.name}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted">
          {t("log.shown", { n: entries.length })}
        </span>
      </div>

      {entries.length === 0 ? (
        <Empty
          icon={<ScrollText className="size-8" />}
          title={t("log.emptyTitle")}
        >
          {t("log.emptyText")}
        </Empty>
      ) : (
        <>
          {/* Phones: one card per entry instead of a sideways-scrolling table */}
          <ul className="space-y-2 sm:hidden">
            {entries.map((e) => {
              const o = OUTCOME[e.outcome];
              return (
                <li
                  key={e.id}
                  className="rounded-xl border border-line bg-panel p-3 text-sm"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium">
                      {changeLabel(e.command)}
                    </span>
                    <span
                      className={cx("shrink-0 text-xs", o?.cls ?? "text-muted")}
                    >
                      {o ? t(o.key) : e.outcome}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-muted">{e.device_name}</p>
                  <p className="mt-1 text-xs text-muted">
                    <span className="tabular-nums">{formatDateTime(e.ts)}</span>{" "}
                    · {sourceLabel(e.source)}
                  </p>
                </li>
              );
            })}
          </ul>
          <Card className="hidden p-0 sm:block sm:p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
                    <th className="px-4 py-3 font-medium">{t("log.when")}</th>
                    <th className="px-4 py-3 font-medium">{t("log.device")}</th>
                    <th className="px-4 py-3 font-medium">{t("log.change")}</th>
                    <th className="px-4 py-3 font-medium">{t("log.source")}</th>
                    <th className="px-4 py-3 font-medium">{t("log.result")}</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => {
                    const o = OUTCOME[e.outcome];
                    return (
                      <tr
                        key={e.id}
                        className="border-b border-line last:border-0"
                      >
                        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-muted">
                          {formatDateTime(e.ts)}
                        </td>
                        <td className="px-4 py-2.5">{e.device_name}</td>
                        <td className="whitespace-nowrap px-4 py-2.5">
                          {changeLabel(e.command)}
                        </td>
                        <td className="px-4 py-2.5 text-muted">
                          {sourceLabel(e.source)}
                        </td>
                        <td
                          className={cx(
                            "whitespace-nowrap px-4 py-2.5",
                            o?.cls ?? "text-muted",
                          )}
                          title={e.detail ?? undefined}
                        >
                          {o ? t(o.key) : e.outcome}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
