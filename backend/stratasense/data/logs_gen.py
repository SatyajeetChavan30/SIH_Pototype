"""SYNTHETIC drilling-parameter logs.

* Offset wells: depth-indexed logs (2 m) with lithology-driven GR/ROP and event
  precursors (torque ramps before sticking, dxc drop + gas before kicks, ECD
  loading before losses). Used by the correlation view and Analog Replay.
* Active well: a time-indexed eRTMAC-style stream (WITS channels) with scripted
  but physically consistent episodes that the real-time detectors must catch.
"""
from __future__ import annotations

import math

import numpy as np

from ..domain.ontology import FORMATION_ORDER
from .synth import (STRUCT_BY_ID, TruthEvent, Well, collapse_gradient, loss_gradient, pore_pressure,
                    section_at)

LITH = {  # (net sand fraction, coal prob, limestone)
    "ALLUVIUM": (0.6, 0, 0), "DHEKIAJULI": (0.55, 0, 0), "NAMSANG": (0.7, 0, 0), "GIRUJAN": (0.15, 0, 0),
    "TIPAM": (0.75, 0, 0), "BARAIL": (0.45, 0.12, 0), "KOPILI": (0.15, 0, 0), "SYLHET": (0.1, 0, 0.8),
    "LANGPAR": (0.5, 0, 0), "BASEMENT": (0.0, 0, 0),
}
GR = {"sand": 45.0, "shale": 108.0, "coal": 32.0, "lime": 24.0}
ROP = {"sand": 26.0, "shale": 13.0, "coal": 32.0, "lime": 7.0}
BIT_IN = [26.0, 17.5, 12.25, 8.5]
FLOW = [1050.0, 900.0, 720.0, 460.0]
CHANNELS = ["md", "tvd", "gr", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "hookload", "gas", "ecd",
            "mw", "dxc", "fm"]


def formation_at(tops_tvd: dict[str, float], tvd: float) -> tuple[str, float]:
    codes = [c for c in FORMATION_ORDER if c in tops_tvd]
    cur = codes[0]
    for c in codes:
        if tops_tvd[c] <= tvd:
            cur = c
    i = FORMATION_ORDER.index(cur)
    top = tops_tvd[cur]
    nxt = FORMATION_ORDER[i + 1] if i + 1 < len(FORMATION_ORDER) else None
    base = tops_tvd.get(nxt, top + 400) if nxt else top + 400
    return cur, float(np.clip((tvd - top) / max(base - top, 1), 0, 1))


def lithology_column(rng, tops_tvd, tvd_grid):
    """Bed-scale lithology per depth sample."""
    lith = np.empty(len(tvd_grid), dtype=object)
    i = 0
    while i < len(tvd_grid):
        fm, _ = formation_at(tops_tvd, tvd_grid[i])
        ntg, coal, lime = LITH[fm]
        thick = int(rng.integers(2, 9))
        r = rng.random()
        if lime and r < lime:
            l = "lime"
        elif coal and r < coal:
            l = "coal"
            thick = int(rng.integers(1, 3))
        elif rng.random() < ntg:
            l = "sand"
        else:
            l = "shale"
        lith[i:i + thick] = l
        i += thick
    return lith


def dxc(rop_m_hr, rpm, wob_klbf, bit_in, mw_ppg):
    r = np.maximum(rop_m_hr * 3.28084, 0.5)
    n = np.maximum(rpm, 1)
    w = np.maximum(wob_klbf * 1000, 1000)
    d = np.log10(r / (60 * n)) / np.log10(12 * w / (1e6 * bit_in))
    return d * (8.9 / mw_ppg)


def offset_logs(rng, well: Well) -> dict[str, np.ndarray]:
    traj = well.trajectory()
    md = np.arange(0, well.td_md, 2.0)
    tvd = traj.tvd_at_md(md)
    inc = traj.inc_at_md(md)
    T = getattr(well, "tops_tvd_full", well.tops_tvd)
    lith = lithology_column(rng, T, tvd)
    struct = STRUCT_BY_ID[well.structure_id]
    n = len(md)
    out = {c: np.zeros(n, dtype=np.float32) for c in CHANNELS}
    sec_idx = np.array([min(3, [i for i, s in enumerate(well.sections) if s["top_md"] <= m <= s["shoe_md"] + 1e-6][0]
                              if any(s["top_md"] <= m <= s["shoe_md"] + 1e-6 for s in well.sections) else 3)
                        for m in md])
    mw = np.array([well.sections[i]["mw_ppg"] for i in sec_idx])
    ecd = np.array([well.sections[i]["ecd_ppg"] for i in sec_idx]) + rng.normal(0, 0.03, n)
    fm_idx = np.zeros(n)
    pp = np.zeros(n)
    for k in range(n):
        fm, rel = formation_at(T, tvd[k])
        fm_idx[k] = FORMATION_ORDER.index(fm)
        pp[k] = pore_pressure(fm, rel, well.lat, well.lon)
    gr = np.array([GR[l] for l in lith]) + rng.normal(0, 7, n)
    base_rop = np.array([ROP[l] for l in lith]) * np.exp(-tvd / 5200) * 2.2
    over = np.clip(pp - mw + 0.6, 0, None)
    rop = base_rop * np.exp(0.9 * over) * rng.lognormal(0, 0.12, n)
    wob = np.array([[12, 20, 25, 22][i] for i in sec_idx]) + rng.normal(0, 1.5, n)
    rpm = np.array([[90, 120, 130, 140][i] for i in sec_idx]) + rng.normal(0, 4, n)
    torque = 3.0 + 0.0032 * md * (1 + inc / 45) + (lith == "coal") * 2.5 + rng.normal(0, 0.45, n)
    spp = 700 + 0.72 * md + rng.normal(0, 25, n)
    flow_in = np.array([FLOW[i] for i in sec_idx]) + rng.normal(0, 5, n)
    flow_out = 100 + rng.normal(0, 1.2, n)
    hook = 35 + 0.043 * md * np.cos(np.radians(inc)) + rng.normal(0, 1.5, n)
    gas = 0.4 + 0.25 * np.clip(pp - 8.9, 0, None) + (lith == "coal") * 0.8 + np.abs(rng.normal(0, 0.12, n))
    bit = np.array([BIT_IN[i] for i in sec_idx])

    def window(center, before, after=0):
        return (md >= center - before) & (md <= center + after)

    for e in well.events:
        pre = window(e.md, 30, 0)
        at = window(e.md, 0, 6)
        if e.hazard in ("STUCK", "TIGHT"):
            ramp = np.clip((md - (e.md - 30)) / 30, 0, 1) * pre
            torque += ramp * torque * 0.45
            hook += at * rng.uniform(25, 55)
            if e.subtype in ("pack-off", "bit balling"):
                spp += ramp * 180 + at * 350
                rop *= np.where(pre, 0.7, 1.0)
        elif e.hazard == "TORQUE":
            torque += (window(e.md, 10, 10)) * rng.normal(6, 2, n)
        elif e.hazard == "INSTAB":
            torque += window(e.md, 15, 10) * np.abs(rng.normal(3, 1.5, n))
            spp += window(e.md, 10, 10) * 120
        elif e.hazard == "LOSS":
            ecd += np.clip((md - (e.md - 30)) / 30, 0, 1) * pre * 0.18
            drop = {"seepage": 6, "partial": 25, "total": 95}.get(e.severity, 20)
            flow_out -= at * drop
        elif e.hazard == "KICK":
            ramp = np.clip((md - (e.md - 50)) / 50, 0, 1) * window(e.md, 50, 0)
            rop *= 1 + ramp * 0.8
            gas += ramp * e.extra.get("gas_pct", 8) * 0.6 + at * e.extra.get("gas_pct", 10)
            if e.subtype == "kick":
                flow_out += at * 18
    out.update(md=md, tvd=tvd, gr=gr, rop=rop, wob=wob, rpm=rpm, torque=torque, spp=spp, flow_in=flow_in,
               flow_out=flow_out, hookload=hook, gas=gas, ecd=ecd, mw=mw, fm=fm_idx)
    out["dxc"] = dxc(rop, rpm, wob, bit, mw)
    return {k: np.asarray(v, dtype=np.float32) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Active well real-time stream
# ---------------------------------------------------------------------------

ACTIVE_START_MD = 1750.0


def active_episodes(active: Well) -> list[dict]:
    """Scripted episodes placed where the (hidden) geology says they happen."""
    traj = active.trajectory()
    T = active.tops_tvd_full

    def md_at(code, rel):
        i = FORMATION_ORDER.index(code)
        top, base = T[code], T[FORMATION_ORDER[i + 1]]
        return float(traj.md_at_tvd(top + rel * (base - top)))

    thief = STRUCT_BY_ID["NDH"].thief_rel
    # overpressure onset where pore pressure exceeds the planned 8-1/2" MW
    mw3 = active.sections[3]["mw_ppg"]
    rels = np.linspace(0.45, 0.98, 200)
    pps = np.array([pore_pressure("BARAIL", r, active.lat, active.lon) for r in rels])
    kick_rel = float(rels[int(np.argmax(pps > mw3))]) if (pps > mw3).any() else 0.85
    return [
        {"id": "S1", "hazard": "LOSS", "label": "Partial losses in depleted Tipam thief sand", "md": md_at("TIPAM", thief),
         "rate": 60.0, "duration_s": 3000},
        {"id": "S2", "hazard": "STUCK", "label": "Stuck-pipe precursor in Barail coal/shale", "md": md_at("BARAIL", 0.30),
         "duration_s": 2400},
        {"id": "S3", "hazard": "KICK", "label": "Overpressure transition and kick in lower Barail", "md": md_at("BARAIL", kick_rel),
         "onset_md": md_at("BARAIL", max(kick_rel - 0.14, 0.45)), "pit_gain": 14.0, "kill_mw": round(float(pps.max()) + 0.35, 2),
         "duration_s": 3600},
        {"id": "S4", "hazard": "LOSS", "label": "Severe losses in fractured Sylhet limestone", "md": md_at("SYLHET", 0.08),
         "rate": 250.0, "duration_s": 2400},
    ]


def active_stream(rng, active: Well) -> tuple[dict[str, np.ndarray], list[dict]]:
    traj = active.trajectory()
    T = active.tops_tvd_full
    episodes = active_episodes(active)
    ep_by_id = {e["id"]: e for e in episodes}
    rows: list[list[float]] = []
    t = 0.0
    md = ACTIVE_START_MD
    pit = 520.0
    next_conn = math.ceil(md / 28.0) * 28.0
    lith_state = ("shale", 0.0)
    mw_override = None  # after kick kill
    fired = set()
    in_episode = None
    ep_t0 = 0.0

    def lith_at(tvd):
        nonlocal lith_state
        l, until = lith_state
        if tvd >= until:
            fm, _ = formation_at(T, tvd)
            ntg, coal, lime = LITH[fm]
            r = rng.random()
            l = "lime" if lime and r < lime else ("coal" if coal and r < coal else ("sand" if rng.random() < ntg else "shale"))
            lith_state = (l, tvd + rng.uniform(2, 8))
        return lith_state[0]

    while md < active.td_md - 1:
        sec = section_at(active.sections, md)
        si = active.sections.index(sec)
        tvd = float(traj.tvd_at_md(md))
        inc = float(traj.inc_at_md(md))
        fm, rel = formation_at(T, tvd)
        mw = mw_override if (mw_override and si == 3) else sec["mw_ppg"]
        pp = pore_pressure(fm, rel, active.lat, active.lon)
        l = lith_at(tvd)
        dt = 60.0
        # episode activation
        for ep in episodes:
            start_md = ep.get("onset_md", ep["md"] - 25)
            if ep["id"] not in fired and md >= start_md:
                fired.add(ep["id"])
                in_episode = ep
                ep_t0 = t
        if in_episode:
            dt = 30.0
        rop = ROP[l] * math.exp(-tvd / 5200) * 2.2 * math.exp(0.9 * max(pp - mw + 0.6, 0)) * rng.lognormal(0, 0.1)
        wob = [12, 20, 25, 22][si] + rng.normal(0, 1.2)
        rpm = [90, 120, 130, 140][si] + rng.normal(0, 3)
        flow_in = FLOW[si] + rng.normal(0, 4)
        torque = 3.0 + 0.0032 * md * (1 + inc / 45) + (l == "coal") * 2.5 + rng.normal(0, 0.35)
        spp = 700 + 0.72 * md + rng.normal(0, 20)
        hook = 35 + 0.043 * md * math.cos(math.radians(inc)) + rng.normal(0, 1.2)
        gas = 0.4 + 0.25 * max(pp - 8.9, 0) + (l == "coal") * 0.6 + abs(rng.normal(0, 0.1))
        flow_out = 100 + rng.normal(0, 1.0)
        ecd = mw + sec["ecd_add"] + 0.012 * rop / 10 + rng.normal(0, 0.02)
        gr = GR[l] + rng.normal(0, 6)
        pumps_on = True
        tag = ""
        # --- connections every stand (pumps off, pick-up overpull reveals hole condition)
        if md >= next_conn:
            next_conn += 28.0
            for k in range(4):
                over = 0.0
                if in_episode and in_episode["id"] == "S2":
                    over = 18 + 14 * min((t - ep_t0) / in_episode["duration_s"], 1) * 2
                rows.append([t, md, tvd, gr, 0, 0, 0, 0, 0 if k < 3 else spp * 0.2, 0, 0, pit,
                             hook + (over + rng.normal(0, 2) if k == 1 else 0), gas + (1.5 if pp > mw - 0.3 and k == 3 else 0),
                             mw, mw, 0, FORMATION_ORDER.index(fm), 1])
                t += 60.0
            continue
        # --- episodes
        if in_episode:
            ep = in_episode
            el = t - ep_t0
            if ep["hazard"] == "LOSS" and md >= ep["md"] - 0.5:
                rate = ep["rate"]
                lt = t - ep.setdefault("t_loss", t)
                frac = min(lt / 600, 1.0)
                if ep["id"] == "S1" and lt > 1800:  # LCM pill pumped per StrataSense recommendation -> cured
                    frac = max(0.0, 1 - (lt - 1800) / 600)
                flow_out -= frac * rate / (flow_in * 60 / 42) * 100
                pit -= frac * rate * dt / 3600
                tag = ep["id"]
                rop *= 0.6
                if lt > ep["duration_s"]:
                    in_episode = None
            elif ep["hazard"] == "LOSS":
                ecd += 0.15 * min((md - (ep["md"] - 25)) / 25, 1)
                tag = ep["id"] + "-pre"
            elif ep["hazard"] == "STUCK":
                ramp = min(el / (ep["duration_s"] * 0.6), 1.0)
                if el > ep["duration_s"] * 0.75:  # crew back-reams + sweeps -> recovers
                    ramp = max(0.0, 1 - (el - ep["duration_s"] * 0.75) / (ep["duration_s"] * 0.25))
                torque *= 1 + 0.55 * ramp + rng.normal(0, 0.12) * ramp
                spp += ramp * (150 + (300 if rng.random() < 0.15 * ramp else 0))
                rop *= 1 - 0.4 * ramp
                tag = "S2"
                if el > ep["duration_s"]:
                    in_episode = None
            elif ep["hazard"] == "KICK":
                if md < ep["md"]:
                    prog = (md - ep["onset_md"]) / max(ep["md"] - ep["onset_md"], 1)
                    rop *= 1 + 0.9 * prog
                    gas += 6 * prog ** 1.5
                    tag = "S3-pre"
                else:
                    k_el = t - ep.setdefault("t_kick", t)
                    if k_el < 900:
                        g = k_el / 900
                        flow_out += 16 * g
                        pit += ep["pit_gain"] * dt / 900
                        gas += 10 + 8 * g
                        rop *= 1.6
                        tag = "S3"
                    elif k_el < 900 + 1800:  # shut-in and kill: no drilling
                        rows.append([t, md, tvd, gr, 0, 0, 0, 0, 0, 0, 0, pit, hook, gas + 6 * (1 - (k_el - 900) / 1800),
                                     ep["kill_mw"], ep["kill_mw"], 0, FORMATION_ORDER.index(fm), 2])
                        t += 60.0
                        continue
                    else:
                        mw_override = ep["kill_mw"]
                        in_episode = None
        step = rop * dt / 3600.0
        rows.append([t, md, tvd, gr, rop, wob, rpm, torque, spp, flow_in, flow_out, pit, hook, gas, mw, ecd,
                     float(dxc(np.array([rop]), np.array([rpm]), np.array([wob]), np.array([BIT_IN[si]]), mw)[0]),
                     FORMATION_ORDER.index(fm), 0])
        t += dt
        md += step * (0.3 if (in_episode and in_episode["hazard"] == "LOSS" and tag == in_episode["id"]) else 1.0)
    cols = ["t", "md", "tvd", "gr", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload", "gas",
            "mw", "ecd", "dxc", "fm", "state"]
    arr = np.array(rows, dtype=np.float64)
    return {c: arr[:, i] for i, c in enumerate(cols)}, episodes
