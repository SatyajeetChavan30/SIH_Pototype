"""Physics-informed real-time detectors on eRTMAC/WITS channels.

Each detector is explainable: it reports the channels that drove it (value vs.
baseline). Thresholds follow common RTOC practice and literature (differential
flow as the earliest kick indicator; multiple indicators for stuck pipe; corrected
d-exponent departure from the normal compaction trend for overpressure).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Signal:
    hazard: str
    detector: str
    level: str                 # watch | warning | critical
    score: float               # 0..1
    title: str
    message: str
    md: float
    drivers: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class Rolling:
    def __init__(self, n: int):
        self.buf: deque = deque(maxlen=n)

    def add(self, v: float) -> None:
        self.buf.append(v)

    def median(self) -> float:
        return float(np.median(self.buf)) if self.buf else float("nan")

    def mad(self) -> float:
        if len(self.buf) < 5:
            return float("nan")
        a = np.asarray(self.buf)
        return float(np.median(np.abs(a - np.median(a)))) * 1.4826 + 1e-6

    def __len__(self) -> int:
        return len(self.buf)


class DetectorBank:
    def __init__(self):
        self.hist: deque = deque(maxlen=240)          # recent on-bottom samples
        self.flow_base = Rolling(60)
        self.torque_base = Rolling(160)
        self.spp_base = Rolling(120)
        self.rop_base = Rolling(120)
        self.gas_base = Rolling(200)
        self.hook_drill = Rolling(60)                 # on-bottom hookload (string weight trend)
        self.excess_hist = Rolling(12)                # normal pick-up drag at connections
        self.conn_over: deque = deque(maxlen=6)
        self.last_mw: float | None = None
        self.pit_hist: deque = deque(maxlen=400)       # (t, pit, pumps_on)
        self.dxc_pts: deque = deque(maxlen=1500)       # (tvd, dxc) for normal trend
        self.dxc_smooth: deque = deque(maxlen=25)
        self.flow_persist = 0
        self.loss_persist = 0
        self.dxc_persist = 0
        self.in_conn = False
        self.conn_max_hook = 0.0
        self.last_md = 0.0

    # ------------------------------------------------------------------
    def update(self, s: dict) -> list[Signal]:
        out: list[Signal] = []
        state = int(s.get("state", 0))
        pumps_on = s["flow_in"] > 50
        self.pit_hist.append((s["t"], s["pit"], pumps_on and state == 0))
        # connections: track pick-up hookload (overpull)
        if state == 1:
            self.in_conn = True
            self.conn_max_hook = max(self.conn_max_hook, s["hookload"])
            return out
        if self.in_conn:
            self.in_conn = False
            if len(self.hook_drill) >= 10:
                excess = self.conn_max_hook - self.hook_drill.median()
                normal = self.excess_hist.median() if len(self.excess_hist) >= 3 else 0.0
                over = max(excess - normal, 0.0)
                self.conn_over.append(over)
                if over < 10:
                    self.excess_hist.add(excess)
            self.conn_max_hook = 0.0
        if state == 2 or not pumps_on:
            if state == 2:
                self._reset_baselines()
            return out
        if self.last_mw is not None and abs(s["mw"] - self.last_mw) > 0.2:
            self._reset_baselines()   # new mud weight -> new normal trends (dxc, torque, SPP)
        self.last_mw = s["mw"]
        self.hook_drill.add(s["hookload"])
        md = s["md"]
        self.last_md = md

        # ---------------- flow / pit : kick & losses
        fb = self.flow_base.median()
        delta = s["flow_out"] - fb if len(self.flow_base) >= 20 else 0.0
        pit_rate = self._pit_rate(900)  # bbl/hr over last 15 min (pumps-on only)
        if delta < 3.0 and delta > -3.0:
            self.flow_base.add(s["flow_out"])
        if delta > 4.0:
            self.flow_persist += 1
        else:
            self.flow_persist = max(self.flow_persist - 1, 0)
        if delta < -2.5:
            self.loss_persist += 1
        else:
            self.loss_persist = max(self.loss_persist - 1, 0)
        gas_b = self.gas_base.median() if len(self.gas_base) >= 30 else s["gas"]
        if s["gas"] < max(gas_b * 2, 1.5):
            self.gas_base.add(s["gas"])

        kick_score = 0.45 * _sig((delta - 5) / 1.5) + 0.4 * _sig((pit_rate - 12) / 5) + 0.15 * _sig((s["gas"] / max(gas_b, 0.3) - 4) / 1.5)
        if self.flow_persist >= 3 and pit_rate > 6:
            lvl = "critical" if (pit_rate > 15 or delta > 8) else "warning"
            out.append(Signal("KICK", "flow-pit", lvl, round(kick_score, 3), "Kick indicators: flow-out gain + pit gain",
                              f"Flow-out +{delta:.1f}% over baseline, pit gaining {pit_rate:.0f} bbl/hr. Stop drilling, flow-check, "
                              "prepare to shut in.", md,
                              [{"channel": "flow_out", "value": round(s['flow_out'], 1), "baseline": round(fb, 1), "unit": "%"},
                               {"channel": "pit_rate", "value": round(pit_rate, 1), "baseline": 0, "unit": "bbl/hr"},
                               {"channel": "gas", "value": round(s['gas'], 2), "baseline": round(gas_b, 2), "unit": "%"}]))
        recent_out = [h["flow_out"] for h in list(self.hist)[-5:]] + [s["flow_out"]]
        delta_s = float(np.mean(recent_out)) - fb if len(self.flow_base) >= 20 else 0.0
        if self.loss_persist >= 3 and pit_rate < -8:
            rate = -pit_rate
            sev = "seepage" if rate < 10 else ("partial" if rate < 100 else "severe")
            lvl = "critical" if rate >= 40 else "warning"
            out.append(Signal("LOSS", "flow-pit", lvl, round(_sig((rate - 10) / 8), 3),
                              "Lost circulation detected",
                              f"Flow-out {delta_s:+.1f}% vs baseline and active pit dropping ~{rate:.0f} bbl/hr ({sev}).", md,
                              [{"channel": "flow_out", "value": round(s['flow_out'], 1), "baseline": round(fb, 1), "unit": "%"},
                               {"channel": "pit_rate", "value": round(pit_rate, 1), "baseline": 0, "unit": "bbl/hr"},
                               {"channel": "ecd", "value": round(s['ecd'], 2), "baseline": None, "unit": "ppg"}],
                              {"rate_bbl_hr": round(rate, 1), "severity": sev}))

        # ---------------- stuck-pipe risk index
        tb = self.torque_base.median() if len(self.torque_base) >= 30 else s["torque"]
        sb = self.spp_base.median() if len(self.spp_base) >= 30 else s["spp"]
        rb = self.rop_base.median() if len(self.rop_base) >= 30 else s["rop"]
        t_ratio = s["torque"] / max(tb, 1e-3)
        recent = [h for h in list(self.hist)[-12:]]
        t_cv = float(np.std([h["torque"] for h in recent]) / max(np.mean([h["torque"] for h in recent]), 1e-3)) if len(recent) >= 6 else 0.0
        spp_mad = self.spp_base.mad()
        spp_spikes = sum(1 for h in recent if not math.isnan(spp_mad) and h["spp"] - sb > 4 * spp_mad)
        over = float(np.mean(list(self.conn_over)[-3:])) if self.conn_over else 0.0
        rop_ratio = s["rop"] / max(rb, 1e-3)
        spri = _sig(0.8 * (t_ratio - 1.15) / 0.1 + 0.6 * (over - 15) / 8 + 0.5 * (spp_spikes - 1.5)
                    + 0.4 * (t_cv - 0.06) / 0.02 - 1.0)
        # slow, always-updating baselines (robust medians) so genuine trend changes are absorbed
        self.torque_base.add(s["torque"])
        self.spp_base.add(s["spp"])
        if rop_ratio > 0.8:
            self.rop_base.add(s["rop"])
        if spri > 0.6 and (t_ratio > 1.2 or over > 20):
            lvl = "critical" if spri > 0.8 else "warning"
            out.append(Signal("STUCK", "stuck-pipe-index", lvl, round(spri, 3), "Stuck-pipe precursors (torque/overpull/SPP)",
                              f"Stuck-pipe risk index {spri:.2f}: torque {t_ratio:.2f}x baseline, overpull {over:.0f} klbs on recent connections, "
                              f"{spp_spikes} SPP spikes in last {len(recent)} samples, ROP {rop_ratio:.2f}x baseline.", md,
                              [{"channel": "torque", "value": round(s['torque'], 1), "baseline": round(tb, 1), "unit": "kft-lbf"},
                               {"channel": "overpull", "value": round(over, 1), "baseline": 0, "unit": "klbs"},
                               {"channel": "spp_spikes", "value": spp_spikes, "baseline": 0, "unit": "count"},
                               {"channel": "rop", "value": round(s['rop'], 1), "baseline": round(rb, 1), "unit": "m/hr"}]))
        # single torque spike (informational)
        tmad = self.torque_base.mad()
        if not math.isnan(tmad) and (s["torque"] - tb) / tmad > 6 and t_ratio > 1.3:
            out.append(Signal("TORQUE", "torque-spike", "watch", 0.4, "Torque spike",
                              f"Torque {s['torque']:.1f} kft-lbf vs baseline {tb:.1f}.", md,
                              [{"channel": "torque", "value": round(s['torque'], 1), "baseline": round(tb, 1), "unit": "kft-lbf"}]))

        # ---------------- overpressure: dxc departure + gas trend
        self.dxc_smooth.append(s["dxc"])
        dxs = float(np.median(self.dxc_smooth))
        trend = self._dxc_trend(s["tvd"])
        if trend is not None:
            dev = (dxs - trend) / trend
            gas_ratio = s["gas"] / max(gas_b, 0.3)
            if dev < -0.09 and gas_ratio > 2.0:
                self.dxc_persist += 1
            else:
                self.dxc_persist = max(self.dxc_persist - 1, 0)
            if dev > -0.05 and gas_ratio < 1.8:
                self.dxc_pts.append((s["tvd"], math.log(max(s["dxc"], 0.05))))
            if self.dxc_persist >= 10:
                score = _sig((-dev - 0.1) / 0.03) * 0.6 + _sig((gas_ratio - 3) / 1.0) * 0.4
                out.append(Signal("KICK", "dxc-gas", "critical" if score > 0.75 else "warning", round(score, 3),
                                  "Overpressure transition (dxc reversal + gas)",
                                  f"Corrected d-exponent {dev * 100:.0f}% below normal compaction trend and background gas "
                                  f"{gas_ratio:.1f}x baseline: formation pressure rising ahead of the bit.", md,
                                  [{"channel": "dxc", "value": round(dxs, 3), "baseline": round(trend, 3), "unit": ""},
                                   {"channel": "gas", "value": round(s['gas'], 2), "baseline": round(gas_b, 2), "unit": "%"},
                                   {"channel": "rop", "value": round(s['rop'], 1), "baseline": round(rb, 1), "unit": "m/hr"}],
                                  {"early_warning": True}))
        else:
            self.dxc_pts.append((s["tvd"], math.log(max(s["dxc"], 0.05))))
        self.hist.append(s)
        return out

    def reset_dxc(self) -> None:
        """New formation top: lithology changes shift dxc, so the normal trend is re-established."""
        self.dxc_pts.clear()
        self.dxc_smooth.clear()
        self.dxc_persist = 0

    def _reset_baselines(self) -> None:
        self.torque_base = Rolling(160)
        self.spp_base = Rolling(120)
        self.rop_base = Rolling(120)
        self.dxc_pts.clear()
        self.dxc_smooth.clear()
        self.dxc_persist = 0
        self.hist.clear()
        self.conn_over.clear()

    def _pit_rate(self, window_s: float) -> float:
        pts = [(t, p) for t, p, on in self.pit_hist if on]
        if len(pts) < 5:
            return 0.0
        t_end = pts[-1][0]
        pts = [(t, p) for t, p in pts if t >= t_end - window_s]
        if len(pts) < 4 or pts[-1][0] - pts[0][0] < 240:
            return 0.0
        t = np.array([x[0] for x in pts])
        p = np.array([x[1] for x in pts])
        slope = np.polyfit(t - t[0], p, 1)[0]
        return float(slope * 3600)

    def _dxc_trend(self, tvd: float) -> float | None:
        if len(self.dxc_pts) < 150:
            return None
        a = np.array(self.dxc_pts)
        a = a[a[:, 0] > tvd - 700]
        if len(a) < 100:
            return None
        k, c = np.polyfit(a[:, 0], a[:, 1], 1)
        return float(math.exp(k * tvd + c))
