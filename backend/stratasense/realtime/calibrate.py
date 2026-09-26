"""Alarm budget via online conformal calibration.

The RTOC states how many non-critical alerts per hour it can act on. Each detector keeps a rolling
reservoir of its own anomaly statistic from recent drilling where it was *not* alarming. When a detector
fires, its conformal p-value p = (1 + #{reservoir >= s}) / (n + 1) says how unusual the statistic is
relative to the well's own recent normal drilling. Under exchangeability the chance that a normal
sample gets p <= alpha is at most alpha (distribution-free; Angelopoulos & Bates, arXiv 2107.07511), so
choosing alpha = budget / (samples per hour x number of detectors) bounds the false-alarm rate.

Two details make this work on drilling data:
- The statistic is the detector's mean score over its own persistence window (a dxc reversal is
  significant because it *persists*, not because one sample is extreme).
- Hindsight admission: a sample only joins the reservoir after ~2.5 h, and is dropped if an alert
  followed, so the slow run-up to a real problem is never learnt as "normal".

Safety floor: critical signals (large kick, severe loss) always pass.
"""
from __future__ import annotations

from collections import deque

import numpy as np

# detector -> persistence window (samples) used for the statistic
WINDOWS = {"flow-pit:KICK": 3, "flow-pit:LOSS": 3, "stuck-pipe-index": 6, "dxc-gas": 10, "torque-spike": 1}
DETECTORS = tuple(WINDOWS)
# dxc departure is measured against its own fitted normal-compaction trend; a long pre-kick build-up would
# otherwise contaminate its reservoir, so its p-value is reported but never used to hold the alert back
UNGATED = {"dxc-gas"}
RESERVOIR = 720          # ~12 h of drilling at 1 sample/min
MIN_CALIB = 60           # below this the gate is open and alerts are marked "uncalibrated"
HINDSIGHT = 150          # samples wait ~2.5 h before joining the reservoir; dropped if an alert follows
DEFAULT_BUDGET = 1.0     # non-critical alerts per hour per console (RTOC-adjustable)


class OnlineConformal:
    def __init__(self, budget_per_hour: float = DEFAULT_BUDGET, samples_per_hour: float = 60.0):
        self.res = {d: deque(maxlen=RESERVOIR) for d in DETECTORS}
        self.win = {d: deque(maxlen=w) for d, w in WINDOWS.items()}
        self.pending = {d: deque() for d in DETECTORS}
        self.stat: dict[str, float] = {}
        self.samples_per_hour = samples_per_hour
        self.enabled = True        # False = report p-values but never suppress (used as the "no budget" baseline)
        self.set_budget(budget_per_hour)
        self.suppressed: dict[str, int] = {d: 0 for d in DETECTORS}
        self.passed: dict[str, int] = {d: 0 for d in DETECTORS}

    def set_budget(self, budget_per_hour: float) -> None:
        self.budget = float(max(0.1, min(budget_per_hour, 20.0)))
        self.alpha = self.budget / (self.samples_per_hour * len(DETECTORS))

    def update(self, scores: dict[str, float]) -> None:
        """Feed this sample's raw detector scores (call before gating)."""
        self.stat = {}
        for d, s in scores.items():
            if d in self.win and s == s:
                self.win[d].append(float(s))
                self.stat[d] = float(np.mean(self.win[d]))

    def observe(self, alarming: set[str]) -> None:
        """Queue this sample's statistics for the reservoir (call after gating)."""
        for d in DETECTORS:
            if d in alarming:
                self.pending[d].clear()      # the run-up to this alert is not normal drilling
                continue
            s = self.stat.get(d)
            if s is None:
                continue
            self.pending[d].append(s)
            if len(self.pending[d]) > HINDSIGHT:
                self.res[d].append(self.pending[d].popleft())

    def calibrated(self, detector: str) -> bool:
        n = len(self.res.get(detector, ()))
        # needs enough history to resolve p-values as small as alpha
        return n >= MIN_CALIB and (n + 1) * self.alpha >= 1.0

    def p_value(self, detector: str) -> float | None:
        s = self.stat.get(detector)
        if s is None or not self.calibrated(detector):
            return None
        a = np.fromiter(self.res[detector], float)
        return float((1 + np.sum(a >= s)) / (len(a) + 1))

    def gate(self, detector: str, level: str, corroborated: bool = False, opened_last_hour: int = 0) -> tuple[bool, float | None, str]:
        """Decide whether a firing detector may open or update a console alert.

        Returns (allow, p, reason). Order of rules:
        1. critical, or corroborated by an offset look-ahead zone -> always shown (safety floor)
        2. detectors in UNGATED carry their own physical normal-trend model -> shown
        3. p <= alpha/10 (overwhelming evidence) -> shown even when the console is over budget
        4. console under budget (non-critical alerts opened in the last hour < budget) and p <= alpha,
           or detector not yet calibrated -> shown
        5. otherwise -> held in the digest (listed, logged, never silently dropped)
        """
        p = self.p_value(detector)
        if not self.enabled:
            allow, why = True, "budget off"
        elif level == "critical" or corroborated:
            allow, why = True, "critical" if level == "critical" else "corroborated by look-ahead"
        elif detector in UNGATED:
            allow, why = True, "physical trend model"
        elif p is not None and p <= self.alpha / 10:
            allow, why = True, "overwhelming evidence"
        elif opened_last_hour >= self.budget:
            allow, why = False, "console over alarm budget"
        elif p is None:
            allow, why = True, "uncalibrated"
        else:
            allow, why = p <= self.alpha, ("unusual for this well" if p <= self.alpha else "within this well's normal variability")
        if detector in self.passed:
            (self.passed if allow else self.suppressed)[detector] += 1
        return allow, p, why

    def state(self) -> dict:
        return {"budget_per_hour": self.budget, "alpha": round(self.alpha, 5),
                "calibrated": {d: self.calibrated(d) for d in DETECTORS},
                "reservoir": {d: len(r) for d, r in self.res.items()},
                "suppressed": dict(self.suppressed), "passed": dict(self.passed)}
