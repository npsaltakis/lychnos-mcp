"""Weather (Open-Meteo) parsing and cloudy sunset automations."""

from datetime import date, datetime, timedelta, timezone

from app.domain.automations import Location, SunTrigger
from app.services.automation_service import next_run
from app.services.sun import sun_event
from app.services.weather_service import WeatherService, parse, usable

T = 1_762_588_800  # 2025-11-08 08:00 UTC


def sample() -> dict:
    return {
        "utc_offset_seconds": 7200,
        "current": {"temperature_2m": 12.1, "apparent_temperature": 8.6, "weather_code": 3, "cloud_cover": 90,
                    "is_day": 1, "wind_speed_10m": 13.5, "relative_humidity_2m": 55},
        "hourly": {"time": [T + 3600 * i for i in range(24)], "cloud_cover": [10] * 6 + [95] * 18},
        "daily": {"time": [T - 8 * 3600 - 7200 + 86400 * i for i in range(2)], "weather_code": [3, 61],
                  "temperature_2m_max": [16.0, 14.0], "temperature_2m_min": [8.0, 9.0],
                  "precipitation_probability_max": [10, 80], "sunrise": [T - 3600, T + 86400 - 3600],
                  "sunset": [T + 8 * 3600, T + 86400 + 8 * 3600]},
    }


def test_parse_and_cloud_lookup() -> None:
    report, clouds = parse(sample(), "Αθήνα")
    assert report.now.temperature == 12.1 and report.days[1].precipitation_probability == 80
    service = WeatherService(lambda: None)
    service._clouds = clouds
    t = datetime.fromtimestamp(T, tz=timezone.utc)
    assert service.cloud_cover_at(t) == 10 and service.cloud_cover_at(t + timedelta(hours=10)) == 95
    assert service.cloud_cover_at(t + timedelta(days=5)) is None
    assert not usable(Location(latitude=0, longitude=0)) and usable(Location(latitude=37.9, longitude=23.7))


def test_cloudy_sunset_runs_earlier() -> None:
    loc = Location(latitude=37.98, longitude=23.73)
    day = date.today() + timedelta(days=1)
    after = datetime(day.year, day.month, day.day, 6).astimezone()
    trig = SunTrigger(event="sunset", offset_min=-10, cloudy_earlier_min=30)
    sunset = sun_event(day, loc.latitude, loc.longitude, "sunset")
    clear = next_run(trig, after, loc, lambda _t: 20)
    cloudy = next_run(trig, after, loc, lambda _t: 85)
    assert clear == sunset - timedelta(minutes=10)
    assert cloudy == sunset - timedelta(minutes=40)
    assert next_run(trig, after, loc, None) == clear
