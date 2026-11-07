import { Power, PowerOff } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { BatchResult, Command } from "../api/types";
import { PRESETS, rgbToHex } from "../lib/color";
import { reportError } from "../lib/commands";
import { t, useT } from "../lib/i18n";
import { notify } from "../store/toasts";
import { Button, Card, swatchClass } from "./ui";

export function summarizeBatch(title: string, result: BatchResult): void {
  const confirmed = result.results.filter((r) => r.outcome === "confirmed").length;
  const failed = result.results.filter((r) => r.outcome === "failed");
  const total = result.results.length;
  if (result.outcome === "nothing_to_do") {
    notify("info", title, t("batch.nothing"));
  } else if (result.outcome === "all_confirmed") {
    notify("success", title, t("batch.allOk", { ok: confirmed, total }));
  } else if (result.outcome === "failed") {
    notify("error", title, failed[0]?.error?.message ?? t("batch.failed"));
  } else {
    notify("warning", title, t("batch.partial", { ok: confirmed, total, failed: failed.length }));
  }
}

export function AllLights({ count, onCount }: { count: number; onCount: number }) {
  const tr = useT();
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (key: string, title: string, command: Command) => {
    setBusy(key);
    try {
      summarizeBatch(title, await api.groupCommand(command));
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-semibold">{tr("all.title")}</h2>
          <p className="text-sm text-muted">{tr("all.onCount", { on: onCount, total: count })}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={busy === "on"}
            disabled={!count || busy !== null}
            onClick={() => run("on", tr("all.on"), { type: "set_power", on: true })}
          >
            <Power className="size-4" aria-hidden /> {tr("all.on")}
          </Button>
          <Button
            busy={busy === "off"}
            disabled={!count || busy !== null}
            onClick={() => run("off", tr("all.off"), { type: "set_power", on: false })}
          >
            <PowerOff className="size-4" aria-hidden /> {tr("all.off")}
          </Button>
        </div>
      </div>
      <div role="group" aria-label={tr("all.colorAll")} className="mt-4 flex flex-wrap items-center gap-2">
        <span className="mr-1 w-full text-sm text-muted sm:w-auto" aria-hidden>{tr("all.colorAll")}</span>
        {PRESETS.map((p) => (
          <button
            key={p.key}
            title={tr(p.key)}
            aria-label={tr("all.colorAllAria", { color: tr(p.key) })}
            disabled={busy !== null || !count}
            onClick={() => run(`c-${p.key}`, tr("all.colorAllAria", { color: tr(p.key) }), { type: "set_color", color: p.rgb })}
            className={swatchClass}
            style={{ background: rgbToHex(p.rgb) }}
          />
        ))}
      </div>
    </Card>
  );
}
