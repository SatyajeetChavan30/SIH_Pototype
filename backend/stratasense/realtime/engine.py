"""Live session: runs StrataSense on the active well's eRTMAC stream (stored replay or a live WITS-0 / WITSML feed).

Per tick: detectors -> look-ahead proximity (formation-aligned offset zones) ->
mud-window check -> fusion (escalate when corroborated) -> analogs + recommendations.
Formation tops are 'picked' (mud-logger style, with lag) as the bit penetrates them,
and the whole look-ahead is re-anchored to the actual tops.
"""
from __future__ import annotations

import re
import uuid

import numpy as np

from .. import audit, config
from ..correlation import formation_at, target_from_well
from ..domain.ontology import FORMATION_BY_CODE, FORMATION_ORDER, HAZARD_BY_CODE, formation_name
from ..kb import KnowledgeBase
from ..risk.evidence import risk_profile
from ..risk.mw_window import check_against_window, mw_window
from ..risk.recommend import recommend
from .analogs import AnalogIndex, live_features
from .calibrate import DEFAULT_BUDGET, OnlineConformal
from .detectors import DetectorBank, Signal
from .fusion import AlertManager
from .toppick import DTWTopPicker, next_formation
from ..data.logs_gen import dxc as dxc_formula

LOOKAHEAD_M = 150.0
ZONE_THRESHOLD = 0.25
RADIUS_KM = 8.0
PICK_LAG_SAMPLES = 3
CHANNELS = ["t", "md", "tvd", "gr", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload", "gas",
            "mw", "ecd", "dxc", "state"]


RAW_CHANNELS = ("rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload", "gas", "mw", "gr", "ecd")


def episode_start_index(ep: dict, t, md) -> int:
    """Where a scenario jump starts: 30 min before a timed incident (real streams: bit depth is not monotonic),
    otherwise 170 m above a scripted episode's onset depth."""
    if ep.get("t") is not None:
        return max(int(np.searchsorted(t, float(ep["t"]) - 1800.0)), 0)
    return max(int(np.searchsorted(md, ep.get("onset_md", ep["md"]) - 170)), 0)


def _hole_in(hole: str | None) -> float:
    """'12-1/4"' -> 12.25 (bit size for the d-exponent)."""
    m = re.match(r"\s*(\d+)(?:-(\d+)/(\d+))?", hole or "")
    if not m:
        return 8.5
    return float(m.group(1)) + (float(m.group(2)) / float(m.group(3)) if m.group(2) else 0.0)


class LiveSession:
    def __init__(self, kb: KnowledgeBase, model=None, analog_index: AnalogIndex | None = None, source=None,
                 top_mode: str | None = None):
        self.kb = kb
        self.model = model
        self.analogs = analog_index
        self.session_id = uuid.uuid4().hex[:12]
        self.audit_buffer: list[dict] = []   # decision-log rows, flushed to the DB by the API layer
        self._emit = True
        self.budget = DEFAULT_BUDGET
        self.source = source
        self.live = source is not None
        self.top_mode_override = top_mode   # None = follow the configured mode (config.TOP_PICK_MODE)
        self.well = kb.active
        if self.live:
            # growing buffer fed by ingest(); no hidden truth ('fm') and no scripted episodes on a real feed
            self.data = {k: [] for k in CHANNELS}
            self.n = 0
            self.episodes = []
            self.t0_epoch: float | None = None
            self._last_raw: dict = {}
            self.derived: set[str] = set()
            self.dropped = 0
        else:
            z = np.load(config.LOGS_DIR / "active_stream.npz")
            self.data = {k: z[k] for k in z.files}
            self.n = len(self.data["t"])
            self.episodes = kb.db.kv_get("active_episodes", [])
        self.reset(0)

    # ------------------------------------------------------------------ state
    def reset(self, start_idx: int = 0) -> None:
        self.i = 0
        self.det = DetectorBank()
        self.conformal = OnlineConformal(self.budget)
        self._gate_of: dict[str, str] = {}          # alert key -> conformal detector key
        self.digest: list[dict] = []                # signals held back by the alarm budget (still visible)
        self._digest_last: dict[str, float] = {}
        self.opened: list[tuple[float, str]] = []   # (t, level) of real-time alerts opened, for alert-load
        self.alerts = AlertManager(on_event=self._audit)
        self.picked: dict[str, float] = {}
        self.pending_picks: list[tuple[int, str, float]] = []
        self.prev_fm = None
        self.prev_t: float | None = None
        self.recent: list[dict] = []
        self.events_log: list[dict] = []
        self.history_events: list[dict] = []   # never popped: geology events for the shift-handover brief
        self.last_analog_md = -1e9
        self.analog_result: dict | None = None
        self.target = target_from_well(self.kb, self.well.id)
        self._recompute_profile(announce=False)
        # formation-top picking: prognosis kept un-anchored so DTW stays independent of the mud-logger picks
        self.prior_tops = {k: dict(v) for k, v in self.tops.items()}
        self.top_mode = self.top_mode_override or config.TOP_PICK_MODE
        if self.live and self.top_mode == "auto":
            self.top_mode = "dtw"      # a raw rig feed carries no mud-logger picks: GR correlation re-anchors
        self.dtw_picks: list[dict] = []
        self.dtw_done: set[str] | None = self._dtw_done_below(float(self.data["tvd"][0])) if self.n else None
        self.toppicker = None
        if self.top_mode in ("dtw", "auto"):
            if not hasattr(self, "_dtw_refs"):
                self._dtw_refs = DTWTopPicker(self.kb, self.well.lat, self.well.lon, {self.well.id}).refs
            self.toppicker = DTWTopPicker(self.kb, self.well.lat, self.well.lon, {self.well.id}, refs=self._dtw_refs)
        self.win = mw_window(self.kb, self.target, 10.0)
        self.last_window_check_md = -1e9
        if start_idx > 0:
            self.fast_forward(start_idx)

    def _dtw_done_below(self, tvd0: float) -> set[str]:
        return {c for c, v in self.prior_tops.items() if v["tvd"] < tvd0 - 20}

    # ------------------------------------------------------------------ live feed
    def ingest(self, rows: list[dict]) -> int:
        """Append raw rig packets (WITS-0 / WITSML channel dicts) after normalising them.

        Real feeds lack channels the replay has, so StrataSense fills them and records which ones are derived:
        tvd from the planned trajectory, rig state inferred from pumps and ROP, d-exponent computed from
        ROP/RPM/WOB/bit size/MW, ECD from mud weight plus the planned annular margin when not sent.
        """
        added = 0
        for r in rows:
            md = r.get("md", r.get("hole_depth"))
            if md is None:
                self.dropped += 1
                continue
            if self.t0_epoch is None:
                self.t0_epoch = r["t_epoch"]
            t = float(r["t_epoch"] - self.t0_epoch)
            if self.data["t"] and t <= self.data["t"][-1]:
                t = self.data["t"][-1] + 1.0          # keep time strictly increasing
            sec = self.target.section_at(md)
            s = {"t": t, "md": float(md)}
            for k in RAW_CHANNELS:
                v = r.get(k, self._last_raw.get(k))
                if v is None:
                    self.derived.add(k)
                    v = {"mw": sec["mw_ppg"], "flow_out": 100.0}.get(k, 0.0)
                s[k] = float(v)
                if k in r:
                    self._last_raw[k] = r[k]
            if "tvd" in r:
                s["tvd"] = float(r["tvd"])
            else:
                s["tvd"] = float(self.target.traj.tvd_at_md(md))
                self.derived.add("tvd")
            if "ecd" not in r and "ecd" not in self._last_raw:
                s["ecd"] = s["mw"] + max(sec["ecd_ppg"] - sec["mw_ppg"], 0.1) + 0.012 * s["rop"] / 10
            pumps = s["flow_in"] > 50
            on_bottom = s["rop"] > 0.2 or ("hole_depth" in r and r["hole_depth"] - md < 1.0)
            s["state"] = 0.0 if pumps and on_bottom else 1.0
            if s["rop"] > 0 and s["wob"] > 0 and s["rpm"] > 0:
                s["dxc"] = float(dxc_formula(np.array([s["rop"]]), np.array([s["rpm"]]), np.array([s["wob"]]),
                                             np.array([_hole_in(sec.get("hole"))]), s["mw"])[0])
            else:
                s["dxc"] = self.data["dxc"][-1] if self.data["dxc"] else 1.0
            self.derived.add("dxc")
            self.derived.add("state")
            for k in CHANNELS:
                self.data[k].append(s[k])
            added += 1
        self.n = len(self.data["t"])
        return added

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
        self._emit = False
        while self.i < min(idx, self.n):
            self._step_one(emit=False)
        self._emit = True
        self.alerts = AlertManager(on_event=self._audit)
        self._gate_of.clear()
        self.opened.clear()
        self.digest.clear()
        self._digest_last.clear()
        self.events_log.clear()
        self.history_events.clear()
        self.audit_buffer.append(audit.make_row("session_jump", session_id=self.session_id, well_id=self.well.id,
                                                t=self.data["t"][max(self.i - 1, 0)], md=self.data["md"][max(self.i - 1, 0)],
                                                payload={"start_index": int(idx)}))

    def set_budget(self, alarms_per_hour: float, actor: str = "RTOC") -> None:
        self.budget = float(alarms_per_hour)
        self.conformal.set_budget(self.budget)
        s = self.recent[-1] if self.recent else None
        self.audit_buffer.append(audit.make_row("budget_changed", session_id=self.session_id, well_id=self.well.id,
                                                t=s["t"] if s else None, md=s["md"] if s else None, actor=actor,
                                                payload={"alarms_per_hour": self.conformal.budget,
                                                         "alpha": self.conformal.alpha}))

    def _opened_last_hour(self, t: float) -> int:
        return sum(1 for t0, lv in self.opened if t0 >= t - 3600 and lv != "critical")

    def _to_digest(self, sig: Signal, s: dict, p: float | None, why: str) -> None:
        """Held-back signals stay visible in a digest and in the decision log (at most once per detector per 15 min)."""
        key = f"{sig.hazard}:{sig.detector}"
        last = self._digest_last.get(key)
        if last is not None and s["t"] - last < 900:
            return
        self._digest_last[key] = s["t"]
        item = {"t": s["t"], "md": round(s["md"], 1), "hazard": sig.hazard, "detector": sig.detector, "level": sig.level,
                "title": sig.title, "message": sig.message, "p_value": None if p is None else round(p, 4), "reason": why}
        self.digest.append(item)
        del self.digest[:-50]
        if self._emit:
            self.audit_buffer.append(audit.make_row("held_in_digest", session_id=self.session_id, well_id=self.well.id,
                                                    t=s["t"], md=s["md"],
                                                    alert={"key": f"RT:{sig.hazard}:{sig.detector}", "hazard": sig.hazard,
                                                           "level": sig.level}, payload=item))

    def alert_load(self, window_h: float = 6.0) -> dict:
        t_now = self.recent[-1]["t"] if self.recent else 0.0
        recent = [lv for t, lv in self.opened if t >= t_now - window_h * 3600]
        span = max(min(window_h, t_now / 3600), 0.25)
        non_crit = sum(1 for lv in recent if lv != "critical")
        return {"window_h": round(span, 2), "opened": len(recent), "non_critical": non_crit,
                "per_hour": round(len(recent) / span, 2), "non_critical_per_hour": round(non_crit / span, 2),
                "budget_per_hour": self.conformal.budget,
                "within_budget": non_crit / span <= self.conformal.budget + 1e-9}

    def _audit(self, alert, event: str, t, md, extra: dict) -> None:
        if event == "opened" and alert.source in ("real-time", "fused"):
            self.opened.append((alert.t, alert.level))
        if not self._emit:
            return
        actor = extra.pop("actor", "StrataSense")
        self.audit_buffer.append(audit.make_row(event, session_id=self.session_id, well_id=self.well.id, t=t, md=md,
                                                alert=alert.to_dict(), actor=actor, payload=extra))

    def pop_audit(self) -> list[dict]:
        out, self.audit_buffer = self.audit_buffer, []
        return out

    def ack(self, alert_id: str, actor: str = "RTOC", key: str | None = None, **extra):
        """extra (e.g. acted_at, queued_offline from a rig tablet that was offline) goes into the decision log."""
        s = self.recent[-1] if self.recent else None
        a = self.alerts.ack(alert_id, actor, s["t"] if s else None, s["md"] if s else None, key=key, **extra)
        if a is None and extra.get("queued_offline") and self._emit:
            # the alert no longer exists in this session, but the person's action must still be on record
            self.audit_buffer.append(audit.make_row("acknowledged", session_id=self.session_id, well_id=self.well.id,
                                                    t=s["t"] if s else None, md=s["md"] if s else None,
                                                    alert={"id": alert_id, "key": key}, actor=actor,
                                                    payload={**extra, "unmatched": True}))
        return a

    def jump_to_episode(self, ep_id: str) -> None:
        if self.live:
            return
        ep = next((e for e in self.episodes if e["id"] == ep_id), None)
        if not ep:
            return
        self.reset(episode_start_index(ep, self.data["t"], self.data["md"]))

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
                "events": self._pop_events(), "done": (not self.live) and self.i >= self.n}

    def _pop_events(self) -> list[dict]:
        out = list(self.events_log)
        self.events_log.clear()
        return out

    def _step_one(self, emit: bool) -> dict:
        s = self.sample(self.i)
        if self.dtw_done is None:
            self.dtw_done = self._dtw_done_below(s["tvd"])
        if "fm" in self.data:
            fm_true = FORMATION_ORDER[int(self.data["fm"][self.i])]
            # mud-logger top pick (truth revealed with lag, as in real operations)
            if self.top_mode != "dtw" and self.prev_fm is not None and fm_true != self.prev_fm and fm_true not in self.picked:
                self.pending_picks.append((self.i + PICK_LAG_SAMPLES, fm_true, s["tvd"]))
            self.prev_fm = fm_true
        for pk in list(self.pending_picks):
            if self.i >= pk[0]:
                self.pending_picks.remove(pk)
                self._pick_top(pk[1], pk[2], s)
        if self.toppicker is not None:
            self.toppicker.add(s)
            self._dtw_step(s)
        fm_est, rel = formation_at(self.tops, s["tvd"], self.kb)
        s["fm_est"] = FORMATION_ORDER.index(fm_est)
        s["formation"] = fm_est
        s["inc"] = float(self.target.traj.inc_at_md(s["md"]))
        self.recent.append(s)
        if len(self.recent) > 120:
            self.recent.pop(0)
        signals = self.det.update(s)
        keep = set()
        self.conformal.update(self.det.scores)
        for sig in signals:
            gate = sig.extra.get("gate")
            p, why = None, None
            if gate is not None:
                existing = self.alerts.alerts.get(f"RT:{sig.hazard}:{sig.detector}")
                if existing is not None and existing.status != "cleared":
                    p = self.conformal.p_value(gate[0])       # already on the console: keep it updated
                else:
                    corroborated = self.alerts.active_lookahead(sig.hazard, s["md"]) is not None
                    allow, p, why = self.conformal.gate(gate[0], sig.level, corroborated,
                                                        self._opened_last_hour(s["t"]))
                    if not allow:
                        self._to_digest(sig, s, p, why)
                        continue
            keep.add(self._handle_signal(sig, s, p, gate[0] if gate else None))
        alarming = {g for k, g in self._gate_of.items()
                    if k in self.alerts.alerts and self.alerts.alerts[k].status != "cleared"}
        self.conformal.observe(alarming)
        self._lookahead(s)
        if s["md"] - self.last_window_check_md > 40 and s["state"] == 0:
            self.last_window_check_md = s["md"]
            self._window_check(s, fm_est)
        if self.prev_t is not None and s["state"] != 0:
            self.alerts.freeze(s["t"] - self.prev_t)
        self.prev_t = s["t"]
        self.alerts.clear_stale(s["t"], keep)
        self.i += 1
        return s

    # ------------------------------------------------------------------ pieces
    def _dtw_step(self, s: dict) -> None:
        """Gamma-ray correlation (DTW) against offsets: auto-pick (mode 'dtw') or independent QC (mode 'auto')."""
        code = next_formation(dict.fromkeys(self.dtw_done, 0.0), self.prior_tops, s["tvd"])
        if code is None:
            return
        prior = self.tops[code] if self.top_mode == "dtw" else self.prior_tops[code]
        p = self.toppicker.try_pick(code, prior["tvd"], prior["sd"], s["tvd"])
        if p is None:
            return
        self.dtw_done.add(code)
        name = formation_name(code)
        rec = {k: p[k] for k in ("formation", "tvd", "sd", "n_refs", "refs")} | {"md": round(s["md"], 1), "t": s["t"]}
        self.dtw_picks.append(rec)
        if self.top_mode == "dtw":
            self._pick_top(code, p["tvd"], s, source="dtw", info=p)
            return
        ml = self.picked.get(code)
        if ml is None:
            msg = (f"GR correlation (DTW, {p['n_refs']} offsets) puts the {name} top at {p['tvd']:,.0f} ± {p['sd']:.0f} m TVD; "
                   f"awaiting mud-logger pick")
            conflict = False
        else:
            delta = p["tvd"] - ml
            conflict = abs(delta) > config.DTW_CONFLICT_M
            msg = (f"GR correlation (DTW, {p['n_refs']} offsets) {'DISAGREES with' if conflict else 'confirms'} the {name} "
                   f"mud-logger pick: {p['tvd']:,.0f} vs {ml:,.0f} m TVD ({delta:+.0f} m)")
            rec["delta_vs_mudlogger"] = round(delta, 1)
        ev = {"type": "dtw_check", "formation": code, "tvd": p["tvd"], "sd": p["sd"], "md": round(s["md"], 1),
              "t": s["t"], "n_refs": p["n_refs"], "conflict": conflict, "source": "dtw", "message": msg}
        self.events_log.append(ev)
        self.history_events.append(ev)
        if conflict:
            self.alerts.upsert(f"GEO:DTW:{code}", "GEO", "geology", "watch", f"Top correlation conflict: {name}",
                               msg + ". Check cuttings and the LWD GR before trusting the re-anchored look-ahead.",
                               s["md"], s["t"], formation=code, confidence=0.5)

    def _pick_top(self, code: str, tvd: float, s: dict, source: str = "mudlogger", info: dict | None = None) -> None:
        pred = self.tops.get(code, {}).get("tvd")
        self.picked[code] = tvd
        self.det.reset_dxc()
        for key in [k for k in self.alerts.alerts if k.startswith("MW:") and not k.endswith(f":{code}")]:
            self.alerts.clear(key, s["t"], "bit left the formation")
        self._recompute_profile()
        delta = tvd - pred if pred is not None else 0.0
        how = (f"auto-picked by GR correlation (DTW, {info['n_refs']} offsets, ± {info['sd']:.0f} m)"
               if source == "dtw" and info else "picked")
        ev = {"type": "top_pick", "formation": code, "tvd": round(tvd, 1), "md": round(s["md"], 1),
              "t": s["t"], "delta_m": round(delta, 1), "source": source,
              "message": f"{formation_name(code)} top {how} at {tvd:,.0f} m TVD "
                         f"({delta:+.0f} m vs prognosis) - look-ahead re-anchored"}
        self.events_log.append(ev)
        self.history_events.append(ev)
        self.alerts.upsert(f"GEO:{code}", "GEO", "geology", "info", f"Top picked: {formation_name(code)}",
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
                title = f"Look-ahead: {hz} zone in {formation_name(z['formation'])} at {z['md0']:,.0f}-{z['md1']:,.0f} m"
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
                               f"Mud outside offset-derived window ({formation_name(fm)})", f["message"], s["md"],
                               s["t"], formation=fm, confidence=f["p"],
                               drivers=[{"channel": "ecd" if f["hazard"] == "LOSS" else "mw", "value": f["value"],
                                         "baseline": f["limit"], "unit": "ppg"}])

    def _handle_signal(self, sig: Signal, s: dict, p_value: float | None = None, gate_key: str | None = None) -> str:
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
                               zone=la.zone if la else None,
                               p_value=None if p_value is None else round(p_value, 4),
                               calibrated=p_value is not None)
        if gate_key:
            self._gate_of[key] = gate_key
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
        i = max(min(self.i, self.n - 1), 0)
        if self.recent:
            s = self.recent[-1]
        elif self.n:
            s = self.sample(i)
        else:   # live feed connected but no packet yet
            sec0 = self.target.sections[0]
            s = {"t": 0.0, "md": 0.0, "tvd": 0.0, "mw": sec0["mw_ppg"], "ecd": sec0["ecd_ppg"]}
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
                "progress": 1.0 if self.live else round(self.i / self.n, 4), "alert_load": self.alert_load(),
                "waiting": self.live and self.n == 0,
                "budget": self.conformal.state(), "session_id": self.session_id, "digest": self.digest[-8:],
                "mode": "live" if self.live else "replay", "top_pick_mode": self.top_mode,
                "stream": ({**self.source.stats(), "derived": sorted(self.derived), "dropped": self.dropped}
                           if self.live else None),
                "episode": next((e["id"] for e in self.episodes if e.get("onset_md", e["md"]) - 200 <= s["md"] <= e["md"] + 80), None)}

    def ribbon(self, md_from: float, span: float = 300.0) -> list[dict]:
        out = []
        for b in self.profile["bins"]:
            if b["md1"] < md_from or b["md0"] > md_from + span:
                continue
            out.append({"md0": b["md0"], "md1": b["md1"], "formation": b["formation"],
                        "risk": {hz: h["final"] for hz, h in b["hazards"].items()}})
        return out
