import { Lightbulb, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Scene } from "../api/types";
import { AllLights, summarizeBatch } from "../components/AllLights";
import { DeviceCard } from "../components/DeviceCard";
import { Button, Empty, PageHeader } from "../components/ui";
import { reportError } from "../lib/commands";
import { sortedViews, useDevices } from "../store/devices";

export function Dashboard() {
  const devices = useDevices((s) => s.devices);
  const scenesVersion = useDevices((s) => s.scenesVersion);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [activating, setActivating] = useState<string | null>(null);

  useEffect(() => {
    api.scenes().then(setScenes).catch(() => undefined);
  }, [scenesVersion]);

  const registered = sortedViews(devices).filter((v) => v.device.registered);
  const onCount = registered.filter((v) => v.runtime.state.power === true).length;

  const activate = async (scene: Scene) => {
    setActivating(scene.id);
    try {
      summarizeBatch(scene.name, await api.activateScene(scene.id));
    } catch (err) {
      reportError(null, err);
    } finally {
      setActivating(null);
    }
  };

  return (
    <>
      <PageHeader title="Αρχική" subtitle="Έλεγχος φώτων σε πραγματικό χρόνο" />
      {registered.length === 0 ? (
        <Empty icon={<Lightbulb className="size-8" />} title="Δεν υπάρχουν καταχωρημένες συσκευές">
          Πήγαινε στις <Link to="/devices" className="text-accent underline">Συσκευές</Link>, κάνε αναζήτηση και
          ενεργοποίησε τις λάμπες που θέλεις να ελέγχεις.
        </Empty>
      ) : (
        <div className="space-y-6">
          <AllLights count={registered.length} onCount={onCount} />

          {scenes.length > 0 && (
            <section aria-labelledby="quick-scenes">
              <h2 id="quick-scenes" className="mb-3 text-sm font-medium text-muted">
                Σκηνές
              </h2>
              <div className="flex flex-wrap gap-2">
                {scenes.map((scene) => (
                  <Button
                    key={scene.id}
                    busy={activating === scene.id}
                    disabled={activating !== null}
                    onClick={() => activate(scene)}
                  >
                    <Play className="size-4" aria-hidden />
                    {scene.name}
                  </Button>
                ))}
              </div>
            </section>
          )}

          <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {registered.map((view) => (
              <DeviceCard key={view.device.id} view={view} />
            ))}
          </section>
        </div>
      )}
    </>
  );
}
