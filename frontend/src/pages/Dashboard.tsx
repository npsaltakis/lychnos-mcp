import { CalendarClock, ChevronRight, Lightbulb, Play, ScrollText } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Scene } from "../api/types";
import { AllLights, summarizeBatch } from "../components/AllLights";
import { RoomSection } from "../components/RoomSection";
import { WeatherCard } from "../components/WeatherCard";
import { WeightCard } from "../components/WeightCard";
import { Button, Card, Empty, PageHeader } from "../components/ui";
import { useActivity, useAutomations, useRooms, useScenes } from "../hooks/useData";
import { reportError } from "../lib/commands";
import { useT } from "../lib/i18n";
import { changeLabel, sourceLabel } from "../lib/labels";
import { formatAgo, formatUpcoming } from "../lib/time";
import { sortedViews, useDevices } from "../store/devices";

export function Dashboard() {
  const t = useT();
  const devices = useDevices((s) => s.devices);
  const rooms = useRooms();
  const scenes = useScenes();
  const automations = useAutomations();
  const activity = useActivity(5);
  const [activating, setActivating] = useState<string | null>(null);

  const registered = sortedViews(devices).filter((v) => v.device.registered);
  const onCount = registered.filter((v) => v.runtime.state.power === true).length;
  const roomIds = new Set(rooms.map((r) => r.id));
  const sections = rooms
    .map((room) => ({ room, views: registered.filter((v) => v.device.room_id === room.id) }))
    .filter((s) => s.views.length > 0);
  const unassigned = registered.filter((v) => !v.device.room_id || !roomIds.has(v.device.room_id));
  const upcoming = automations
    .filter((a) => a.enabled && a.next_run_at)
    .sort((a, b) => (a.next_run_at! < b.next_run_at! ? -1 : 1))
    .slice(0, 3);

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
      <PageHeader title={t("home.title")} subtitle={t("home.subtitle")} />
      <div className="mb-6 space-y-4">
        <WeatherCard />
        <WeightCard />
      </div>
      {registered.length === 0 ? (
        <Empty icon={<Lightbulb className="size-8" />} title={t("home.emptyTitle")}>
          {t("home.emptyBefore")}
          <Link to="/devices" className="text-accent underline">
            {t("nav.devices")}
          </Link>
          {t("home.emptyAfter")}
        </Empty>
      ) : (
        <div className="space-y-8">
          <AllLights count={registered.length} onCount={onCount} />

          {(scenes.length > 0 || upcoming.length > 0) && (
            <div className="grid gap-4 lg:grid-cols-2">
              {scenes.length > 0 && (
                <Card>
                  <h2 className="mb-3 text-sm font-medium text-muted">{t("home.scenes")}</h2>
                  <div className="flex flex-wrap gap-2">
                    {scenes.map((scene) => (
                      <Button key={scene.id} busy={activating === scene.id} disabled={activating !== null} onClick={() => activate(scene)}>
                        <Play className="size-4" aria-hidden />
                        {scene.name}
                      </Button>
                    ))}
                  </div>
                </Card>
              )}
              {upcoming.length > 0 && (
                <Card>
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="text-sm font-medium text-muted">{t("home.upcoming")}</h2>
                    <Link to="/automations" className="inline-flex items-center text-xs text-muted hover:text-white">
                      {t("home.all")} <ChevronRight className="size-3.5" aria-hidden />
                    </Link>
                  </div>
                  <ul className="space-y-2">
                    {upcoming.map((a) => (
                      <li key={a.id} className="flex items-center justify-between gap-3 text-sm">
                        <span className="inline-flex min-w-0 items-center gap-2">
                          <CalendarClock className="size-4 shrink-0 text-accent" aria-hidden />
                          <span className="truncate">{a.name}</span>
                        </span>
                        <span className="shrink-0 tabular-nums text-muted">{formatUpcoming(a.next_run_at)}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          )}

          {rooms.length === 0 && (
            <p className="text-sm text-muted">
              {t("home.roomsTipBefore")}
              <Link to="/devices" className="text-accent underline">
                {t("nav.devices")}
              </Link>
              {t("home.roomsTipAfter")}
            </p>
          )}

          {sections.map(({ room, views }) => (
            <RoomSection key={room.id} room={room} views={views} />
          ))}
          {unassigned.length > 0 && <RoomSection room={null} views={unassigned} />}

          {activity.length > 0 && (
            <Card>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium text-muted">{t("home.recent")}</h2>
                <Link to="/log" className="inline-flex items-center gap-1 text-xs text-muted hover:text-white">
                  <ScrollText className="size-3.5" aria-hidden /> {t("nav.log")}
                </Link>
              </div>
              <ul className="divide-y divide-line text-sm">
                {activity.map((e) => (
                  <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-2">
                    <span className="min-w-0">
                      <span className="font-medium">{e.device_name}</span> · {changeLabel(e.command)}
                    </span>
                    <span className="text-xs text-muted">
                      {sourceLabel(e.source)} · {formatAgo(e.ts)}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
