import { Bluetooth, Check, Pencil, Plus, Scale, Trash2, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { ScaleCandidate, ScaleStatus, ScaleView } from "../api/types";
import { reportError } from "../lib/commands";
import { t, useT } from "../lib/i18n";
import { formatAgo } from "../lib/time";
import { notify } from "../store/toasts";
import { Button, Card } from "./ui";
import { fmtKg } from "./WeightCard";

const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

/** One added scale on the Devices page. */
export function ScaleRow({ scale, onChange }: { scale: ScaleView; onChange: (s: ScaleStatus) => void }) {
  const tr = useT();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(scale.name);
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (key: string, fn: () => Promise<ScaleStatus>) => {
    setBusy(key);
    try {
      onChange(await fn());
      setEditing(false);
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-start gap-4">
        <Scale className="mt-1 size-6 shrink-0 text-accent" aria-hidden />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) void act("name", () => api.renameScale(scale.address, name.trim()));
              }}
            >
              <input autoFocus value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label={tr("dev.nameAria")} className={input} />
              <Button type="submit" variant="primary" busy={busy === "name"} aria-label={tr("common.save")}>
                <Check className="size-4" />
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(false)} aria-label={tr("common.cancel")}>
                <X className="size-4" />
              </Button>
            </form>
          ) : (
            <div className="flex items-center gap-2">
              <h3 className="truncate font-semibold">{scale.name}</h3>
              <button
                onClick={() => setEditing(true)}
                className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-panel-2 hover:text-white"
                aria-label={`${tr("common.rename")} ${scale.name}`}
              >
                <Pencil className="size-4" />
              </button>
            </div>
          )}
          <p className="mt-1 text-xs text-muted">
            {scale.model_label} · {scale.address} · Bluetooth
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-md bg-panel-2 px-2 py-0.5 text-gray-300">{tr("scale.cap")}</span>
            <span className="text-muted">
              {!scale.last_seen
                ? tr("scale.notSeen")
                : scale.last_kg != null
                  ? tr("scale.lastReading", { kg: fmtKg(scale.last_kg), when: formatAgo(scale.last_seen) })
                  : tr("scale.signal", { when: formatAgo(scale.last_seen) })}
            </span>
          </div>
        </div>
        <Button
          variant="danger"
          aria-label={tr("common.delete")}
          busy={busy === "del"}
          onClick={() => {
            if (confirm(tr("scale.confirmRemove", { name: scale.name }))) void act("del", () => api.removeScale(scale.address));
          }}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </Card>
  );
}

/** Scales found by the last search that are not added yet. */
export function ScaleCandidates({
  found,
  onAdded,
}: {
  found: ScaleCandidate[];
  onAdded: (s: ScaleStatus, address: string) => void;
}) {
  const tr = useT();
  const [busy, setBusy] = useState<string | null>(null);
  if (found.length === 0) return null;

  const add = async (c: ScaleCandidate) => {
    setBusy(c.address);
    try {
      onAdded(await api.addScale(c.address), c.address);
      notify("success", t("scale.added"), c.model_label);
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="border-accent/40">
      <h2 className="mb-2 flex items-center gap-2 font-semibold">
        <Bluetooth className="size-5 text-accent" aria-hidden /> {tr("scale.foundTitle")}
      </h2>
      <ul className="divide-y divide-line text-sm">
        {found.map((c) => (
          <li key={c.address} className="flex flex-wrap items-center justify-between gap-3 py-2">
            <span>
              <span className="font-medium">{c.model_label}</span>
              <span className="text-muted">
                {" · "}
                {c.address}
                {c.kg != null && c.kg > 0 && ` · ${fmtKg(c.kg)} kg`}
              </span>
            </span>
            <Button variant="primary" busy={busy === c.address} onClick={() => void add(c)}>
              <Plus className="size-4" aria-hidden /> {tr("scale.add")}
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
