import {
  Cloud,
  CloudDrizzle,
  CloudFog,
  CloudLightning,
  CloudMoon,
  CloudRain,
  CloudSnow,
  CloudSun,
  Droplets,
  MapPin,
  Moon,
  Sun,
  Sunset,
  Wind,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { WeatherReport } from "../api/types";
import { locale, useT, type MessageKey } from "../lib/i18n";
import { Card } from "./ui";

const REFRESH_MS = 10 * 60 * 1000;

/** WMO weather code → icon and label key. */
function describe(code: number, day = true): [LucideIcon, MessageKey] {
  if (code === 0) return [day ? Sun : Moon, "wx.clear"];
  if (code <= 2) return [day ? CloudSun : CloudMoon, "wx.partly"];
  if (code === 3) return [Cloud, "wx.cloudy"];
  if (code <= 48) return [CloudFog, "wx.fog"];
  if (code <= 57) return [CloudDrizzle, "wx.drizzle"];
  if (code <= 67 || (code >= 80 && code <= 82)) return [CloudRain, "wx.rain"];
  if (code <= 77 || code === 85 || code === 86) return [CloudSnow, "wx.snow"];
  return [CloudLightning, "wx.storm"];
}

const hhmm = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }) : "—";

export function WeatherCard() {
  const tr = useT();
  const [report, setReport] = useState<WeatherReport | null | undefined>(undefined);

  useEffect(() => {
    const load = () => api.weather().then(setReport).catch(() => setReport(null));
    void load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  if (report === undefined) return null;
  if (report === null)
    return (
      <Card className="flex items-center gap-3 text-sm text-muted">
        <MapPin className="size-5 flex-none" aria-hidden />
        <span>
          {tr("wx.noLocation")}{" "}
          <Link to="/settings" className="text-accent underline">
            {tr("nav.settings")}
          </Link>
        </span>
      </Card>
    );

  const { now, days } = report;
  const [Icon, label] = describe(now.weather_code, now.is_day);
  const today = days[0];
  const next = days.slice(1, 3);

  return (
    <Card>
      <h2 className="sr-only">{tr("wx.title")}</h2>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-3">
          <Icon className="size-10 text-accent" aria-hidden />
          <div>
            <p className="text-3xl font-semibold tabular-nums leading-none">{Math.round(now.temperature)}°</p>
            <p className="mt-1 text-sm text-muted">
              {tr(label)}
              {report.location_label && ` · ${report.location_label}`}
            </p>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-5 gap-y-1 text-sm sm:flex sm:gap-5">
          {today && (
            <div>
              <dt className="text-muted">{tr("wx.today")}</dt>
              <dd className="tabular-nums">
                {Math.round(today.temp_max)}° / {Math.round(today.temp_min)}°
              </dd>
            </div>
          )}
          {now.apparent_temperature != null && (
            <div>
              <dt className="text-muted">{tr("wx.feels")}</dt>
              <dd className="tabular-nums">{Math.round(now.apparent_temperature)}°</dd>
            </div>
          )}
          {today?.precipitation_probability != null && (
            <div>
              <dt className="flex items-center gap-1 text-muted">
                <Droplets className="size-3.5" aria-hidden /> {tr("wx.rainChance")}
              </dt>
              <dd className="tabular-nums">{today.precipitation_probability}%</dd>
            </div>
          )}
          {now.wind_speed != null && (
            <div>
              <dt className="flex items-center gap-1 text-muted">
                <Wind className="size-3.5" aria-hidden /> {tr("wx.wind")}
              </dt>
              <dd className="tabular-nums">{Math.round(now.wind_speed)} km/h</dd>
            </div>
          )}
          {today?.sunset && (
            <div>
              <dt className="flex items-center gap-1 text-muted">
                <Sunset className="size-3.5" aria-hidden /> {tr("wx.sunset")}
              </dt>
              <dd className="tabular-nums">{hhmm(today.sunset)}</dd>
            </div>
          )}
        </dl>

        {next.length > 0 && (
          <ul className="flex gap-4 text-sm sm:ml-auto">
            {next.map((d) => {
              const [DayIcon, dayLabel] = describe(d.weather_code);
              return (
                <li key={d.date} className="flex items-center gap-2">
                  <DayIcon className="size-5 text-muted" aria-label={tr(dayLabel)} />
                  <span>
                    <span className="block text-xs text-muted">
                      {new Date(`${d.date}T12:00:00`).toLocaleDateString(locale(), { weekday: "short" })}
                    </span>
                    <span className="tabular-nums">
                      {Math.round(d.temp_max)}° / {Math.round(d.temp_min)}°
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
