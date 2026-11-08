"""Weather from Open-Meteo (free, no account, no key) for the location set in Settings.

Used for the weather card on the home page and for sunset automations that start earlier when it is
cloudy. Refreshed every 30 minutes; if the internet is down the last forecast is kept and nothing
else is affected.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

from app.domain.automations import Location

log = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Lychnos/0.1 (self-hosted smart home dashboard)"
REFRESH_S = 1800.0
RETRY_S = 300.0


class WeatherNow(BaseModel):
    temperature: float
    apparent_temperature: float | None = None
    weather_code: int
    cloud_cover: int | None = None
    is_day: bool = True
    wind_speed: float | None = None
    humidity: int | None = None


class WeatherDay(BaseModel):
    date: str
    weather_code: int
    temp_max: float
    temp_min: float
    precipitation_probability: int | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None


class WeatherReport(BaseModel):
    now: WeatherNow
    days: list[WeatherDay]
    fetched_at: datetime
    location_label: str = ""


def usable(location: Location | None) -> bool:
    """A location of exactly 0, 0 is the empty default, not a real place."""
    return location is not None and not (location.latitude == 0 and location.longitude == 0)


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value is not None else None


def parse(data: dict, label: str = "") -> tuple[WeatherReport, list[tuple[datetime, int]]]:
    cur = data["current"]
    daily = data["daily"]
    days = [
        WeatherDay(
            date=datetime.fromtimestamp(daily["time"][i] + data.get("utc_offset_seconds", 0), tz=timezone.utc).date().isoformat(),
            weather_code=int(daily["weather_code"][i]),
            temp_max=float(daily["temperature_2m_max"][i]),
            temp_min=float(daily["temperature_2m_min"][i]),
            precipitation_probability=(daily.get("precipitation_probability_max") or [None] * (i + 1))[i],
            sunrise=_ts(daily["sunrise"][i]),
            sunset=_ts(daily["sunset"][i]),
        )
        for i in range(len(daily["time"]))
    ]
    report = WeatherReport(
        now=WeatherNow(
            temperature=float(cur["temperature_2m"]),
            apparent_temperature=cur.get("apparent_temperature"),
            weather_code=int(cur["weather_code"]),
            cloud_cover=cur.get("cloud_cover"),
            is_day=bool(cur.get("is_day", 1)),
            wind_speed=cur.get("wind_speed_10m"),
            humidity=cur.get("relative_humidity_2m"),
        ),
        days=days,
        fetched_at=datetime.now(timezone.utc),
        location_label=label,
    )
    hourly = data.get("hourly") or {}
    clouds = [
        (datetime.fromtimestamp(t, tz=timezone.utc), int(c))
        for t, c in zip(hourly.get("time") or [], hourly.get("cloud_cover") or [], strict=False)
        if c is not None
    ]
    return report, clouds


def _address_label(display_name: str) -> dict[str, str]:
    parts = [p.strip() for p in display_name.split(",") if p.strip()]
    if len(parts) >= 3 and parts[1].isdigit():  # "Shop name, 10, Street, …": drop the shop
        parts = parts[1:]
    if len(parts) >= 2 and parts[0].isdigit():  # house number first
        return {"name": f"{parts[1]} {parts[0]}", "region": ", ".join(parts[2:5])}
    return {"name": parts[0] if parts else "", "region": ", ".join(parts[1:4])}


class WeatherService:
    def __init__(
        self, location_getter: Callable[[], Location | None], http: httpx.AsyncClient | None = None,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        self._location = location_getter
        self._http = http
        self._on_update = on_update
        self._report: WeatherReport | None = None
        self._clouds: list[tuple[datetime, int]] = []
        self._fetched_for: tuple[float, float] | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15)
        self._task = asyncio.create_task(self._loop(), name="weather")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._http is not None:
            await self._http.aclose()

    async def report(self) -> WeatherReport | None:
        loc = self._location()
        if not usable(loc):
            return None
        assert loc is not None
        if self._fetched_for != (loc.latitude, loc.longitude):
            await self.refresh()
        return self._report

    async def search_places(self, query: str, language: str = "el") -> list[dict]:
        """Find an address, street, town or city (OpenStreetMap Nominatim; Open-Meteo as a fallback)."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15)
        try:
            response = await self._http.get(
                NOMINATIM_URL,
                params={"q": query, "format": "jsonv2", "limit": 6, "accept-language": language, "layer": "address"},
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            found = [
                {
                    # "10, Ερμού, Μοναστηράκι, …" → name "Ερμού 10", region "Μοναστηράκι, …"
                    **_address_label(r.get("display_name", "")),
                    "latitude": round(float(r["lat"]), 4),
                    "longitude": round(float(r["lon"]), 4),
                }
                for r in response.json()
            ]
            unique = {(f["latitude"], f["longitude"]): f for f in found}
            if unique:
                return list(unique.values())
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        response = await self._http.get(
            GEOCODE_URL, params={"name": query, "count": 8, "language": language, "format": "json"}
        )
        response.raise_for_status()
        return [
            {
                "name": r.get("name", ""),
                "region": ", ".join(x for x in (r.get("admin1"), r.get("country")) if x),
                "latitude": round(float(r["latitude"]), 4),
                "longitude": round(float(r["longitude"]), 4),
            }
            for r in response.json().get("results") or []
        ]

    def cloud_cover_at(self, when: datetime) -> int | None:
        """Forecast cloud cover (%) for the hour around ``when``, if known."""
        best = None
        for ts, cover in self._clouds:
            gap = abs((ts - when).total_seconds())
            if gap <= 5400 and (best is None or gap < best[0]):
                best = (gap, cover)
        return best[1] if best else None

    async def refresh(self) -> None:
        loc = self._location()
        if not usable(loc) or self._http is None:
            return
        assert loc is not None
        async with self._lock:
            params = {
                "latitude": loc.latitude, "longitude": loc.longitude, "timeformat": "unixtime", "timezone": "auto",
                "forecast_days": 3,
                "current": "temperature_2m,apparent_temperature,weather_code,cloud_cover,is_day,wind_speed_10m,relative_humidity_2m",
                "hourly": "cloud_cover",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            }
            response = await self._http.get(URL, params=params)
            response.raise_for_status()
            self._report, self._clouds = parse(response.json(), loc.label)
            self._fetched_for = (loc.latitude, loc.longitude)
        if self._on_update is not None:
            self._on_update()

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
                delay = REFRESH_S
            except Exception as exc:
                log.warning("weather refresh failed", extra={"ctx": {"error": exc.__class__.__name__}})
                delay = RETRY_S
            await asyncio.sleep(delay)
