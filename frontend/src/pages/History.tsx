import { History as HistoryIcon } from "lucide-react";
import { Card, Empty, PageHeader, cx } from "../components/ui";
import { useActivity } from "../hooks/useData";
import { sourceLabel } from "../lib/labels";

const OUTCOME: Record<string, { text: string; cls: string }> = {
  confirmed: { text: "Επιβεβαιώθηκε", cls: "text-emerald-400" },
  unconfirmed: { text: "Χωρίς επιβεβαίωση", cls: "text-amber-300" },
  mismatch: { text: "Διαφορετική κατάσταση", cls: "text-amber-300" },
  failed: { text: "Απέτυχε", cls: "text-red-400" },
};

export function History() {
  const entries = useActivity(200);

  return (
    <>
      <PageHeader title="Ιστορικό" subtitle="Οι τελευταίες 200 εντολές: τι άλλαξε, πότε και από πού." />
      {entries.length === 0 ? (
        <Empty icon={<HistoryIcon className="size-8" />} title="Καμία εντολή ακόμα">
          Κάθε εντολή από το dashboard, το Claude, σκηνές και αυτοματισμούς καταγράφεται εδώ.
        </Empty>
      ) : (
        <Card className="p-0 sm:p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-4 py-3 font-medium">Ώρα</th>
                  <th className="px-4 py-3 font-medium">Συσκευή</th>
                  <th className="px-4 py-3 font-medium">Εντολή</th>
                  <th className="px-4 py-3 font-medium">Από</th>
                  <th className="px-4 py-3 font-medium">Αποτέλεσμα</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => {
                  const o = OUTCOME[e.outcome] ?? { text: e.outcome, cls: "text-muted" };
                  return (
                    <tr key={e.id} className="border-b border-line last:border-0">
                      <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-muted">
                        {new Date(e.ts).toLocaleString("el-GR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                      </td>
                      <td className="px-4 py-2.5">{e.device_name}</td>
                      <td className="whitespace-nowrap px-4 py-2.5">{e.command}</td>
                      <td className="px-4 py-2.5 text-muted">{sourceLabel(e.source)}</td>
                      <td className={cx("whitespace-nowrap px-4 py-2.5", o.cls)} title={e.detail ?? undefined}>
                        {o.text}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
