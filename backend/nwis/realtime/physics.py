"""Physics baselines for surface drilling channels (simplified hydraulics + soft-string torque and drag).

Each channel's expected value is a physics-shaped function of depth, inclination, mud weight and flow
rate with a small number of coefficients (think "friction factor") calibrated online on recent quiet,
on-bottom samples - the same way field engineers calibrate torque-and-drag friction factors against
measured data. Unlike a rolling median, the expectation moves with depth and responds immediately to a
mud-weight change, so there is no blind spot while a new baseline is re-learnt.

Assumptions (Tier-1 screening level, stated for reviewers):
- Buoyancy factor BF = 1 - MW/65.5 (steel density 65.5 ppg).
- Pump pressure losses scale with MW * Q^1.8 * length (turbulent pipe/annular flow, Bingham-plastic-like).
- Soft-string drag/torque scale with buoyed weight times (cos inc for axial load, 1 + 2 sin inc for side force).
- ECD excess over MW = annular friction (constant per section at fixed Q) + cuttings loading proportional to ROP.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

STEEL_PPG = 65.5
MIN_FIT = 40          # quiet samples needed before a channel is considered calibrated
WINDOW = 240          # rolling calibration window (samples)


def buoyancy(mw: float) -> float:
    return 1.0 - mw / STEEL_PPG


def basis(channel: str, s: dict, inc_deg: float) -> np.ndarray:
    md_km = s["md"] / 1000.0
    bf = buoyancy(s["mw"])
    inc = math.radians(inc_deg)
    q = max(s["flow_in"], 1.0) / 1000.0
    if channel == "spp":
        return np.array([1.0, s["mw"] * q ** 1.8 * md_km])
    if channel == "torque":
        return np.array([1.0, bf * md_km * (1.0 + 2.0 * math.sin(inc)), s.get("wob", 0.0) / 10.0])
    if channel == "hookload":
        return np.array([1.0, bf * md_km * math.cos(inc)])
    if channel == "ecd_excess":
        return np.array([1.0, s["rop"] / 10.0])
    raise KeyError(channel)


def _measured(channel: str, s: dict) -> float:
    return s["ecd"] - s["mw"] if channel == "ecd_excess" else s[channel]


class PhysicsBaseline:
    """Online-calibrated physics expectation per channel, with a robust residual scale."""

    CHANNELS = ("spp", "torque", "hookload", "ecd_excess")

    def __init__(self):
        self.X = {c: deque(maxlen=WINDOW) for c in self.CHANNELS}
        self.y = {c: deque(maxlen=WINDOW) for c in self.CHANNELS}
        self.coef: dict[str, np.ndarray | None] = {c: None for c in self.CHANNELS}
        self.scale: dict[str, float] = {c: float("nan") for c in self.CHANNELS}
        self._since_fit = 0

    def calibrated(self, channel: str) -> bool:
        return self.coef[channel] is not None

    def expected(self, s: dict, inc_deg: float) -> dict[str, float | None]:
        out = {}
        for c in self.CHANNELS:
            coef = self.coef[c]
            if coef is None:
                out[c] = None
                continue
            v = float(basis(c, s, inc_deg) @ coef)
            out[c] = v + s["mw"] if c == "ecd_excess" else v
        return out

    def residual_z(self, s: dict, inc_deg: float) -> dict[str, float]:
        exp = self.expected(s, inc_deg)
        z = {}
        for c, e in exp.items():
            if e is None or not math.isfinite(self.scale[c]):
                continue
            meas = s["ecd"] if c == "ecd_excess" else s[c]
            z[c] = (meas - e) / self.scale[c]
        return z

    def observe(self, s: dict, inc_deg: float, quiet: bool) -> None:
        """Add an on-bottom sample to the calibration set when nothing abnormal is going on."""
        if not quiet:
            return
        for c in self.CHANNELS:
            self.X[c].append(basis(c, s, inc_deg))
            self.y[c].append(_measured(c, s))
        self._since_fit += 1
        if self._since_fit >= 10:
            self._fit()
            self._since_fit = 0

    def _fit(self) -> None:
        for c in self.CHANNELS:
            if len(self.y[c]) < MIN_FIT:
                continue
            X = np.asarray(self.X[c])
            y = np.asarray(self.y[c])
            w = np.ones(len(y))
            coef = None
            for _ in range(3):   # iteratively reweighted least squares (Huber-like): outliers barely move the fit
                sw = np.sqrt(w)
                coef, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
                r = y - X @ coef
                mad = float(np.median(np.abs(r - np.median(r)))) * 1.4826 + 1e-6
                w = np.clip(2.0 * mad / np.maximum(np.abs(r), 1e-9), 0.0, 1.0)
            r = y - X @ coef
            self.coef[c] = coef
            self.scale[c] = float(np.median(np.abs(r - np.median(r)))) * 1.4826 + 1e-6
