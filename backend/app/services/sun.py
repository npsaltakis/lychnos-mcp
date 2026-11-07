"""Sunrise / sunset times (NOAA "sunrise equation", accurate to about a minute; no dependencies).

Reference: https://en.wikipedia.org/wiki/Sunrise_equation
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Literal

J2000 = 2451545.0
UNIX_EPOCH_JD = 2440587.5
OBLIQUITY_DEG = 23.4397
HORIZON_DEG = -0.833  # refraction + solar disc radius


def _julian_noon(day: date) -> float:
    # date.toordinal(): 0001-01-01 == 1, whose Julian Day at 00:00 UTC is 1721425.5
    return day.toordinal() + 1721425.0


def _jd_to_datetime(jd: float) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=(jd - UNIX_EPOCH_JD) * 86400)


def sun_event(
    day: date, latitude: float, longitude: float, event: Literal["sunrise", "sunset"]
) -> datetime | None:
    """UTC time of sunrise/sunset on ``day`` at the location, or None (polar day/night)."""
    n = _julian_noon(day) - J2000 + 0.0008
    j_star = n - longitude / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    j_transit = J2000 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sin_dec = math.sin(lam) * math.sin(math.radians(OBLIQUITY_DEG))
    cos_dec = math.cos(math.asin(sin_dec))
    phi = math.radians(latitude)
    cos_omega = (math.sin(math.radians(HORIZON_DEG)) - math.sin(phi) * sin_dec) / (math.cos(phi) * cos_dec)
    if not -1.0 <= cos_omega <= 1.0:
        return None
    omega = math.degrees(math.acos(cos_omega))
    jd = j_transit - omega / 360 if event == "sunrise" else j_transit + omega / 360
    return _jd_to_datetime(jd)
