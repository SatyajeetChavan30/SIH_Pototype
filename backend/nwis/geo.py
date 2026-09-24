"""Geospatial and wellbore-geometry utilities."""
from __future__ import annotations

import math

import numpy as np

EARTH_R_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (vectorised over numpy arrays)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def offset_latlon(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Shift a surface location by local north/east metres."""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def minimum_curvature(md, inc_deg, azi_deg):
    """Minimum-curvature survey calculation.

    Returns arrays (tvd, north, east, dls_deg_per_30m) for stations md/inc/azi.
    """
    md = np.asarray(md, dtype=float)
    inc = np.radians(np.asarray(inc_deg, dtype=float))
    azi = np.radians(np.asarray(azi_deg, dtype=float))
    n = len(md)
    tvd = np.zeros(n)
    north = np.zeros(n)
    east = np.zeros(n)
    dls = np.zeros(n)
    for i in range(1, n):
        dmd = md[i] - md[i - 1]
        i1, i2, a1, a2 = inc[i - 1], inc[i], azi[i - 1], azi[i]
        cos_dl = math.cos(i2 - i1) - math.sin(i1) * math.sin(i2) * (1 - math.cos(a2 - a1))
        dl = math.acos(max(-1.0, min(1.0, cos_dl)))
        rf = 1.0 if dl < 1e-9 else 2 / dl * math.tan(dl / 2)
        north[i] = north[i - 1] + dmd / 2 * (math.sin(i1) * math.cos(a1) + math.sin(i2) * math.cos(a2)) * rf
        east[i] = east[i - 1] + dmd / 2 * (math.sin(i1) * math.sin(a1) + math.sin(i2) * math.sin(a2)) * rf
        tvd[i] = tvd[i - 1] + dmd / 2 * (math.cos(i1) + math.cos(i2)) * rf
        dls[i] = math.degrees(dl) * 30.0 / dmd if dmd > 0 else 0.0
    return tvd, north, east, dls


class Trajectory:
    """Interpolating wrapper over a survey (MD <-> TVD, inclination)."""

    def __init__(self, md, inc, azi):
        self.md = np.asarray(md, dtype=float)
        self.inc = np.asarray(inc, dtype=float)
        self.azi = np.asarray(azi, dtype=float)
        self.tvd, self.north, self.east, self.dls = minimum_curvature(self.md, self.inc, self.azi)

    def md_at_tvd(self, tvd):
        """MD for a TVD (assumes TVD monotonic, true for inc < 90 deg). Extrapolates along last inclination."""
        tvd = np.asarray(tvd, dtype=float)
        out = np.interp(tvd, self.tvd, self.md)
        beyond = tvd > self.tvd[-1]
        if np.any(beyond):
            c = max(math.cos(math.radians(self.inc[-1])), 0.05)
            out = np.where(beyond, self.md[-1] + (tvd - self.tvd[-1]) / c, out)
        return out

    def tvd_at_md(self, md):
        md = np.asarray(md, dtype=float)
        out = np.interp(md, self.md, self.tvd)
        beyond = md > self.md[-1]
        if np.any(beyond):
            c = math.cos(math.radians(self.inc[-1]))
            out = np.where(beyond, self.tvd[-1] + (md - self.md[-1]) * c, out)
        return out

    def inc_at_md(self, md):
        return np.interp(md, self.md, self.inc)
