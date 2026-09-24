"""Live session: replays the active well's eRTMAC stream and runs NWIS on it.

Per tick: detectors -> look-ahead proximity (formation-aligned offset zones) ->
mud-window check -> fusion (escalate when corroborated) -> analogs + recommendations.
Formation tops are 'picked' (mud-logger style, with lag) as the bit penetrates them,
and the whole look-ahead is re-anchored to the actual tops.
"""
from __future__ import annotations

import numpy as np

from .. import config
from ..correlation import formation_at, target_from_well
from ..domain.ontology import FORMATION_BY_CODE, FORMATION_ORDER, HAZARD_BY_CODE
from ..kb import KnowledgeBase
from ..risk.evidence import risk_profile
from ..risk.mw_window import check_against_window, mw_window
from ..risk.recommend import recommend
from .analogs import AnalogIndex, live_features
from .detectors import DetectorBank, Signal
from .fusion import AlertManager

LOOKAHEAD_M = 150.0
ZONE_THRESHOLD = 0.25
RADIUS_KM = 8.0
PICK_LAG_SAMPLES = 3
CHANNELS = ["t", "md", "tvd", "gr", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload", "gas",
            "mw", "ecd", "dxc", "state"]


class LiveSession:
    def __init__(self, kb: KnowledgeBase, model=None, analog_index: AnalogIndex | None = None):
        self.kb = kb
        self.model = model
        self.analogs = analog_index
        z = np.load(config.LOGS_DIR / "active_stream.npz")
        self.data = {k: z[k] for k in z.files}
        self.n = len(self.data["t"])
        self.episodes = kb.db.kv_get("active_episodes", [])
        self.well = kb.active
        self.reset(0)

    # ------------------------------------------------------------------ state
    def reset(self, start_idx: int = 0) -> None:
        self.i = 0
        self.det = DetectorBank()
        self.alerts = AlertManager()
        self.picked: dict[str, float] = {}
        self.pending_picks: list[tuple[int, str, float]] = []
        self.prev_fm = None
        self.recent: list[dict] = []
        self.events_log: list[dict] = []
        self.last_analog_md = -1e9
        self.analog_result: dict | None = None
        self.target = target_from_well(self.kb, self.well.id)
        self._recompute_profile(announce=False)
        self.win = mw_window(self.kb, self.target, 10.0)
        self.last_window_check_md = -1e9
        if start_idx > 0:
            self.fast_forward(start_idx)

    def _recompute_profile(self, announce: bool = True) -> None:
        self.target.picked_tops_tvd = dict(self.picked)
        self.profile = risk_profile(self.kb, self.target, RADIUS_KM, model=self.model)
        self.zones = self.profile["zones"]
        self.tops = self.profile["tops"]
        # stable keys (hazard, formation, rank) so re-anchoring updates rather than duplicates alerts
        rank: dict = {}
        for z in self.zones:
            k = (z["hazard"], z["formation"])
            rank[k] = rank.get(k, 0) + 1
            z["key"] = f"LA:{z['hazard']}:{z['formation']}:{rank[k]}"

    def fast_forward(self, idx: int) -> None:
        """Advance silently (warm-up) to idx; alerts raised during warm-up are discarded."""
        while self.i < min(idx, self.n):
            self._step_one(emit=False)
        self.alerts = AlertManager()
        self.events_log.clear()

    def jump_to_episode(self, ep_id: str) -> None:
        ep = next((e for e in self.episodes if e["id"] == ep_id), None)
        if not ep:
            return
        start_md = ep.get("onset_md", ep["md"]) - 170
        idx = int(np.searchsorted(self.data["md"], start_md))
        self.reset(max(idx, 0))

    # ------------------------------------------------------------------ main
    def sample(self, i: int) -> dict:
        s = {k: float(self.data[k][i]) for k in CHANNELS}
        return s

    def step(self, k: int = 1) -> dict:
        samples = []
        for _ in range(k):
            if self.i >= self.n:
                break
            samples.append(self._step_one(emit=True))
        t_now = samples[-1]["t"] if samples else None
        return {"samples": samples, "alerts": self.alerts.pop_changed(t_now), "status": self.status(),
                "events": self._pop_events(), "done": self.i >= self.n}

    def _pop_events(self) -> list[dict]:
        out = list(self.events_log)
        self.events_log.clear()
        return out

    def _step_one(self, emit: bool) -> dict:
        s = self.sample(self.i)
        fm_true = FORMATION_ORDER[int(self.data["fm"][self.i])]
        # mud-logger top pick (truth revealed with lag, as in real operations)
        if self.prev_fm is not None and fm_true != self.prev_fm and fm_true not in self.picked:
            self.pending_picks.append((self.i + PICK_LAG_SAMPLES, fm_true, s["tvd"]))
        self.prev_fm = fm_true
        for pk in list(self.pending_picks):
            if self.i >= pk[0]:
                self.pending_picks.remove(pk)
                self._pick_top(pk[1], pk[2], s)
        fm_est, rel = formation_at(self.tops, s["tvd"], self.kb)
        s["fm_est"] = FORMATION_ORDER.index(fm_est)
        s["formation"] = fm_est
        self.recent.append(s)
        if len(self.recent) > 120:
            self.recent.pop(0)
        signals = self.det.update(s)
        keep = set()
        for sig in signals:
            keep.add(self._handle_signal(sig, s))
        self._lookahead(s)
        if s["md"] - self.last_window_check_md > 40 and s["state"] == 0:
            self.last_window_check_md = s["md"]
            self._window_check(s, fm_est)
        self.alerts.clear_stale(s["t"], keep)
        self.i += 1
        return s

    # ------------------------------------------------------------------ pieces
    def _pick_top(self, code: str, tvd: float, s: dict) -> None:
        pred = self.tops.get(code, {}).get("tvd")
        self.picked[code] = tvd
        self.det.reset_dxc()
        self._recompute_profile()
        delta = tvd - pred if pred is not None else 0.0
        self.events_log.append({"type": "top_pick", "formation": code, "tvd": round(tvd, 1), "md": round(s["md"], 1),
                                "t": s["t"], "delta_m": round(delta, 1),
                                "message": f"{FORMATION_BY_CODE[code].name} top picked at {tvd:,.0f} m TVD "
                                           f"({delta:+.0f} m vs prognosis) - look-ahead re-anchored"})
        self.alerts.upsert(f"GEO:{code}", "GEO", "geology", "info", f"Top picked: {FORMATION_BY_CODE[code].name}",
                           f"Picked at {tvd:,.0f} m TVD ({delta:+.0f} m vs offset-predicted top). All deeper offset hazards "
                           f"re-projected.", s["md"], s["t"], formation=code, confidence=1.0)

    def _zone_payload(self, z: dict) -> dict:
        return {k: z[k] for k in ("hazard", "md0", "md1", "formation", "peak", "p_offsets", "p_model", "lo", "hi",
                                  "n_exposed", "n_events", "drivers")}

    def _lookahead(self, s: dict) -> None:
        md = s["md"]
        for z in self.zones:
            dist = z["md0"] - md
            key = z["key"]
            if 0 < dist <= LOOKAHEAD_M or (z["md0"] <= md <= z["md1"]):
                inside = z["md0"] <= md <= z["md1"]
                lvl = "warning" if z["peak"] >= 0.45 or z["n_events"] >= 4 else "watch"
                hz = HAZARD_BY_CODE[z["hazard"]].label
                where = "Bit is INSIDE" if inside else f"Bit {dist:,.0f} m above"
                title = f"Look-ahead: {hz} zone in {FORMATION_BY_CODE[z['formation']].name} at {z['md0']:,.0f}-{z['md1']:,.0f} m"
                msg = (f"{where} the interval where {z['n_events']} of {z['n_exposed']} offset wells that drilled it recorded "
                       f"{hz.lower()} (offset evidence {z['p_offsets']:.0%}, 90% CI {z['lo']:.0%}-{z['hi']:.0%}; "
                       f"model {z['p_model']:.0%} at planned mud/trajectory).")
                existing = self.alerts.alerts.get(key)
                recs = existing.recommendations if existing and existing.recommendations else \
                    recommend(self.kb, z["hazard"], z["formation"], {o["well_id"] for o in self.profile["offsets"]})
                self.alerts.upsert(key, z["hazard"], "look-ahead", lvl, title, msg, md, s["t"], formation=z["formation"],
                                   confidence=z["peak"], evidence=z["evidence"], recommendations=recs,
                                   zone=self._zone_payload(z), drivers=[{"channel": d["factor"], "value": d["delta"],
                                                                         "baseline": None, "unit": "Δp"} for d in z.get("drivers", [])])
            elif md > z["md1"] + 30 and key in self.alerts.alerts:
                self.alerts.clear(key, s["t"], "zone passed")

    def _window_check(self, s: dict, fm: str) -> None:
        for f in check_against_window(self.win, fm, s["mw"], s["ecd"]):
            key = f"MW:{f['hazard']}:{fm}"
            self.alerts.upsert(key, f["hazard"], "mud-window", "warning" if f["p"] < 0.5 else "critical",
                               f"Mud outside offset-derived window ({FORMATION_BY_CODE[fm].name})", f["message"], s["md"],
                               s["t"], formation=fm, confidence=f["p"],
                               drivers=[{"channel": "ecd" if f["hazard"] == "LOSS" else "mw", "value": f["value"],
                                         "baseline": f["limit"], "unit": "ppg"}])

    def _handle_signal(self, sig: Signal, s: dict) -> str:
        la = self.alerts.active_lookahead(sig.hazard, s["md"])
        if sig.hazard == "STUCK" and la is None:
            la = self.alerts.active_lookahead("TORQUE", s["md"]) or self.alerts.active_lookahead("INSTAB", s["md"])
        key = f"RT:{sig.hazard}:{sig.detector}"
        level, title, conf = sig.level, sig.title, sig.score
        corroborated = la is not None
        if corroborated:
            conf = 1 - (1 - sig.score) * (1 - la.confidence)
            level = "critical" if sig.level in ("warning", "critical") else "warning"
            title = f"{sig.title} - CORROBORATED by offset look-ahead"
        recs = recommend(self.kb, sig.hazard, s.get("formation"), {o["well_id"] for o in self.profile["offsets"]})
        analogs = self._analogs(s)
        a = self.alerts.upsert(key, sig.hazard, "fused" if corroborated else "real-time", level, title, sig.message,
                               s["md"], s["t"], formation=s.get("formation"), confidence=round(conf, 3),
                               drivers=sig.drivers, evidence=la.evidence if la else [], recommendations=recs,
                               analogs=analogs, corroborated=corroborated,
                               zone=la.zone if la else None)
        return key

    def _analogs(self, s: dict) -> list:
        if self.analogs is None:
            return []
        if abs(s["md"] - self.last_analog_md) > 15 or self.analog_result is None:
            f = live_features(self.recent)
            if f is not None:
                self.analog_result = self.analogs.query(f, 5)
                self.last_analog_md = s["md"]
        return (self.analog_result or {}).get("analogs", [])

    def analog_snapshot(self) -> dict:
        f = live_features(self.recent)
        return self.analogs.query(f, 5) if (f is not None and self.analogs is not None) else {"analogs": [], "forecast": {}}

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        i = min(self.i, self.n - 1)
        s = self.recent[-1] if self.recent else self.sample(i)
        fm, rel = formation_at(self.tops, s["tvd"], self.kb)
        idx = FORMATION_ORDER.index(fm)
        nxt = next((c for c in FORMATION_ORDER[idx + 1:] if c in self.tops), None)
        next_top = None
        if nxt:
            next_top = {"formation": nxt, "md": self.tops[nxt]["md"], "tvd": self.tops[nxt]["tvd"],
                        "sd": self.tops[nxt]["sd"], "distance_m": round(self.tops[nxt]["md"] - s["md"], 1)}
        f = self.win["formations"].get(fm, {})
        ahead = [{**self._zone_payload(z), "distance_m": round(z["md0"] - s["md"], 1)} for z in self.zones
                 if z["md1"] >= s["md"]][:6]
        return {"i": self.i, "n": self.n, "t": s["t"], "md": round(s["md"], 1), "tvd": round(s["tvd"], 1),
                "formation": fm, "rel": round(rel, 3), "next_top": next_top, "mw": s["mw"], "ecd": round(s["ecd"], 2),
                "window": f.get("window") if f else None, "picked": self.picked, "zones_ahead": ahead,
                "progress": round(self.i / self.n, 4),
                "episode": next((e["id"] for e in self.episodes if e.get("onset_md", e["md"]) - 200 <= s["md"] <= e["md"] + 80), None)}

    def ribbon(self, md_from: float, span: float = 300.0) -> list[dict]:
        out = []
        for b in self.profile["bins"]:
            if b["md1"] < md_from or b["md0"] > md_from + span:
                continue
            out.append({"md0": b["md0"], "md1": b["md1"], "formation": b["formation"],
                        "risk": {hz: h["final"] for hz, h in b["hazards"].items()}})
        return out
