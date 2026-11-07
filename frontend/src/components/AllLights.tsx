import { Power, PowerOff } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { BatchResult, Command } from "../api/types";
import { PRESETS, rgbToHex } from "../lib/color";
import { reportError } from "../lib/commands";
import { notify } from "../store/toasts";
import { Button, Card } from "./ui";

export function summarizeBatch(title: string, result: BatchResult): void {
  const confirmed = result.results.filter((r) => r.outcome === "confirmed").length;
  const failed = result.results.filter((r) => r.outcome === "failed");
  const total = result.results.length;
  if (result.outcome === "nothing_to_do") {
    notify("info", title, "Καμία συσκευή δεν υποστηρίζει αυτή την ενέργεια");
  } else if (result.outcome === "all_confirmed") {
    notify("success", title, `Επιβεβαιώθηκε σε ${confirmed}/${total} συσκευές`);
  } else if (result.outcome === "failed") {
    notify("error", title, failed[0]?.error?.message ?? "Απέτυχε σε όλες τις συσκευές");
  } else {
    notify("warning", title, `Επιβεβαιώθηκε σε ${confirmed}/${total}, ${failed.length} απέτυχαν`);
  }
}

export function AllLights({ count, onCount }: { count: number; onCount: number }) {
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
          <h2 className="font-semibold">Όλα τα φώτα</h2>
          <p className="text-sm text-muted">
            {onCount} από {count} αναμμένα
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={busy === "on"}
            disabled={!count || busy !== null}
            onClick={() => run("on", "Όλα ON", { type: "set_power", on: true })}
          >
            <Power className="size-4" aria-hidden /> Όλα ON
          </Button>
          <Button
            busy={busy === "off"}
            disabled={!count || busy !== null}
            onClick={() => run("off", "Όλα OFF", { type: "set_power", on: false })}
          >
            <PowerOff className="size-4" aria-hidden /> Όλα OFF
          </Button>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="mr-1 text-sm text-muted">Χρώμα σε όλα:</span>
        {PRESETS.map((p) => (
          <button
            key={p.name}
            title={p.name}
            aria-label={`${p.name} σε όλα`}
            disabled={busy !== null || !count}
            onClick={() => run(`c-${p.name}`, `Όλα ${p.name}`, { type: "set_color", color: p.rgb })}
            className="size-8 rounded-full border-2 border-white/10 transition-transform hover:scale-110 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40"
            style={{ background: rgbToHex(p.rgb) }}
          />
        ))}
      </div>
    </Card>
  );
}
